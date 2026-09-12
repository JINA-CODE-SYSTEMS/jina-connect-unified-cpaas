"""Promote the WhatsApp ``wamid`` to a column and make it unique (#330).

Inbound ingestion had no idempotency key. Meta redelivers a webhook whenever
it does not see a timely 200 — and sometimes anyway — and ``wa.tasks``
created a ``Messages`` row unconditionally, so the customer's message appeared
twice and, worse, the chat_flow trigger fired twice: the dispatcher's replay
guard keys on the new row's pk, which a redelivery always makes fresh.

The wamid was already on the row, buried in ``content["_meta"]``. A JSON key
cannot carry a uniqueness constraint usefully, so it moves to a real column
and gains a partial unique index over ``(tenant, platform,
provider_message_id)``.

Three things this has to tolerate, hence the shape:

* **Rows with no provider id.** Outbound rows, and inbound from platforms
  whose payload carries no stable id, leave the column NULL. The index
  predicate excludes NULL *and* the empty string rather than relying on the
  backend's NULL semantics.
* **Existing rows.** The backfill below copies the id out of the JSON for
  inbound WhatsApp rows.
* **Duplicates already on disk.** The very bug being fixed may have written
  some, and the constraint is added after the backfill, so the backfill
  claims the id for the *earliest* row of each group and leaves the later
  copies NULL. They stay in the inbox — this migration does not delete a
  customer's messages — but they no longer block the index, and the next
  redelivery of any of those ids will be recognised.

The backfill is safe to re-run: it only ever fills a NULL, and it seeds its
"already claimed" set from the column itself, so a second pass over a
partially-migrated table assigns nothing twice.
"""

from django.db import migrations, models

#: Where the id lived before this migration.
_META_KEY = "wa_message_id"

#: Rows written in one ``bulk_update``.
_BATCH = 500


def _wamid(row) -> str:
    content = row.content if isinstance(row.content, dict) else {}
    meta = content.get("_meta")
    if not isinstance(meta, dict):
        return ""
    value = meta.get(_META_KEY)
    return value if isinstance(value, str) else ""


def backfill_provider_message_id(apps, schema_editor):
    """Copy ``content["_meta"]["wa_message_id"]`` into the new column."""
    Messages = apps.get_model("team_inbox", "Messages")

    # Ids some earlier (or interrupted) pass already claimed, so a re-run does
    # not hand the same id to a second row.
    claimed = set(
        Messages.objects.filter(platform="WHATSAPP", provider_message_id__isnull=False)
        .exclude(provider_message_id="")
        .values_list("tenant_id", "provider_message_id")
    )

    pending = []

    # Oldest first: where the bug wrote duplicates, the id belongs to the copy
    # the inbox has been showing the longest, and pk breaks the tie when two
    # rows share a timestamp.
    rows = (
        Messages.objects.filter(platform="WHATSAPP", direction="INCOMING", provider_message_id__isnull=True)
        .order_by("timestamp", "pk")
        .iterator(chunk_size=_BATCH)
    )

    for row in rows:
        wamid = _wamid(row)
        if not wamid:
            continue
        key = (row.tenant_id, wamid)
        if key in claimed:
            continue
        claimed.add(key)
        row.provider_message_id = wamid
        pending.append(row)
        if len(pending) >= _BATCH:
            Messages.objects.bulk_update(pending, ["provider_message_id"])
            pending = []

    if pending:
        Messages.objects.bulk_update(pending, ["provider_message_id"])


class Migration(migrations.Migration):
    dependencies = [
        ("team_inbox", "0011_messagetag_description_messagetag_is_active_and_more"),
    ]

    operations = [
        migrations.AddField(
            model_name="messages",
            name="provider_message_id",
            field=models.CharField(
                blank=True,
                help_text=(
                    "Provider's own message id (WhatsApp wamid) for inbound idempotency. "
                    "Unique per tenant and platform when set; left unset for outbound rows "
                    "and for platforms that give us no stable inbound id."
                ),
                max_length=255,
                null=True,
            ),
        ),
        # Backwards is a no-op rather than a wipe: the column is dropped by
        # reversing the AddField above, and the ids are still in the JSON.
        migrations.RunPython(backfill_provider_message_id, migrations.RunPython.noop),
        migrations.AddConstraint(
            model_name="messages",
            constraint=models.UniqueConstraint(
                condition=models.Q(
                    ("provider_message_id__isnull", False), models.Q(("provider_message_id", ""), _negated=True)
                ),
                fields=("tenant", "platform", "provider_message_id"),
                name="message_provider_msg_id_uniq",
            ),
        ),
    ]

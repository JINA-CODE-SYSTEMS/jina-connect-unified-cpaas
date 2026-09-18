"""Archiving contacts in bulk — what it hides, and what it must not destroy.

"Bulk delete" on the contacts screen is an archive: ``is_active`` goes false
and the row stays. That is not timidity, it is what the schema requires. Five
things cascade off ``TenantContact`` — team-inbox messages and events,
WhatsApp conversations, per-recipient broadcast rows, CTWA leads — so a real
delete takes the conversation history and the records refunds are counted from
(#271) with it. Worse, ``marketing_opt_out`` lives on this row and nowhere
else: delete a contact who replied STOP, re-import the number from a file, and
they are opted back in with no trace that they ever objected.

``is_active`` was free to mean this. Nothing in the codebase set it false on a
contact before now.

HOW TO RUN:
    python -m pytest contacts/tests/test_bulk_archive.py -v
"""

from __future__ import annotations

import itertools

import pytest
from django.contrib.auth import get_user_model
from rest_framework.test import APIClient

from contacts.models import MarketingOptOutSource, TenantContact
from tenants.models import Tenant, TenantUser

User = get_user_model()

ARCHIVE_URL = "/contacts/bulk-archive/"
RESTORE_URL = "/contacts/bulk-restore/"
LIST_URL = "/contacts/"

_seq = itertools.count(1)


# ─────────────────────────────────────────────────────────────────────────────
# Fixtures
# ─────────────────────────────────────────────────────────────────────────────


@pytest.fixture()
def tenant(db):
    return Tenant.objects.create(name=f"Org {next(_seq)}")


def _owner(tenant):
    """An owner of *tenant*, which is who holds ``contact.delete``."""
    from tenants.models import TenantRole

    n = next(_seq)
    user = User.objects.create_user(username=f"owner{n}", email=f"owner{n}@example.test", password="pw")  # noqa: S106
    role, _ = TenantRole.objects.get_or_create(tenant=tenant, slug="owner", defaults={"name": "Owner", "priority": 100})
    TenantUser.objects.create(user=user, tenant=tenant, role=role, is_active=True)
    return user


def _client_for(user):
    client = APIClient()
    client.force_authenticate(user=user)
    return client


def _contact(tenant, **kwargs):
    n = next(_seq)
    return TenantContact.objects.create(
        tenant=tenant,
        phone=kwargs.pop("phone", f"+2782{n:07d}"),
        first_name=kwargs.pop("first_name", f"C{n}"),
        **kwargs,
    )


# ─────────────────────────────────────────────────────────────────────────────
# The feature
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.django_db
def test_archiving_hides_contacts_from_the_list():
    tenant = Tenant.objects.create(name="Hide")
    api = _client_for(_owner(tenant))
    kept, archived = _contact(tenant), _contact(tenant)

    response = api.post(ARCHIVE_URL, {"ids": [archived.pk]}, format="json")

    assert response.status_code == 200, response.data
    assert response.data["archived"] == 1

    listed = api.get(LIST_URL).data
    rows = listed["results"] if isinstance(listed, dict) else listed
    assert [row["id"] for row in rows] == [kept.pk]


@pytest.mark.django_db
def test_the_row_and_everything_hanging_off_it_survives():
    """The reason this is an archive at all.

    Asserted on the opt-out specifically because it is the one that cannot be
    reconstructed from anywhere else, and because re-importing the number is
    an ordinary thing to do afterwards.
    """
    tenant = Tenant.objects.create(name="Survives")
    api = _client_for(_owner(tenant))
    contact = _contact(tenant)
    contact.set_marketing_opt_out(opted_out=True, source=MarketingOptOutSource.KEYWORD)

    api.post(ARCHIVE_URL, {"ids": [contact.pk]}, format="json")

    contact.refresh_from_db()
    assert contact.is_active is False
    assert contact.marketing_opt_out is True, "the contact's own STOP must outlive the archive"
    assert contact.marketing_opt_out_source == MarketingOptOutSource.KEYWORD


@pytest.mark.django_db
def test_restoring_puts_them_back():
    tenant = Tenant.objects.create(name="Restore")
    api = _client_for(_owner(tenant))
    contact = _contact(tenant)

    api.post(ARCHIVE_URL, {"ids": [contact.pk]}, format="json")
    response = api.post(RESTORE_URL, {"ids": [contact.pk]}, format="json")

    assert response.status_code == 200, response.data
    assert response.data["restored"] == 1
    contact.refresh_from_db()
    assert contact.is_active is True


@pytest.mark.django_db
def test_the_archive_view_is_reachable_by_asking_for_it():
    """Restore is impossible from a screen that cannot show archived rows."""
    tenant = Tenant.objects.create(name="View")
    api = _client_for(_owner(tenant))
    contact = _contact(tenant)
    api.post(ARCHIVE_URL, {"ids": [contact.pk]}, format="json")

    listed = api.get(LIST_URL, {"is_active": "false"}).data
    rows = listed["results"] if isinstance(listed, dict) else listed

    assert [row["id"] for row in rows] == [contact.pk]


# ─────────────────────────────────────────────────────────────────────────────
# Scoping — the ids come from the client and prove nothing
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.django_db
def test_another_organisations_contact_cannot_be_archived():
    """#346's lesson, applied to a write that takes ids straight from a body."""
    mine, theirs = Tenant.objects.create(name="Mine"), Tenant.objects.create(name="Theirs")
    api = _client_for(_owner(mine))
    victim = _contact(theirs)

    response = api.post(ARCHIVE_URL, {"ids": [victim.pk]}, format="json")

    assert response.status_code == 200, response.data
    assert response.data["archived"] == 0
    assert response.data["not_found"] == [victim.pk]
    victim.refresh_from_db()
    assert victim.is_active is True


@pytest.mark.django_db
def test_a_mixed_request_archives_only_what_it_may():
    mine, theirs = Tenant.objects.create(name="Mixed mine"), Tenant.objects.create(name="Mixed theirs")
    api = _client_for(_owner(mine))
    ours, victim = _contact(mine), _contact(theirs)

    response = api.post(ARCHIVE_URL, {"ids": [ours.pk, victim.pk]}, format="json")

    assert response.data["archived"] == 1
    assert response.data["not_found"] == [victim.pk]
    victim.refresh_from_db()
    assert victim.is_active is True


# ─────────────────────────────────────────────────────────────────────────────
# Counts that mean what they say
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.django_db
def test_archiving_the_same_contact_twice_reports_it_honestly():
    tenant = Tenant.objects.create(name="Twice")
    api = _client_for(_owner(tenant))
    contact = _contact(tenant)

    api.post(ARCHIVE_URL, {"ids": [contact.pk]}, format="json")
    response = api.post(ARCHIVE_URL, {"ids": [contact.pk]}, format="json")

    assert response.data["archived"] == 0
    assert response.data["already_archived"] == 1


@pytest.mark.django_db
def test_a_repeated_id_counts_once():
    tenant = Tenant.objects.create(name="Dupes")
    api = _client_for(_owner(tenant))
    contact = _contact(tenant)

    response = api.post(ARCHIVE_URL, {"ids": [contact.pk, contact.pk]}, format="json")

    assert response.data["archived"] == 1


@pytest.mark.django_db
def test_an_empty_or_oversized_request_is_refused():
    tenant = Tenant.objects.create(name="Bounds")
    api = _client_for(_owner(tenant))

    assert api.post(ARCHIVE_URL, {"ids": []}, format="json").status_code == 400
    assert api.post(ARCHIVE_URL, {"ids": list(range(1, 1002))}, format="json").status_code == 400


# ─────────────────────────────────────────────────────────────────────────────
# The consequence that would make archiving a lie
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.django_db
def test_send_to_everyone_skips_archived_contacts():
    """Hiding someone from the list while still messaging them is worse than
    not hiding them, because the operator believes they are gone.

    Driven through ``BroadcastSerializer`` rather than by repeating its query
    here: a test that re-derives the filter would pass with the call site
    unchanged, which is the failure this repository keeps meeting.
    """
    from broadcast.models import BroadcastPlatformChoices, BroadcastStatusChoices
    from broadcast.serializers import BroadcastSerializer

    tenant = Tenant.objects.create(name="Blast")
    kept, archived = _contact(tenant), _contact(tenant)
    archived.is_active = False
    archived.save(update_fields=["is_active"])

    serializer = BroadcastSerializer(
        data={
            "tenant": tenant.pk,
            "name": "All hands",
            "platform": BroadcastPlatformChoices.WHATSAPP,
            "status": BroadcastStatusChoices.DRAFT,
            # ``recipients`` is required by the model, so a real caller always
            # names some; ``select_all`` then replaces them with the resolved
            # set. Naming the archived contact here makes the assertion sharp:
            # it is dropped by the resolution, not merely absent from it.
            "recipients": [archived.pk],
            "select_all": True,
        }
    )
    serializer.is_valid(raise_exception=True)
    broadcast = serializer.save()

    assert list(broadcast.recipients.values_list("pk", flat=True)) == [kept.pk]
    assert archived.pk not in set(broadcast.recipients.values_list("pk", flat=True))

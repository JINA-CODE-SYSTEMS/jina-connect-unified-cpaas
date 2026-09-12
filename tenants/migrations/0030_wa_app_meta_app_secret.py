"""Give the WhatsApp app a home for the client's own META app secret (#311).

Part of #305: each client brings their own Meta app rather than the platform
owning one. The handover story so far is ``waba_id`` + ``phone_number_id`` +
access token, and the app secret is a fourth item with nowhere to put it —
which is why #306 (per-app ``X-Hub-Signature-256`` verification) cannot be
finished. The signature is a symmetric HMAC-SHA256 over the raw body keyed on
the *sending* app's secret, so the verifier has to hold the key that produced
it and Meta offers no delegated alternative.

The column is an ``EncryptedTextField``, following #289 exactly: Fernet at
rest, so a dump, a nightly backup or a read replica carries ciphertext rather
than a working signing key for every organisation at once. Reading it needs
``FIELD_ENCRYPTION_KEY``.

Two things this migration deliberately does not do:

* it does not touch ``app_secret``, which is the **Gupshup** app secret and a
  different credential for a different provider — ``meta_app_secret`` is
  additive, and no value is moved between them;
* it adds no ``bsp_credentials`` key for the new secret. That mapping exists to
  absorb secrets older clients already send inside the plaintext JSON; nobody
  has ever sent an app secret that way, so an entry would create a plaintext
  intake route instead of preserving one.

Nothing reads the column yet. It is empty for every existing row and an empty
value changes no behaviour: the legacy deployment-wide ``settings.META_APP_SECRET``
path in ``wa/views.py`` is untouched, so reversing is lossless in terms of
behaviour — it drops whatever secrets clients have entered, and they would have
to be entered again, which is the ordinary cost of dropping a credential column.

The ``bsp_credentials`` ``AlterField`` is help text only: the column now names
all three encrypted columns it defers secrets to, so the API docs do not
advertise a shorter list than the model enforces.
"""

import encrypted_model_fields.fields
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("tenants", "0029_wa_app_webhook_identifier"),
    ]

    operations = [
        migrations.AddField(
            model_name="tenantwaapp",
            name="meta_app_secret",
            field=encrypted_model_fields.fields.EncryptedTextField(
                blank=True,
                default="",
                help_text=(
                    "The client's own META app secret, used to verify X-Hub-Signature-256 on their "
                    "webhook deliveries. Distinct from app_secret, which is the Gupshup app secret. "
                    "Encrypted at rest, write-only through the API, and never returned or logged."
                ),
            ),
        ),
        migrations.AlterField(
            model_name="tenantwaapp",
            name="bsp_credentials",
            field=models.JSONField(
                blank=True,
                null=True,
                help_text=(
                    "Non-secret BSP configuration. Tokens and secrets are stored in the encrypted "
                    "bsp_access_token / bsp_partner_app_token / meta_app_secret columns instead."
                ),
            ),
        ),
    ]

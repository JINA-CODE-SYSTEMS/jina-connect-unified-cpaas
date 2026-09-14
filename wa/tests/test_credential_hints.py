"""An operator may see that a secret is set, never the secret.

The credentials screen showed empty boxes for a stored access token and META app
secret, which reads as "not configured" — so an operator reasonably concludes the
values were lost and re-enters them, or asks for a reveal.

A reveal is the wrong answer. #289 made these `EncryptedTextField` so the
plaintext leaves the database for nothing but a Graph call, and an endpoint
returning a live META token — one that can send as the tenant, read their message
history and rewrite their templates — would be gated on `is_superuser`, which is
all-or-nothing (#358). The question an operator is really asking is "is one set,
and is it the one I pasted"; four trailing characters answer both.

"Does it work" is the preflight's question (#311), and it asks META.
"""

import uuid

import pytest

from tenants.models import BSPChoices, Tenant, TenantWAApp
from wa.serializers import WAAppSafeSerializer, WAAppSerializer

TOKEN = "EAAG" + "x" * 180 + "7Fx9"
SECRET = "abcdef0123456789abcdef0123456789"


def _app(**overrides):
    suffix = uuid.uuid4().hex[:8]
    tenant = Tenant.objects.create(name=f"Hints-{suffix}")
    fields = {
        "tenant": tenant,
        "app_name": f"app-{suffix}",
        "app_id": f"gs_{suffix}",
        "app_secret": f"secret_{suffix}",  # noqa: S106 — the Gupshup one, unrelated
        "wa_number": f"+27115{uuid.uuid4().int % 10**6:06d}",
        "bsp": BSPChoices.META,
    }
    fields.update(overrides)
    return TenantWAApp.objects.create(**fields)


@pytest.mark.django_db()
def test_a_stored_token_is_shown_as_a_hint_not_a_value():
    app = _app(bsp_access_token=TOKEN, meta_app_secret=SECRET)

    data = WAAppSerializer(app).data

    assert data["access_token_hint"] == "…7Fx9"
    assert data["meta_app_secret_hint"] == "…6789"
    # The assertion that matters: the secret itself is nowhere in the payload.
    assert TOKEN not in str(data)
    assert SECRET not in str(data)


@pytest.mark.django_db()
def test_an_unset_secret_is_distinguishable_from_a_set_one():
    """Empty rather than a row of dots, so "not configured" reads as itself."""
    app = _app()

    data = WAAppSerializer(app).data

    assert data["access_token_hint"] == ""
    assert data["meta_app_secret_hint"] == ""


@pytest.mark.django_db()
def test_the_hint_is_short_enough_to_be_worthless_on_its_own():
    app = _app(bsp_access_token=TOKEN)

    hint = WAAppSerializer(app).data["access_token_hint"]

    # Four characters plus the leading ellipsis. A hint that grew into a
    # substring long enough to be useful to somebody else is the failure here.
    assert len(hint) == 5
    assert TOKEN.endswith(hint.lstrip("…"))


@pytest.mark.django_db()
def test_a_role_that_cannot_see_identifiers_cannot_see_hints_either():
    """#251 hides BSP identifiers below ADMIN; a secret hint belongs on that side."""
    app = _app(bsp_access_token=TOKEN, meta_app_secret=SECRET)

    data = WAAppSafeSerializer(app).data

    assert "access_token_hint" not in data
    assert "meta_app_secret_hint" not in data

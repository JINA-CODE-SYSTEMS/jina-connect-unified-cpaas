"""Per-app META webhook signature verification (#306, second half).

``X-Hub-Signature-256`` is an HMAC-SHA256 over the raw body keyed on the **app
secret of the app that sent it**. With N client-owned Meta apps there are N
different secrets, so one deployment-wide ``settings.META_APP_SECRET`` had no
setting in which multi-app delivery was both working and authenticated: set it
to one client's value and every other client's events failed verification, leave
it empty and nobody's were verified at all.

Two pieces unblocked this. #310 put the sending app's identity in the URL path,
so the app is known before a byte of the body is read — the body's own
identifiers are worthless until the HMAC over it has been checked, which is the
circle the per-app URL breaks. #311 added ``TenantWAApp.meta_app_secret``, the
client's own secret, encrypted at rest. This is the verification that spends
both.

What these tests pin, and what an implementation could plausibly get wrong:

* a body signed by app A and delivered to **app B's** endpoint is rejected —
  which is the whole acceptance criterion, and the thing one shared secret can
  never do;
* an app's **own** secret wins over the deployment-wide one, rather than the
  deployment-wide one being tried first or as well;
* an app with **no** secret and no fallback rejects, under its **own** reason
  code, distinguishable from both a bad signature and the legacy
  deployment-is-unconfigured fault;
* the deployment-wide **fallback still works**, because every pre-#311 install is
  in exactly that configuration and an upgrade must not stop verifying their
  traffic;
* the comparison is still ``hmac.compare_digest``, over the digest the *app's
  own* secret produces;
* rejections are **counted by reason and attributed to the app**, because META is
  answered 200 whatever happens and a counter is the only way a client whose
  secret rotated is ever noticed to have gone quiet;
* every rejection is still **HTTP 200** — a non-200 throttles delivery to the
  whole deployment, so one client's stale secret must not slow every other
  client's events;
* no secret and no signature digest reaches a log line or a response body.

The legacy unsuffixed receiver is out of scope except where it is asserted
*unchanged*: it has no app identity at the point the signature is checked and so
can only ever use the deployment-wide secret. Its own suite is
``wa/tests/test_meta_webhook_signature.py``. Per-app ``hub.verify_token``
validation is #307 and nothing here touches ``MetaWebhookView.get``.

HOW TO RUN:
    .venv/bin/python -m pytest wa/tests/test_per_app_signature_verification.py -v
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import uuid
from contextlib import contextmanager
from unittest.mock import patch

import pytest
from django.urls import reverse

from wa.services import webhook_identity
from wa.views import (
    SIG_APP_SECRET_MISSING,
    SIG_BAD_HEADER,
    SIG_MISMATCH,
    SIG_UNVERIFIABLE,
)

LEGACY_URL = "/wa/v2/webhooks/meta/"

# Distinctive, and distinct from each other, so a leak into a log line or a
# response body cannot be mistaken for anything else and cannot be explained
# away as "some other secret happened to match".
APP_A_SECRET = "app-a-own-meta-secret-4f19c7must-never-be-logged"
APP_B_SECRET = "app-b-own-meta-secret-2ba806must-never-be-logged"
DEPLOYMENT_SECRET = "deployment-wide-meta-secret-9d3e11must-never-be-logged"


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────


def _tenant():
    from tenants.models import Tenant

    return Tenant.objects.create(name=f"SigAppTenant-{uuid.uuid4().hex[:8]}", is_active=True)


def _wa_app(*, meta_app_secret: str = "", **overrides):
    """A META app, optionally holding its own Meta app secret (#311)."""
    from wa.models import WAApp

    fields = {
        "tenant": _tenant(),
        "app_name": f"app-{uuid.uuid4().hex[:6]}",
        "app_id": f"gs-{uuid.uuid4().hex[:8]}",
        # The *Gupshup* app secret. Deliberately a different value: the two
        # columns are different credentials and must never substitute.
        "app_secret": f"gupshup-{uuid.uuid4().hex[:8]}",
        "wa_number": f"+1{uuid.uuid4().int % 10**10:010d}",
        "waba_id": f"waba-{uuid.uuid4().hex[:8]}",
        "phone_number_id": f"pn-{uuid.uuid4().hex[:8]}",
        "bsp": "META",
        "meta_app_secret": meta_app_secret,
        "is_active": True,
    }
    fields.update(overrides)
    return WAApp.objects.create(**fields)


def _url(wa_app) -> str:
    return reverse("wa:meta-webhook-app", kwargs={"webhook_identifier": wa_app.webhook_identifier})


def _body(text: str = "hello") -> dict:
    return {
        "object": "whatsapp_business_account",
        "entry": [
            {
                "id": f"waba-body-{uuid.uuid4().hex[:6]}",
                "changes": [
                    {
                        "field": "messages",
                        "value": {
                            "metadata": {"phone_number_id": f"pn-body-{uuid.uuid4().hex[:6]}"},
                            "messages": [
                                {
                                    "id": f"wamid.{uuid.uuid4().hex[:12]}",
                                    "from": "919000000001",
                                    "timestamp": "1700000000",
                                    "type": "text",
                                    "text": {"body": text},
                                }
                            ],
                        },
                    }
                ],
            }
        ],
    }


def _raw(body: dict | None = None) -> bytes:
    return json.dumps(body if body is not None else _body()).encode("utf-8")


def _digest(raw: bytes, secret: str) -> str:
    return hmac.new(secret.encode("utf-8"), raw, hashlib.sha256).hexdigest()


def _sign(raw: bytes, secret: str) -> str:
    return "sha256=" + _digest(raw, secret)


def _post(client, url: str, raw: bytes, signature: str | None = None):
    extra = {} if signature is None else {"HTTP_X_HUB_SIGNATURE_256": signature}
    return client.post(url, data=raw, content_type="application/json", **extra)


class _Collector(logging.Handler):
    """Collects rendered messages from ``wa.views``.

    ``caplog`` cannot see them: ``LOGGING`` sets ``propagate: False`` on the
    ``wa`` logger, so its records never reach the handler pytest installs on the
    root logger.
    """

    def __init__(self):
        super().__init__()
        self.lines: list[str] = []

    def emit(self, record):  # noqa: D102
        self.lines.append(record.getMessage())

    @property
    def text(self) -> str:
        return "\n".join(self.lines)


@contextmanager
def _view_logs():
    handler = _Collector()
    view_logger = logging.getLogger("wa.views")
    previous_level = view_logger.level
    view_logger.addHandler(handler)
    view_logger.setLevel(logging.DEBUG)
    try:
        yield handler
    finally:
        view_logger.removeHandler(handler)
        view_logger.setLevel(previous_level)


@pytest.fixture(autouse=True)
def _no_pipeline_dispatch():
    """Ingestion is what is under test, not the downstream Celery pipeline."""
    with patch("wa.signals._dispatch"):
        yield


@pytest.fixture(autouse=True)
def _no_dev_escape_hatch(settings):
    """Nothing here is testing ``META_WEBHOOK_ALLOW_UNSIGNED``; it has its own
    suite. Pinned off so a local ``.env`` cannot make these pass by bypassing
    the very thing they check."""
    settings.META_WEBHOOK_ALLOW_UNSIGNED = False
    settings.DEBUG = False


# ─────────────────────────────────────────────────────────────────────────────
# Acceptance: one app's secret does not verify another app's endpoint
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.django_db
def test_a_body_signed_with_one_apps_secret_is_rejected_at_another_apps_endpoint(client, settings):
    """The acceptance criterion, and the property a single shared secret cannot
    have: app A's signature is not app B's authentication."""
    from wa.models import WAWebhookEvent

    settings.META_APP_SECRET = ""
    app_a = _wa_app(meta_app_secret=APP_A_SECRET)
    app_b = _wa_app(meta_app_secret=APP_B_SECRET)

    raw = _raw()
    response = _post(client, _url(app_b), raw, _sign(raw, APP_A_SECRET))

    assert response.status_code == 200
    assert response.json() == {"status": "ignored", "reason": SIG_MISMATCH}
    assert not WAWebhookEvent.objects.filter(wa_app__in=[app_a, app_b]).exists()


@pytest.mark.django_db
def test_the_very_same_body_is_accepted_at_its_own_apps_endpoint(client, settings):
    """The positive control for the test above.

    Without it, "A's body is rejected at B's URL" would also pass on an
    implementation that rejects everything, which is the failure mode a
    fail-closed change is most likely to introduce.
    """
    from wa.models import WAWebhookEvent

    settings.META_APP_SECRET = ""
    app_a = _wa_app(meta_app_secret=APP_A_SECRET)
    _wa_app(meta_app_secret=APP_B_SECRET)

    raw = _raw()
    signature = _sign(raw, APP_A_SECRET)

    response = _post(client, _url(app_a), raw, signature)

    assert response.json()["status"] == "received"
    assert WAWebhookEvent.objects.filter(wa_app=app_a).count() == 1


@pytest.mark.django_db
def test_each_of_two_apps_is_verified_against_its_own_secret_in_both_directions(client, settings):
    """Symmetry, in one test, so neither app can be the one that happens to work.

    Two apps, two secrets, four deliveries: each app's own signature is accepted
    at its own URL and refused at the other's. Nothing here is satisfied by an
    implementation that reads a single secret, whichever one it reads.
    """
    from wa.models import WAWebhookEvent

    settings.META_APP_SECRET = ""
    app_a = _wa_app(meta_app_secret=APP_A_SECRET)
    app_b = _wa_app(meta_app_secret=APP_B_SECRET)

    for app, own_secret, other_secret in (
        (app_a, APP_A_SECRET, APP_B_SECRET),
        (app_b, APP_B_SECRET, APP_A_SECRET),
    ):
        good = _raw(_body(f"own-{app.pk}"))
        assert _post(client, _url(app), good, _sign(good, own_secret)).json()["status"] == "received"

        bad = _raw(_body(f"other-{app.pk}"))
        assert _post(client, _url(app), bad, _sign(bad, other_secret)).json()["reason"] == SIG_MISMATCH

    assert WAWebhookEvent.objects.filter(wa_app=app_a).count() == 1
    assert WAWebhookEvent.objects.filter(wa_app=app_b).count() == 1


# ─────────────────────────────────────────────────────────────────────────────
# Precedence: the app's own secret, not the deployment's
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.django_db
def test_an_app_with_its_own_secret_does_not_accept_the_deployment_wide_one(client, settings):
    """Precedence, asserted the only way that cannot be faked.

    Both secrets are configured and they differ, so a body signed with the
    *deployment* secret is a valid signature under exactly one of the two
    candidate keys. Rejecting it proves the app's own secret was the key used —
    an implementation that tried both, or preferred the setting, would accept.
    """
    from wa.models import WAWebhookEvent

    settings.META_APP_SECRET = DEPLOYMENT_SECRET
    app = _wa_app(meta_app_secret=APP_A_SECRET)

    raw = _raw()
    response = _post(client, _url(app), raw, _sign(raw, DEPLOYMENT_SECRET))

    assert response.json()["reason"] == SIG_MISMATCH
    assert not WAWebhookEvent.objects.filter(wa_app=app).exists()

    # ...and the app's own secret does work, with the deployment secret still set.
    assert _post(client, _url(app), raw, _sign(raw, APP_A_SECRET)).json()["status"] == "received"
    assert WAWebhookEvent.objects.filter(wa_app=app).count() == 1


@pytest.mark.django_db
def test_an_apps_own_secret_is_read_decrypted_and_not_as_ciphertext(client, settings):
    """``meta_app_secret`` is an ``EncryptedTextField``: the stored bytes are
    Fernet ciphertext and only attribute access decrypts them. Keying the HMAC on
    the raw column would reject every genuine delivery, so this asserts the
    verification agrees with a signature computed from the plaintext, on an
    instance re-fetched from the database rather than the one just saved.
    """
    from wa.models import WAApp

    settings.META_APP_SECRET = ""
    app = _wa_app(meta_app_secret=APP_A_SECRET)

    reloaded = WAApp.objects.get(pk=app.pk)
    assert reloaded.meta_app_secret == APP_A_SECRET, "fixture precondition: the field round-trips"

    raw = _raw()
    assert _post(client, _url(reloaded), raw, _sign(raw, APP_A_SECRET)).json()["status"] == "received"


@pytest.mark.django_db
def test_a_stored_secret_with_surrounding_whitespace_still_verifies(client, settings):
    """``EncryptedTextField`` renders as a textarea in the admin, so a pasted
    secret arrives with a trailing newline more often than not. An HMAC keyed on
    ``"<secret>\\n"`` refuses every genuine delivery as ``invalid_signature`` —
    indistinguishable from a rotated secret, and near-undiagnosable from outside.
    """
    settings.META_APP_SECRET = ""
    app = _wa_app(meta_app_secret=f"  {APP_A_SECRET}\n")

    raw = _raw()
    response = _post(client, _url(app), raw, _sign(raw, APP_A_SECRET))

    assert response.json()["status"] == "received"


# ─────────────────────────────────────────────────────────────────────────────
# No secret at all: its own reason code, never acceptance
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.django_db
def test_an_app_with_no_secret_and_no_fallback_rejects_under_its_own_reason(client, settings):
    """Acceptance: an app with no stored secret rejects rather than accepts, and
    says so with a code of its own rather than borrowing the bad-signature one.
    """
    from wa.models import WAWebhookEvent

    settings.META_APP_SECRET = ""
    app = _wa_app(meta_app_secret="")

    raw = _raw()
    response = _post(client, _url(app), raw, _sign(raw, APP_A_SECRET))

    assert response.status_code == 200
    assert response.json() == {"status": "ignored", "reason": SIG_APP_SECRET_MISSING}
    assert not WAWebhookEvent.objects.filter(wa_app=app).exists()


@pytest.mark.django_db
def test_the_no_secret_reason_is_not_the_bad_signature_reason_nor_the_deployment_one(client, settings):
    """The three rejections have to stay separable at the receiving end.

    A bad signature means the client signed with the wrong key; a missing per-app
    secret means this client's onboarding never finished; the deployment-wide
    code means the *install* is unconfigured. Each sends whoever is paged
    somewhere different, so the codes are compared as whole strings and also
    checked not to be substrings of one another — a caller doing a substring
    match on the body would otherwise silently conflate two of them.
    """
    settings.META_APP_SECRET = ""
    no_secret_app = _wa_app(meta_app_secret="")
    secreted_app = _wa_app(meta_app_secret=APP_A_SECRET)

    raw = _raw()
    missing = _post(client, _url(no_secret_app), raw, _sign(raw, APP_A_SECRET)).json()["reason"]
    mismatch = _post(client, _url(secreted_app), raw, _sign(raw, APP_B_SECRET)).json()["reason"]
    legacy = _post(client, LEGACY_URL, raw, _sign(raw, APP_A_SECRET)).json()["reason"]

    assert missing == SIG_APP_SECRET_MISSING
    assert mismatch == SIG_MISMATCH
    assert legacy == SIG_UNVERIFIABLE

    codes = [missing, mismatch, legacy]
    assert len(set(codes)) == 3, codes
    for one in codes:
        for other in codes:
            if one is not other:
                assert one not in other, f"{one!r} is a substring of {other!r}"


@pytest.mark.django_db
def test_an_unsigned_delivery_to_a_secretless_app_is_the_missing_secret_fault(client, settings):
    """Ordering: with nothing to verify against, the header is not the problem.

    The secret is selected before the header is looked at, so a delivery that is
    both unsigned *and* unverifiable reports the fault the operator can act on
    rather than the client's.
    """
    settings.META_APP_SECRET = ""
    app = _wa_app(meta_app_secret="")

    response = _post(client, _url(app), _raw(), signature=None)

    assert response.json()["reason"] == SIG_APP_SECRET_MISSING
    assert response.json()["reason"] != SIG_BAD_HEADER


# ─────────────────────────────────────────────────────────────────────────────
# The deployment-wide fallback, which every pre-#311 install relies on
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.django_db
def test_an_app_with_no_secret_of_its_own_still_verifies_against_the_deployment_secret(client, settings):
    """The upgrade path. Existing installs have ``META_APP_SECRET`` set and the
    per-app column empty; their deliveries must keep being verified, or this
    change is an outage dressed as hardening.
    """
    from wa.models import WAWebhookEvent

    settings.META_APP_SECRET = DEPLOYMENT_SECRET
    app = _wa_app(meta_app_secret="")

    raw = _raw()
    response = _post(client, _url(app), raw, _sign(raw, DEPLOYMENT_SECRET))

    assert response.json()["status"] == "received"
    assert WAWebhookEvent.objects.filter(wa_app=app).count() == 1


@pytest.mark.django_db
def test_the_fallback_is_a_fallback_and_not_a_bypass(client, settings):
    """An app with no secret of its own is *not* exempt from verification: the
    deployment secret still has to match.
    """
    from wa.models import WAWebhookEvent

    settings.META_APP_SECRET = DEPLOYMENT_SECRET
    app = _wa_app(meta_app_secret="")

    raw = _raw()
    response = _post(client, _url(app), raw, _sign(raw, APP_A_SECRET))

    assert response.json()["reason"] == SIG_MISMATCH
    assert not WAWebhookEvent.objects.filter(wa_app=app).exists()


@pytest.mark.django_db
def test_a_secreted_app_and_a_fallback_app_coexist_on_one_deployment(client, settings):
    """The migration state that actually exists mid-rollout: one client onboarded
    with their own secret, one not yet. Both must work, each against its own key,
    and neither key may verify the other's endpoint.
    """
    settings.META_APP_SECRET = DEPLOYMENT_SECRET
    onboarded = _wa_app(meta_app_secret=APP_A_SECRET)
    legacy = _wa_app(meta_app_secret="")

    raw = _raw()
    assert _post(client, _url(onboarded), raw, _sign(raw, APP_A_SECRET)).json()["status"] == "received"
    assert _post(client, _url(legacy), raw, _sign(raw, DEPLOYMENT_SECRET)).json()["status"] == "received"
    assert _post(client, _url(onboarded), raw, _sign(raw, DEPLOYMENT_SECRET)).json()["reason"] == SIG_MISMATCH
    assert _post(client, _url(legacy), raw, _sign(raw, APP_A_SECRET)).json()["reason"] == SIG_MISMATCH


# ─────────────────────────────────────────────────────────────────────────────
# The legacy receiver is unchanged
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.django_db
def test_the_legacy_path_cannot_use_a_per_app_secret(client, settings):
    """Unsuffixed URL, so no identity until the body is parsed — and the body is
    what the signature is meant to establish. The app's own secret is therefore
    unreachable there, by construction rather than by omission, and a client who
    registered the shared URL is still verified against the shared secret.
    """
    from wa.models import WAWebhookEvent

    settings.META_APP_SECRET = DEPLOYMENT_SECRET
    body = _body()
    app = _wa_app(
        meta_app_secret=APP_A_SECRET,
        phone_number_id=body["entry"][0]["changes"][0]["value"]["metadata"]["phone_number_id"],
        waba_id=body["entry"][0]["id"],
    )
    raw = _raw(body)

    assert _post(client, LEGACY_URL, raw, _sign(raw, APP_A_SECRET)).json()["reason"] == SIG_MISMATCH
    assert not WAWebhookEvent.objects.filter(wa_app=app).exists()

    assert _post(client, LEGACY_URL, raw, _sign(raw, DEPLOYMENT_SECRET)).json()["status"] == "received"
    assert WAWebhookEvent.objects.filter(wa_app=app).count() == 1


@pytest.mark.django_db
def test_the_legacy_path_with_no_secret_keeps_its_original_reason_code(client, settings):
    """``missing_app_secret`` still means what it meant before this change, so
    whatever is already alerting on it does not have to be retaught.
    """
    settings.META_APP_SECRET = ""
    _wa_app(meta_app_secret=APP_A_SECRET)

    response = _post(client, LEGACY_URL, _raw())

    assert response.status_code == 200
    assert response.json() == {"status": "ignored", "reason": SIG_UNVERIFIABLE}


# ─────────────────────────────────────────────────────────────────────────────
# Constant time, over the right key
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.django_db
def test_the_comparison_is_constant_time_and_over_the_apps_own_digest(client, settings):
    """Two properties one assertion can carry.

    The pair handed to ``hmac.compare_digest`` is (what we computed, what was
    presented). Asserting that exact pair is present proves both that the
    comparison went through ``compare_digest`` — a hand-rolled ``==`` would leak
    timing — and that the digest we computed is the one the *app's own* secret
    produces, which a deployment-wide key could not have produced.
    """
    import wa.views as views

    settings.META_APP_SECRET = DEPLOYMENT_SECRET
    app = _wa_app(meta_app_secret=APP_A_SECRET)

    raw = _raw()
    presented = _digest(raw, APP_A_SECRET)

    calls: list[tuple] = []
    real_compare = hmac.compare_digest

    def _recording_compare(a, b):
        calls.append((a, b))
        return real_compare(a, b)

    views.hmac.compare_digest = _recording_compare
    try:
        assert _post(client, _url(app), raw, f"sha256={presented}").json()["status"] == "received"
    finally:
        views.hmac.compare_digest = real_compare

    assert (_digest(raw, APP_A_SECRET), presented) in calls, calls
    assert (_digest(raw, DEPLOYMENT_SECRET), presented) not in calls, "the deployment secret was tried"


# ─────────────────────────────────────────────────────────────────────────────
# Rejections are counted, by reason, attributed to the app
# ─────────────────────────────────────────────────────────────────────────────


def _counts(app, *reasons):
    """The current per-reason rejection totals for *app* (``None`` = legacy).

    Deltas, always: the counters are shared Redis keys bucketed by UTC day and
    ``TenantWAApp`` primary keys are reused across test-database rollbacks, so an
    absolute count carries whatever earlier tests in the same day contributed.
    Clearing the cache is not an option either — under django_redis that is a
    FLUSHDB of every key in the cache database.
    """
    from tenants.models import BSPChoices

    return {r: webhook_identity.signature_rejections(BSPChoices.META, r, wa_app=app) for r in reasons}


@pytest.mark.django_db
def test_a_mismatch_is_counted_under_its_reason_and_against_the_receiving_app(client, settings):
    """Acceptance: counted by reason, attributable to an app.

    META is answered 200 whatever happens, so nothing in the response tells an
    operator that a client's events stopped being believed. This counter is it.
    """
    settings.META_APP_SECRET = ""
    victim = _wa_app(meta_app_secret=APP_B_SECRET)
    bystander = _wa_app(meta_app_secret=APP_A_SECRET)

    reasons = (SIG_MISMATCH, SIG_APP_SECRET_MISSING)
    before_victim = _counts(victim, *reasons)
    before_bystander = _counts(bystander, *reasons)

    raw = _raw()
    assert _post(client, _url(victim), raw, _sign(raw, APP_A_SECRET)).json()["reason"] == SIG_MISMATCH

    after_victim = _counts(victim, *reasons)
    after_bystander = _counts(bystander, *reasons)

    assert after_victim[SIG_MISMATCH] - before_victim[SIG_MISMATCH] == 1
    # Attribution is real, not a deployment-wide total wearing an app's name:
    # the app that was *not* delivered to did not move, and neither did the
    # other reason's bucket for the app that was.
    assert after_victim[SIG_APP_SECRET_MISSING] == before_victim[SIG_APP_SECRET_MISSING]
    assert after_bystander == before_bystander


@pytest.mark.django_db
def test_a_missing_per_app_secret_is_counted_under_its_own_reason(client, settings):
    """The two no-secret faults are counted apart, or the distinct reason codes
    buy nothing an operator can see."""
    settings.META_APP_SECRET = ""
    app = _wa_app(meta_app_secret="")

    reasons = (SIG_APP_SECRET_MISSING, SIG_MISMATCH, SIG_UNVERIFIABLE)
    before = _counts(app, *reasons)

    raw = _raw()
    assert _post(client, _url(app), raw, _sign(raw, APP_A_SECRET)).json()["reason"] == SIG_APP_SECRET_MISSING

    after = _counts(app, *reasons)

    assert after[SIG_APP_SECRET_MISSING] - before[SIG_APP_SECRET_MISSING] == 1
    assert after[SIG_MISMATCH] == before[SIG_MISMATCH]
    assert after[SIG_UNVERIFIABLE] == before[SIG_UNVERIFIABLE]


@pytest.mark.django_db
def test_a_legacy_rejection_is_counted_where_no_app_could_be_identified(client, settings):
    """Attribution stops where identification does.

    The acceptance wording is "attributable to an app wherever one could be
    identified", and the legacy receiver is the case where one could not: it has
    no identity at the point the signature is checked. Its rejections land in an
    unattributed bucket rather than being charged to some arbitrary app.
    """
    settings.META_APP_SECRET = ""
    app = _wa_app(meta_app_secret=APP_A_SECRET)

    before_unattributed = _counts(None, SIG_UNVERIFIABLE)
    before_app = _counts(app, SIG_UNVERIFIABLE)

    assert _post(client, LEGACY_URL, _raw()).json()["reason"] == SIG_UNVERIFIABLE

    after_unattributed = _counts(None, SIG_UNVERIFIABLE)
    after_app = _counts(app, SIG_UNVERIFIABLE)

    assert after_unattributed[SIG_UNVERIFIABLE] - before_unattributed[SIG_UNVERIFIABLE] == 1
    assert after_app == before_app


@pytest.mark.django_db
def test_an_accepted_delivery_is_not_counted_as_a_rejection(client, settings):
    """The counter would be useless if it also moved on success."""
    settings.META_APP_SECRET = ""
    app = _wa_app(meta_app_secret=APP_A_SECRET)

    reasons = (SIG_MISMATCH, SIG_APP_SECRET_MISSING, SIG_BAD_HEADER)
    before = _counts(app, *reasons)

    raw = _raw()
    assert _post(client, _url(app), raw, _sign(raw, APP_A_SECRET)).json()["status"] == "received"

    assert _counts(app, *reasons) == before


# ─────────────────────────────────────────────────────────────────────────────
# META still gets a 200, whatever happened
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.django_db
@pytest.mark.parametrize(
    ("app_secret", "deployment_secret", "signing_secret", "send_header", "expected_reason"),
    [
        # Signed with another app's secret — the acceptance case.
        (APP_B_SECRET, "", APP_A_SECRET, True, SIG_MISMATCH),
        # Signed with the deployment secret at an app that has its own.
        (APP_B_SECRET, DEPLOYMENT_SECRET, DEPLOYMENT_SECRET, True, SIG_MISMATCH),
        # No secret anywhere for this app.
        ("", "", APP_A_SECRET, True, SIG_APP_SECRET_MISSING),
        ("", "", APP_A_SECRET, False, SIG_APP_SECRET_MISSING),
        # Falling back to the deployment secret, but signed with the wrong key.
        ("", DEPLOYMENT_SECRET, APP_A_SECRET, True, SIG_MISMATCH),
        # Secret available, header absent or unusable.
        (APP_B_SECRET, "", APP_B_SECRET, False, SIG_BAD_HEADER),
    ],
    ids=[
        "signed-by-another-app",
        "signed-with-the-deployment-secret",
        "no-secret-anywhere",
        "no-secret-anywhere-and-unsigned",
        "fallback-but-wrong-key",
        "secret-present-header-absent",
    ],
)
def test_every_per_app_rejection_still_answers_meta_with_200(
    client, settings, app_secret, deployment_secret, signing_secret, send_header, expected_reason
):
    """A non-200 makes META throttle delivery to the whole deployment, so one
    client's stale secret would slow every other client's events down. The
    rejection lives in the body and the counter, never in the status code.
    """
    from wa.models import WAWebhookEvent

    settings.META_APP_SECRET = deployment_secret
    app = _wa_app(meta_app_secret=app_secret)

    raw = _raw()
    response = _post(client, _url(app), raw, _sign(raw, signing_secret) if send_header else None)

    assert response.status_code == 200
    assert response.json() == {"status": "ignored", "reason": expected_reason}
    assert not WAWebhookEvent.objects.filter(wa_app=app).exists()


# ─────────────────────────────────────────────────────────────────────────────
# Which secret ran is recorded; the secret itself never is
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.django_db
def test_an_accepted_delivery_records_that_the_apps_own_secret_verified_it(client, settings):
    """A delivery is logged with the scope of the key that verified it.

    "Verified" is not a useful statement on its own.

    ``secret_scope=app`` is what distinguishes a client authenticated *as
    themselves* from one merely holding the deployment's shared secret — the
    latter is what the original single-secret behaviour could claim, and the
    distinction is the whole of this ticket.
    """
    settings.META_APP_SECRET = DEPLOYMENT_SECRET
    app = _wa_app(meta_app_secret=APP_A_SECRET)

    raw = _raw()
    with _view_logs() as logs:
        assert _post(client, _url(app), raw, _sign(raw, APP_A_SECRET)).json()["status"] == "received"

    assert "secret_scope=app" in logs.text
    assert "secret_scope=deployment" not in logs.text


@pytest.mark.django_db
def test_a_fallback_delivery_records_that_it_used_the_shared_secret(client, settings):
    """The other half of the pair. An app still on the shared secret is visible
    as such in the logs, which is how a half-finished rollout is spotted.
    """
    settings.META_APP_SECRET = DEPLOYMENT_SECRET
    app = _wa_app(meta_app_secret="")

    raw = _raw()
    with _view_logs() as logs:
        assert _post(client, _url(app), raw, _sign(raw, DEPLOYMENT_SECRET)).json()["status"] == "received"

    assert "secret_scope=deployment" in logs.text
    assert "secret_scope=app" not in logs.text


@pytest.mark.django_db
def test_no_secret_or_signature_digest_reaches_a_log_line(client, settings):
    """Acceptance, and the place a leak would actually land: rejections are
    logged, and log sinks are a far wider audience than the client who owns the
    secret. Covers all three keys in play and the digests derived from them,
    across every outcome — accepted, mismatched, unsigned and unverifiable.
    """
    settings.META_APP_SECRET = DEPLOYMENT_SECRET
    secreted = _wa_app(meta_app_secret=APP_A_SECRET)
    secretless = _wa_app(meta_app_secret="")

    raw = _raw()
    digests = [_digest(raw, s) for s in (APP_A_SECRET, APP_B_SECRET, DEPLOYMENT_SECRET)]

    with _view_logs() as logs:
        _post(client, _url(secreted), raw, _sign(raw, APP_A_SECRET))  # accepted
        _post(client, _url(secreted), raw, _sign(raw, APP_B_SECRET))  # mismatch
        _post(client, _url(secreted), raw, None)  # no header
        _post(client, _url(secretless), raw, _sign(raw, DEPLOYMENT_SECRET))  # fallback, accepted
        settings.META_APP_SECRET = ""
        _post(client, _url(secretless), raw, _sign(raw, APP_A_SECRET))  # unverifiable

    assert logs.lines, "these deliveries must leave a trace"
    for secret in (APP_A_SECRET, APP_B_SECRET, DEPLOYMENT_SECRET):
        assert secret not in logs.text
    for digest in digests:
        assert digest not in logs.text
    # The names are fine and useful; the values are not.
    assert "META_APP_SECRET=" not in logs.text
    assert "meta_app_secret=" not in logs.text


@pytest.mark.django_db
def test_no_secret_reaches_a_response_body_either(client, settings):
    """The rejection body is returned to whoever POSTed, which on a public
    endpoint is anyone. It carries a reason code and nothing else.
    """
    settings.META_APP_SECRET = DEPLOYMENT_SECRET
    app = _wa_app(meta_app_secret=APP_A_SECRET)

    raw = _raw()
    for signature in (_sign(raw, APP_B_SECRET), None, "sha256=00", "garbage"):
        body = _post(client, _url(app), raw, signature).content.decode()
        for secret in (APP_A_SECRET, APP_B_SECRET, DEPLOYMENT_SECRET):
            assert secret not in body
        assert set(json.loads(body)) == {"status", "reason"}

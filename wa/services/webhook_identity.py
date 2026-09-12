"""
Per-app webhook identity — who sent this delivery, before the body is read (#310).

A webhook receiver has to authenticate a delivery before it can believe
anything in it, and for META that means an HMAC-SHA256 over the raw body keyed
on the *sending app's* secret. Which app is that? Until now the only answer
available was ``entry[0].id`` or ``metadata.phone_number_id`` — both inside the
body, both worthless until the signature is checked, and the signature cannot
be checked without first knowing the app. One deployment-wide secret is the
only configuration that escapes the circle, and it is exactly the configuration
that cannot serve several client-owned apps.

So the identity moves into the URL::

    POST /wa/v2/webhooks/meta/<webhook_identifier>/     ← one app, one secret
    POST /wa/v2/webhooks/meta/                          ← legacy, global secret

``TenantWAApp.webhook_identifier`` is the opaque string in that path. This
module is the receiver side of it: resolve it to an app in one indexed query,
build the URL a client pastes into their own dashboard, count the deliveries
addressed to identifiers nobody owns and the ones whose signature could not be
verified, and keep the full value out of the logs.

BSP-agnostic by construction (#305 D-4). Bring-your-own-app and Embedded Signup
(#258, gated on Tech Provider status in #190) coexist permanently, so the
identity layer may not be Meta-shaped: a receiver is registered by adding one
row to ``_RECEIVER_URL_NAMES`` and the matching pair of URL patterns, and
nothing else in here knows which BSP it is serving.

``wa.views._verify_meta_signature`` now spends that identity, through
:func:`select_app_secret` below: on a per-app URL the HMAC is keyed on the
resolved app's own ``TenantWAApp.meta_app_secret`` (#311's column, #306's second
half), falling back to the deployment-wide ``settings.META_APP_SECRET`` only
where an app has none.

The GET handshake spends the same identity, through :func:`select_verify_token`
(#307): ``hub.verify_token`` is measured against the resolved app's own
``TenantWAApp.webhook_verify_token``, and against the deployment-wide
``<BSP>_WEBHOOK_VERIFY_TOKEN`` setting only for an app that has none and on the
legacy path, which has no app to ask. The two selectors are deliberate siblings —
one request, one identity, spent twice — and :func:`verify_token` reports which
scope answered so the client-facing setup screen can only ever show a token the
receiver will actually check.
"""

from __future__ import annotations

import logging
from typing import Optional

from django.conf import settings as django_settings
from django.core.cache import cache
from django.urls import reverse
from django.utils import timezone

from tenants.models import (
    WA_WEBHOOK_IDENTIFIER_ALPHABET,
    BSPChoices,
    mask_wa_webhook_identifier,
)

logger = logging.getLogger(__name__)

# ──────────────────────────────────────────────────────────────────────────────
# Receiver registry
# ──────────────────────────────────────────────────────────────────────────────

#: BSP -> (legacy URL name, per-app URL name), namespaced as ``wa.urls`` is
#: included in ``jina_connect.urls`` (``namespace="wa"``).
#:
#: Both names are kept because both paths are permanent: the legacy one is
#: registered in live Meta App Dashboards and with Gupshup, and breaking it
#: would make an upgrade require every self-hoster to re-register. Adding a BSP
#: receiver means adding a row here plus the two ``path()`` entries in
#: ``wa/urls.py`` — there is no Meta-only branch anywhere below.
_RECEIVER_URL_NAMES: dict[str, tuple[str, str]] = {
    BSPChoices.META: ("wa:meta-webhook", "wa:meta-webhook-app"),
    BSPChoices.GUPSHUP: ("wa:gupshup-webhook", "wa:gupshup-webhook-app"),
}

#: Deployment setting holding each receiver's *fallback* verify token: what the
#: legacy unsuffixed path checks, and what a per-app URL falls back to for an app
#: with no token of its own. One per BSP, and the only deployment-wide half left
#: since #307 — :func:`select_verify_token` prefers the sending app's own
#: ``webhook_verify_token``. Registered here rather than branched on anywhere, so
#: a third BSP's handshake gets per-app tokens from the shape (#305 D-4).
_VERIFY_TOKEN_SETTINGS: dict[str, str] = {
    BSPChoices.META: "META_WEBHOOK_VERIFY_TOKEN",
    BSPChoices.GUPSHUP: "GUPSHUP_WEBHOOK_VERIFY_TOKEN",
}

#: Shortest and longest path segment that could be an identifier. Generated
#: ones are 36 characters (a 4-character prefix plus 32 from 24 random bytes);
#: the bounds are wider so a future prefix or token length is not a migration.
_MIN_IDENTIFIER_LENGTH = 16
_MAX_IDENTIFIER_LENGTH = 64


# ──────────────────────────────────────────────────────────────────────────────
# Rejection counter
# ──────────────────────────────────────────────────────────────────────────────

#: Deliveries addressed to an identifier no app owns, per BSP per UTC day.
#:
#: In the cache, not a table, because the acceptance condition for an unknown
#: identifier is that it writes *nothing* to the database: an endpoint that is
#: public, unauthenticated and reachable by anyone who can guess a URL shape is
#: the last place to put an unbounded INSERT. Counting in Redis keeps the
#: rejection free and still makes a scan visible. #305 D-7 wants these durable
#: and aggregated with a retention policy, which is a schema and a sweep of its
#: own; the daily bucketing and the TTL here are the shape that grows into it.
_UNKNOWN_IDENTIFIER_KEY_PREFIX = "wa:webhook:unknown-identifier"

#: How long a daily bucket lives, shared by every rejection counter in this
#: module so they expire together and one retention answer covers all of them.
REJECTION_COUNTER_TTL = 60 * 60 * 24 * 7  # a week of daily buckets
#: Original name, kept so nothing importing it breaks.
UNKNOWN_IDENTIFIER_COUNTER_TTL = REJECTION_COUNTER_TTL


def _counter_key(bsp: str, day=None) -> str:
    day = day or timezone.now()
    return f"{_UNKNOWN_IDENTIFIER_KEY_PREFIX}:{bsp}:{day:%Y%m%d}"


def _bump(key: str) -> int:
    """Increment a daily counter atomically; return the new total.

    ``cache.add`` then ``cache.incr`` is the house pattern (see
    ``telegram.services.rate_limiter``): the first is atomic create-if-absent,
    the second an atomic increment, so concurrent workers cannot lose counts to
    a read-modify-write race. The ``ValueError`` arm covers the key expiring
    between the two calls, which is rare and must not raise into a webhook
    response — the whole point of this path is to answer 200 and do nothing
    expensive.
    """
    cache.add(key, 0, timeout=REJECTION_COUNTER_TTL)
    try:
        return cache.incr(key)
    except ValueError:
        cache.set(key, 1, timeout=REJECTION_COUNTER_TTL)
        return 1


def record_unknown_identifier(bsp: str) -> int:
    """Count one delivery to an unowned identifier; return the day's total."""
    return _bump(_counter_key(bsp))


def unknown_identifier_rejections(bsp: str, day=None) -> int:
    """How many unowned-identifier deliveries *bsp* has seen on *day* (UTC)."""
    return int(cache.get(_counter_key(bsp, day)) or 0)


# ──────────────────────────────────────────────────────────────────────────────
# Signature-rejection counter (#306)
# ──────────────────────────────────────────────────────────────────────────────

#: Deliveries refused because their ``X-Hub-Signature-256`` could not be
#: verified, bucketed per BSP, per reason code, per app and per UTC day.
#:
#: This counter is the *only* way a dropped delivery is ever noticed. META must
#: be answered 200 whatever happens — a non-200 throttles delivery to the whole
#: deployment — so a client whose app secret rotates goes silently quiet: their
#: events keep arriving, keep failing the HMAC, and keep being answered "fine".
#: The reason code separates that case (``invalid_signature``) from "you never
#: gave us a secret to check against" (``app_secret_not_configured``), and the
#: app component says *whose* events stopped being believed, which a
#: deployment-wide total cannot.
#:
#: Same storage argument as the unknown-identifier counter above: the endpoint
#: is public and unauthenticated, so the rejection path may not perform an
#: unbounded INSERT. #305 D-7 turns both into a durable aggregate.
_SIGNATURE_REJECTION_KEY_PREFIX = "wa:webhook:signature-rejection"

#: What stands in for the app component when no app could be identified — the
#: legacy unsuffixed receiver, which has no identity until the body is parsed
#: and so cannot attribute its own rejections. Deliberately not blank, so a
#: key is never ambiguous about whether an app was known.
_NO_APP = "-"


def _signature_rejection_key(bsp: str, reason: str, app_pk=None, day=None) -> str:
    day = day or timezone.now()
    return f"{_SIGNATURE_REJECTION_KEY_PREFIX}:{bsp}:{reason}:{app_pk or _NO_APP}:{day:%Y%m%d}"


def record_signature_rejection(bsp: str, reason: str, wa_app=None) -> int:
    """Count one signature rejection; return the day's total for that bucket.

    *wa_app* is the app the delivery was attributed to, when one could be
    identified — which, on a per-app URL, is always, because the path named it
    before the body was read (#310). ``None`` is the legacy receiver.
    """
    return _bump(_signature_rejection_key(bsp, reason, getattr(wa_app, "pk", None)))


def signature_rejections(bsp: str, reason: str, wa_app=None, day=None) -> int:
    """How many deliveries *bsp* refused for *reason* on *day* (UTC).

    Scoped to *wa_app* when given; to the unattributable legacy bucket
    otherwise. There is no "all apps" total on purpose — the question worth
    asking is which client went quiet, and summing over a wildcard would need a
    key scan against Redis on a public code path.
    """
    return int(cache.get(_signature_rejection_key(bsp, reason, getattr(wa_app, "pk", None), day)) or 0)


# ──────────────────────────────────────────────────────────────────────────────
# Handshake-rejection counter (#307)
# ──────────────────────────────────────────────────────────────────────────────

#: Handshakes refused because ``hub.verify_token`` did not match, bucketed per
#: BSP, per app and per UTC day.
#:
#: Counted for two different readers. A burst against one app is someone
#: guessing at that app's token, or a client re-verifying their URL with a stale
#: one — the two look the same from here, and both are worth a look. A spread
#: across many apps is a scan, which the unknown-identifier counter cannot see
#: because a scanner who has a real callback URL passes that check.
#:
#: The presented token is deliberately *not* part of the key. It is an
#: attacker-supplied guess at a secret — or, just as often, another tenant's
#: real token sent to the wrong endpoint — and a cache key is written down in
#: exactly the places a secret must not be (#307). Same storage argument as the
#: counters above: the handshake is public and unauthenticated, so its rejection
#: path may not perform an unbounded INSERT.
_VERIFY_TOKEN_REJECTION_KEY_PREFIX = "wa:webhook:verify-token-rejection"


def _verify_token_rejection_key(bsp: str, app_pk=None, day=None) -> str:
    day = day or timezone.now()
    return f"{_VERIFY_TOKEN_REJECTION_KEY_PREFIX}:{bsp}:{app_pk or _NO_APP}:{day:%Y%m%d}"


def record_verify_token_rejection(bsp: str, wa_app=None) -> int:
    """Count one failed handshake; return the day's total for that bucket.

    *wa_app* is the app whose URL was addressed, which on a per-app URL is
    always known because the path named it (#310). ``None`` is the legacy
    unsuffixed receiver, which has no identity to attribute a handshake to.
    """
    return _bump(_verify_token_rejection_key(bsp, getattr(wa_app, "pk", None)))


def verify_token_rejections(bsp: str, wa_app=None, day=None) -> int:
    """How many handshakes *bsp* refused on *day* (UTC), for *wa_app*'s URL.

    Scoped to *wa_app* when given; to the legacy receiver's bucket otherwise.
    No "all apps" total, for the reason given at :func:`signature_rejections`.
    """
    return int(cache.get(_verify_token_rejection_key(bsp, getattr(wa_app, "pk", None), day)) or 0)


# ──────────────────────────────────────────────────────────────────────────────
# Resolution
# ──────────────────────────────────────────────────────────────────────────────


def looks_like_identifier(identifier: Optional[str]) -> bool:
    """Whether *identifier* could be one of ours, without asking the database.

    A public URL shape attracts scanners, and every one of their paths would
    otherwise cost a query. The check is on shape alone — it says nothing about
    whether any app holds the value.
    """
    if not identifier:
        return False
    if not (_MIN_IDENTIFIER_LENGTH <= len(identifier) <= _MAX_IDENTIFIER_LENGTH):
        return False
    return set(identifier) <= WA_WEBHOOK_IDENTIFIER_ALPHABET


def resolve_app(identifier: Optional[str]):
    """The ``WAApp`` whose ``webhook_identifier`` is *identifier*, or ``None``.

    **One indexed query, always.** ``webhook_identifier`` carries a unique
    constraint, so this is a single-row lookup through that index and its cost
    does not move when the instance goes from one hosted app to a hundred
    (#305 D-1). That is the property the per-app URL exists to buy: the
    alternative — trying each candidate app's secret against the body — is
    O(apps) HMACs over a full request body, per delivery.

    No ``bsp`` filter and no ``is_active`` filter. The identifier already names
    exactly one app, so filtering on ``bsp`` could only turn "this app is on
    another BSP" into "unknown identifier" and lose the distinction the caller
    needs; and the legacy path has never filtered on ``is_active`` either, so
    filtering here would give the two paths different behaviour for a
    deactivated app — a difference this ticket is not the place to introduce.
    """
    from wa.models import WAApp

    if not looks_like_identifier(identifier):
        return None

    return WAApp.objects.filter(webhook_identifier=identifier).first()


def mask(identifier: Optional[str]) -> str:
    """The part of *identifier* that may be logged. Never the whole of it."""
    return mask_wa_webhook_identifier(identifier)


# ──────────────────────────────────────────────────────────────────────────────
# Which secret verifies this delivery (#306, second half)
# ──────────────────────────────────────────────────────────────────────────────

#: Where the secret that keyed a delivery's HMAC came from. Every META delivery
#: — accepted or rejected — is logged with one of these, because "verified"
#: without saying *against what* is the statement that hid the original
#: fail-open: a deployment with no secret at all logged one warning and then
#: reported every forged body as fine.
SECRET_SCOPE_APP = "app"  # the sending app's own ``meta_app_secret`` (#311)
SECRET_SCOPE_DEPLOYMENT = "deployment"  # the shared ``settings.META_APP_SECRET``
SECRET_SCOPE_NONE = "none"  # nothing to check against; the delivery is refused


def select_app_secret(wa_app=None) -> tuple[str, str]:
    """The secret that should key *wa_app*'s HMAC, and which scope it came from.

    Returns ``(secret, scope)``. The scope is part of the answer rather than
    something the caller infers, because the two non-empty cases mean different
    things and must be distinguishable in a log line and in a reason code: an
    app verified against its own secret is authenticated *as that app*, while
    one verified against the deployment-wide secret is only authenticated as
    "someone holding this deployment's secret" — correct for a single-app
    install, and the reason one shared secret can never separate two clients.

    **Per-app first.** ``TenantWAApp.meta_app_secret`` is the client's own Meta
    app secret, encrypted at rest (#289/#324) and decrypted by
    ``encrypted_model_fields`` on attribute access — read it, never the raw
    column, or you get ciphertext and reject everything.

    **Deployment-wide second, not instead.** Dropping the fallback would break
    every existing install the moment it upgraded: the live deployment and
    self-hosters have ``META_APP_SECRET`` set and no per-app column filled, and
    their deliveries must keep verifying. So an app with no secret of its own is
    not a failure — it is the pre-#311 configuration, and it keeps working.

    ``None`` for *wa_app* is the legacy unsuffixed receiver, which has no
    identity at this point in the request and so has only the shared secret
    available. Passing an app is what makes the per-app secret reachable at all.

    The stored value is stripped. ``EncryptedTextField`` renders as a textarea
    in the admin, so a pasted secret arrives with a trailing newline far more
    often than not, and an HMAC keyed on ``"<secret>\\n"`` rejects every genuine
    delivery with ``invalid_signature`` — a failure that looks exactly like a
    rotated secret and is nearly impossible to diagnose from the outside. The
    setting is deliberately *not* stripped: that is existing behaviour on a path
    this ticket is not changing.
    """
    if wa_app is not None:
        own_secret = (getattr(wa_app, "meta_app_secret", "") or "").strip()
        if own_secret:
            return own_secret, SECRET_SCOPE_APP

    deployment_secret = getattr(django_settings, "META_APP_SECRET", "") or ""
    if deployment_secret:
        return deployment_secret, SECRET_SCOPE_DEPLOYMENT

    return "", SECRET_SCOPE_NONE


# ──────────────────────────────────────────────────────────────────────────────
# Which token completes this handshake (#307)
# ──────────────────────────────────────────────────────────────────────────────

#: Whose verify token a handshake is checked against. The same three scopes
#: :func:`select_app_secret` reports, and reported for the same reason: a
#: handshake that "passed" without naming the token it passed against is the
#: sentence that hid a deployment-wide secret being shared between tenants.
VERIFY_TOKEN_SCOPE_APP = "app"  # the addressed app's own ``webhook_verify_token``
VERIFY_TOKEN_SCOPE_DEPLOYMENT = "deployment"  # the shared ``<BSP>_WEBHOOK_VERIFY_TOKEN`` setting
VERIFY_TOKEN_SCOPE_NONE = "none"  # nothing configured anywhere; the check cannot run


def deployment_verify_token(bsp: str) -> str:
    """The deployment-wide verify token setting for *bsp*, or ``""``.

    Read through :data:`_VERIFY_TOKEN_SETTINGS` rather than by naming a setting,
    so a BSP added to the registry gets a handshake without this module growing
    a branch for it.
    """
    setting_name = _VERIFY_TOKEN_SETTINGS.get(bsp, "")
    return (getattr(django_settings, setting_name, "") if setting_name else "") or ""


def select_verify_token(bsp: str, wa_app=None) -> tuple[str, str]:
    """The token *wa_app*'s handshake must present, and which scope it came from.

    Returns ``(token, scope)``. Deliberately the same shape, the same precedence
    and the same three scopes as :func:`select_app_secret`: one move, made twice
    on the same request — the POST picks a secret per app, the GET picks a token
    per app — and two conventions for it would be two things to keep in step.

    **Per-app first, and per-app only.** ``TenantWAApp.webhook_verify_token``
    (#307's column) is the app's own token, and when it has one that is the
    *whole* of what its URL accepts. There is no second chance at the
    deployment-wide value here, and that omission is the ticket: a fallback
    would mean every holder of the shared token could still complete the
    handshake for every client's endpoint, which is exactly the cross-tenant
    secret being removed. Encrypted at rest, decrypted by
    ``encrypted_model_fields`` on attribute access — read the attribute, never
    the raw column.

    **Deployment-wide for an app that has none.** That is the pre-#307
    configuration, and an upgrade must not break a handshake that works today:
    the column is blank until ``tenants/0031`` fills it, and a fixture or a row
    written around ``save()`` can still be blank afterwards.

    ``None`` for *wa_app* is the legacy unsuffixed receiver, which has no
    identity during a handshake — there is no body to route from — and so has
    only the deployment-wide setting available. That path is unchanged on
    purpose: it is registered in live dashboards (#310).

    The stored token is stripped, for the reason given at
    :func:`select_app_secret`: a value that arrives through a textarea arrives
    with a trailing newline more often than not, and a token compared as
    ``"<token>\\n"`` refuses every genuine handshake. The setting is left alone,
    which is existing behaviour on the path this ticket does not change.
    """
    if wa_app is not None:
        own_token = (getattr(wa_app, "webhook_verify_token", "") or "").strip()
        if own_token:
            return own_token, VERIFY_TOKEN_SCOPE_APP

    deployment_token = deployment_verify_token(bsp)
    if deployment_token:
        return deployment_token, VERIFY_TOKEN_SCOPE_DEPLOYMENT

    return "", VERIFY_TOKEN_SCOPE_NONE


# ──────────────────────────────────────────────────────────────────────────────
# The URLs a client configures
# ──────────────────────────────────────────────────────────────────────────────


def has_receiver(bsp: str) -> bool:
    """Whether *bsp* has a receiver of its own, rather than a borrowed one.

    The question :func:`_url_names` cannot answer in its return value, since it
    answers with a URL either way (#334). Ask this before believing a callback
    URL for a BSP that might not be META or Gupshup.
    """
    return bsp in _RECEIVER_URL_NAMES


def _url_names(bsp: str) -> tuple[str, str]:
    """The (legacy, per-app) URL names for *bsp*.

    Falls back to the Gupshup receiver for a BSP with no receiver of its own,
    which is what ``wa.admin`` and the subscription viewset already did when
    they built a webhook URL. Wrong is better than absent here: the fallback is
    visible in the URL a client is handed, where an exception at setup time
    would instead be an unexplained 500 on an unrelated screen.

    It is no longer *silent*, though (#334). A borrowed path cannot be spotted
    in the URL by anyone who does not already know which receivers exist, so the
    fallback says so in the log — and :func:`has_receiver` is the same answer for
    a caller that would rather not ask at all.
    """
    names = _RECEIVER_URL_NAMES.get(bsp)
    if names is not None:
        return names

    logger.warning(
        "wa webhook: no receiver is registered for bsp=%s — falling back to the %s receiver, "
        "so this callback URL names a path that will not recognise its own deliveries",
        bsp,
        BSPChoices.GUPSHUP,
    )
    return _RECEIVER_URL_NAMES[BSPChoices.GUPSHUP]


def callback_path(wa_app) -> str:
    """This app's own receiver path, identifier included."""
    from wa.adapters import resolve_bsp

    _legacy, per_app = _url_names(resolve_bsp(wa_app))
    return reverse(per_app, kwargs={"webhook_identifier": wa_app.webhook_identifier})


def legacy_callback_path(bsp: str) -> str:
    """The shared, single-app receiver path for *bsp*.

    Kept reachable and unchanged on purpose: it is the URL already registered
    in live dashboards. It authenticates against the deployment-wide secret, so
    it can only ever serve one client's app — it must not be handed to a second.
    """
    legacy, _per_app = _url_names(bsp)
    return reverse(legacy)


def _public_base_url(request=None) -> str:
    """Where this deployment says it lives.

    ``DEFAULT_WEBHOOK_BASE_URL`` first, because the URL is about to be pasted
    into a BSP dashboard and a deployment's statement of its own public address
    is more trustworthy than the ``Host`` header of whichever request happened
    to ask — behind a proxy that header is frequently an internal name, and it
    is client-supplied. ``request`` is the fallback so a setup screen on a
    deployment that never configured the setting still shows a usable URL.
    """
    base = (getattr(django_settings, "DEFAULT_WEBHOOK_BASE_URL", "") or "").rstrip("/")
    if base:
        return base
    if request is not None:
        return request.build_absolute_uri("/").rstrip("/")
    return ""


def callback_url(wa_app, request=None) -> str:
    """The absolute per-app callback URL a client registers with their BSP."""
    return f"{_public_base_url(request)}{callback_path(wa_app)}"


def registration_callback_url(wa_app, request=None) -> str:
    """The URL this deployment registers with a BSP for *wa_app* (#334).

    The per-app one — the same string :func:`webhook_setup` tells the client to
    paste, byte for byte, which is the whole point of this function existing.
    Until #334 the two disagreed: the setup screen handed over
    ``/wa/v2/webhooks/<bsp>/<identifier>/`` while every registration path sent
    the BSP the legacy ``/wa/v2/webhooks/<bsp>/``, and the two surfaces sit in
    the same header in WhatsApp settings. A client pastes ours, somebody later
    presses "Refresh Webhooks" — which reads as routine maintenance — and the
    deployment quietly re-registers a different path. Whether the new one wins is
    the BSP's business; inbound messages stopping with both sides looking
    correctly configured is the failure #310 exists to prevent.

    Safe to move because the legacy path is permanent (#310): every deployment
    that has it registered keeps working, this only changes what *we* register
    from here on, and the per-app URL is the one that can be authenticated per
    app — its own app secret keys the HMAC (#306) and its own token completes the
    handshake (#307), neither of which the shared path can do for a second
    client.

    One function rather than four call sites choosing for themselves: the API
    refresh action, both admin "reset & re-register" actions and the Gupshup
    auto-register task all ask here, so "which URL do we register" has one
    answer and cannot drift back into two.
    """
    return callback_url(wa_app, request=request)


def legacy_callback_url(wa_app, request=None) -> str:
    """The absolute *legacy* callback URL for whichever BSP *wa_app* is on.

    No longer what subscription refresh registers — that is
    :func:`registration_callback_url` since #334 — but still what the receiver
    serves and what deployments registered before it, which is why this keeps
    composing the same string from the same one place. Three callers built it
    themselves — ``wa.admin``, ``tenants.admin`` and the v2 subscription
    viewset — each from its own copy of a two-entry BSP-to-path dict, and each
    keyed on the **raw** ``bsp`` column with a Gupshup fallback. A blank column
    means META everywhere else (:func:`wa.adapters.resolve_bsp`, #265), so those
    three handed a blank-BSP app the Gupshup receiver and registered a callback
    that would answer its own deliveries with ``unknown_app``.

    Resolving through :func:`~wa.adapters.resolve_bsp` is therefore not a
    tidy-up: it changes the answer for that case, to the right one. The other
    two cases produce byte-identical strings to what the inline dicts produced,
    which is what the tests pin.

    This is deliberately *not* :func:`callback_url`. This path authenticates
    against the deployment-wide secret and token, so it can only ever serve a
    single app and must not be handed to a second client — which is why #334
    moved registration off it rather than moving it here.
    """
    from wa.adapters import resolve_bsp

    return f"{_public_base_url(request)}{legacy_callback_path(resolve_bsp(wa_app))}"


def verify_token(wa_app) -> tuple[str, str]:
    """The verify token for *wa_app*'s handshake, and the scope it has.

    Returns ``(token, scope)`` where scope is ``"app"``, ``"deployment"`` or
    ``"none"``.

    The same call the receiver makes, through the same
    :func:`select_verify_token`, which is the whole of the contract this function
    is for: a setup screen may only show a token the handshake will actually
    check. Showing an app-scoped token that the receiver ignores would read as
    "verification works" right up to the day it is relied on, so the scope is
    not a label applied here — it is whatever the receiver would answer for this
    app, and it says ``"app"`` exactly when the app's own column is what the
    challenge will be measured against.
    """
    from wa.adapters import resolve_bsp

    return select_verify_token(resolve_bsp(wa_app), wa_app=wa_app)


def webhook_setup(wa_app, request=None) -> dict:
    """Everything a client needs to point their own app at this deployment.

    The one payload behind the "webhook setup" endpoint: the URL to paste into
    their BSP dashboard's callback field, and the token to paste beside it. The
    two are issued together because they are configured together, and both are
    now this app's own — ``verify_token_scope`` says ``"app"`` when the token
    shown is the one the receiver will measure the challenge against (#307), and
    ``callback_url`` is the same string this deployment itself registers (#334),
    so the screen and the refresh button can no longer disagree about which URL
    is authoritative.
    """
    from wa.adapters import resolve_bsp

    token, scope = verify_token(wa_app)
    return {
        "wa_app": str(wa_app.pk),
        "bsp": resolve_bsp(wa_app),
        "callback_url": callback_url(wa_app, request=request),
        "identifier_hint": mask(wa_app.webhook_identifier),
        "verify_token": token,
        "verify_token_scope": scope,
        "verify_token_configured": bool(token),
    }

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
addressed to identifiers nobody owns, and keep the full value out of the logs.

BSP-agnostic by construction (#305 D-4). Bring-your-own-app and Embedded Signup
(#258, gated on Tech Provider status in #190) coexist permanently, so the
identity layer may not be Meta-shaped: a receiver is registered by adding one
row to ``_RECEIVER_URL_NAMES`` and the matching pair of URL patterns, and
nothing else in here knows which BSP it is serving.

What this module deliberately does **not** do:

* choose a signature secret — the per-app app-secret column is #311 and the
  verification that reads it is #306's second half;
* validate ``hub.verify_token`` per app — that is #307, which is why
  :func:`webhook_setup` reports the *scope* of the token it returns rather than
  implying the handshake already checks a per-app one.

Both are unblocked by this module existing; neither is half-implemented here.
"""

from __future__ import annotations

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

#: Deployment setting holding the verify token each receiver's handshake
#: currently checks. One per BSP, deployment-wide — #307 replaces the *source*
#: of these with the app's own token; the shape of this mapping is what it
#: replaces, not the callers.
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
UNKNOWN_IDENTIFIER_COUNTER_TTL = 60 * 60 * 24 * 7  # a week of daily buckets


def _counter_key(bsp: str, day=None) -> str:
    day = day or timezone.now()
    return f"{_UNKNOWN_IDENTIFIER_KEY_PREFIX}:{bsp}:{day:%Y%m%d}"


def record_unknown_identifier(bsp: str) -> int:
    """Count one delivery to an unowned identifier; return the day's total.

    ``cache.add`` then ``cache.incr`` is the house pattern (see
    ``telegram.services.rate_limiter``): the first is atomic create-if-absent,
    the second an atomic increment, so concurrent workers cannot lose counts to
    a read-modify-write race. The ``ValueError`` arm covers the key expiring
    between the two calls, which is rare and must not raise into a webhook
    response — the whole point of this path is to answer 200 and do nothing
    expensive.
    """
    key = _counter_key(bsp)
    cache.add(key, 0, timeout=UNKNOWN_IDENTIFIER_COUNTER_TTL)
    try:
        return cache.incr(key)
    except ValueError:
        cache.set(key, 1, timeout=UNKNOWN_IDENTIFIER_COUNTER_TTL)
        return 1


def unknown_identifier_rejections(bsp: str, day=None) -> int:
    """How many unowned-identifier deliveries *bsp* has seen on *day* (UTC)."""
    return int(cache.get(_counter_key(bsp, day)) or 0)


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
# The URLs a client configures
# ──────────────────────────────────────────────────────────────────────────────


def _url_names(bsp: str) -> tuple[str, str]:
    """The (legacy, per-app) URL names for *bsp*.

    Falls back to the Gupshup receiver for a BSP with no receiver of its own,
    which is what ``wa.admin`` and the subscription viewset already do when
    they build a webhook URL. Wrong is better than absent here: the fallback is
    visible in the URL a client is handed, where an exception at setup time
    would instead be an unexplained 500 on an unrelated screen.
    """
    return _RECEIVER_URL_NAMES.get(bsp, _RECEIVER_URL_NAMES[BSPChoices.GUPSHUP])


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


def legacy_callback_url(wa_app, request=None) -> str:
    """The absolute *legacy* callback URL for whichever BSP *wa_app* is on.

    What subscription refresh registers. Three callers built this string
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

    This is deliberately *not* :func:`callback_url`. The per-app URL is the one
    a client should be given; this one authenticates against the
    deployment-wide secret and so can only ever serve a single app. Registering
    it is what existing deployments already do, and changing that is #307's
    decision to make, not this helper's.
    """
    from wa.adapters import resolve_bsp

    return f"{_public_base_url(request)}{legacy_callback_path(resolve_bsp(wa_app))}"


def verify_token(wa_app) -> tuple[str, str]:
    """The verify token for *wa_app*'s handshake, and the scope it has.

    Returns ``(token, scope)`` where scope is ``"deployment"`` or ``"app"``.

    Today it is always the deployment-wide one, because that is what the
    handshake in ``wa.views`` actually validates. Returning an app-scoped token
    that nothing checks would be worse than returning none: the client would
    paste a token, the handshake would accept some *other* token, and the
    mismatch would surface as "verification works" until the day it is relied
    on. #307 wires ``WASubscription.verify_token`` into the handshake and flips
    this to ``"app"`` — the scope field is here so the screen consuming it does
    not have to change when that happens.
    """
    from wa.adapters import resolve_bsp

    setting_name = _VERIFY_TOKEN_SETTINGS.get(resolve_bsp(wa_app), "")
    token = getattr(django_settings, setting_name, "") if setting_name else ""
    return token or "", "deployment"


def webhook_setup(wa_app, request=None) -> dict:
    """Everything a client needs to point their own app at this deployment.

    The one payload behind the "webhook setup" endpoint: the URL to paste into
    their BSP dashboard's callback field, and the token to paste beside it. The
    two are issued together because they are configured together, and because
    #307 will change where the token comes from without changing that.
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

"""Read-only, time-boxed, audited "view as organisation" sessions (#300).

A platform admin answering a support question needs to see what an
organisation sees. #301 closed the accidental version of that — superuser
credentials plus any organisation's ``X-ACCESS-KEY`` produced a token
indistinguishable from that organisation's owner's, with no bound and no
record. This module is the deliberate replacement, and it is deliberately
narrow:

* **Read-only.** Full control would let a platform admin spend a customer's
  wallet, submit a template or send a broadcast with nothing in the data to
  say it was not the customer. So a token carrying ``impersonated_by`` is
  refused on every non-safe method, server-side, at two independent layers
  (see ``impersonation_write_denial``).
* **Time-boxed and non-refreshable.** ``IMPERSONATION_TOKEN_LIFETIME``, and no
  refresh token is ever minted, so a forgotten tab stops being a key to a
  customer's account without anyone having to remember to close it.
* **Audited.** Issuing a token writes a ``users.ImpersonationSession`` row
  naming the real user. The row is not a side-note: ``enforce_impersonation``
  refuses a token whose row is missing, ended or past its expiry, so the audit
  trail is load-bearing — an unaudited impersonation token cannot be used.

The token is an ordinary ``AccessToken`` with extra claims rather than a new
token class on purpose: a custom ``token_type`` would be rejected by
``AUTH_TOKEN_CLASSES`` everywhere, including on the reads this exists for.
That is also why the enforcement below lives on the request path rather than
in token validation.
"""

from datetime import datetime, timedelta
from datetime import timezone as dt_timezone

from rest_framework.exceptions import AuthenticationFailed, PermissionDenied
from rest_framework.permissions import SAFE_METHODS
from rest_framework_simplejwt.tokens import AccessToken

# ── Claims ──────────────────────────────────────────────────────────────
# ``impersonated_by`` is the claim the whole feature keys on: its presence is
# what makes a session read-only, and its value is the *real* user — never the
# organisation's. Everything else here exists so the frontend banner can name
# the organisation and count down without a second request.
IMPERSONATED_BY_CLAIM = "impersonated_by"
IMPERSONATED_BY_USERNAME_CLAIM = "impersonated_by_username"
IMPERSONATED_TENANT_NAME_CLAIM = "impersonated_tenant_name"
READ_ONLY_CLAIM = "read_only"

# 15 minutes, the short end of the 15–30 range the ticket settled on. Long
# enough to read an inbox and a broadcast report, short enough that a tab left
# open over lunch is already dead. Re-issuing is one POST, so the cost of the
# shorter bound falls on the operator, not on the customer.
#
# A module constant rather than a setting because the point is *not* to inherit
# SIMPLE_JWT["ACCESS_TOKEN_LIFETIME"], which is 90 days.
IMPERSONATION_TOKEN_LIFETIME = timedelta(minutes=15)

# The only non-safe requests an impersonated token may make. Ending a session
# is itself a POST, and the holder of the token is the one who knows the
# session is over, so without this exemption the audit row could never be
# closed by the only party in a position to close it.
#
# Matched on the resolved URL name, so the exemption names one endpoint and
# cannot be claimed by a view declaring an attribute or by a client sending a
# header. Keep this set at one entry; every addition is a hole in the control.
WRITE_EXEMPT_VIEW_NAMES = frozenset({"impersonation-end"})

READ_ONLY_MESSAGE = (
    "This session is an impersonated, read-only view of another organisation. "
    "Writes are refused while impersonating — exit the session to act as yourself."
)

SESSION_OVER_MESSAGE = "This impersonation session has ended or expired. Start a new one to continue."


# ── Claim reading ───────────────────────────────────────────────────────


def _claims(token) -> dict:
    """The payload of whatever DRF left in ``request.auth``.

    It is a SimpleJWT token on the JWT path, a ``Tenant`` on the access-key
    path, and ``None`` when unauthenticated — so this never assumes a payload
    is there to be read.
    """
    payload = getattr(token, "payload", token)
    return payload if isinstance(payload, dict) else {}


def impersonated_actor_id(token):
    """The real user's id carried by ``token``, or None when it is not borrowed."""
    return _claims(token).get(IMPERSONATED_BY_CLAIM)


def request_is_impersonated(request) -> bool:
    """Whether this request is being made with an impersonation token.

    Reads the attribute ``CustomJWTAuthentication`` copies onto the user first,
    and falls back to ``request.auth`` for callers reached before or without it.
    """
    if getattr(request.user, IMPERSONATED_BY_CLAIM, None):
        return True
    return bool(impersonated_actor_id(getattr(request, "auth", None)))


def impersonated_tenant_id(request):
    """The id of the one organisation this request may read, or None (#326).

    None means "not an impersonated request" — the caller should leave its
    queryset alone. An id means every row served must belong to that tenant.

    The id comes from the signed ``tenant_id`` claim, which
    ``issue_impersonation_token`` writes from the same ``tenant`` it records on
    the ``ImpersonationSession`` row, so the scope applied here and the
    organisation named in the audit trail cannot disagree.

    A borrowed token with no ``tenant_id`` cannot be issued by this module, so
    reaching that state means the claim was lost somewhere between issuing and
    here. It raises rather than returning None, because returning None would
    hand the session every organisation — the precise failure #326 exists to
    close, arrived at by a different route.

    Both claims are read off ``request.user``, where
    ``CustomJWTAuthentication.get_user`` stamps them, and **not** from
    ``request.auth`` — unlike ``request_is_impersonated``, which needs the token
    on the paths that run before the user is resolved. Touching ``request.auth``
    is not free: on a DRF ``Request`` that has not been authenticated it *runs*
    authentication, and with no authenticators that replaces ``request.user``
    with ``AnonymousUser``. This function is called from ``get_queryset``, where
    the user is already resolved and must stay resolved.
    """
    if not getattr(request.user, IMPERSONATED_BY_CLAIM, None):
        return None

    tenant_id = getattr(request.user, "tenant_id", None)
    if tenant_id is None:
        raise PermissionDenied(
            "This impersonation token names no organisation, so the reads it "
            "would make cannot be confined to one. Start a new session."
        )
    return tenant_id


# ── Issuing ─────────────────────────────────────────────────────────────


def issue_impersonation_token(actor, tenant):
    """Mint a short-lived, read-only token for ``tenant`` and record the session.

    Returns ``(raw_token, session)``. No refresh token exists to return: the
    session ends when the access token expires, or earlier when it is exited.

    ``is_superuser`` stays true on the token because the read path depends on
    it — ``TenantRolePermission`` grants reads to superusers and the actor has
    no role in the target organisation, so a token claiming otherwise could not
    read the dashboard this exists to show. The write side of that bypass is
    taken back by ``impersonation_write_denial``, which runs *before* the
    superuser short-circuit.
    """
    from users.models import ImpersonationSession

    token = AccessToken.for_user(actor)
    # AccessToken.for_user stamps SIMPLE_JWT's 90-day lifetime; replace it.
    token.set_exp(lifetime=IMPERSONATION_TOKEN_LIFETIME)

    token["username"] = actor.username
    token["is_superuser"] = True
    token["tenant_id"] = tenant.id
    token[IMPERSONATED_BY_CLAIM] = actor.pk
    token[IMPERSONATED_BY_USERNAME_CLAIM] = actor.username
    token[IMPERSONATED_TENANT_NAME_CLAIM] = tenant.name
    token[READ_ONLY_CLAIM] = True

    session = ImpersonationSession.start(
        actor=actor,
        tenant=tenant,
        token_jti=token["jti"],
        expires_at=datetime.fromtimestamp(token["exp"], tz=dt_timezone.utc),
    )

    return str(token), session


# ── Enforcement ─────────────────────────────────────────────────────────


def is_refusable_write(request) -> bool:
    """Whether this request is the kind an impersonated session may not make.

    Deliberately knows nothing about who is asking — it reads the method and
    the resolved URL name only, so it is safe to call from inside
    authentication, where ``request.user`` is not yet resolved and touching it
    would recurse.
    """
    if request.method in SAFE_METHODS:
        return False
    view_name = getattr(getattr(request, "resolver_match", None), "view_name", None)
    return view_name not in WRITE_EXEMPT_VIEW_NAMES


def impersonation_write_denial(request):
    """The reason this request must be refused, or None to let it through.

    Returns a message rather than raising so a DRF permission class can use it
    the way DRF expects (``self.message`` plus ``False``). The authentication
    layer raises instead — see ``enforce_impersonation``.
    """
    if not is_refusable_write(request):
        return None
    if not request_is_impersonated(request):
        return None
    return READ_ONLY_MESSAGE


def live_session_for(token):
    """The live ``ImpersonationSession`` for ``token``, or None if it is over.

    None covers three cases that must all stop the token: no audit row was
    written for it, the session was exited, or its recorded expiry has passed.
    """
    from users.models import ImpersonationSession

    jti = _claims(token).get("jti")
    if not jti:
        return None
    session = ImpersonationSession.objects.filter(token_jti=jti).first()
    if session is None or not session.is_live:
        return None
    return session


def enforce_impersonation(request, validated_token):
    """Apply every impersonation bound to one authenticated request.

    Called from ``CustomJWTAuthentication`` — the one place every JWT-bearing
    HTTP request passes through regardless of which permission classes a
    viewset declares, including the unauthenticated ones, which have no
    permission class that could refuse anything. A permission class alone would
    only cover the viewsets that remembered to list it.

    No-ops for ordinary tokens: the ``impersonated_by`` check is a dict lookup,
    and the session query only runs for a borrowed token.
    """
    if not impersonated_actor_id(validated_token):
        return None

    session = live_session_for(validated_token)
    if session is None:
        # 401, not 403: the credential itself is no longer good.
        raise AuthenticationFailed(SESSION_OVER_MESSAGE)

    # ``is_refusable_write`` rather than ``impersonation_write_denial`` because
    # we are inside authentication: the latter reads ``request.user``, and
    # ``request.user`` is what called us.
    if is_refusable_write(request):
        raise PermissionDenied(READ_ONLY_MESSAGE)

    return session

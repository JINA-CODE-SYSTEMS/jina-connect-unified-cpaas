"""Which routes to a working WhatsApp app this deployment actually offers.

There are three ways an organisation can end up able to send WhatsApp, and a
deployment does not necessarily offer all three:

``client_meta_app``
    The client already holds a Meta app and a WABA and hands over four values
    (``waba_id``, ``phone_number_id``, an access token and an app secret). This
    is the BYO route, always available, and the one ``POST /wa/v2/apps/`` serves.

``gupshup_embedded_signup``
    The client has no WABA and applies for one through Gupshup's Embedded
    Signup, which ``POST /tenants/tenant-gupshup/create-app/`` starts by minting
    an ESF URL valid for four days. The client ends up with Gupshup as their
    BSP, and Gupshup holding the commercial relationship — which is exactly why
    a deployment that resells its own Meta apps, or that onboards clients
    bringing their own, switches it off.

``meta_embedded_signup``
    The first-party equivalent: apply for a WABA without leaving the product and
    without a BSP in the middle. Not built — it needs Meta App Review and live
    token exchange — and so it is not listed here. It is listed the day it is
    built, and the clients reading this endpoint pick it up for free.

**Why this is one module and not a check at each call site.** The failure it is
written against is the one #310's client half hit and #641 recorded: a screen
offering a door the server will not open. If the UI decides on its own which
onboarding routes exist, then the moment a deployment flips a switch the client
is showing a button that 403s, with no explanation the user can act on. So the
server answers "which doors are open" and "which doors will I refuse" from the
same statement, and the client renders whatever it is told.

Switching Gupshup self-signup off refuses exactly two actions — the two that
*mint* something new. Reading ESF status, syncing WABA info, sending, receiving,
billing, and every app that already exists are untouched. A switch that stranded
a live customer would not be a configuration option, it would be an outage.
"""

from __future__ import annotations

from django.conf import settings

#: Stable keys. Clients branch on these, so they are contract — a rename is a
#: breaking change even though the human-readable labels beside them are not.
CLIENT_META_APP = "client_meta_app"
GUPSHUP_EMBEDDED_SIGNUP = "gupshup_embedded_signup"

#: The ``code`` a refusal carries, so a client can tell "this deployment does not
#: offer that route" apart from "your role may not do that" and from "Gupshup
#: said no". All three are plausible answers to the same button and they want
#: three different things said to the user.
SELF_SIGNUP_DISABLED_CODE = "gupshup_self_signup_disabled"

SELF_SIGNUP_DISABLED_DETAIL = (
    "This deployment does not offer WhatsApp signup through Gupshup. "
    "Connect a WhatsApp Business Account you already own by adding its Meta app "
    "credentials, or ask your platform administrator to onboard it for you."
)


def gupshup_self_signup_enabled() -> bool:
    """Whether a new Gupshup app or ESF URL may be minted on this deployment.

    Read through ``getattr`` with a True default rather than off
    ``settings.GUPSHUP_SELF_SIGNUP_ENABLED`` directly, so that a settings module
    predating this switch — an older private deployment, or a test settings
    override built by copying one — keeps the behaviour it has today instead of
    silently losing its only self-serve onboarding route.
    """
    return bool(getattr(settings, "GUPSHUP_SELF_SIGNUP_ENABLED", True))


def onboarding_routes() -> list[dict]:
    """The routes this deployment offers, in the order a UI should present them.

    ``available`` is the server's answer and the only one a client should act
    on. ``reason`` is populated only when a route is unavailable, so that a
    client can say *why* rather than silently hiding an option the user was
    told about elsewhere.
    """
    gupshup = gupshup_self_signup_enabled()
    return [
        {
            "key": CLIENT_META_APP,
            "available": True,
            "requires_permission": "wa_app.manage",
            "reason": None,
        },
        {
            "key": GUPSHUP_EMBEDDED_SIGNUP,
            "available": gupshup,
            "requires_permission": "wa_app.manage",
            "reason": None if gupshup else "disabled_by_platform",
        },
    ]

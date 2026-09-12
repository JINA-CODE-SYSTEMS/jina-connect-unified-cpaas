"""
BSP Adapter Factory
===================

Public API::

    from wa.adapters import get_bsp_adapter, resolve_bsp

    adapter = get_bsp_adapter(wa_app)     # returns the right adapter for the app's BSP
    result  = adapter.submit_template(template)

To add a new BSP:

1. Create ``wa/adapters/<bsp_name>.py`` with a class that extends
   ``BaseBSPAdapter`` (see ``base.py``).
2. Register it in ``_ADAPTER_REGISTRY`` below with the matching
   ``BSPChoices`` value.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Dict, Type

from wa.adapters.base import (
    AdapterResult,  # noqa: F401 — re-export
    BaseBSPAdapter,
)
from wa.adapters.gupshup import GupshupAdapter
from wa.adapters.meta_direct import MetaDirectAdapter
from wa.models import BSPChoices

if TYPE_CHECKING:
    from wa.models import WAApp

logger = logging.getLogger(__name__)


# ──────────────────────────────────────────────────────────────────────────────
# Registry — maps BSPChoices values → adapter classes.
# Add new BSPs here as they are implemented.
# ──────────────────────────────────────────────────────────────────────────────

_ADAPTER_REGISTRY: Dict[str, Type[BaseBSPAdapter]] = {
    BSPChoices.META: MetaDirectAdapter,
    BSPChoices.GUPSHUP: GupshupAdapter,
    # BSPChoices.TWILIO: TwilioAdapter,          # TODO
    # BSPChoices.MESSAGEBIRD: MessageBirdAdapter, # TODO
}

#: The BSP a WAApp is on when its ``bsp`` column is blank or null.
#:
#: This constant exists because the answer used to be given in three places
#: and they disagreed (#265). The factory defaulted to META; both send paths
#: treated "not exactly META" as Gupshup and raised "Gupshup credentials
#: missing"; the sync mapper and the webhook view required an exact META
#: match and rejected the app outright. A blank column therefore produced a
#: META adapter, a Gupshup mapper and a refused webhook — for the same row.
#:
#: Every caller now reads this through :func:`resolve_bsp`, so there is one
#: answer by construction rather than by three sites happening to agree.
DEFAULT_BSP: str = BSPChoices.META

# The adapter to use when wa_app.bsp is blank / null.
_DEFAULT_ADAPTER_CLASS: Type[BaseBSPAdapter] = _ADAPTER_REGISTRY[DEFAULT_BSP]


def resolve_bsp(wa_app: "WAApp") -> str:
    """Which BSP this app is on.

    The single answer to that question. Prefer this over reading
    ``wa_app.bsp`` directly: a blank column is not "no BSP", it is
    :data:`DEFAULT_BSP`, and a caller that compares the raw column against
    ``BSPChoices.META`` silently takes the other branch for it.
    """
    return (getattr(wa_app, "bsp", "") or "").strip() or DEFAULT_BSP


def bsp_q(bsp: str):
    """A ``Q`` matching every WAApp on *bsp*, including a blank column.

    ``filter(bsp=BSPChoices.META)`` looks like it means "the META apps" and
    does not: it misses every row whose column is blank, even though
    :func:`resolve_bsp` says those apps are on META. The META webhook
    receiver used exactly that filter, so a blank-BSP app's webhooks were
    answered with ``unknown_app`` while its templates submitted fine (#265).

    Any query that means "apps on this BSP" has to spell the default out,
    and this is the one place that does.
    """
    from django.db.models import Q

    q = Q(bsp=bsp)
    if bsp == DEFAULT_BSP:
        q |= Q(bsp="") | Q(bsp__isnull=True)
    return q


def get_bsp_adapter(wa_app: "WAApp") -> BaseBSPAdapter:
    """
    Factory that returns the correct BSP adapter for the given WAApp.

    Resolution order:
    1. ``wa_app.bsp`` looked up in ``_ADAPTER_REGISTRY``.
    2. If ``bsp`` is blank/null → ``_DEFAULT_ADAPTER_CLASS`` (META Direct).
    3. If the BSP is not yet implemented → raises ``NotImplementedError``.

    Returns:
        An initialised ``BaseBSPAdapter`` subclass.
    """
    if not (wa_app.bsp or "").strip():
        logger.info(f"WAApp {wa_app.id} has no BSP set — defaulting to {DEFAULT_BSP}")

    bsp = resolve_bsp(wa_app)
    adapter_cls = _ADAPTER_REGISTRY.get(bsp)
    if adapter_cls is None:
        raise NotImplementedError(f"No BSP adapter registered for '{bsp}'. Available: {list(_ADAPTER_REGISTRY.keys())}")

    return adapter_cls(wa_app)

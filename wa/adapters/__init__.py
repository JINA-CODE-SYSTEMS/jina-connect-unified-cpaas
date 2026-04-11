"""
BSP Adapter Factory
===================

Public API::

    from wa.adapters import get_bsp_adapter

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

# The adapter to use when wa_app.bsp is blank / null.
_DEFAULT_ADAPTER_CLASS: Type[BaseBSPAdapter] = MetaDirectAdapter


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
    bsp = (wa_app.bsp or "").strip()

    if not bsp:
        logger.info(f"WAApp {wa_app.id} has no BSP set — defaulting to META Direct")
        return _DEFAULT_ADAPTER_CLASS(wa_app)

    adapter_cls = _ADAPTER_REGISTRY.get(bsp)
    if adapter_cls is None:
        raise NotImplementedError(f"No BSP adapter registered for '{bsp}'. Available: {list(_ADAPTER_REGISTRY.keys())}")

    return adapter_cls(wa_app)

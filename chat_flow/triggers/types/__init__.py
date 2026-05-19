"""Concrete trigger types ship under this package (#188).

Each submodule registers itself with ``@register_trigger`` at import
time. ``chat_flow.apps.ChatFlowConfig.ready`` imports this package so
the registry is populated before any flow ``clean()`` runs.

Adding a new trigger type:

  1. Create ``chat_flow/triggers/types/<your_name>.py``.
  2. Subclass ``BaseTrigger``, declare a Pydantic ``config_model``,
     implement ``matches(event, config) -> bool``.
  3. Decorate with ``@register_trigger(\"<your_name>\")``.
  4. Import the module here so the registry sees it at boot.
  5. Document the ``event.extra`` fields the trigger reads.
"""

from __future__ import annotations

# Importing each module registers its trigger class with the registry.
from chat_flow.triggers.types import (  # noqa: F401
    ctwa_referral_received,
    inbound_keyword_match,
)

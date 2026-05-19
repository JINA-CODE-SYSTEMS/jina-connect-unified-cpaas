"""Save-time validation for ChatFlow.triggers (#188).

Called from :meth:`chat_flow.models.ChatFlow.clean` so a flow with a
malformed ``triggers`` payload fails to save with a clear per-entry
error message — same UX as the platform / flow_data validators that
already live on the model.

Validation enforced here:

  * ``triggers`` is a list (or None / empty, which is the legacy default).
  * Every entry is a dict with a non-empty ``type`` field.
  * The ``type`` is registered in the trigger registry.
  * The ``config`` payload validates against the trigger's
    :attr:`config_model` (Pydantic).
  * No two entries within one flow are exact duplicates
    (same type + same config). Duplicates aren't outright broken,
    but they double-spawn sessions and almost always indicate a UX
    bug we want to surface at save time.
"""

from __future__ import annotations

import json

from django.core.exceptions import ValidationError
from pydantic import ValidationError as PydanticValidationError

from chat_flow.triggers.registry import get_trigger_cls, list_trigger_types


def validate_trigger_list(triggers) -> None:
    if triggers in (None, []):
        return
    if not isinstance(triggers, list):
        raise ValidationError({"triggers": "Must be a list of {type, config} entries."})

    seen: set[str] = set()
    for i, entry in enumerate(triggers):
        if not isinstance(entry, dict):
            raise ValidationError({"triggers": f"Entry {i}: must be an object."})

        type_name = entry.get("type")
        if not type_name or not isinstance(type_name, str):
            raise ValidationError({"triggers": f"Entry {i}: missing or non-string 'type'."})

        try:
            cls = get_trigger_cls(type_name)
        except LookupError:
            raise ValidationError(
                {"triggers": (f"Entry {i}: unknown trigger type {type_name!r}. Known: {list_trigger_types()}")}
            )

        config = entry.get("config") or {}
        if not isinstance(config, dict):
            raise ValidationError({"triggers": f"Entry {i}: 'config' must be an object."})

        try:
            cls.config_model(**config)
        except PydanticValidationError as exc:
            # Pydantic's per-field error path is informative for the
            # frontend; surface it verbatim under the 'triggers' key.
            raise ValidationError({"triggers": f"Entry {i} ({type_name}): {exc.errors()}"}) from exc

        # Duplicate detection — same type + same config payload (after
        # canonical JSON sort) is rejected.
        key = type_name + ":" + json.dumps(config, sort_keys=True, default=str)
        if key in seen:
            raise ValidationError({"triggers": f"Entry {i}: duplicate of an earlier trigger."})
        seen.add(key)


__all__ = ["validate_trigger_list"]

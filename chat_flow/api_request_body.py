"""One answer to the question "what does an API node put on the wire?".

The flow editor offers three body types — JSON, FORM and RAW — and saves the
choice as ``api_body_type``. Until this module existed the executor branched
on ``"json"`` and sent everything else as ``data=<str>``, so FORM and RAW were
the same code path: no URL-encoding, and no ``Content-Type`` header, because
requests only sets one for ``json=`` or a dict ``data=``.

The rule that validates the body (``API_004``) and the executor that sends it
now both read this module, so a body the sidebar accepts is a body the
executor can send.

``build_request_body`` never raises and never silently downgrades: a body that
cannot be built comes back with ``error`` set and empty ``kwargs``, and it is
the caller's job to refuse to make the request.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any, Dict, Optional, Tuple

FORM_CONTENT_TYPE = "application/x-www-form-urlencoded"
JSON_CONTENT_TYPE = "application/json"

#: The body types the editor offers. Anything else is treated as ``raw``.
BODY_TYPES: Tuple[str, ...] = ("json", "form", "raw")

DEFAULT_BODY_TYPE = "json"

#: ``{{name}}`` — a placeholder the executor substitutes before sending.
PLACEHOLDER_PATTERN = re.compile(r"\{\{\s*(.+?)\s*\}\}")

#: Substituted into a body before parsing it for validation. A bare ``0`` is
#: valid JSON both inside quotes (``"0"``) and outside them (``0``), so a
#: document that parses with it is a document that can parse at run time.
VALIDATION_PLACEHOLDER = "0"


@dataclass(frozen=True)
class RequestBody:
    """What to merge into ``requests.request(**kwargs)`` for this body.

    ``kwargs``  — ``{"json": ...}`` or ``{"data": ...}``, or empty.
    ``headers`` — headers this body type requires; the caller applies them
                  only where the operator has not set their own.
    ``error``   — set when the body cannot be built. ``kwargs`` is then empty
                  and the request must not be made.
    """

    kwargs: Dict[str, Any] = field(default_factory=dict)
    headers: Dict[str, str] = field(default_factory=dict)
    error: Optional[str] = None


def normalise_body_type(body_type: Optional[str]) -> str:
    """Fold an unknown or missing body type onto the editor's default."""
    candidate = (body_type or DEFAULT_BODY_TYPE).strip().lower()
    return candidate if candidate in BODY_TYPES else "raw"


def build_request_body(body_type: Optional[str], body_str: Optional[str]) -> RequestBody:
    """Turn the saved body text into request kwargs for *body_type*."""
    if not body_str or not body_str.strip():
        return RequestBody()

    kind = normalise_body_type(body_type)

    if kind == "json":
        try:
            return RequestBody(kwargs={"json": json.loads(body_str)})
        except (json.JSONDecodeError, TypeError, ValueError) as exc:
            return RequestBody(error=f"Body is set to JSON but is not valid JSON: {exc}")

    if kind == "form":
        return RequestBody(kwargs={"data": _form_payload(body_str)}, headers={"Content-Type": FORM_CONTENT_TYPE})

    return RequestBody(kwargs={"data": body_str})


def describe_json_body(body_str: Optional[str]) -> Optional[str]:
    """Why a JSON body would fail to send, or ``None`` if it would not.

    Placeholders are substituted with a scalar first, because ``{{name}}`` is
    not JSON and is never sent as itself.
    """
    if not body_str or not body_str.strip():
        return None

    probe = PLACEHOLDER_PATTERN.sub(VALIDATION_PLACEHOLDER, body_str)
    try:
        json.loads(probe)
    except (json.JSONDecodeError, TypeError, ValueError) as exc:
        return str(exc)
    return None


def _form_payload(body_str: str) -> Any:
    """A dict where the operator wrote an object, the string where they did not.

    A dict is the useful case: requests URL-encodes it, so a substituted value
    containing ``&`` or ``=`` cannot split the body into extra fields. An
    operator who has already written ``a=1&b=2`` gets that string sent as-is.
    """
    try:
        parsed = json.loads(body_str)
    except (json.JSONDecodeError, TypeError, ValueError):
        return body_str

    if not isinstance(parsed, dict):
        return body_str

    return {key: _form_value(value) for key, value in parsed.items()}


def _form_value(value: Any) -> Any:
    """Form fields are text. Lists survive as repeated fields."""
    if isinstance(value, bool):
        return "true" if value else "false"
    if value is None:
        return ""
    if isinstance(value, (str, int, float)):
        return value
    if isinstance(value, list):
        return [_form_value(item) for item in value]
    return json.dumps(value)

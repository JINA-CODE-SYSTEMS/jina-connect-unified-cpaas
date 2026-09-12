"""Masked "equivalent curl" debug output, shared by every BSP HTTP client (#336).

Each client reconstructs the request it is about to make as a pasteable ``curl``
command, keeps it on ``last_curl_command`` and emits it for diagnosis. That is a
genuinely useful artefact — a rejected template submission is almost impossible
to argue about without the exact request — but the reconstruction used to include
the ``Authorization`` header verbatim and was written to stdout with ``print``.
So every Graph call published a live access token, and ``last_curl_command`` is
read by callers that copy it into a task result and a template's debug blob.

Two rules, both enforced here rather than at each call site:

* **Mask where the string is built.** ``last_curl_command`` is a public property
  with callers that persist it, so a mask applied at the print site would leave
  the durable paths open. Everything a client shows a human goes through
  :func:`build_form_curl`, :func:`build_json_curl` or :func:`curl_block`, and all
  three mask before they return.
* **Mask by the *name* of the thing, not by a list of known credentials.** A
  header called ``Authorization`` is a credential whatever the provider calls its
  token, and ``meta_app_secret`` (#311, read by the webhook receiver since #306)
  travels the same route. :func:`is_credential_name` decides, so a credential
  added later is masked without anyone remembering to come back here.

What survives masking is the part that makes the diagnostic worth keeping: the
method, the URL, the body, the non-credential headers, and the auth *scheme*
(``-H "Authorization: Bearer [redacted]"``). Substitute your own token and the
command runs.

Output goes to ``logging`` at debug level, never to ``print``: being switchable
is the main reason a request reconstruction is defensible at all. Every client
logs under the ``wa.utility.apis`` prefix, which is the one knob that silences
all of it.

The redaction convention follows ``wa.services.meta_preflight._redact`` (#311):
replace the literal credential with ``[redacted]`` and keep going.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Iterable, Mapping
from typing import Any, Optional
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

#: What a masked value reads as. Matches ``meta_preflight._redact`` so grepping a
#: log for one convention finds both.
REDACTED = "[redacted]"

#: Header names that are a credential outright, matched case-insensitively and
#: exactly. Names that merely *contain* a hint are caught by
#: :data:`CREDENTIAL_NAME_HINTS` instead.
CREDENTIAL_HEADERS = frozenset(
    {
        "authorization",
        "proxy-authorization",
        "cookie",
        "set-cookie",
        "apikey",
        "api-key",
        "x-api-key",
        "x-apikey",
    }
)

#: Substrings that make a header or parameter name credential-shaped. Compared
#: against the name with ``-`` folded to ``_``, so ``X-App-Secret``,
#: ``app_secret`` and ``access-token`` all match.
#:
#: Spelled as a tuple of fragments rather than a constant per credential: bandit
#: B105 fires on the *name* of anything holding a string literal when the name
#: reads like a credential, and a screenful of per-credential false positives
#: would train the eye to skip the real ones.
CREDENTIAL_NAME_HINTS = (
    "authorization",
    "access_token",
    "refresh_token",
    "id_token",
    "auth_token",
    "api_token",
    "apikey",
    "api_key",
    "app_secret",
    "client_secret",
    "secret",
    "password",
    "passwd",
    "credential",
    "signature",
)

#: Auth schemes worth keeping in a masked header: knowing the request sent
#: ``Bearer`` rather than ``OAuth`` is half of diagnosing a 401, and the scheme
#: is not the secret.
_KNOWN_SCHEMES = frozenset({"bearer", "basic", "oauth", "digest", "apikey", "hmac"})

#: The shortest string worth scrubbing out of free text by value. Below this a
#: "credential" is more likely to be a substring of something innocent —
#: name-based masking covers the header itself regardless of length.
_MIN_SCRUBBABLE = 8

_BANNER = "=" * 80


def is_credential_name(name: Any) -> bool:
    """Does a header or parameter with this name carry a credential?"""
    lowered = str(name).strip().lower()
    if lowered in CREDENTIAL_HEADERS:
        return True
    folded = lowered.replace("-", "_")
    return any(hint in folded for hint in CREDENTIAL_NAME_HINTS)


def mask_value(name: Any, value: Any) -> str:
    """Mask one header or parameter value, keeping its auth scheme if it has one."""
    text = "" if value is None else str(value)
    if not is_credential_name(name):
        return text
    scheme, _, rest = text.partition(" ")
    if rest.strip() and scheme.lower() in _KNOWN_SCHEMES:
        return f"{scheme} {REDACTED}"
    return REDACTED


def mask_mapping(values: Optional[Mapping]) -> dict:
    """Copy a header/parameter mapping with every credential-shaped value masked."""
    if not isinstance(values, Mapping):
        return {}
    return {key: mask_value(key, value) for key, value in values.items()}


def mask_body(data: Any) -> Any:
    """Mask credential-shaped keys in a request body, leaving the payload alone.

    A template submission body is the thing being diagnosed, so it is copied
    through untouched apart from keys like ``access_token`` — which Graph does
    accept in a body, and which would otherwise slip past header masking.
    """
    if isinstance(data, Mapping):
        return {key: (mask_value(key, value) if is_credential_name(key) else value) for key, value in data.items()}
    return data


def redact(text: Any, *secrets: Any) -> str:
    """Scrub literal credential values out of arbitrary text.

    The belt to :func:`mask_mapping`'s braces: a token pasted into a URL, echoed
    in a provider error or formatted into a string by some path that never saw a
    header dict still comes out masked.
    """
    cleaned = "" if text is None else str(text)
    for secret in secrets:
        candidate = "" if secret is None else str(secret)
        if len(candidate) >= _MIN_SCRUBBABLE:
            cleaned = cleaned.replace(candidate, REDACTED)
    return cleaned


def redact_url(url: Any) -> str:
    """Mask credential-shaped query parameters in a URL.

    Meta accepts ``?access_token=`` as an alternative to the header, and a signed
    media URL carries its own parameters. The URL is rebuilt only when something
    was actually masked, so an ordinary URL is handed back byte for byte.
    """
    text = "" if url is None else str(url)
    if "?" not in text:
        return text
    try:
        parts = urlsplit(text)
        pairs = parse_qsl(parts.query, keep_blank_values=True)
    except ValueError:
        return text
    if not any(is_credential_name(key) for key, _ in pairs):
        return text
    masked = [(key, REDACTED if is_credential_name(key) else value) for key, value in pairs]
    # ``safe`` keeps the mask spelled the same way everywhere rather than as
    # ``%5Bredacted%5D``, so one grep finds every masked value in a log.
    query = urlencode(masked, safe="[]")
    return urlunsplit((parts.scheme, parts.netloc, parts.path, query, parts.fragment))


def curl_block(title: str, command: str, *, secrets: Iterable[Any] = ()) -> str:
    """Wrap a ready-made curl command in the banner the clients have always used.

    For the hand-rolled reconstructions (multipart uploads, binary downloads)
    that do not go through the builders below. Still scrubbed on the way out.
    """
    body = redact(command, *secrets)
    return f"{_BANNER}\n{title}\n{_BANNER}\n{body}\n{_BANNER}"


def _header_args(headers: Optional[Mapping], quote: str = '"') -> str:
    return "".join(f" \\\n  -H {quote}{key}: {value}{quote}" for key, value in mask_mapping(headers).items())


def header_args(headers: Optional[Mapping], quote: str = '"') -> str:
    """Masked ``-H`` arguments for a hand-rolled curl reconstruction."""
    return _header_args(headers, quote)


def _query_string(data: Any) -> str:
    if not isinstance(data, Mapping):
        return ""
    return "&".join(f"{key}={value}" for key, value in mask_body(data).items() if value is not None)


def build_form_curl(
    method: str,
    url: Any,
    headers: Optional[Mapping],
    data: Any,
    *,
    title: str = "EQUIVALENT CURL COMMAND FOR DEBUG:",
    secrets: Iterable[Any] = (),
) -> str:
    """Reconstruct a form-encoded request as a masked curl command.

    One implementation for the Meta, Gupshup and WATI clients, which carried
    three copies of it — byte-identical in two of them.
    """
    safe_url = redact_url(url)
    command = f'curl -X {method} "{safe_url}"'

    if method == "GET" and isinstance(data, Mapping) and data:
        params = _query_string(data)
        if params:
            separator = "?" if "?" not in safe_url else "&"
            command = f'curl -X {method} "{safe_url}{separator}{params}"'
        command += _header_args(headers)
    else:
        command += _header_args(headers)
        if method in ("POST", "PUT", "PATCH") and data:
            if isinstance(data, Mapping):
                for key, value in mask_body(data).items():
                    if value is None:
                        continue
                    command += f' \\\n  --data-urlencode "{key}={value}"'
            else:
                command += f" \\\n  --data-urlencode '{data}'"

    return curl_block(title, command, secrets=secrets)


def build_json_curl(
    method: str,
    url: Any,
    headers: Optional[Mapping],
    data: Any,
    *,
    title: str = "EQUIVALENT CURL COMMAND (JSON) FOR DEBUG:",
    secrets: Iterable[Any] = (),
) -> str:
    """Reconstruct a JSON-bodied request as a masked curl command."""
    safe_url = redact_url(url)
    command = f'curl -X {method} "{safe_url}"'

    if method == "GET" and isinstance(data, Mapping) and data:
        params = _query_string(data)
        if params:
            separator = "?" if "?" not in safe_url else "&"
            command = f'curl -X {method} "{safe_url}{separator}{params}"'
        command += _header_args(headers)
    else:
        command += _header_args(headers)
        if method in ("POST", "PUT", "PATCH", "DELETE") and data:
            json_str = json.dumps(mask_body(data), indent=2)
            escaped = json_str.replace('"', '\\"')
            command += f' \\\n  -d "{escaped}"'

    return curl_block(title, command, secrets=secrets)


def log_curl(logger: logging.Logger, command: str) -> None:
    """Emit a (already masked) curl reconstruction at debug level.

    ``print`` cannot be filtered, which is what made this output indefensible:
    a container collects stdout and keeps it for longer than anyone would choose
    for a request dump.
    """
    logger.debug("%s", command)


def log_request_failure(
    logger: logging.Logger,
    *,
    method: str,
    url: Any,
    body: Any = None,
    body_label: str = "Data sent",
    secrets: Iterable[Any] = (),
) -> None:
    """Log the request side of a non-2xx, without restating the headers.

    The masked curl command logged just before this already carries the full
    header set; a second, separately-masked copy of the same dict is only one
    more place for the next credential header to escape through.
    """
    logger.debug(
        "BSP request failed — %s %s\n%s: %s",
        method,
        redact_url(url),
        body_label,
        redact(body, *secrets),
    )

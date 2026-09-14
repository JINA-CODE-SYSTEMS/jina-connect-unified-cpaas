"""Which *fields* a response body carries, for the write-only acceptance tests.

Not a test module — pytest collects ``test_*.py`` only.

Two tests assert that the write-only credential fields never come back out of
the API (#275, #289). Both asked the question as a substring search over the
serialized body::

    assert "meta_app_secret" not in body

which answers a different question than the one they mean. A substring match
cannot tell a field from a field whose *name merely starts the same way*, and
the moment #370 added ``meta_app_secret_hint`` — a masked tail, deliberately
readable, carrying none of the secret — both tests failed on a body with
nothing wrong in it.

Asking for the key names instead is the question they were always asking: is
there a field called ``meta_app_secret`` in this response. The value
assertions in those tests are left as substring searches, because there the
substring *is* the question — a secret must not appear anywhere in the body,
under any key, at any depth, however it was spelled into it.

Recursive because a read response is not flat: a list endpoint nests rows
under ``results``, and a leak one level down is the same leak.
"""

from __future__ import annotations

from typing import Any


def json_field_names(payload: Any) -> set[str]:
    """Every key name appearing anywhere in *payload*, at any depth.

    Args:
        payload: parsed JSON — dict, list, or scalar.

    Returns:
        set of key names; empty for a scalar.
    """
    names: set[str] = set()
    if isinstance(payload, dict):
        for key, value in payload.items():
            names.add(str(key))
            names |= json_field_names(value)
    elif isinstance(payload, (list, tuple)):
        for item in payload:
            names |= json_field_names(item)
    return names

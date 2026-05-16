"""Per-call IVR session state in Redis (#168).

The compiler builds the "happy path" response the answer webhook
returns; once a ``voice.gather_dtmf`` or ``voice.record`` node
collects input, the provider POSTs the result to the gather webhook,
which calls into the session to:

  * read where in the flow we are (``current_node_id``)
  * record gathered DTMF / speech / recording id
  * decide the next node (gather webhook picks the matching outgoing
    edge based on the buttons / digits)
  * write the updated cursor back

Lost session detection: if ``IVRSession.exists(call_id)`` is ``False``
the gather webhook returns an apology TwiML and hangs up.

Key layout: ``voice:ivr:{call_id}`` → JSON dict. TTL =
``VOICE_MAX_CALL_DURATION_SECONDS + 60`` so a stuck call eventually
expires.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from django.conf import settings
from redis.exceptions import WatchError

from abstract.webhooks import _get_redis_client

logger = logging.getLogger(__name__)


# Cap on optimistic-lock retries before ``update`` gives up — high
# enough to absorb a couple of racing gather webhooks, low enough that
# a stuck loop fails fast.
_WATCH_RETRIES = 5


def _key(call_id: str) -> str:
    return f"voice:ivr:{call_id}"


def _ttl_seconds() -> int:
    base = getattr(settings, "VOICE_MAX_CALL_DURATION_SECONDS", 3600)
    return int(base) + 60


class IVRSession:
    """Redis-backed per-call IVR cursor.

    Construct with a call id; reads / writes the session dict via
    Redis. The class itself doesn't cache so concurrent gather
    webhooks see the latest state.
    """

    def __init__(self, call_id: str):
        self.call_id = call_id

    # ── lifecycle ───────────────────────────────────────────────────────

    @classmethod
    def create(
        cls,
        call_id: str,
        *,
        flow_id,
        entry_node_id: str | None = None,
        variables: dict[str, Any] | None = None,
    ) -> "IVRSession":
        """Initialise a session in Redis and return the wrapper."""
        state = {
            "flow_id": str(flow_id) if flow_id is not None else None,
            "current_node_id": entry_node_id,
            "dtmf_buffer": "",
            "variables": variables or {},
            "retry_counts": {},
        }
        client = _get_redis_client()
        client.set(_key(call_id), json.dumps(state), ex=_ttl_seconds())
        return cls(call_id)

    @classmethod
    def exists(cls, call_id: str) -> bool:
        client = _get_redis_client()
        return bool(client.exists(_key(call_id)))

    def delete(self) -> None:
        client = _get_redis_client()
        client.delete(_key(self.call_id))

    # ── read / write ────────────────────────────────────────────────────

    def get(self) -> dict[str, Any] | None:
        """Return the full state dict, or ``None`` if the session has
        expired / never existed."""
        client = _get_redis_client()
        raw = client.get(_key(self.call_id))
        if raw is None:
            return None
        if isinstance(raw, bytes):
            raw = raw.decode()
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            logger.warning(
                "[voice.ivr.session] %s has invalid JSON in Redis",
                self.call_id,
            )
            return None

    def update(self, **patch: Any) -> dict[str, Any]:
        """Merge ``patch`` into the stored state and persist atomically.

        Uses Redis WATCH / MULTI / EXEC so two concurrent gather
        webhooks for the same call can't read-modify-write the
        variables dict with the later write winning (which silently
        dropped the earlier var). Retries up to ``_WATCH_RETRIES``
        times on contention. (#179 review)
        """
        client = _get_redis_client()
        key = _key(self.call_id)
        for _attempt in range(_WATCH_RETRIES):
            pipe = client.pipeline()
            try:
                pipe.watch(key)
                raw = pipe.get(key)
                if raw is None:
                    raise RuntimeError(f"IVR session for call {self.call_id} does not exist (expired?)")
                if isinstance(raw, bytes):
                    raw = raw.decode()
                state = json.loads(raw)

                # Shallow merge, with nested merge for ``variables`` so
                # we don't wipe earlier vars when stamping a new one.
                merged_patch = dict(patch)
                if "variables" in patch and isinstance(patch["variables"], dict):
                    merged_patch["variables"] = {
                        **(state.get("variables") or {}),
                        **patch["variables"],
                    }
                state.update(merged_patch)

                pipe.multi()
                pipe.set(key, json.dumps(state), ex=_ttl_seconds())
                pipe.execute()
                return state
            except WatchError:
                # Another writer beat us; loop and re-read.
                continue
            finally:
                try:
                    pipe.reset()
                except Exception:  # noqa: BLE001
                    pass

        raise RuntimeError(
            f"IVR session for call {self.call_id} could not be updated after {_WATCH_RETRIES} contention retries"
        )

    # ── convenience accessors ───────────────────────────────────────────

    def advance_to(self, node_id: str) -> dict[str, Any]:
        """Move the cursor to ``node_id`` and clear the DTMF buffer."""
        return self.update(current_node_id=node_id, dtmf_buffer="")

    def record_dtmf(self, digits: str) -> dict[str, Any]:
        """Append ``digits`` to the DTMF buffer."""
        state = self.get()
        if state is None:
            raise RuntimeError(f"IVR session for call {self.call_id} does not exist (expired?)")
        new_buffer = (state.get("dtmf_buffer") or "") + digits
        return self.update(dtmf_buffer=new_buffer)

    def set_variable(self, name: str, value: Any) -> dict[str, Any]:
        return self.update(variables={name: value})

    def increment_retry(self, node_id: str) -> int:
        """Bump the retry counter for ``node_id`` and return the new value."""
        state = self.get()
        if state is None:
            raise RuntimeError(f"IVR session for call {self.call_id} does not exist (expired?)")
        retries = {**(state.get("retry_counts") or {})}
        retries[node_id] = int(retries.get(node_id, 0)) + 1
        self.update(retry_counts=retries)
        return retries[node_id]

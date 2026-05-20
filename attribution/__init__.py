"""Meta Conversions API attribution loop (#197).

Owns :class:`attribution.models.AttributionEvent` and the async batched
CAPI push task. Critical contracts (locked):

  * ``Lead`` event fires on ``CtwaLead.qualification_status``
    transitioning to ``qualified``, NEVER on first inbound message.
  * ``event_id`` = ``f\"{lead_id}-{event_name}-{sequence}\"`` so
    re-qualification (a contact qualifying, going cold, qualifying
    again) produces distinct events that don't collide with the
    pixel-side dedup key.
  * Match keys priority: ``ctwa_clid`` → ``fbc`` (synthesised) →
    SHA-256 phone → SHA-256 email.
  * Consent gate: never hash + send without per-region consent.

The actual CAPI push HTTP call is stubbed pending #190 Meta App
Review. The signal-driven event creation, retry/DLQ logic, and
``event_id`` strategy are real.
"""

default_app_config = "attribution.apps.AttributionConfig"

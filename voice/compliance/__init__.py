"""Voice compliance helpers (#171).

* ``time_of_day`` — TCPA-style call-time windows. Voice dispatcher
  consults this before placing a call; out-of-window dispatches get
  rescheduled to the next allowed time.
* ``consent`` — recording-consent gating. Adapters check
  ``recording_consent_required(call)`` before turning on call
  recording; tenants can mandate explicit consent via
  ``TenantVoiceApp.recording_requires_consent``.
"""

from __future__ import annotations

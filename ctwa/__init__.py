"""Click-to-WhatsApp Ads (#187 epic).

This app owns:

* :class:`ctwa.models.CtwaCampaign` — first-class campaign object
  (NOT a flag on ``Broadcast`` — broadcasts are business-initiated,
  CTWA is user-initiated, different lifecycle).
* :class:`ctwa.models.CtwaLead` — one row per CTWA-originated
  conversation.
* :mod:`ctwa.ingestion` — called from ``wa/tasks.py`` after an
  inbound WA message is persisted; matches the ad referral payload
  to a campaign (or flags an orphan) and creates the lead.
* :mod:`ctwa.tasks.reconcile_orphans` — weekly Celery beat that
  walks orphan leads, queries Meta Marketing API for the ad id, and
  attaches a shadow campaign when found.

Critical design contracts (locked in #187):

  * ``Lead`` CAPI event fires on ``qualification_status`` transition
    to ``qualified``, NEVER on first inbound message. See
    :attr:`CtwaCampaign.qualification_signal`.
  * Orphan campaigns (ad_id with no matching local row) still create
    the lead and fire the trigger — paused-but-cached ads keep
    sending traffic for hours.
  * ``WaConversation`` is the unit that holds service-window state;
    every ``CtwaLead`` links to one.
"""

default_app_config = "ctwa.apps.CtwaConfig"

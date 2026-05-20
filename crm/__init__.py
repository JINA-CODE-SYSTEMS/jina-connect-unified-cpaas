"""CRM connector framework (#198).

Net-new app — no CRM connectors existed pre-CTWA (roadmap issues
#36–#38 confirm). Owns:

* :class:`crm.models.CrmConnection` — per-tenant OAuth / API-key state.
* :class:`crm.models.CrmEntityMapping` — maps Jina-side status values
  to CRM-side custom field values.
* :class:`crm.models.CrmSyncEvent` — audit log of inbound webhooks
  (Salesforce especially will need this for support cases).
* :class:`crm.adapters.base.CrmConnector` ABC — every CRM gets a
  subclass.
* HubSpot + Salesforce + generic-webhook adapters with idempotency
  keying (``external_event_id``) so we don't echo our own outbound
  push back into the system.

External HTTP calls are stubbed pending OAuth-app creation on each
CRM. The framework, idempotency logic, and audit log are real.
"""

default_app_config = "crm.apps.CrmConfig"

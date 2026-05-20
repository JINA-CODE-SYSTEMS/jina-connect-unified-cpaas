"""CRM connector adapters (#198).

Importing this package registers HubSpot + Salesforce + generic-webhook
adapters into the connector registry. Each connector subclasses
:class:`crm.adapters.base.CrmConnector` and is keyed off
:class:`crm.constants.CrmProvider`.
"""

from __future__ import annotations

from crm.adapters import generic_webhook, hubspot, salesforce  # noqa: F401
from crm.adapters.base import CrmConnector, get_connector

__all__ = ["CrmConnector", "get_connector"]

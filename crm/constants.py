from __future__ import annotations

from django.db import models


class CrmProvider(models.TextChoices):
    HUBSPOT = "hubspot", "HubSpot"
    SALESFORCE = "salesforce", "Salesforce"
    GENERIC_WEBHOOK = "generic_webhook", "Generic webhook"

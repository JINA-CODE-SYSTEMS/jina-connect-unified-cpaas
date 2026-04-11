"""
WhatsApp Services

This package contains services for WhatsApp integration:
- sync_templates: Syncing templates between META Direct API and BSPs
- META Template: Template creation via META Direct API
- Quota: Managing WhatsApp message quotas
- Template Notifications: Sending template-related notifications
"""

from wa.services.meta_template_service import (
    MetaTemplateService,
    MetaTemplateServiceError,
    TemplateCategory,
    TemplateType,
    get_validator_class,
)

# Re-export sync_templates for convenience
from wa.services.sync_templates import (
    BaseSyncService,
    BSPType,
    GupshupSyncService,
    SyncError,
    get_bsp_type_from_app,
    get_sync_service,
)

__all__ = [
    # META Template
    "MetaTemplateService",
    "MetaTemplateServiceError",
    "TemplateCategory",
    "TemplateType",
    "get_validator_class",
    # Sync Templates
    "BaseSyncService",
    "BSPType",
    "SyncError",
    "GupshupSyncService",
    "get_sync_service",
    "get_bsp_type_from_app",
]

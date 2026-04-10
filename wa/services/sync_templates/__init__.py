"""
Template Sync Services

This package provides services for syncing templates between META Direct API
and various BSPs (Business Solution Providers).

When templates are created via META Direct API, BSPs need to sync their
internal records with META's template database.

Supported BSPs:
- Gupshup (implemented)
- WATI (planned)
- AISensy (planned)
- Interakt (planned)
- Yellow.ai (planned)

Usage:
    from wa.services.sync_templates import get_sync_service, BSPType
    
    # Get sync service for a BSP app
    sync_service = get_sync_service(bsp_app)
    if sync_service:
        bsp_id = sync_service.sync_and_get_bsp_id(element_name)
"""

from wa.services.sync_templates.base import (
    BaseSyncService,
    BSPType,
    SyncError,
    get_bsp_type_from_app,
    get_sync_service,
)
from wa.services.sync_templates.gupshup import GupshupSyncService

__all__ = [
    # Base
    "BaseSyncService",
    "BSPType",
    "SyncError",
    "get_bsp_type_from_app",
    "get_sync_service",
    # Implementations
    "GupshupSyncService",
]

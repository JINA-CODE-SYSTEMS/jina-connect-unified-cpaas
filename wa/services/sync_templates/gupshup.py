"""
Gupshup Template Sync Service

This module provides the sync service implementation for Gupshup BSP.

Gupshup Partner API endpoints used:
- Sync: POST /partner/app/{appId}/template/sync
- Get templates: GET /partner/app/{appId}/templates?elementName=<name>
- Get template by ID: GET /partner/app/{appId}/templates/{templateId}
"""

import logging
from typing import Any, Dict, Optional

from wa.services.sync_templates.base import (
    BaseSyncService,
    BSPType,
    SyncError,
    register_sync_service,
)

logger = logging.getLogger(__name__)


@register_sync_service(BSPType.GUPSHUP)
class GupshupSyncService(BaseSyncService):
    """
    Sync service for Gupshup BSP.

    Gupshup maintains its own template database that needs to be synced
    with META's template database after templates are created via META Direct API.

    Required credentials on bsp_app (TenantWAApp):
    - app_id: Gupshup application ID
    - app_secret: Gupshup app secret (used for API authorization)
    """

    bsp_type = BSPType.GUPSHUP

    def __init__(self, bsp_app):
        """
        Initialize Gupshup sync service.

        Args:
            bsp_app: TenantGupshupApp model instance
        """
        # Store references before calling parent __init__
        # (parent calls _validate_configuration which needs these)
        self._bsp_app = bsp_app
        super().__init__(bsp_app)

    def _validate_configuration(self) -> None:
        """Validate Gupshup app configuration."""
        if not hasattr(self._bsp_app, "app_id") or not self._bsp_app.app_id:
            raise SyncError("Gupshup app_id not configured", code="missing_app_id", bsp_type=self.bsp_type.value)

        if not hasattr(self._bsp_app, "app_secret") or not self._bsp_app.app_secret:
            raise SyncError(
                "Gupshup app_secret not configured", code="missing_app_secret", bsp_type=self.bsp_type.value
            )

        self.app_id = self._bsp_app.app_id
        self.app_secret = self._bsp_app.app_secret

    @property
    def headers(self) -> Dict[str, str]:
        """Get authorization headers for Gupshup Partner API."""
        return {"Authorization": self.app_secret, "Content-Type": "application/json"}

    def _get_template_api(self):
        """
        Get configured Gupshup Template API instance.

        Uses the API class from wa.utility.apis.gupshup.template_api
        for consistent API handling.
        """
        from wa.utility.apis.gupshup.template_api import TemplateAPI

        return TemplateAPI(appId=self.app_id, token=self.app_secret)

    def sync_templates(self) -> Dict[str, Any]:
        """
        Trigger template sync on Gupshup.

        Calls: POST /partner/app/{appId}/template/sync

        Returns:
            dict: Sync response from Gupshup

        Raises:
            SyncError: If sync fails
        """
        api = self._get_template_api()

        logger.info(f"[Gupshup] Triggering template sync for app {self.app_id}")

        try:
            result = api.sync_templates_with_meta()
            logger.info(f"[Gupshup] Template sync triggered successfully: {result}")
            return result

        except Exception as e:
            raise SyncError(f"Gupshup sync failed: {str(e)}", code="sync_failed", bsp_type=self.bsp_type.value)

    def get_template_by_element_name(self, element_name: str) -> Optional[Dict[str, Any]]:
        """
        Get template from Gupshup by element name.

        Calls: GET /partner/app/{appId}/templates?elementName=<name>

        Args:
            element_name: The template's element name

        Returns:
            dict or None: Template data if found
        """
        api = self._get_template_api()

        logger.info(f"[Gupshup] Fetching template '{element_name}' for app {self.app_id}")

        try:
            result = api.get_template_by_element_name(element_name)

            if not result:
                return None

            # Handle different response formats
            templates = result.get("templates", result.get("data", []))

            if isinstance(templates, list) and templates:
                # Find exact match by element name
                for template in templates:
                    if template.get("elementName") == element_name:
                        return template
                # Return first result if no exact match
                return templates[0]
            elif isinstance(result, dict) and result.get("elementName") == element_name:
                # Direct template object
                return result

            return None

        except Exception as e:
            logger.error(f"[Gupshup] Error fetching template: {e}")
            return None

    def get_bsp_id_from_template_data(self, template_data: Dict[str, Any]) -> Optional[str]:
        """
        Extract Gupshup template ID from template data.

        Gupshup uses 'id' or 'templateId' for their internal ID.

        Args:
            template_data: Template data from Gupshup API

        Returns:
            str or None: Gupshup template ID
        """
        return template_data.get("id") or template_data.get("templateId")

    def get_all_templates(self, status: str = None) -> list:
        """
        Get all templates from Gupshup.

        Args:
            status: Optional filter by status (APPROVED, PENDING, REJECTED)

        Returns:
            list: List of template dictionaries
        """
        api = self._get_template_api()

        try:
            result = api.get_all_templates(status=status)
            return result.get("templates", result.get("data", []))
        except Exception as e:
            logger.error(f"[Gupshup] Error fetching all templates: {e}")
            return []

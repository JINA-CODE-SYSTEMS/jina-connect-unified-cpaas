"""
Base Template Sync Service

This module provides the abstract base class for template sync services.
All BSP-specific implementations should inherit from BaseSyncService.
"""

import logging
import time
from abc import ABC, abstractmethod
from enum import Enum
from typing import Any, Dict, Optional, Type

logger = logging.getLogger(__name__)


class BSPType(str, Enum):
    """
    Supported BSP types for template sync services.

    Values must match tenants.models.BSPChoices for consistency.
    """

    GUPSHUP = "GUPSHUP"
    WATI = "WATI"
    AISENSY = "AISENSY"
    INTERAKT = "INTERAKT"
    YELLOW_AI = "YELLOW_AI"
    META = "META"  # Direct META integration, no BSP
    MESSAGEBIRD = "MESSAGEBIRD"  # Not implemented yet


class SyncError(Exception):
    """
    Custom exception for template sync errors.

    Attributes:
        message: Human-readable error message
        code: Machine-readable error code
        bsp_type: BSP type where error occurred
    """

    def __init__(self, message: str, code: str = "sync_error", bsp_type: Optional[str] = None):
        super().__init__(message)
        self.message = message
        self.code = code
        self.bsp_type = bsp_type

    def __str__(self):
        prefix = f"[{self.bsp_type}] " if self.bsp_type else ""
        return f"{prefix}{self.message} (code: {self.code})"


class BaseSyncService(ABC):
    """
    Abstract base class for BSP template sync services.

    All BSP-specific implementations should inherit from this class
    and implement the required abstract methods.

    Flow:
    1. Template created via META Direct API → meta_template_id stored
    2. Call sync_templates() to trigger BSP synchronization
    3. Call get_template_by_element_name() to fetch template from BSP
    4. Extract BSP ID using get_bsp_id_from_template_data()
    5. Store BSP ID in bsp_id field

    Example:
        class MySyncService(BaseSyncService):
            bsp_type = BSPType.MY_BSP

            def _validate_configuration(self) -> None:
                if not self.bsp_app.api_key:
                    raise SyncError("API key required", code="missing_api_key")

            @property
            def headers(self) -> Dict[str, str]:
                return {"Authorization": f"Bearer {self.bsp_app.api_key}"}

            def sync_templates(self) -> Dict[str, Any]:
                # Call BSP sync API
                ...

            def get_template_by_element_name(self, element_name: str):
                # Fetch template from BSP
                ...

            def get_bsp_id_from_template_data(self, template_data):
                return template_data.get("id")
    """

    # Must be set by subclass
    bsp_type: BSPType = None

    def __init__(self, bsp_app):
        """
        Initialize with a BSP app instance.

        Args:
            bsp_app: The tenant's BSP app model instance (e.g., TenantGupshupApp)
        """
        self.bsp_app = bsp_app
        self._validate_configuration()

    @abstractmethod
    def _validate_configuration(self) -> None:
        """
        Validate that the BSP app is properly configured.

        Should check for required credentials and raise SyncError if missing.

        Raises:
            SyncError: If configuration is invalid
        """
        pass

    @property
    @abstractmethod
    def headers(self) -> Dict[str, str]:
        """
        Get authorization headers for BSP API.

        Returns:
            dict: Headers to include in API requests
        """
        pass

    @abstractmethod
    def sync_templates(self) -> Dict[str, Any]:
        """
        Trigger template sync on BSP.

        This tells the BSP to fetch the latest templates from META's database.

        Returns:
            dict: Sync response from BSP

        Raises:
            SyncError: If sync fails
        """
        pass

    @abstractmethod
    def get_template_by_element_name(self, element_name: str) -> Optional[Dict[str, Any]]:
        """
        Get template details from BSP by element name.

        Args:
            element_name: The template's element name (unique identifier)

        Returns:
            dict or None: Template data if found, None otherwise
        """
        pass

    @abstractmethod
    def get_bsp_id_from_template_data(self, template_data: Dict[str, Any]) -> Optional[str]:
        """
        Extract BSP's internal template ID from template data.

        Different BSPs use different field names for their internal ID.

        Args:
            template_data: Template data returned by BSP API

        Returns:
            str or None: BSP template ID
        """
        pass

    def get_bsp_id_for_template(
        self, element_name: str, max_retries: int = 3, retry_delay: float = 2.0
    ) -> Optional[str]:
        """
        Get BSP's internal template ID for a template by element name.

        Retries the lookup since BSP may take time to sync after being triggered.

        Args:
            element_name: The template's element name
            max_retries: Maximum number of retry attempts
            retry_delay: Delay between retries in seconds

        Returns:
            str or None: BSP template ID if found
        """
        for attempt in range(max_retries):
            template_data = self.get_template_by_element_name(element_name)

            if template_data:
                bsp_id = self.get_bsp_id_from_template_data(template_data)
                if bsp_id:
                    logger.info(f"[{self.bsp_type.value}] Found BSP ID '{bsp_id}' for template '{element_name}'")
                    return str(bsp_id)

            if attempt < max_retries - 1:
                logger.info(
                    f"[{self.bsp_type.value}] Template not found yet, "
                    f"retrying in {retry_delay}s... (attempt {attempt + 1}/{max_retries})"
                )
                time.sleep(retry_delay)

        logger.warning(
            f"[{self.bsp_type.value}] Could not find BSP ID for template '{element_name}' after {max_retries} attempts"
        )
        return None

    def sync_and_get_bsp_id(self, element_name: str, sync_delay: float = 2.0) -> Optional[str]:
        """
        Trigger sync and then get the BSP ID for a template.

        This is the main method to call after creating a template via META Direct API.

        Args:
            element_name: The template's element name
            sync_delay: Delay after triggering sync before fetching template

        Returns:
            str or None: BSP ID if sync and lookup successful
        """
        try:
            # Step 1: Trigger sync
            logger.info(f"[{self.bsp_type.value}] Triggering template sync...")
            self.sync_templates()

            # Step 2: Wait for sync to complete
            time.sleep(sync_delay)

            # Step 3: Get the BSP ID
            return self.get_bsp_id_for_template(element_name)

        except SyncError as e:
            logger.error(f"[{self.bsp_type.value}] Sync failed: {e}")
            return None
        except Exception as e:
            logger.exception(f"[{self.bsp_type.value}] Unexpected error during sync: {e}")
            return None


# =============================================================================
# Registry and Factory
# =============================================================================

# Registry of sync service implementations
# Populated by importing BSP-specific modules
SYNC_SERVICE_REGISTRY: Dict[BSPType, Type[BaseSyncService]] = {}


def register_sync_service(bsp_type: BSPType):
    """
    Decorator to register a sync service implementation.

    Usage:
        @register_sync_service(BSPType.GUPSHUP)
        class GupshupSyncService(BaseSyncService):
            ...
    """

    def decorator(cls: Type[BaseSyncService]):
        SYNC_SERVICE_REGISTRY[bsp_type] = cls
        return cls

    return decorator


def get_bsp_type_from_app(bsp_app) -> Optional[BSPType]:
    """
    Determine BSP type from app instance.

    Uses TenantWAApp.bsp field which stores BSPChoices values.
    BSPType enum values match BSPChoices for direct conversion.

    Args:
        bsp_app: BSP app model instance (TenantWAApp)

    Returns:
        BSPType or None
    """
    # Check for explicit bsp field (TenantWAApp.bsp)
    if hasattr(bsp_app, "bsp") and bsp_app.bsp:
        bsp_value = bsp_app.bsp.upper()
        try:
            # Direct conversion - BSPType values match BSPChoices
            return BSPType(bsp_value)
        except ValueError:
            logger.warning(f"Unknown BSP type: {bsp_value}")
            return None

    # Legacy fallback: Infer from model class name
    model_name = bsp_app.__class__.__name__.lower()

    bsp_keywords = {
        "gupshup": BSPType.GUPSHUP,
        "wati": BSPType.WATI,
        "aisensy": BSPType.AISENSY,
        "interakt": BSPType.INTERAKT,
        "yellow": BSPType.YELLOW_AI,
    }

    for keyword, bsp_type in bsp_keywords.items():
        if keyword in model_name:
            return bsp_type

    return None


def get_sync_service(bsp_app) -> Optional[BaseSyncService]:
    """
    Factory function to get the appropriate sync service for a BSP app.

    Automatically detects BSP type and returns the correct implementation.

    Args:
        bsp_app: BSP app model instance (e.g., TenantGupshupApp)

    Returns:
        Sync service instance or None if not supported/configured

    Example:
        from wa.services.sync_templates import get_sync_service

        sync_service = get_sync_service(tenant_gupshup_app)
        if sync_service:
            bsp_id = sync_service.sync_and_get_bsp_id("my_template_name")
    """
    try:
        bsp_type = get_bsp_type_from_app(bsp_app)

        if not bsp_type:
            logger.warning(f"Could not determine BSP type for app: {bsp_app}")
            return None

        if bsp_type == BSPType.META:
            # No sync needed for direct META integration
            logger.info("META Direct integration - no BSP sync required")
            return None

        if bsp_type == BSPType.MESSAGEBIRD:
            # MessageBird not implemented yet
            logger.info("MessageBird sync not implemented yet")
            return None

        # Import implementations to populate registry
        # This is done lazily to avoid circular imports
        _import_sync_implementations()

        service_class = SYNC_SERVICE_REGISTRY.get(bsp_type)

        if not service_class:
            logger.warning(f"No sync service registered for BSP type: {bsp_type}")
            return None

        return service_class(bsp_app)

    except SyncError as e:
        logger.warning(f"Sync service not available: {e}")
        return None
    except Exception as e:
        logger.exception(f"Error creating sync service: {e}")
        return None


def _import_sync_implementations():
    """Import all sync service implementations to populate the registry."""
    # Import implementations - they will self-register via decorator
    try:
        from wa.services.sync_templates import gupshup  # noqa: F401
    except ImportError:
        pass

    # Add more imports as implementations are added:
    # from wa.services.sync_templates import wati  # noqa: F401
    # from wa.services.sync_templates import aisensy  # noqa: F401

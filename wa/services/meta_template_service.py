"""
META Direct API Template Service

This service handles template creation via META's WhatsApp Business API directly,
bypassing BSP (Gupshup) for template management operations.

Usage:
    Template Creation: META Direct API
    Message Sending: BSP (Gupshup) - unchanged
"""

import json
import logging
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple, Type

logger = logging.getLogger(__name__)


class TemplateCategory(str, Enum):
    """Template categories supported by META API"""

    MARKETING = "MARKETING"
    UTILITY = "UTILITY"
    AUTHENTICATION = "AUTHENTICATION"


class TemplateType(str, Enum):
    """Template types for determining the appropriate validator"""

    MARKETING = "marketing"
    UTILITY = "utility"
    CATALOG = "catalog"
    COUPON_CODE = "coupon_code"
    LTO = "lto"
    CAROUSEL = "carousel"
    MPM = "mpm"
    SPM = "spm"
    PRODUCT_CARD_CAROUSEL = "product_card_carousel"
    CHECKOUT = "checkout"
    ORDER_STATUS = "order_status"


class MetaTemplateServiceError(Exception):
    """Custom exception for META template service errors"""

    def __init__(self, message: str, errors: Optional[List[Dict]] = None, code: str = "unknown"):
        super().__init__(message)
        self.errors = errors or []
        self.code = code


def get_validator_class(template_type: str) -> Type:
    """
    Get the appropriate META Direct API validator class for a template type.

    Args:
        template_type: One of TemplateType values

    Returns:
        Pydantic validator class
    """
    from wa.utility.validators.meta_direct.create.carousel_template_request import CarouselTemplateRequestValidator
    from wa.utility.validators.meta_direct.create.catalog_template_request import CatalogTemplateRequestValidator
    from wa.utility.validators.meta_direct.create.checkout_template_request import CheckoutTemplateRequestValidator
    from wa.utility.validators.meta_direct.create.coupon_code_template_request import CouponCodeTemplateRequestValidator
    from wa.utility.validators.meta_direct.create.lto_template_request import LTOTemplateRequestValidator
    from wa.utility.validators.meta_direct.create.marketing_template_request import MarketingTemplateRequestValidator
    from wa.utility.validators.meta_direct.create.mpm_template_request import MPMTemplateRequestValidator
    from wa.utility.validators.meta_direct.create.order_status_template_request import (
        OrderStatusTemplateRequestValidator,
    )
    from wa.utility.validators.meta_direct.create.product_card_carousel_template_request import (
        ProductCardCarouselTemplateRequestValidator,
    )
    from wa.utility.validators.meta_direct.create.spm_template_request import SPMTemplateRequestValidator
    from wa.utility.validators.meta_direct.create.utility_template_request import UtilityTemplateRequestValidator

    validator_map = {
        TemplateType.MARKETING.value: MarketingTemplateRequestValidator,
        TemplateType.UTILITY.value: UtilityTemplateRequestValidator,
        TemplateType.CATALOG.value: CatalogTemplateRequestValidator,
        TemplateType.COUPON_CODE.value: CouponCodeTemplateRequestValidator,
        TemplateType.LTO.value: LTOTemplateRequestValidator,
        TemplateType.CAROUSEL.value: CarouselTemplateRequestValidator,
        TemplateType.MPM.value: MPMTemplateRequestValidator,
        TemplateType.SPM.value: SPMTemplateRequestValidator,
        TemplateType.PRODUCT_CARD_CAROUSEL.value: ProductCardCarouselTemplateRequestValidator,
        TemplateType.CHECKOUT.value: CheckoutTemplateRequestValidator,
        TemplateType.ORDER_STATUS.value: OrderStatusTemplateRequestValidator,
    }

    return validator_map.get(template_type, MarketingTemplateRequestValidator)


def get_waba_id(wa_app) -> Optional[str]:
    """
    Get WABA ID from TenantWAApp's related WABAInfo.

    Args:
        wa_app: TenantWAApp instance

    Returns:
        str or None: WABA ID if available
    """
    try:
        if hasattr(wa_app, "waba_info") and wa_app.waba_info:
            return wa_app.waba_info.waba_id
    except Exception:
        pass
    return None


def get_meta_access_token(wa_app) -> Optional[str]:
    """
    Get META access token for API calls.

    Priority:
    1. bsp_access_token on TenantWAApp (per-app, encrypted at rest)
    2. META_PERM_TOKEN from settings (for direct META API access)
    3. Fall back to app_secret (legacy - may not work with META API)

    Args:
        wa_app: TenantWAApp instance

    Returns:
        str or None: Access token for META API
    """
    from django.conf import settings

    # Priority 1: Per-app META access token. This tested ``meta_access_token``,
    # a field no model has ever declared, so the branch was dead and template
    # sync silently ran on the global token — or on app_secret — even for a
    # tenant that had configured its own. #289 gave the token a real home.
    if wa_app.bsp_access_token:
        return wa_app.bsp_access_token

    # Priority 2: Global META permanent token from settings
    meta_perm_token = getattr(settings, "META_PERM_TOKEN", None)
    if meta_perm_token:
        return meta_perm_token

    # Priority 3: Fallback to app_secret (may not work for META Direct API)
    logger.warning("Using app_secret as META token - this may not work for META Direct API")
    return wa_app.app_secret


class MetaTemplateService:
    """
    Service for managing WhatsApp templates via META Direct API.

    This service:
    - Validates template data using Pydantic validators
    - Submits templates to META's Graph API
    - Stores templates in WATemplate model (for compatibility)
    - Returns proper error messages for validation failures
    """

    def __init__(self, wa_app):
        """
        Initialize the service with a TenantWAApp.

        Args:
            wa_app: TenantWAApp instance
        """
        self.wa_app = wa_app
        self.waba_id = get_waba_id(wa_app)
        self.access_token = get_meta_access_token(wa_app)

        if not self.waba_id:
            raise MetaTemplateServiceError(
                "WABA ID not configured. Please set up WABAInfo for this app.", code="waba_not_configured"
            )

    def _get_meta_api(self):
        """Get configured META Template API instance"""
        from wa.utility.apis.meta.template_api import TemplateAPI

        api = TemplateAPI(token=self.access_token)
        api.waba_id = self.waba_id
        return api

    def validate_template(
        self, template_type: str, data: Dict[str, Any]
    ) -> Tuple[Dict[str, Any], Optional[List[Dict]]]:
        """
        Validate template data using the appropriate Pydantic validator.

        Args:
            template_type: Type of template (marketing, utility, etc.)
            data: Template data to validate

        Returns:
            Tuple of (validated_payload, errors)
        """
        from pydantic import ValidationError

        try:
            validator_class = get_validator_class(template_type)
            validated = validator_class(**data)

            # Get META API payload
            if hasattr(validated, "to_meta_payload"):
                return validated.to_meta_payload(), None
            else:
                return validated.model_dump(exclude_none=True), None

        except ValidationError as e:
            errors = [
                {
                    "field": ".".join(str(loc) for loc in err.get("loc", [])),
                    "message": err.get("msg", ""),
                    "type": err.get("type", ""),
                }
                for err in e.errors()
            ]
            return None, errors
        except Exception as e:
            logger.exception(f"Validation error: {e}")
            return None, [{"field": "general", "message": str(e), "type": "validation_error"}]

    def create_template(self, template_type: str, data: Dict[str, Any], save_to_db: bool = True) -> Dict[str, Any]:
        """
        Create a template via META Direct API.

        Args:
            template_type: Type of template (marketing, utility, etc.)
            data: Template data including name, language, category, components
            save_to_db: Whether to save the template to WATemplate model

        Returns:
            Dict with template_id, status, and other metadata

        Raises:
            MetaTemplateServiceError: If validation or API call fails
        """
        # Validate the template
        payload, errors = self.validate_template(template_type, data)

        if errors:
            raise MetaTemplateServiceError("Template validation failed", errors=errors, code="validation_failed")

        # Submit to META API
        api = self._get_meta_api()

        logger.info(f"Submitting template '{payload.get('name')}' to META Direct API")
        logger.debug(f"Payload: {json.dumps(payload, indent=2)}")

        response = api.apply_for_template(payload)

        # Process response
        if response.get("id"):
            # Success
            result = {
                "template_id": response.get("id"),
                "status": response.get("status", "PENDING"),
                "name": payload.get("name"),
                "category": payload.get("category"),
                "language": payload.get("language"),
                "meta_response": response,
                "bsp_id": None,  # Will be populated after BSP sync
            }

            # Optionally save to WATemplate model for compatibility
            if save_to_db:
                template = self._save_to_model(data, payload, result)
                result["db_id"] = template.id
                # ``bsp_id`` is not an attribute of WATemplate — the column
                # is ``bsp_template_id``, and the old name raised
                # AttributeError here (#337).
                result["bsp_id"] = template.bsp_template_id

            return result

        elif response.get("error"):
            # META API error
            error_data = response.get("error", {})
            error_message = error_data.get("message", "Unknown META API error")
            error_code = error_data.get("code", "unknown")

            raise MetaTemplateServiceError(
                f"[{error_code}] {error_message}",
                errors=[{"field": "api", "message": error_message, "type": f"meta_api_error_{error_code}"}],
                code=str(error_code),
            )
        else:
            raise MetaTemplateServiceError(
                f"Unexpected response from META API: {json.dumps(response)}", code="unexpected_response"
            )

    def _save_to_model(self, original_data: Dict[str, Any], meta_payload: Dict[str, Any], result: Dict[str, Any]):
        """
        Save template to WATemplate model for compatibility.

        This allows the template to be used with existing message sending flows.

        ``MetaTemplateService`` is instantiated nowhere in the repository — the
        live template-create path is ``WATemplateV2ViewSet.create`` ->
        ``MetaDirectAdapter.submit_template`` — so this method was never
        reached, which is the only reason four separate fatal name errors could
        sit here unnoticed (#337):

        * ``submission_debug_info=`` — not a field on ``WATemplate`` or its
          base, and in no migration. ``TypeError``.
        * ``template_id=`` — a read-only ``@property``, not a legacy column.
          ``TypeError``.
        * ``save(skip_legacy_validation=True)`` — ``Model.save()`` takes no such
          flag. ``TypeError``.
        * ``template.bsp_id`` in the caller — the column is
          ``bsp_template_id``. ``AttributeError``.

        The first of those raised *after* ``create_template`` had submitted the
        template to Meta and Meta had accepted it, so the damage would have been
        an accepted template with no local row. Do not reintroduce
        ``submission_debug_info`` — or the ``debug_info`` blob that fed it —
        without adding the field and a migration.
        """
        from wa.models import StatusChoices, WATemplate

        # Map META status to WATemplate status
        status_mapping = {
            "APPROVED": StatusChoices.APPROVED,
            "PENDING": StatusChoices.PENDING,
            "IN_APPEAL": StatusChoices.PENDING,
            "REJECTED": StatusChoices.FAILED,
        }

        template_status = status_mapping.get(result.get("status", "PENDING").upper(), StatusChoices.PENDING)

        # Extract components from original data
        components = original_data.get("components", [])

        # Find body, header, footer from components
        body_text = ""
        header_text = ""
        footer_text = ""
        buttons = []

        for comp in components:
            comp_type = comp.get("type", "").upper()
            if comp_type == "BODY":
                body_text = comp.get("text", "")
            elif comp_type == "HEADER":
                if comp.get("format", "").upper() == "TEXT":
                    header_text = comp.get("text", "")
            elif comp_type == "FOOTER":
                footer_text = comp.get("text", "")
            elif comp_type == "BUTTONS":
                buttons = comp.get("buttons", [])

        # Determine template_type from original data or category
        template_type = original_data.get("template_type", "TEXT")

        # Get META template ID from result
        meta_template_id = result.get("template_id")

        template = WATemplate(
            wa_app=self.wa_app,
            name=meta_payload.get("name", ""),
            element_name=meta_payload.get("name", ""),
            language_code=meta_payload.get("language", "en"),
            category=meta_payload.get("category", "MARKETING"),
            template_type=template_type,
            status=template_status,
            # ``template_id`` is not a legacy column to mirror into — it is a
            # read-only property returning ``bsp_template_id or
            # meta_template_id``, so passing it here raised TypeError, and the
            # line below already gives the property its value (#337).
            meta_template_id=meta_template_id,  # Store META template ID
            content=body_text,
            header=header_text,
            footer=footer_text,
            buttons=buttons if buttons else None,
            vertical=original_data.get("vertical", "GENERAL"),
        )
        # Plain ``save()``: the former ``skip_legacy_validation=True`` was
        # forwarded to ``Model.save()``, which rejects it, and there are no
        # legacy Gupshup validators left in ``save()`` for it to bypass (#337).
        template.save()

        # Sync with BSP to get BSP ID (async-friendly, non-blocking on failure)
        self._sync_with_bsp(template)

        return template

    def _sync_with_bsp(self, template) -> None:
        """
        Trigger async BSP sync via Celery task.

        This is done after creating a template via META Direct API.
        The BSP needs to sync their database with META's template database.

        Note: Template status updates come from:
        - BSP webhooks (Gupshup, WATI, etc.)
        - META Direct webhooks (message_template_status_update)
        - Manual check via GET /api/wa/templates/{id}/check-status/

        Args:
            template: WATemplate instance
        """
        from tenants.models import BSPChoices

        # Skip BSP sync if tenant is using META Direct (no BSP)
        from wa.adapters import resolve_bsp
        from wa.tasks import sync_template_with_bsp_task

        if resolve_bsp(self.wa_app) == BSPChoices.META:
            logger.info(f"Skipping BSP sync for template '{template.element_name}' - tenant uses META Direct (no BSP)")
            return

        # Queue BSP sync task (e.g., Gupshup template sync)
        try:
            sync_template_with_bsp_task.delay(template_id=template.pk, wa_id=self.wa_app.pk)
            logger.info(f"Queued BSP sync task for template '{template.element_name}'")
        except Exception as e:
            # Don't fail template creation if task queueing fails
            logger.error(f"Failed to queue BSP sync task for template '{template.element_name}': {e}")

    def get_template_status(self, template_id: str) -> Dict[str, Any]:
        """
        Get template status from META API.

        Args:
            template_id: META template ID

        Returns:
            Dict with status information
        """
        api = self._get_meta_api()
        response = api.get_template_status(template_id)
        return response

    def delete_template(self, template_name: str) -> Dict[str, Any]:
        """
        Delete a template via META API.

        Args:
            template_name: Name of the template to delete

        Returns:
            Dict with deletion result
        """
        api = self._get_meta_api()

        # META uses DELETE /{waba_id}/message_templates?name={template_name}
        url = f"{api.BASE_URL}{self.waba_id}/message_templates"
        request_data = {"method": "DELETE", "url": url, "headers": api.headers, "params": {"name": template_name}}

        response = api.make_request(request_data)
        return response

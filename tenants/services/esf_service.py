"""
ESF (Embedded Signup Flow) Service for generating WhatsApp onboarding URLs.

This service handles:
1. Getting partner token from Gupshup
2. Creating new Gupshup app for tenant
3. Generating ESF URL for tenant onboarding
4. Storing and managing ESF URLs with expiration
"""

import logging
from datetime import timedelta
from typing import Optional

from django.utils import timezone

from tenants.models import Tenant, TenantWAApp
from wa.utility.apis.gupshup.waba import WABAAPI
from wa.utility.data_model.gupshup.partner_token import PartnerToken

logger = logging.getLogger(__name__)


class ESFServiceError(Exception):
    """Custom exception for ESF service errors."""

    pass


class ESFService:
    """
    Service for managing Embedded Signup Flow (ESF) URLs for WhatsApp Business onboarding.

    The ESF URL allows tenants to complete their WhatsApp Business Account setup
    through an embedded signup experience. The URL is valid for 4 days.
    """

    ESF_URL_VALIDITY_DAYS = 4

    def __init__(self, wa_app: TenantWAApp):
        """
        Initialize the ESF service with a TenantWAApp instance.

        Args:
            wa_app: The TenantWAApp instance to generate ESF URL for.
        """
        self.wa_app = wa_app

    def _get_partner_token(self) -> str:
        """
        Get partner token from Gupshup using platform credentials.

        Returns:
            str: The partner token for API authentication.

        Raises:
            ESFServiceError: If token generation fails.
        """
        try:
            # Initialize WABA API with empty token (for partner login)
            waba_api = WABAAPI(
                appId=self.wa_app.app_id,
                token="",  # No token needed for partner login
            )

            # Get credentials from settings
            partner_credentials = PartnerToken()

            if not partner_credentials.email or not partner_credentials.password:
                raise ESFServiceError(
                    "Gupshup partner credentials not configured. Please set GUPSHUP_EMAIL and GUPSHUP_PASSWORD in settings."
                )

            # Make the partner token request
            response = waba_api.get_partner_token(
                {"email": partner_credentials.email, "password": partner_credentials.password}
            )

            if not response:
                raise ESFServiceError("Empty response from partner token API")

            # Check for success response
            if response.get("status") == "error":
                error_msg = response.get("message", "Unknown error")
                raise ESFServiceError(f"Failed to get partner token: {error_msg}")

            token = response.get("token")
            if not token:
                raise ESFServiceError("No token in partner token response")

            logger.info(f"Successfully obtained partner token for app {self.wa_app.app_id}")
            return token

        except ESFServiceError:
            raise
        except Exception as e:
            logger.exception(f"Error getting partner token: {e}")
            raise ESFServiceError(f"Failed to get partner token: {str(e)}")

    def sync_waba_info(self) -> dict:
        """
        Sync WABA info from Gupshup API and update the WABAInfo model.

        Returns:
            dict: Contains sync status and WABA info.

        Raises:
            ESFServiceError: If sync fails.
        """
        from tenants.models import WABAInfo

        try:
            # Initialize WABA API with app secret
            waba_api = WABAAPI(appId=self.wa_app.app_id, token=self.wa_app.app_secret)

            # Fetch WABA details
            response = waba_api.get_waba_details()

            if not response:
                raise ESFServiceError("Empty response from WABA info API")

            # Check for error in response
            if response.get("status") == "error":
                error_msg = response.get("message", "Unknown error")
                raise ESFServiceError(f"Failed to get WABA info: {error_msg}")

            # Update WABAInfo model
            waba_info, error = WABAInfo.update_from_api_response(self.wa_app, response)

            if error:
                logger.warning(f"WABA info sync had errors: {error}")

            is_active = self.wa_app.is_waba_active

            logger.info(f"Synced WABA info for app {self.wa_app.app_id}, active={is_active}")

            return {
                "synced": True,
                "is_waba_active": is_active,
                "account_status": waba_info.account_status if waba_info else None,
                "phone": waba_info.phone if waba_info else None,
                "verified_name": waba_info.verified_name if waba_info else None,
                "message": "WABA info synced successfully",
            }

        except ESFServiceError:
            raise
        except Exception as e:
            logger.exception(f"Error syncing WABA info: {e}")
            raise ESFServiceError(f"Failed to sync WABA info: {str(e)}")

    def _get_tenant_user_firstname(self) -> str:
        """
        Get the first name of a user associated with this app's tenant.

        Tries in order:
        1. First tenant user's first name
        2. Tenant's created_by user's first name

        Returns:
            str: The first name of the user, or empty string if not found.
        """
        try:
            # Try 1: Get from tenant_users
            tenant_user = self.wa_app.tenant.tenant_users.select_related("user").first()
            if tenant_user and tenant_user.user and tenant_user.user.first_name:
                logger.debug(f"Got first name from tenant_user: {tenant_user.user.first_name}")
                return tenant_user.user.first_name

            # Try 2: Get from tenant's created_by
            if self.wa_app.tenant.created_by and self.wa_app.tenant.created_by.first_name:
                logger.debug(f"Got first name from tenant.created_by: {self.wa_app.tenant.created_by.first_name}")
                return self.wa_app.tenant.created_by.first_name

            logger.warning(f"No user first_name found for tenant {self.wa_app.tenant_id}")
        except Exception as e:
            logger.warning(f"Could not get tenant user first name: {e}")
        return ""

    def generate_esf_url(
        self, regenerate: bool = False, user: Optional[str] = None, lang: Optional[str] = None, force: bool = False
    ) -> dict:
        """
        Generate ESF URL for WhatsApp Business onboarding.

        Args:
            regenerate: If True, forces generation of a new URL even if one exists.
            user: Optional user identifier to include in the ESF link.
                  If not provided, uses the first tenant user's first name.
            lang: Optional language code for the ESF experience (e.g., 'en', 'es').
            force: If True, bypasses the is_waba_active check (use with caution).

        Returns:
            dict: Contains 'esf_url', 'expires_at', and 'is_new' flag.

        Raises:
            ESFServiceError: If ESF URL generation fails or WABA is already active.
        """
        # Check if WABA is already active - prevent accidental ESF regeneration
        if self.wa_app.is_waba_active and not force:
            raise ESFServiceError(
                f"WABA is already active for app {self.wa_app.app_id}. "
                "ESF URL generation is not allowed for active WABA accounts. "
                "Use force=True to override this check (not recommended)."
            )

        # Check if we have a valid existing URL and regenerate is not requested
        if not regenerate and self.wa_app.is_esf_url_valid:
            return {
                "esf_url": self.wa_app.esf_url,
                "expires_at": self.wa_app.esf_url_expires_at,
                "is_new": False,
                "message": "Using existing valid ESF URL",
            }

        # If user not provided, get from tenant user's first name
        if not user:
            user = self._get_tenant_user_firstname()

        try:
            # Step 1: Get partner token
            partner_token = self._get_partner_token()

            # Step 2: Initialize WABA API with the partner token
            waba_api = WABAAPI(
                appId=self.wa_app.app_id, token=partner_token, regenerate=regenerate, user=user or "", lang=lang or ""
            )

            # Step 3: Generate ESF link
            response = waba_api.generate_esf_link({})

            if not response:
                raise ESFServiceError("Empty response from ESF link API")

            # Check for error in response
            if response.get("status") == "error":
                error_msg = response.get("message", "Unknown error")
                raise ESFServiceError(f"Failed to generate ESF link: {error_msg}")

            # Extract the ESF URL from response
            esf_url = response.get("link") or response.get("url") or response.get("esf_link")

            if not esf_url:
                # If the response itself is the URL (some APIs return it directly)
                if isinstance(response, str) and response.startswith("http"):
                    esf_url = response
                else:
                    logger.error(f"ESF link response: {response}")
                    raise ESFServiceError("No ESF URL in response")

            # Step 4: Calculate expiration (4 days from now)
            expires_at = timezone.now() + timedelta(days=self.ESF_URL_VALIDITY_DAYS)

            # Step 5: Save to the model
            self.wa_app.esf_url = esf_url
            self.wa_app.esf_url_expires_at = expires_at
            self.wa_app.save(update_fields=["esf_url", "esf_url_expires_at"])

            logger.info(f"Successfully generated ESF URL for app {self.wa_app.app_id}, expires at {expires_at}")

            return {
                "esf_url": esf_url,
                "expires_at": expires_at,
                "is_new": True,
                "message": "New ESF URL generated successfully",
            }

        except ESFServiceError:
            raise
        except Exception as e:
            logger.exception(f"Error generating ESF URL: {e}")
            raise ESFServiceError(f"Failed to generate ESF URL: {str(e)}")

    def get_esf_url_status(self) -> dict:
        """
        Get the current status of the ESF URL.

        Returns:
            dict: Contains current ESF URL info and validity status.
        """
        is_valid = self.wa_app.is_esf_url_valid

        # Get WABA info if available
        waba_account_status = None
        try:
            if hasattr(self.wa_app, "waba_info") and self.wa_app.waba_info:
                waba_account_status = self.wa_app.waba_info.account_status
        except:
            pass

        return {
            "has_esf_url": bool(self.wa_app.esf_url),
            "esf_url": self.wa_app.esf_url if is_valid else None,
            "expires_at": self.wa_app.esf_url_expires_at,
            "is_valid": is_valid,
            "is_waba_active": self.wa_app.is_waba_active,
            "waba_account_status": waba_account_status,
            "app_id": self.wa_app.app_id,
            "app_name": self.wa_app.app_name,
        }

    @classmethod
    def generate_for_tenant(
        cls,
        tenant_id: int,
        app_id: Optional[str] = None,
        regenerate: bool = False,
        user: Optional[str] = None,
        lang: Optional[str] = None,
    ) -> dict:
        """
        Class method to generate ESF URL for a tenant.

        Args:
            tenant_id: The ID of the tenant.
            app_id: Optional specific app_id. If not provided, uses the first app.
            regenerate: If True, forces generation of a new URL.
            user: Optional user identifier for the ESF link.
            lang: Optional language code.

        Returns:
            dict: Contains 'esf_url', 'expires_at', and status info.

        Raises:
            ESFServiceError: If tenant or app not found, or generation fails.
        """
        try:
            if app_id:
                wa_app = TenantWAApp.objects.get(tenant_id=tenant_id, app_id=app_id)
            else:
                wa_app = TenantWAApp.objects.filter(tenant_id=tenant_id).first()

            if not wa_app:
                raise ESFServiceError(f"No WA app found for tenant {tenant_id}")

            service = cls(wa_app)
            return service.generate_esf_url(regenerate=regenerate, user=user, lang=lang)

        except TenantWAApp.DoesNotExist:
            raise ESFServiceError(f"WA app not found for tenant {tenant_id} with app_id {app_id}")

    @classmethod
    def get_partner_token_static(cls) -> str:
        """
        Static method to get partner token without requiring a wa_app instance.
        Used for creating new apps.

        Returns:
            str: The partner token for API authentication.

        Raises:
            ESFServiceError: If token generation fails.
        """
        try:
            # Initialize WABA API with placeholder values (for partner login)
            waba_api = WABAAPI(
                appId="placeholder",
                token="",  # No token needed for partner login
            )

            # Get credentials from settings
            partner_credentials = PartnerToken()

            if not partner_credentials.email or not partner_credentials.password:
                raise ESFServiceError(
                    "Gupshup partner credentials not configured. Please set GUPSHUP_EMAIL and GUPSHUP_PASSWORD in settings."
                )

            # Make the partner token request
            response = waba_api.get_partner_token(
                {"email": partner_credentials.email, "password": partner_credentials.password}
            )

            if not response:
                raise ESFServiceError("Empty response from partner token API")

            # Check for success response
            if response.get("status") == "error":
                error_msg = response.get("message", "Unknown error")
                raise ESFServiceError(f"Failed to get partner token: {error_msg}")

            token = response.get("token")
            if not token:
                raise ESFServiceError("No token in partner token response")

            logger.info("Successfully obtained partner token")
            return token

        except ESFServiceError:
            raise
        except Exception as e:
            logger.exception(f"Error getting partner token: {e}")
            raise ESFServiceError(f"Failed to get partner token: {str(e)}")

    @classmethod
    def _generate_app_name(cls) -> str:
        """
        Generate a unique single-word app name using coolname library.

        Returns:
            str: A single-word app name (e.g., 'happypanda', 'bluefox')
        """
        from coolname import generate_slug

        # Generate a 2-word slug and join without separator for single word
        slug = generate_slug(2)  # e.g., 'happy-panda'
        # Remove hyphens to make it a single word
        return slug.replace("-", "")

    @classmethod
    def create_app_for_tenant(
        cls,
        tenant_id: int,
        template_messaging: bool = True,
    ) -> dict:
        """
        Create a new Gupshup app for a tenant and generate ESF URL.

        This is the main entry point for tenant onboarding:
        1. Auto-generates a unique app name using coolname
        2. Gets partner token
        3. Creates new app on Gupshup
        4. Creates TenantGupshupApp record
        5. Generates ESF URL with lang=en_US

        Args:
            tenant_id: The ID of the tenant.
            template_messaging: Enable template messaging (default: True).

        Returns:
            dict: Contains app details and ESF URL info.

        Raises:
            ESFServiceError: If app creation fails.
        """
        try:
            # Verify tenant exists
            try:
                tenant = Tenant.objects.get(id=tenant_id)
            except Tenant.DoesNotExist:
                raise ESFServiceError(f"Tenant with ID {tenant_id} not found")

            # Auto-generate app name
            app_name = cls._generate_app_name()

            # Step 1: Get partner token
            partner_token = cls.get_partner_token_static()

            # Step 2: Create app on Gupshup
            waba_api = WABAAPI(
                appId="placeholder",  # Not needed for app creation
                token=partner_token,
            )

            # Prepare app creation data
            app_data = {"name": app_name, "templateMessaging": template_messaging, "disableOptinPrefUrl": False}

            response = waba_api.create_new_app(app_data)

            if not response:
                raise ESFServiceError("Empty response from create app API")

            # Check for error in response
            if response.get("status") == "error":
                error_msg = response.get("message", "Unknown error")
                raise ESFServiceError(f"Failed to create Gupshup app: {error_msg}")

            # Extract app details from response
            app_id = response.get("app", {}).get("id") or response.get("appId") or response.get("id")
            app_token = response.get("app", {}).get("token") or response.get("token") or response.get("app_token")

            if not app_id:
                logger.error(f"Create app response: {response}")
                raise ESFServiceError("No app_id in create app response")

            # Step 3: Create TenantWAApp record
            # Note: wa_number will be updated after ESF completion
            wa_app = TenantWAApp.objects.create(
                tenant=tenant,
                app_name=app_name,
                app_id=app_id,
                app_secret=app_token or "",  # Will be updated after ESF
                wa_number="+10000000000",  # Placeholder - updated after onboarding
            )

            logger.info(f"Created TenantWAApp for tenant {tenant_id}: app_id={app_id}")

            result = {
                "app_id": app_id,
                "app_name": app_name,
                "app_secret": app_token,
                "tenant_id": tenant_id,
                "wa_app_pk": wa_app.pk,
                "message": "WA app created successfully",
            }

            # Step 4: Generate ESF URL with lang=en_US
            try:
                service = cls(wa_app)
                esf_result = service.generate_esf_url(regenerate=False, user=None, lang="en_US")
                result["esf_url"] = esf_result.get("esf_url")
                result["esf_url_expires_at"] = esf_result.get("expires_at")
                result["message"] = "Gupshup app created and ESF URL generated successfully"
            except ESFServiceError as e:
                # App was created but ESF generation failed
                result["esf_error"] = str(e)
                result["message"] = "Gupshup app created but ESF URL generation failed"
                logger.warning(f"ESF URL generation failed for app {app_id}: {e}")

            return result

        except ESFServiceError:
            raise
        except Exception as e:
            logger.exception(f"Error creating Gupshup app: {e}")
            raise ESFServiceError(f"Failed to create Gupshup app: {str(e)}")

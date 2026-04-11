from django.http import HttpResponse
from drf_yasg import openapi
from drf_yasg.utils import swagger_auto_schema
from rest_framework import status
from rest_framework.decorators import action
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from termcolor import cprint

from abstract.viewsets.base import BaseTenantModelViewSet
from tenants.models import TenantWAApp
from tenants.serializers import TenantGupshupAppsSerializer
from tenants.services.esf_service import ESFService, ESFServiceError
from tenants.utility.guphup_webhook_handler import GupshupWebhookHandler


class TenantGupshupAppsViewSet(BaseTenantModelViewSet):
    """
    A viewset for managing Gupshup apps for a tenant.
    """

    queryset = TenantWAApp.objects.all()
    serializer_class = TenantGupshupAppsSerializer
    lookup_field = "app_id"
    lookup_url_kwarg = "app_id"
    required_permissions = {
        "list": "wa_app.view",
        "retrieve": "wa_app.view",
        "create": "wa_app.manage",
        "partial_update": "wa_app.manage",
        "create_app": "wa_app.manage",
        "token_info": "wa_app.view",
        "generate_esf_url": "wa_app.manage",
        "esf_url_status": "wa_app.view",
        "sync_waba_info": "wa_app.manage",
        "default": "wa_app.view",
    }

    def get_queryset(self):
        """
        Override queryset for webhook actions to bypass tenant filtering.
        Webhooks come from external sources (Gupshup) and need access to all apps.
        """
        if self.action in ["webhook_billing", "webhook_template", "webhook_messages", "webhook_misc"]:
            return TenantWAApp.objects.all()
        return super().get_queryset()

    @swagger_auto_schema(
        operation_description="""
Create a new Gupshup app for the tenant and generate ESF URL.

This is the entry point for WhatsApp onboarding:
1. Auto-generates a unique single-word app name using coolname
2. Creates a new app on Gupshup platform
3. Creates TenantGupshupApp record
4. Generates ESF (Embedded Signup Flow) URL with lang=en_US

The ESF URL is valid for 4 days and allows the tenant to complete
their WhatsApp Business Account setup.
        """,
        operation_summary="Create Gupshup App for WhatsApp Onboarding",
        operation_id="create_gupshup_app",
        tags=["Gupshup Apps"],
        request_body=None,
        responses={
            201: openapi.Response(
                description="Gupshup app created successfully",
                schema=openapi.Schema(
                    type=openapi.TYPE_OBJECT,
                    properties={
                        "app_id": openapi.Schema(type=openapi.TYPE_STRING, description="The created Gupshup app ID"),
                        "app_name": openapi.Schema(type=openapi.TYPE_STRING, description="The auto-generated app name"),
                        "app_secret": openapi.Schema(type=openapi.TYPE_STRING, description="The app secret/token"),
                        "tenant_id": openapi.Schema(type=openapi.TYPE_INTEGER, description="The tenant ID"),
                        "gupshup_app_pk": openapi.Schema(
                            type=openapi.TYPE_INTEGER, description="The TenantGupshupApp primary key"
                        ),
                        "esf_url": openapi.Schema(
                            type=openapi.TYPE_STRING, description="The ESF URL for WhatsApp onboarding"
                        ),
                        "esf_url_expires_at": openapi.Schema(
                            type=openapi.TYPE_STRING, format="date-time", description="ESF URL expiration timestamp"
                        ),
                        "message": openapi.Schema(type=openapi.TYPE_STRING, description="Status message"),
                    },
                ),
                examples={
                    "application/json": {
                        "app_id": "abc123xyz",
                        "app_name": "brilliant-phoenix",
                        "app_secret": "secret_token_here",
                        "tenant_id": 1,
                        "gupshup_app_pk": 5,
                        "esf_url": "https://hub.gupshup.io/esf/...",
                        "esf_url_expires_at": "2026-01-11T10:30:00Z",
                        "message": "App created successfully with ESF URL",
                    }
                },
            ),
            400: openapi.Response(
                description="Bad request - No tenant associated or creation failed",
                schema=openapi.Schema(
                    type=openapi.TYPE_OBJECT,
                    properties={
                        "error": openapi.Schema(type=openapi.TYPE_STRING, description="Error message"),
                    },
                ),
                examples={"application/json": {"error": "No tenant associated with this user"}},
            ),
        },
    )
    @action(detail=False, methods=["post"], url_path="create-app", url_name="create-app")
    def create_app(self, request, *args, **kwargs):
        """
        Create a new Gupshup app for the tenant and generate ESF URL.

        This is the entry point for WhatsApp onboarding:
        1. Auto-generates a unique single-word app name using coolname
        2. Creates a new app on Gupshup platform
        3. Creates TenantGupshupApp record
        4. Generates ESF (Embedded Signup Flow) URL with lang=en_US

        The ESF URL is valid for 4 days and allows the tenant to complete
        their WhatsApp Business Account setup.

        Request Body: None required (app name is auto-generated)

        Returns:
            - app_id: The created Gupshup app ID
            - app_name: The auto-generated app name
            - app_secret: The app secret/token
            - tenant_id: The tenant ID
            - gupshup_app_pk: The TenantGupshupApp primary key
            - esf_url: The ESF URL
            - esf_url_expires_at: ESF URL expiration
            - message: Status message
        """
        # Get tenant from the user's context
        tenant = getattr(request, "tenant", None)
        if not tenant:
            # Try to get tenant from user's tenant_users
            user = request.user
            if hasattr(user, "user_tenants") and user.user_tenants.exists():
                tenant = user.user_tenants.first().tenant

        if not tenant:
            return Response({"error": "No tenant associated with this user"}, status=status.HTTP_400_BAD_REQUEST)

        try:
            result = ESFService.create_app_for_tenant(tenant_id=tenant.id)
            return Response(result, status=status.HTTP_201_CREATED)
        except ESFServiceError as e:
            return Response({"error": str(e)}, status=status.HTTP_400_BAD_REQUEST)

    @action(
        detail=True,
        methods=["get", "post"],
        url_path="webhook-billing",
        url_name="webhook-billing",
        permission_classes=[AllowAny],
    )
    def webhook_billing(self, request, *args, **kwargs):
        """
        A custom action to handle webhook requests from Gupshup.
        GET: Used for activation check.
        POST: Used for webhook event handling.
        """
        wa_app = self.get_object()
        if request.method == "GET":
            # Activation check logic (customize as needed)
            return Response({"status": "webhook active", "app_id": getattr(wa_app, "app_id", None)})
        elif request.method == "POST":
            # Handle the webhook logic here
            cprint("Received webhook-billing POST request", "green")
            handler = GupshupWebhookHandler()
            handler.handle_webhook_billing(request.data, wa_app=wa_app)
            return Response({"status": "webhook received"})
        return Response({"error": "Method not allowed"}, status=405)

    @action(
        detail=True,
        methods=["get", "post"],
        url_path="webhook-template",
        url_name="webhook-template",
        permission_classes=[AllowAny],
    )
    def webhook_template(self, request, *args, **kwargs):
        """
        A custom action to handle webhook template requests from Gupshup.
        GET: Used for template activation check.
        POST: Used for webhook event handling.
        """
        wa_app = self.get_object()
        if request.method == "GET":
            # Template activation check logic (customize as needed)
            return Response({"status": "template webhook active", "app_id": getattr(wa_app, "app_id", None)})
        elif request.method == "POST":
            # Handle the webhook logic here
            cprint("Received webhook-template POST request", "green")
            handler = GupshupWebhookHandler()
            handler.handle_webhook_template(request.data, wa_app=wa_app)
            return Response({"status": "template webhook received"})
        return Response({"error": "Method not allowed"}, status=405)

    @action(
        detail=True,
        methods=["get", "post"],
        url_path="webhook-messages",
        url_name="webhook-messages",
        permission_classes=[AllowAny],
    )
    def webhook_messages(self, request, *args, **kwargs):
        """
        A custom action to handle webhook message requests from Gupshup.
        GET: Used for message activation check.
        POST: Used for webhook event handling.
        """
        wa_app = self.get_object()
        if request.method == "GET":
            # Message activation check logic (customize as needed)
            return Response({"status": "message webhook active", "app_id": getattr(wa_app, "app_id", None)})
        elif request.method == "POST":
            # Handle the webhook logic here
            cprint("Received webhook-messages POST request", "green")
            handler = GupshupWebhookHandler()
            handler.handle_webhook_message(request.data, wa_app=wa_app)
            return Response({"status": "message webhook received"})
        return Response({"error": "Method not allowed"}, status=405)

    @action(
        detail=True,
        methods=["get", "post"],
        url_path="webhook-misc",
        url_name="webhook-misc",
        permission_classes=[AllowAny],
    )
    def webhook_misc(self, request, *args, **kwargs):
        """
        A custom action to handle miscellaneous webhook requests from Gupshup.
        GET: Used for miscellaneous activation check.
        POST: Used for webhook event handling.
        """
        wa_app = self.get_object()
        if request.method == "GET":
            # Miscellaneous activation check logic (customize as needed)
            return Response({"status": "misc webhook active", "app_id": getattr(wa_app, "app_id", None)})
        elif request.method == "POST":
            # Handle the webhook logic here
            cprint("Received webhook-misc POST request", "green")
            handler = GupshupWebhookHandler()
            handler.handle_webhook_misc(request.data, wa_app=wa_app)
            return Response({"status": "misc webhook received"})
        return Response({"error": "Method not allowed"}, status=405)

    @action(detail=True, methods=["get"], url_path="token-info", url_name="token-info")
    def token_info(self, request, *args, **kwargs):
        """
        A custom action to retrieve token information for the WA app.
        GET: Used to fetch token details.
        """
        wa_app = self.get_object()
        token_info = {
            "app_secret": wa_app.app_secret,
        }
        return Response({"token_info": token_info})

    @action(detail=True, methods=["post"], url_path="generate-esf-url", url_name="generate-esf-url")
    def generate_esf_url(self, request, *args, **kwargs):
        """
        Generate an Embedded Signup Flow (ESF) URL for WhatsApp Business onboarding.

        The ESF URL allows the tenant to complete their WhatsApp Business Account setup.
        The URL is valid for 4 days. Use regenerate=true to force generation of a new URL.

        IMPORTANT: If WABA is already active, regeneration is blocked by default.
        Use force=true to override this protection (use with caution).

        Request Body (optional):
            - regenerate (bool): Force generation of a new URL even if one exists. Default: false
            - force (bool): Force regeneration even if WABA is active. Default: false
            - user (str): Optional user identifier to include in the ESF link.
            - lang (str): Optional language code for the ESF experience (e.g., 'en', 'es').

        Returns:
            - esf_url: The generated ESF URL
            - expires_at: When the URL expires
            - is_new: Whether a new URL was generated or existing one returned
            - is_waba_active: Whether WABA is currently active
            - message: Status message
        """
        wa_app = self.get_object()

        # Get optional parameters from request
        regenerate = request.data.get("regenerate", False)
        force = request.data.get("force", False)
        user = request.data.get("user", None)
        lang = request.data.get("lang", None)

        # Convert string 'true'/'false' to boolean if needed
        if isinstance(regenerate, str):
            regenerate = regenerate.lower() == "true"
        if isinstance(force, str):
            force = force.lower() == "true"

        try:
            service = ESFService(wa_app)
            result = service.generate_esf_url(regenerate=regenerate, force=force, user=user, lang=lang)
            return Response(result, status=status.HTTP_200_OK)
        except ESFServiceError as e:
            return Response({"error": str(e)}, status=status.HTTP_400_BAD_REQUEST)

    @action(detail=True, methods=["get"], url_path="esf-url-status", url_name="esf-url-status")
    def esf_url_status(self, request, *args, **kwargs):
        """
        Get the current status of the ESF URL for this Gupshup app.

        Returns:
            - has_esf_url: Whether an ESF URL exists
            - esf_url: The ESF URL (only if still valid)
            - expires_at: When the URL expires
            - is_valid: Whether the URL is still valid (not expired)
            - app_id: The Gupshup app ID
            - app_name: The Gupshup app name
            - is_waba_active: Whether the WABA is active
            - waba_account_status: The WABA account status from Gupshup
        """
        wa_app = self.get_object()
        service = ESFService(wa_app)
        result = service.get_esf_url_status()
        return Response(result, status=status.HTTP_200_OK)

    @action(detail=True, methods=["post"], url_path="sync-waba-info", url_name="sync-waba-info")
    def sync_waba_info(self, request, *args, **kwargs):
        """
        Sync WABA (WhatsApp Business Account) info from Gupshup API.

        This fetches the latest WABA details from Gupshup and updates the local
        WABAInfo record. Use this to check if a WABA has been successfully
        onboarded after completing the ESF flow.

        Returns:
            - success: Whether the sync was successful
            - is_waba_active: Whether the WABA is active
            - waba_info: WABA details (if available)
            - message: Status message
        """
        wa_app = self.get_object()

        try:
            service = ESFService(wa_app)
            waba_info = service.sync_waba_info()

            # Refresh to get updated is_waba_active
            wa_app.refresh_from_db()

            if waba_info:
                return Response(
                    {
                        "success": True,
                        "is_waba_active": wa_app.is_waba_active,
                        "waba_info": {
                            "account_status": waba_info.account_status,
                            "waba_id": waba_info.waba_id,
                            "phone": waba_info.phone,
                            "country_code": waba_info.country_code,
                        },
                        "message": "WABA info synced successfully",
                    },
                    status=status.HTTP_200_OK,
                )
            else:
                return Response(
                    {
                        "success": False,
                        "is_waba_active": False,
                        "waba_info": None,
                        "message": "No WABA info available from Gupshup",
                    },
                    status=status.HTTP_200_OK,
                )

        except ESFServiceError as e:
            return Response({"error": str(e), "success": False}, status=status.HTTP_400_BAD_REQUEST)

    @action(
        detail=False,
        methods=["post", "get"],
        url_path="test-webhook",
        url_name="test-webhook",
        permission_classes=[AllowAny],
    )
    def test_webhook(self, request, *args, **kwargs):
        """
        A custom action to test webhook requests from Gupshup/Meta.
        GET: Used for webhook verification (Meta challenge-response).
        POST: Used for webhook event handling.
        """
        if request.method == "GET":
            # Meta webhook verification: must return hub.challenge as plain text
            hub_mode = request.GET.get("hub.mode")
            hub_verify_token = request.GET.get("hub.verify_token")
            hub_challenge = request.GET.get("hub.challenge")

            cprint("Received test-webhook GET request", "green")
            cprint(f"hub.mode: {hub_mode}", "green")
            cprint(f"hub.verify_token: {hub_verify_token}", "green")
            cprint(f"hub.challenge: {hub_challenge}", "green")

            # TODO: Add your verify token validation here
            # WEBHOOK_VERIFY_TOKEN = getattr(settings, 'WEBHOOK_VERIFY_TOKEN', 'your_secret_token')
            # if hub_mode == "subscribe" and hub_verify_token == WEBHOOK_VERIFY_TOKEN:

            if hub_challenge:
                # Return challenge as plain text (required by Meta)
                return HttpResponse(hub_challenge, content_type="text/plain", status=200)
            else:
                return HttpResponse("No challenge provided", status=400)

        elif request.method == "POST":
            # Handle the webhook logic here
            cprint("Received test-webhook POST request", "green")
            handler = GupshupWebhookHandler()
            handler.handle_test_webhook(request.data)
            return Response({"status": "test webhook received"})
        return Response({"error": "Method not allowed"}, status=405)

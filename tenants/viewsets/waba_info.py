from rest_framework import status
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.throttling import UserRateThrottle

from abstract.viewsets.base import BaseTenantModelViewSet
from tenants.models import WABAInfo
from tenants.serializers import WABAInfoSerializer
from wa.adapters import get_bsp_adapter


class WABASyncThrottle(UserRateThrottle):
    """
    Custom throttle class for WABA sync operations.
    Limits sync requests to 1 per 30 seconds (2 per minute).
    """

    scope = "waba_sync"
    rate = "2/minute"  # Fallback rate if not in settings


class WABAInfoViewSet(BaseTenantModelViewSet):
    """
    A viewset for managing WABA (WhatsApp Business Account) information.
    Provides CRUD operations for WABA data linked to WA apps.
    """

    queryset = WABAInfo.objects.all()
    serializer_class = WABAInfoSerializer
    lookup_field = "wa_app__app_id"
    required_permissions = {
        "list": "wa_app.view",
        "retrieve": "wa_app.view",
        "create": "wa_app.manage",
        "partial_update": "wa_app.manage",
        "sync_waba_info": "wa_app.manage",
        "default": "wa_app.view",
    }

    def get_queryset(self):
        """
        Get queryset with optimized select_related for wa_app.

        Scoped explicitly because this override never reaches
        ``BaseTenantModelViewSet.get_queryset``, so an impersonated session would
        otherwise read every organisation's WABA records (#326).
        """
        return self.scope_to_impersonated_tenant(WABAInfo.objects.select_related("wa_app").all())

    @action(
        detail=True,
        methods=["get"],
        url_path="sync-waba-info",
        url_name="sync-waba-info",
        throttle_classes=[WABASyncThrottle],
    )
    def sync_waba_info(self, request, *args, **kwargs):
        """
        Sync WABA information from whichever BSP this app is on.

        Previously this imported Gupshup's partner API directly, so on a Meta
        Direct app it called the wrong provider with the wrong credentials and
        ``messaging_limit`` was never populated — leaving the quota check at its
        conservative 50-recipient fallback and refusing every larger broadcast
        (#267). It now goes through ``get_bsp_adapter``, so the provider is
        whatever the app is configured for.

        Throttled to 2 calls per minute to protect the provider's own limits.

        Error Responses:
        - 400: app not live, WABA/phone-number id missing or mismatched
        - 401: authentication failed
        - 429: too many requests
        - 500: internal server error
        """
        waba_info = self.get_object()
        wa_app = waba_info.wa_app

        try:
            result = get_bsp_adapter(wa_app).fetch_waba_info()

            if not result.success:
                error_message = result.error_message or "Unknown error"

                # Record the failure so an operator can see *when* syncing began
                # failing, not merely that the values look stale.
                waba_info.last_sync_error = {
                    "status": "error",
                    "message": error_message,
                    "provider": result.provider,
                }
                waba_info.save(update_fields=["last_sync_error"])

                if "Authentication Failed" in error_message or "authentication" in error_message.lower():
                    code = status.HTTP_401_UNAUTHORIZED
                elif "Too Many Requests" in error_message or "rate limit" in error_message.lower():
                    code = status.HTTP_429_TOO_MANY_REQUESTS
                else:
                    # App not live, WABA id issues, credential gaps, etc.
                    code = status.HTTP_400_BAD_REQUEST

                return Response({"status": "error", "message": error_message}, status=code)

            updated_waba_info, _ = WABAInfo.update_from_adapter_data(wa_app, result.data)

            serializer = self.get_serializer(updated_waba_info)
            return Response(serializer.data, status=status.HTTP_200_OK)

        except Exception as e:
            return Response(
                {"status": "error", "message": f"Failed to sync WABA info: {str(e)}"},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

from abstract.viewsets.base import BaseTenantModelViewSet
from rest_framework import status
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.throttling import UserRateThrottle
from tenants.models import WABAInfo
from tenants.serializers import WABAInfoSerializer
from wa.utility.apis.gupshup.waba import WABAAPI


class WABASyncThrottle(UserRateThrottle):
    """
    Custom throttle class for WABA sync operations.
    Limits sync requests to 1 per 30 seconds (2 per minute).
    """
    scope = 'waba_sync'
    rate = '2/minute'  # Fallback rate if not in settings


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
        """
        return WABAInfo.objects.select_related('wa_app').all()
    
    @action(
        detail=True,
        methods=["get"],
        url_path="sync-waba-info",
        url_name="sync-waba-info",
        throttle_classes=[WABASyncThrottle]
    )
    def sync_waba_info(self, request, *args, **kwargs):
        """
        Manually sync WABA information from Gupshup API.
        Throttled to 1 call per 30 seconds to prevent API abuse.
        
        POST: Fetches latest WABA info and updates the database.
        
        Error Responses:
        - 400: App not live, WABA ID not found, invalid WABA ID
        - 401: Authentication failed
        - 429: Too many requests (10 per minute from Gupshup)
        - 500: Internal server error
        """
        waba_info = self.get_object()
        wa_app = waba_info.wa_app
        
        try:
            # Initialize WABA API client
            waba_api = WABAAPI(appId=wa_app.app_id, token=wa_app.app_secret)
            
            # Fetch WABA details from Gupshup
            response = waba_api.get_waba_details()
            
            # Update WABA info from API response
            updated_waba_info, error = WABAInfo.update_from_api_response(wa_app, response)
            
            # Handle error response from API
            if error:
                error_message = error.get('message', 'Unknown error')
                # Map common error messages to appropriate status codes
                if 'Authentication Failed' in error_message:
                    return Response(
                        {"status": "error", "message": error_message},
                        status=status.HTTP_401_UNAUTHORIZED
                    )
                elif 'Too Many Requests' in error_message:
                    return Response(
                        {"status": "error", "message": error_message},
                        status=status.HTTP_429_TOO_MANY_REQUESTS
                    )
                elif 'Internal Server Error' in error_message:
                    return Response(
                        {"status": "error", "message": error_message},
                        status=status.HTTP_400_BAD_REQUEST
                    )
                else:
                    # App not live, WABA ID issues, etc.
                    return Response(
                        {"status": "error", "message": error_message},
                        status=status.HTTP_400_BAD_REQUEST
                    )
            
            # Return updated data on success
            serializer = self.get_serializer(updated_waba_info)
            return Response(serializer.data, status=status.HTTP_200_OK)
            
        except Exception as e:
            return Response(
                {"status": "error", "message": f"Failed to sync WABA info: {str(e)}"},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )

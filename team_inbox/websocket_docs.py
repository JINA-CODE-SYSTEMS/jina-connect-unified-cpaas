"""
WebSocket API Documentation for Team Inbox
This creates a custom documentation endpoint that integrates with your existing Swagger setup
"""

from django.shortcuts import render
from drf_yasg import openapi
from drf_yasg.utils import swagger_auto_schema
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response


@api_view(["GET"])
@permission_classes([IsAuthenticated])
@swagger_auto_schema(
    operation_description="Get WebSocket connection information for team inbox",
    responses={
        200: openapi.Response(
            description="WebSocket connection details",
            schema=openapi.Schema(
                type=openapi.TYPE_OBJECT,
                properties={
                    "websocket_url": openapi.Schema(type=openapi.TYPE_STRING, description="WebSocket connection URL"),
                    "authentication": openapi.Schema(type=openapi.TYPE_OBJECT, description="Authentication methods"),
                    "supported_message_types": openapi.Schema(
                        type=openapi.TYPE_ARRAY, items=openapi.Schema(type=openapi.TYPE_STRING)
                    ),
                    "example_messages": openapi.Schema(
                        type=openapi.TYPE_OBJECT, description="Example WebSocket messages"
                    ),
                },
            ),
        )
    },
)
def websocket_info(request):
    """
    Get WebSocket API documentation and connection details

    This endpoint provides all the information needed to connect to and use
    the team inbox WebSocket API, including authentication methods, message types,
    and example payloads.
    """

    tenant_id = request.GET.get("tenant_id", "{tenant_id}")
    base_url = request.build_absolute_uri("/").replace("http://", "ws://").replace("https://", "wss://")

    return Response(
        {
            "websocket_url": f"{base_url}ws/team-inbox/{tenant_id}/",
            "authentication": {
                "methods": [
                    {
                        "type": "JWT Query Parameter",
                        "description": "Include JWT token in query parameter (mobile apps)",
                        "example": f"{base_url}ws/team-inbox/{tenant_id}/?token=YOUR_JWT_TOKEN",
                        "recommended_for": "Mobile applications",
                    },
                    {
                        "type": "JWT Authorization Header",
                        "description": "Include JWT token in Authorization header (web apps)",
                        "example": "Authorization: Bearer YOUR_JWT_TOKEN",
                        "recommended_for": "Web applications",
                    },
                ],
                "get_token_endpoint": request.build_absolute_uri("/token/"),
                "token_format": "JWT (JSON Web Token)",
            },
            "supported_message_types": {
                "client_to_server": ["send_message", "mark_as_read", "get_messages", "client_info"],
                "server_to_client": [
                    "connection_established",
                    "new_message",
                    "contact_message",
                    "message_sent",
                    "message_history",
                    "messages_read",
                    "error",
                ],
            },
            "message_examples": {
                "send_message": {"type": "send_message", "content": {"text": "Hello team!"}, "platform": "WHATSAPP"},
                "mark_as_read": {"type": "mark_as_read", "message_ids": ["msg_123", "msg_456"]},
                "get_messages": {"type": "get_messages", "limit": 50, "offset": 0},
            },
            "response_examples": {
                "connection_established": {
                    "type": "connection_established",
                    "tenant_id": tenant_id,
                    "user_id": "user_123",
                    "timestamp": "2024-01-01T12:00:00Z",
                },
                "new_message": {
                    "type": "new_message",
                    "message": {
                        "id": 123,
                        "content": {"text": "Hello!"},
                        "platform": "WHATSAPP",
                        "author": "USER",
                        "timestamp": "2024-01-01T12:00:00Z",
                    },
                },
                "contact_message": {
                    "type": "contact_message",
                    "message": {
                        "id": 124,
                        "content": {"text": "Customer inquiry"},
                        "platform": "WHATSAPP",
                        "author": "CONTACT",
                        "timestamp": "2024-01-01T12:01:00Z",
                    },
                    "priority": "high",
                },
            },
            "connection_flow": [
                "1. Get JWT token from /token/ endpoint",
                "2. Connect to WebSocket URL with token",
                "3. Wait for connection_established message",
                "4. Send/receive messages as needed",
                "5. Handle reconnection if connection drops",
            ],
            "error_codes": {
                "4001": "Unauthorized - Invalid or missing JWT token",
                "4003": "Forbidden - No access to specified tenant",
                "4000": "Bad Request - Missing tenant_id in URL",
            },
        }
    )


def websocket_testing_page(request):
    """
    Render a WebSocket testing page (like Swagger UI for REST APIs)
    """
    context = {
        "websocket_base_url": request.build_absolute_uri("/").replace("http://", "ws://").replace("https://", "wss://"),
        "token_endpoint": request.build_absolute_uri("/token/"),
        "user": request.user if request.user.is_authenticated else None,
    }
    return render(request, "team_inbox/websocket_test.html", context)

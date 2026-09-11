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
            # Kept in step with TeamInboxConsumer.receive() and the types it
            # actually sends — the list used to advertise a "send_message"
            # the consumer has never handled, so a client following this
            # document got back "Unknown message type" (#274). Replies are
            # posted over REST, not the socket.
            "supported_message_types": {
                "client_to_server": [
                    "mark_as_read",
                    "get_timeline",
                    "get_chat_list",
                    "typing_indicator",
                    "client_info",
                ],
                "server_to_client": [
                    "connection_established",
                    "timeline",
                    "chat_list",
                    "new_message",
                    "messages_read",
                    "typing_indicator",
                    "message_status_update",
                    "payment_status_update",
                    "assignment_update",
                    "mention_notification",
                    "error",
                ],
            },
            "message_examples": {
                "mark_as_read": {"type": "mark_as_read", "message_ids": [123, 456], "contact_id": 789},
                "get_timeline": {"type": "get_timeline", "contact_id": 789, "limit": 50, "offset": 0},
                "get_chat_list": {"type": "get_chat_list", "limit": 50, "offset": 0, "search": "john"},
                "typing_indicator": {"type": "typing_indicator", "contact_id": 789, "is_typing": True},
                "client_info": {"type": "client_info", "client_type": "web"},
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
                "messages_read": {
                    "type": "messages_read",
                    "message_ids": [123, 124],
                    "contact_id": 789,
                    "user_id": "user_123",
                    "user_name": "Jane Doe",
                    "timestamp": "2024-01-01T12:01:00Z",
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

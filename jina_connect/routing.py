"""
Main WebSocket routing configuration for the entire project
"""

from channels.auth import AuthMiddlewareStack
from channels.routing import ProtocolTypeRouter, URLRouter
from channels.security.websocket import AllowedHostsOriginValidator
from django.core.asgi import get_asgi_application
from team_inbox.routing import websocket_urlpatterns as team_inbox_websockets


# Combine all WebSocket URL patterns from different apps
websocket_urlpatterns = []
websocket_urlpatterns.extend(team_inbox_websockets)

application = ProtocolTypeRouter({
    # HTTP requests go to Django's ASGI application
    "http": get_asgi_application(),
    
    # WebSocket requests go to our custom routing
    "websocket": AllowedHostsOriginValidator(
        AuthMiddlewareStack(
            URLRouter(websocket_urlpatterns)
        )
    ),
})
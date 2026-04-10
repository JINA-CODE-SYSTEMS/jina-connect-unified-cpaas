"""
WebSocket routing for team inbox
"""

from django.urls import re_path
from team_inbox.consumers import NotificationConsumer, TeamInboxConsumer

websocket_urlpatterns = [
    # Team inbox WebSocket endpoint
    # URL: ws://localhost:8000/ws/team-inbox/{tenant_id}/
    re_path(r'ws/team-inbox/(?P<tenant_id>\d+)/$', TeamInboxConsumer.as_asgi()),
    
    # General notifications WebSocket endpoint  
    # URL: ws://localhost:8000/ws/notifications/
    re_path(r'ws/notifications/$', NotificationConsumer.as_asgi()),
]
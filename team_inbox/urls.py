"""
URL configuration for team inbox app
"""

from django.urls import include, path
from rest_framework.routers import DefaultRouter
from team_inbox.viewsets import MessagesViewSet, TeamInboxStatsViewSet
from team_inbox.views import ExportPDFView
from team_inbox.websocket_docs import websocket_info, websocket_testing_page

app_name = 'team_inbox'

# Create router for ViewSets
router = DefaultRouter()
router.register(r'messages', MessagesViewSet, basename='messages')
router.register(r'stats', TeamInboxStatsViewSet, basename='stats')

urlpatterns = [
    # API endpoints
    path('api/', include(router.urls)),
    path('api/export-pdf/', ExportPDFView.as_view(), name='export-pdf'),
    
    # WebSocket documentation and testing
    path('websocket/info/', websocket_info, name='websocket_info'),
    path('websocket/test/', websocket_testing_page, name='websocket_test'),
    
    # WebSocket endpoints are handled in routing.py
    # ws://localhost:8000/ws/team-inbox/{tenant_id}/
]
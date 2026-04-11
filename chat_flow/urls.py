from django.urls import include, path
from rest_framework.routers import DefaultRouter

from .viewsets import ChatFlowAnalyticsViewSet, ChatFlowEdgeViewSet, ChatFlowNodeViewSet, ChatFlowViewSet

app_name = "chat_flow"

router = DefaultRouter()
router.register(r"flows", ChatFlowViewSet, basename="chatflow")
router.register(r"nodes", ChatFlowNodeViewSet, basename="chatflownode")
router.register(r"edges", ChatFlowEdgeViewSet, basename="chatflowedge")
router.register(r"analytics", ChatFlowAnalyticsViewSet, basename="chatflow-analytics")

urlpatterns = [
    path("", include(router.urls)),
]

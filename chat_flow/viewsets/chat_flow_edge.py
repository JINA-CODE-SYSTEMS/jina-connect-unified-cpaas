"""
ChatFlow Edge ViewSet Module

This module contains the ChatFlowEdgeViewSet which handles:
- CRUD operations for flow edges (connections between nodes)
- Button-to-transition mapping management
- Flow navigation logic configuration
"""

from abstract.viewsets.base import BaseTenantModelViewSet

from ..models import ChatFlowEdge
from ..serializers import ChatFlowEdgeSerializer


class ChatFlowEdgeViewSet(BaseTenantModelViewSet):
    """
    ViewSet for managing chat flow edges.

    This viewset manages the connections between nodes in a chat flow.
    Each edge represents a possible transition from one node to another,
    triggered by specific button clicks or user actions.

    Key Features:
    - Complete edge lifecycle management
    - Flow-specific edge filtering
    - Button text to transition mapping
    - Conditional navigation support

    Query Parameters:
    - flow_id: Filter edges by specific flow

    Edge Components:
    - source_node: The starting node of the transition
    - target_node: The destination node
    - button_text: The button that triggers this transition
    - conditions: Optional conditions for the transition

    Use Cases:
    - Connecting nodes in the flow editor
    - Defining button-triggered navigation
    - Setting up conditional flows
    - Managing complex conversation paths
    """

    serializer_class = ChatFlowEdgeSerializer
    queryset = ChatFlowEdge.objects.all()
    required_permissions = {
        "list": "chatflow.view",
        "retrieve": "chatflow.view",
        "create": "chatflow.create",
        "update": "chatflow.edit",
        "partial_update": "chatflow.edit",
        "destroy": "chatflow.delete",
        "default": "chatflow.view",
    }

    def get_queryset(self):
        """
        Tenant-scoped queryset (via BaseTenantModelViewSet) with
        optional flow_id filter.

        #268: Switched to BaseTenantModelViewSet for proper tenant isolation.
        """
        queryset = super().get_queryset()
        flow_id = self.request.query_params.get("flow_id")
        if flow_id:
            queryset = queryset.filter(flow_id=flow_id)
        return queryset

"""
ChatFlow Node ViewSet Module

This module contains the ChatFlowNodeViewSet which handles:
- CRUD operations for individual flow nodes
- Node-specific queries and filtering
- Template association management
"""

from abstract.viewsets.base import BaseTenantModelViewSet

from ..models import ChatFlowNode
from ..serializers import ChatFlowNodeSerializer


class ChatFlowNodeViewSet(BaseTenantModelViewSet):
    """
    ViewSet for managing chat flow nodes.

    This viewset provides full CRUD operations for individual nodes
    within chat flows. Each node represents a single message or
    interaction point in the conversation flow.

    Key Features:
    - Complete node lifecycle management
    - Flow-specific node filtering
    - Template association handling
    - Position and metadata management

    Query Parameters:
    - flow_id: Filter nodes by specific flow

    Use Cases:
    - Adding new nodes to existing flows
    - Updating node content and settings
    - Managing node positions in ReactFlow
    - Template assignment and validation
    """

    serializer_class = ChatFlowNodeSerializer
    queryset = ChatFlowNode.objects.all()
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

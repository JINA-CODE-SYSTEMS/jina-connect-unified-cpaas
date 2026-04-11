"""
ChatFlow ViewSet Module

This module contains the main ChatFlowViewSet which handles:
- CRUD operations for chat flows
- ReactFlow integration
- Template management for flows
"""

import logging

from django.utils import timezone
from django_filters.rest_framework import DjangoFilterBackend
from rest_framework import serializers as drf_serializers
from rest_framework import status
from rest_framework.decorators import action
from rest_framework.filters import OrderingFilter, SearchFilter
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from abstract.viewsets.base import BaseTenantModelViewSet
from contacts.models import AssigneeTypeChoices, TenantContact
from tenants.permission_classes import TenantRolePermission
from wa.models import StatusChoices, WATemplate

from ..models import ChatFlow, UserChatFlowSession
from ..serializers import ApprovedTemplateSerializer, ChatFlowCreateUpdateSerializer, ChatFlowSerializer
from ..services.flow_processor import ChatFlowProcessor
from ..services.graph_executor import clear_graph_cache
from ..validators import validate_reactflow_data

logger = logging.getLogger(__name__)


class ChatFlowViewSet(BaseTenantModelViewSet):
    """
    ViewSet for managing chat flows with ReactFlow integration.

    This viewset provides comprehensive chat flow management including:
    - Creating and editing flows with ReactFlow JSON structure
    - Managing flow sessions for individual users
    - Processing button clicks and flow navigation
    - Analytics and monitoring for flow performance
    - Template integration for WhatsApp messaging

    Key Features:
    - Multi-tenant support with automatic tenant filtering
    - ReactFlow visual editor integration
    - Real-time session management
    - Comprehensive analytics tracking
    - Template approval workflow integration
    """

    serializer_class = ChatFlowSerializer
    queryset = ChatFlow.objects.all()
    permission_classes = [IsAuthenticated, TenantRolePermission]
    filter_backends = [DjangoFilterBackend, SearchFilter, OrderingFilter]
    filterset_fields = {
        "is_active": ["exact"],
    }
    search_fields = ["name"]
    ordering_fields = ["created_at", "updated_at", "name"]
    ordering = ["-updated_at"]
    http_method_names = ["get", "post", "patch", "put"]
    required_permissions = {
        "list": "chatflow.view",
        "retrieve": "chatflow.view",
        "create": "chatflow.create",
        "update": "chatflow.edit",
        "partial_update": "chatflow.edit",
        "approved_templates": "chatflow.view",
        "rules": "chatflow.view",
        "validate_flow": "chatflow.view",
        "validate_node": "chatflow.view",
        "deactivate": "chatflow.edit",
        "activate": "chatflow.edit",
        "default": "chatflow.view",
    }

    def get_serializer_class(self):
        """
        Returns the appropriate serializer class based on the action.

        Uses ChatFlowCreateUpdateSerializer for create/update operations
        to handle ReactFlow JSON structure, and ChatFlowSerializer for
        read operations with detailed relationship data.
        """
        if self.action in ["create", "update", "partial_update"]:
            return ChatFlowCreateUpdateSerializer
        return ChatFlowSerializer

    def _check_active_sessions_for_flow_data(self, request):
        """
        If flow_data is being modified, check for active sessions
        and return a 409 Response if any exist, otherwise return None.
        """
        if "flow_data" in request.data:
            instance = self.get_object()
            active_count = UserChatFlowSession.objects.filter(flow=instance, is_active=True).count()
            if active_count > 0:
                return Response(
                    {
                        "error": "flow_has_active_sessions",
                        "active_session_count": active_count,
                        "message": f"Cannot edit flow while {active_count} session(s) are active. Deactivate the flow first.",
                    },
                    status=status.HTTP_409_CONFLICT,
                )
        return None

    def update(self, request, *args, **kwargs):
        conflict = self._check_active_sessions_for_flow_data(request)
        if conflict:
            return conflict
        return super().update(request, *args, **kwargs)

    def partial_update(self, request, *args, **kwargs):
        conflict = self._check_active_sessions_for_flow_data(request)
        if conflict:
            return conflict
        return super().partial_update(request, *args, **kwargs)

    @action(detail=True, methods=["post"], url_path="deactivate")
    def deactivate(self, request, pk=None):
        """
        Deactivate a flow and gracefully end all active sessions.

        POST /api/flows/{id}/deactivate/

        Steps:
        1. If flow is already inactive, return 200 with status: already_inactive
        2. End all active UserChatFlowSession records
        3. Set flow.is_active = False
        4. Clear the graph cache for this flow
        5. Unassign contacts assigned to this flow

        Request body (optional):
            { "cancellation_reason": "string" }

        Response (200):
            { "status": "deactivated", "sessions_ended": N, "flow_id": id }
        """
        instance = self.get_object()

        # 1. Already inactive check
        if not instance.is_active:
            return Response(
                {"status": "already_inactive", "flow_id": instance.id, "message": "Flow is already inactive."},
                status=status.HTTP_200_OK,
            )

        cancellation_reason = request.data.get("cancellation_reason", "")
        now = timezone.now()

        # 2. End all active sessions
        active_sessions = UserChatFlowSession.objects.filter(flow=instance, is_active=True)
        sessions_ended = active_sessions.count()
        active_sessions.update(
            is_active=False, ended_at=now, cancellation_reason=cancellation_reason or "Flow deactivated"
        )

        # 3. Deactivate the flow
        instance.is_active = False
        instance.save(update_fields=["is_active"])

        # 4. Clear graph cache
        clear_graph_cache(instance.id)

        # 5. Unassign contacts assigned to this flow
        TenantContact.objects.filter(assigned_to_type=AssigneeTypeChoices.CHATFLOW, assigned_to_id=instance.id).update(
            assigned_to_type=AssigneeTypeChoices.UNASSIGNED, assigned_to_id=None
        )

        logger.info(
            "Flow %s deactivated: %d sessions ended. Reason: %s",
            instance.id,
            sessions_ended,
            cancellation_reason or "N/A",
        )

        return Response(
            {"status": "deactivated", "sessions_ended": sessions_ended, "flow_id": instance.id},
            status=status.HTTP_200_OK,
        )

    @action(detail=True, methods=["post"], url_path="activate")
    def activate(self, request, pk=None):
        """
        Activate a flow after editing or initial setup.

        POST /api/flows/{id}/activate/

        Steps:
        1. If flow is already active, return 200 with status: already_active
        2. Validate flow has start_template and flow_data with nodes
        3. Set flow.is_active = True and save

        Response (200):
            { "status": "activated", "flow_id": id }
        """
        instance = self.get_object()

        # 1. Already active check
        if instance.is_active:
            return Response(
                {"status": "already_active", "flow_id": instance.id, "message": "Flow is already active."},
                status=status.HTTP_200_OK,
            )

        # 2. Validate prerequisites
        errors = []
        if not instance.start_template:
            errors.append("Flow must have a start_template before activation.")

        flow_data = instance.flow_data
        if not flow_data or not isinstance(flow_data, dict) or not flow_data.get("nodes"):
            errors.append("Flow must have flow_data with at least one node before activation.")

        if errors:
            return Response({"error": "flow_not_ready", "messages": errors}, status=status.HTTP_400_BAD_REQUEST)

        # 3. Activate the flow
        instance.is_active = True
        instance.save(update_fields=["is_active"])

        logger.info("Flow %s activated.", instance.id)

        return Response({"status": "activated", "flow_id": instance.id}, status=status.HTTP_200_OK)

    @action(detail=False, methods=["get"])
    def approved_templates(self, request):
        """
        Get all approved templates with or without buttons that can be used in chat flows.

        Returns templates that:
        - Belong to the current tenant
        - Have APPROVED status
        - Are suitable for flow integration

        This endpoint is used by the frontend to populate template
        selection dropdowns when building chat flows.
        """
        templates = WATemplate.objects.filter(tenant=request.user.tenant, status=StatusChoices.APPROVED)

        serializer = ApprovedTemplateSerializer(templates, many=True)
        return Response(serializer.data)

    @action(detail=False, methods=["get"], url_path="rules")
    def rules(self, request):
        """
        Get documentation for all available validation rules.

        Returns the full list of registered rules with their IDs,
        descriptions, categories, and severity levels so the frontend
        can display constraints in the flow builder UI.

        Response format:
            [
                {
                    "rule_id": "STRUCT_001",
                    "description": "An edge cannot connect a node to itself",
                    "category": "structural",
                    "severity": "error",
                    "enabled": true
                },
                ...
            ]
        """
        rules_docs = ChatFlowProcessor.get_flow_validation_rules()
        return Response(rules_docs)

    @action(detail=False, methods=["post"], url_path="validate")
    def validate_flow(self, request):
        """
        Dry-run validation: validate flow_data against all business rules
        without saving anything to the database.

        This allows the frontend to check a flow for errors/warnings
        before the user commits a save.

        Request body:
            {
                "flow_data": { "nodes": [...], "edges": [...], "viewport": {...} },
                "skip_db_checks": false   // optional, default false
            }

        Response (200):
            {
                "is_valid": true/false,
                "error_count": 0,
                "warning_count": 1,
                "violations": [
                    {
                        "rule_id": "STRUCT_002",
                        "message": "...",
                        "node_id": "node-3",
                        "edge_id": null,
                        "severity": "warning",
                        "details": {}
                    }
                ]
            }
        """
        flow_data = request.data.get("flow_data")
        if not flow_data or not isinstance(flow_data, dict):
            raise drf_serializers.ValidationError({"flow_data": "A valid flow_data JSON object is required."})

        required_keys = ["nodes", "edges"]
        for key in required_keys:
            if key not in flow_data:
                raise drf_serializers.ValidationError({"flow_data": f"flow_data must contain '{key}' field."})

        skip_db_checks = request.data.get("skip_db_checks", False)

        try:
            # Run Pydantic structural validation (same as create/update)
            validate_reactflow_data(flow_data)

            result = ChatFlowProcessor.validate_flow_rules(
                flow_data=flow_data,
                skip_db_checks=bool(skip_db_checks),
            )
            return Response(result.to_dict(), status=status.HTTP_200_OK)
        except ValueError as e:
            return Response(
                {"flow_data": str(e)},
                status=status.HTTP_400_BAD_REQUEST,
            )
        except Exception as e:
            logger.exception("Unexpected error during flow validation")
            return Response(
                {"error": f"Validation failed unexpectedly: {str(e)}"},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

    @action(detail=False, methods=["post"], url_path="validate-node")
    def validate_node(self, request):
        """
        Real-time single-node validation.

        Validates one node against all business rules, optionally within
        the context of the full flow. Use this for instant feedback as the
        user edits a node in the flow builder.

        Request body:
            {
                "node": { "id": "node-1", "type": "template", "data": {...}, ... },
                "flow_data": { ... }   // optional – full flow context for cross-node checks
            }

        Response (200):
            {
                "is_valid": true/false,
                "error_count": 0,
                "warning_count": 0,
                "violations": []
            }
        """
        node = request.data.get("node")
        if not node or not isinstance(node, dict):
            raise drf_serializers.ValidationError({"node": "A valid node JSON object is required."})

        if "id" not in node:
            raise drf_serializers.ValidationError({"node": "Node must have an 'id' field."})

        flow_data = request.data.get("flow_data")  # optional context

        try:
            from ..rules.validator import FlowValidatorService

            validator = FlowValidatorService(skip_db_checks=True)
            result = validator.validate_node(node=node, flow_data=flow_data)
            return Response(result.to_dict(), status=status.HTTP_200_OK)
        except Exception as e:
            logger.exception("Unexpected error during node validation")
            return Response(
                {"error": f"Node validation failed unexpectedly: {str(e)}"},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

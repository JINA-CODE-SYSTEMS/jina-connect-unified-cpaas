"""
ChatFlow Analytics ViewSet Module

Provides read-only analytics endpoints for chat flow performance:
- Flow completion rate
- Node-level drop-off stats
- Average session duration
- Button click distribution
- Active sessions count
"""

import logging

from django.db.models import Avg, Count, F
from django.db.models.functions import Extract
from rest_framework import serializers as drf_serializers
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.viewsets import GenericViewSet

from tenants.permission_classes import TenantRolePermission

from ..models import ChatFlow, ChatFlowEdge, ChatFlowNode, UserChatFlowSession

logger = logging.getLogger(__name__)


class ChatFlowAnalyticsViewSet(GenericViewSet):
    """
    Read-only analytics ViewSet for chat flow performance metrics.

    All endpoints are scoped to the requesting user's tenant.

    Endpoints
    ---------
    GET /analytics/completion-rate/?flow_id=<id>
    GET /analytics/node-drop-off/?flow_id=<id>
    GET /analytics/average-duration/?flow_id=<id>
    GET /analytics/button-clicks/?flow_id=<id>
    GET /analytics/active-sessions/?flow_id=<id>   (flow_id optional)
    """

    serializer_class = drf_serializers.Serializer  # placeholder for schema generation

    permission_classes = [IsAuthenticated, TenantRolePermission]
    http_method_names = ["get"]
    required_permissions = {
        "default": "chatflow.view",
    }

    # ── helpers ────────────────────────────────────────────────────

    def _get_tenant(self, request):
        return getattr(request.user, "tenant", None)

    def _get_flow_or_400(self, request):
        """Return a tenant-scoped ChatFlow instance or raise a 400."""
        flow_id = request.query_params.get("flow_id")
        if not flow_id:
            raise drf_serializers.ValidationError({"flow_id": "This query parameter is required."})
        tenant = self._get_tenant(request)
        try:
            return ChatFlow.objects.get(pk=flow_id, tenant=tenant)
        except ChatFlow.DoesNotExist:
            raise drf_serializers.ValidationError({"flow_id": "Chat flow not found."})

    def _sessions_qs(self, flow, request):
        """Base queryset for sessions of *flow* within optional date range."""
        qs = UserChatFlowSession.objects.filter(
            flow=flow,
            tenant=self._get_tenant(request),
        )
        date_from = request.query_params.get("date_from")
        date_to = request.query_params.get("date_to")
        if date_from:
            qs = qs.filter(started_at__date__gte=date_from)
        if date_to:
            qs = qs.filter(started_at__date__lte=date_to)
        return qs

    # ── 1. Flow completion rate ───────────────────────────────────

    @action(detail=False, methods=["get"], url_path="completion-rate")
    def completion_rate(self, request):
        """
        Percentage of sessions that completed the flow.

        Query params:
            flow_id  (required)
            date_from, date_to  (optional, YYYY-MM-DD)

        Response:
            {
                "flow_id": 1,
                "total_sessions": 150,
                "completed_sessions": 90,
                "completion_rate": 60.0
            }
        """
        flow = self._get_flow_or_400(request)
        sessions = self._sessions_qs(flow, request)

        total = sessions.count()
        completed = sessions.filter(is_complete=True).count()
        rate = round((completed / total) * 100, 2) if total else 0.0

        return Response(
            {
                "flow_id": flow.pk,
                "total_sessions": total,
                "completed_sessions": completed,
                "completion_rate": rate,
            }
        )

    # ── 2. Node-level drop-off stats ─────────────────────────────

    @action(detail=False, methods=["get"], url_path="node-drop-off")
    def node_drop_off(self, request):
        """
        For each node in the flow, count how many sessions stopped there
        without completing.

        Query params:
            flow_id  (required)
            date_from, date_to  (optional)

        Response:
            {
                "flow_id": 1,
                "total_dropped": 45,
                "nodes": [
                    {
                        "node_id": "welcome-node",
                        "drop_off_count": 20,
                        "drop_off_pct": 44.44
                    },
                    ...
                ]
            }
        """
        flow = self._get_flow_or_400(request)
        sessions = self._sessions_qs(flow, request)

        dropped = sessions.filter(is_active=False, is_complete=False)
        total_dropped = dropped.count()

        node_stats = dropped.values("current_node_id").annotate(drop_off_count=Count("id")).order_by("-drop_off_count")

        nodes = [
            {
                "node_id": row["current_node_id"],
                "drop_off_count": row["drop_off_count"],
                "drop_off_pct": round((row["drop_off_count"] / total_dropped) * 100, 2) if total_dropped else 0.0,
            }
            for row in node_stats
        ]

        return Response(
            {
                "flow_id": flow.pk,
                "total_dropped": total_dropped,
                "nodes": nodes,
            }
        )

    # ── 3. Average session duration ──────────────────────────────

    @action(detail=False, methods=["get"], url_path="average-duration")
    def average_duration(self, request):
        """
        Average time (in seconds) between session start and end for
        finished sessions (both completed and abandoned).

        Query params:
            flow_id  (required)
            date_from, date_to  (optional)

        Response:
            {
                "flow_id": 1,
                "finished_sessions": 120,
                "avg_duration_seconds": 245.3,
                "avg_duration_completed_seconds": 180.5,
                "avg_duration_abandoned_seconds": 310.1
            }
        """
        flow = self._get_flow_or_400(request)
        sessions = self._sessions_qs(flow, request)

        finished = sessions.filter(ended_at__isnull=False)

        # Overall average
        avg_all = finished.aggregate(avg_secs=Avg(Extract(F("ended_at") - F("started_at"), "epoch")))["avg_secs"]

        # Completed sessions average
        avg_completed = finished.filter(is_complete=True).aggregate(
            avg_secs=Avg(Extract(F("ended_at") - F("started_at"), "epoch"))
        )["avg_secs"]

        # Abandoned sessions average
        avg_abandoned = finished.filter(is_complete=False).aggregate(
            avg_secs=Avg(Extract(F("ended_at") - F("started_at"), "epoch"))
        )["avg_secs"]

        return Response(
            {
                "flow_id": flow.pk,
                "finished_sessions": finished.count(),
                "avg_duration_seconds": round(avg_all, 2) if avg_all else 0.0,
                "avg_duration_completed_seconds": (round(avg_completed, 2) if avg_completed else 0.0),
                "avg_duration_abandoned_seconds": (round(avg_abandoned, 2) if avg_abandoned else 0.0),
            }
        )

    # ── 4. Button click distribution ─────────────────────────────

    @action(detail=False, methods=["get"], url_path="button-clicks")
    def button_clicks(self, request):
        """
        Distribution of button clicks across all edges of a flow,
        derived from how many sessions passed through each node transition.

        Because each session records *current_node_id*, we infer that every
        session whose current_node_id == edge.target_node.node_id must have
        clicked the corresponding button to get there.

        For a more accurate count we look at sessions that ever reached
        each target node (completed or not).

        Query params:
            flow_id  (required)
            date_from, date_to  (optional)

        Response:
            {
                "flow_id": 1,
                "total_clicks": 300,
                "buttons": [
                    {
                        "edge_id": "edge-1",
                        "source_node_id": "welcome-node",
                        "target_node_id": "menu-node",
                        "button_text": "Get Started",
                        "click_count": 120,
                        "click_pct": 40.0
                    },
                    ...
                ]
            }
        """
        flow = self._get_flow_or_400(request)
        sessions = self._sessions_qs(flow, request)

        edges = ChatFlowEdge.objects.filter(flow=flow).select_related("source_node", "target_node")

        # Collect all node_ids a session has ever been at.
        # Sessions that *passed through* a node ended up beyond it,
        # so we count sessions whose current_node_id == target OR
        # who are complete (reached the end).
        # A simpler (and accurate enough) proxy: count sessions where
        # current_node_id matches the target_node or any node *after* it.
        # For the MVP, we count sessions that reached each target_node_id.
        target_node_ids = [e.target_node.node_id for e in edges]
        reached_counts = (
            sessions.filter(current_node_id__in=target_node_ids).values("current_node_id").annotate(cnt=Count("id"))
        )
        reached_map = {r["current_node_id"]: r["cnt"] for r in reached_counts}

        # For completed sessions, they passed through earlier nodes too.
        # Add completed count to every node that is NOT a terminal node.
        completed_count = sessions.filter(is_complete=True).count()
        terminal_node_ids = self._terminal_node_ids(flow)

        buttons = []
        total_clicks = 0
        for edge in edges:
            target_nid = edge.target_node.node_id
            count = reached_map.get(target_nid, 0)
            # Completed sessions also passed through non-terminal targets
            if target_nid not in terminal_node_ids:
                count += completed_count
            total_clicks += count
            buttons.append(
                {
                    "edge_id": edge.edge_id,
                    "source_node_id": edge.source_node.node_id,
                    "target_node_id": target_nid,
                    "button_text": edge.button_text,
                    "click_count": count,
                }
            )

        # Calculate percentages
        for btn in buttons:
            btn["click_pct"] = round((btn["click_count"] / total_clicks) * 100, 2) if total_clicks else 0.0

        buttons.sort(key=lambda b: b["click_count"], reverse=True)

        return Response(
            {
                "flow_id": flow.pk,
                "total_clicks": total_clicks,
                "buttons": buttons,
            }
        )

    # ── 5. Active sessions count ─────────────────────────────────

    @action(detail=False, methods=["get"], url_path="active-sessions")
    def active_sessions(self, request):
        """
        Number of currently active (in-progress) sessions, optionally
        scoped to a single flow.

        Query params:
            flow_id   (optional — omit for tenant-wide count)

        Response:
            {
                "flow_id": 1 | null,
                "active_sessions": 42,
                "by_flow": [                    // only when flow_id is omitted
                    {"flow_id": 1, "flow_name": "...", "active": 10},
                    ...
                ]
            }
        """
        tenant = self._get_tenant(request)
        flow_id = request.query_params.get("flow_id")

        base_qs = UserChatFlowSession.objects.filter(
            is_active=True,
            tenant=tenant,
        )

        if flow_id:
            base_qs = base_qs.filter(flow_id=flow_id)
            return Response(
                {
                    "flow_id": int(flow_id),
                    "active_sessions": base_qs.count(),
                }
            )

        # Tenant-wide breakdown
        total = base_qs.count()
        by_flow = base_qs.values("flow_id", "flow__name").annotate(active=Count("id")).order_by("-active")

        return Response(
            {
                "flow_id": None,
                "active_sessions": total,
                "by_flow": [
                    {
                        "flow_id": row["flow_id"],
                        "flow_name": row["flow__name"],
                        "active": row["active"],
                    }
                    for row in by_flow
                ],
            }
        )

    # ── private helpers ──────────────────────────────────────────

    @staticmethod
    def _terminal_node_ids(flow):
        """Return node_ids that have no outgoing edges (end nodes)."""
        nodes_with_outgoing = set(ChatFlowEdge.objects.filter(flow=flow).values_list("source_node__node_id", flat=True))
        all_node_ids = set(ChatFlowNode.objects.filter(flow=flow).values_list("node_id", flat=True))
        return all_node_ids - nodes_with_outgoing

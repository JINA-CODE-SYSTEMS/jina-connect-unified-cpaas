"""Introspection endpoint for the chat_flow trigger registry (#188).

The frontend flow-builder calls ``GET .../triggers/types/`` to render
the trigger-config form. Returning the Pydantic-generated JSON Schema
keeps the contract version-stable — adding a new trigger type or
config field doesn't require a frontend deploy as long as the schema
shape is honoured.
"""

from __future__ import annotations

from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from chat_flow.triggers.registry import trigger_introspection


class TriggerTypesView(APIView):
    """``GET /chat_flow/api/v1/triggers/types/`` — list registered
    trigger types with their config JSON Schemas.

    Read-only, authenticated. Tenant-agnostic (the registry is global
    to the deployment) — exposing it to any authenticated user is
    fine; the schemas are public knowledge once a flow is configured.
    """

    permission_classes = [IsAuthenticated]

    def get(self, request):
        return Response({"types": trigger_introspection()})

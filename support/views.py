"""Support tickets for platform admins, and the GitHub webhook that keeps them current."""

import json
import logging

from django.http import HttpResponse, JsonResponse
from django.utils.decorators import method_decorator
from django.views import View
from django.views.decorators.csrf import csrf_exempt
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from support import conf, github, services
from support.models import SupportTicket, TicketStatus
from support.serializers import (
    CustomerTextSerializer,
    SupportTicketDetailSerializer,
    SupportTicketListSerializer,
)
from tenants.permission_classes import IsPlatformOperator, TenantRolePermission

logger = logging.getLogger(__name__)

# What a customer is told when GitHub cannot be reached. The ticket is left as
# it was, so trying again is the right advice.
UNREACHABLE = "We couldn't reach the support team just now. Please try again in a few minutes."


class SupportTicketViewSet(viewsets.ReadOnlyModelViewSet):
    """
    BugDrop reports filed from this deployment, for its platform admins.

    - GET  /support/tickets/?status=active|open|in_progress|resolved|closed
    - GET  /support/tickets/{id}/
    - GET  /support/tickets/summary/
    - POST /support/tickets/{id}/comment/  {"text": "..."}
    - POST /support/tickets/{id}/reopen/   {"text": "..."}  — required: what is still wrong
    - POST /support/tickets/{id}/confirm/
    """

    # TenantRolePermission is here for its impersonation guard: a "view as
    # organisation" session keeps is_superuser, and must stay read-only.
    permission_classes = [IsAuthenticated, IsPlatformOperator, TenantRolePermission]
    queryset = SupportTicket.objects.all()
    filter_backends = []

    def get_serializer_class(self):
        return SupportTicketListSerializer if self.action == "list" else SupportTicketDetailSerializer

    def get_queryset(self):
        qs = SupportTicket.objects.filter(github_repo=conf.repo())
        if self.action == "retrieve":
            qs = qs.prefetch_related("events__author")
        wanted = self.request.query_params.get("status")
        if wanted == "active":
            qs = qs.exclude(status=TicketStatus.CLOSED)
        elif wanted in TicketStatus.values:
            qs = qs.filter(status=wanted)
        return qs

    @action(detail=False, methods=["get"])
    def summary(self, request):
        qs = SupportTicket.objects.filter(github_repo=conf.repo())
        return Response(
            {
                "configured": conf.is_configured(),
                "active": qs.exclude(status=TicketStatus.CLOSED).count(),
                "awaiting_confirmation": qs.filter(status=TicketStatus.RESOLVED).count(),
                "auto_close_hours": conf.auto_close_hours(),
            }
        )

    def _act(self, request, fn, *args):
        ticket = self.get_object()
        try:
            fn(ticket, *args)
        except services.TicketStateError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_409_CONFLICT)
        except github.GitHubError:
            logger.exception("support: GitHub call failed for ticket %s", ticket.pk)
            return Response({"detail": UNREACHABLE}, status=status.HTTP_502_BAD_GATEWAY)
        ticket.refresh_from_db()
        return Response(SupportTicketDetailSerializer(self._fresh(ticket)).data)

    def _fresh(self, ticket):
        return SupportTicket.objects.prefetch_related("events__author").get(pk=ticket.pk)

    def _text(self, request):
        serializer = CustomerTextSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        return serializer.validated_data["text"]

    @action(detail=True, methods=["post"])
    def comment(self, request, pk=None):
        return self._act(request, services.customer_comment, request.user, self._text(request))

    @action(detail=True, methods=["post"])
    def reopen(self, request, pk=None):
        return self._act(request, services.customer_reopen, request.user, self._text(request))

    @action(detail=True, methods=["post"])
    def confirm(self, request, pk=None):
        return self._act(request, services.customer_confirm, request.user)


@method_decorator(csrf_exempt, name="dispatch")
class GitHubWebhookView(View):
    """Receives ``issues`` and ``issue_comment`` deliveries for ``SUPPORT_GITHUB_REPO``.

    Authenticated by signature, not by user: GitHub signs each delivery with the
    webhook secret, and anything unsigned or mis-signed is refused before the
    body is parsed.
    """

    http_method_names = ["post"]

    def post(self, request):
        if not conf.is_configured():
            return HttpResponse(status=404)
        if not github.verify_signature(request.body, request.headers.get("X-Hub-Signature-256")):
            return HttpResponse(status=401)

        event = request.headers.get("X-GitHub-Event", "")
        if event == "ping":
            return JsonResponse({"ok": True})
        try:
            payload = json.loads(request.body)
        except ValueError:
            return HttpResponse(status=400)

        repo = ((payload.get("repository") or {}).get("full_name") or "").lower()
        if repo != conf.repo().lower():
            return JsonResponse({"ok": True, "ignored": "repository"})

        handler = {"issues": services.handle_issues_event, "issue_comment": services.handle_comment_event}.get(event)
        if handler is None:
            return JsonResponse({"ok": True, "ignored": "event"})
        # A failure is a 500 on purpose: GitHub records it, and the delivery can
        # be redelivered from the repository's webhook settings once fixed.
        ticket = handler(payload)
        return JsonResponse({"ok": True, "ticket": ticket.pk if ticket else None})

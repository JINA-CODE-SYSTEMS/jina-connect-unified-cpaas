"""
WAApp ViewSet - BSP Agnostic WhatsApp App Configuration

Provides CRUD operations for WhatsApp Business Account configurations.
Frontend uses this to manage connected WhatsApp accounts.
"""

from django_filters import rest_framework as filters
from drf_yasg import openapi
from drf_yasg.utils import no_body, swagger_auto_schema
from rest_framework import status
from rest_framework.decorators import action
from rest_framework.response import Response

from abstract.viewsets.base import BaseTenantModelViewSet
from wa.models import BSPChoices, WAApp
from wa.serializers import (
    WAAppCreateSerializer,
    WAAppListSerializer,
    WAAppSafeCreateSerializer,
    WAAppSafeSerializer,
    WAAppSerializer,
)


class WAAppFilter(filters.FilterSet):
    """Filter for WAApp listing."""

    phone_number = filters.CharFilter(field_name="wa_number", lookup_expr="icontains")
    bsp = filters.ChoiceFilter(choices=BSPChoices.choices)
    is_active = filters.BooleanFilter()
    is_verified = filters.BooleanFilter()
    tenant = filters.NumberFilter(field_name="tenant__id")

    class Meta:
        model = WAApp
        fields = ["phone_number", "bsp", "is_active", "is_verified", "tenant"]


class WAAppViewSet(BaseTenantModelViewSet):
    """
    ViewSet for managing WhatsApp Business App configurations.

    Provides endpoints to:
    - List all connected WhatsApp accounts for the tenant
    - Create new WhatsApp account connections
    - Retrieve, update, and manage individual accounts
    - Check quota and verification status

    All operations are tenant-scoped based on authenticated user.
    """

    queryset = WAApp.objects.all()
    serializer_class = WAAppSerializer
    filterset_class = WAAppFilter
    search_fields = ["app_name", "wa_number", "waba_id"]
    ordering_fields = ["created_at", "updated_at", "name", "phone_number"]
    ordering = ["-created_at"]
    http_method_names = ["get", "post", "patch", "delete"]
    required_permissions = {
        "list": "wa_app.view",
        "retrieve": "wa_app.view",
        "create": "wa_app.manage",
        "partial_update": "wa_app.manage",
        "destroy": "wa_app.delete",
        "quota": "wa_app.view",
        "reset_counter": "wa_app.manage",
        # The strictest thing this viewset does, on the same key as the actions
        # that *change* an app — reading a stored secret is at least as
        # consequential as rotating one, and a viewer must never reach it.
        "reveal_credential": "wa_app.manage",
        "capabilities": "wa_app.view",
        # Deliberately ``view`` and not ``manage``, unlike every other action
        # that touches onboarding. This one answers "what can be done here and
        # by whom" — a viewer who cannot act still needs to be told what the
        # options are and who to ask, rather than shown an empty page. What it
        # must not do is *imply* the caller may act, which is why the response
        # carries ``can_manage`` explicitly.
        "onboarding_options": "wa_app.view",
        # The per-app callback URL is a setup credential, not app metadata (#310).
        "webhook_setup": "wa_app.manage",
        # Reads the stored credentials and talks to META with them. Same gate as
        # the writes that put them there, not the gate that reads app metadata
        # (#311).
        "preflight": "wa_app.manage",
        "default": "wa_app.view",
    }

    def get_serializer_class(self):
        """
        #251: ADMIN/OWNER (priority >= 80) get full BSP identifiers.
        MANAGER and below get WAAppSafeSerializer (no app_id, waba_id, phone_number_id).
        List action uses WAAppListSerializer for all roles (already minimal).

        #311: create gets the matching *create* serializer, which is the one
        carrying the META required-identifier validation. That validation was
        written, exported and never reached from here, so ``POST /wa/v2/apps/``
        accepted ``bsp: "META"`` with neither identifier set.

        The privilege branch is applied first and the create variant chosen
        inside it, deliberately: each create serializer subclasses the serializer
        that role already gets, so wiring this in adds a rule without adding a
        readable or writable field to either level. A role below priority 80 that
        holds ``wa_app.manage`` sees exactly the field surface it saw before.

        #353: a platform operator has no ``TenantUser`` row at all, and reading a
        role priority off it concluded "below manager" for the one caller who is
        above every manager — the operator onboarding an organisation they are
        not a member of, which is the entire premise of #345. "No membership"
        is not a low role; it means not scoped to an organisation, and
        ``acting_as_platform_operator`` is where that is decided for the whole
        codebase, so this cannot drift from what the write scoping (#346, #352)
        already concluded about the same person. It is deliberately an ``or``
        and not a replacement: the #251 rule below priority 80 is untouched, and
        an impersonated session — superuser, membershipless, and therefore the
        exact shape of an operator — is excluded there rather than here.
        """
        if self.action == "list":
            return WAAppListSerializer

        tu = self._get_tenant_user()
        privileged = bool(tu and tu.role and tu.role.priority >= 80) or self.acting_as_platform_operator()

        if self.action == "create":
            return WAAppCreateSerializer if privileged else WAAppSafeCreateSerializer

        return WAAppSerializer if privileged else WAAppSafeSerializer

    @swagger_auto_schema(
        operation_description="List all WhatsApp Business Apps for the current tenant",
        operation_summary="List WA Apps",
        operation_id="list_wa_apps",
        tags=["WhatsApp Apps (v2)"],
        manual_parameters=[
            openapi.Parameter(
                "phone_number",
                openapi.IN_QUERY,
                description="Filter by phone number (partial match)",
                type=openapi.TYPE_STRING,
                required=False,
            ),
            openapi.Parameter(
                "bsp",
                openapi.IN_QUERY,
                description="Filter by Business Solution Provider",
                type=openapi.TYPE_STRING,
                enum=["META", "GUPSHUP", "TWILIO", "MESSAGEBIRD"],
                required=False,
            ),
            openapi.Parameter(
                "is_active",
                openapi.IN_QUERY,
                description="Filter by active status",
                type=openapi.TYPE_BOOLEAN,
                required=False,
            ),
            openapi.Parameter(
                "is_verified",
                openapi.IN_QUERY,
                description="Filter by verification status",
                type=openapi.TYPE_BOOLEAN,
                required=False,
            ),
            openapi.Parameter(
                "search",
                openapi.IN_QUERY,
                description="Search in name, phone_number, waba_id",
                type=openapi.TYPE_STRING,
                required=False,
            ),
            openapi.Parameter(
                "ordering",
                openapi.IN_QUERY,
                description="Order results by field (prefix with - for descending)",
                type=openapi.TYPE_STRING,
                enum=["created_at", "-created_at", "name", "-name", "phone_number", "-phone_number"],
                required=False,
            ),
        ],
        responses={
            200: openapi.Response(description="List of WhatsApp Apps", schema=WAAppListSerializer(many=True)),
            401: openapi.Response(description="Authentication required"),
            403: openapi.Response(description="Permission denied"),
        },
    )
    def list(self, request, *args, **kwargs):
        """List all WhatsApp Business Apps for the current tenant."""
        return super().list(request, *args, **kwargs)

    @swagger_auto_schema(
        operation_description="Create a new WhatsApp Business App connection",
        operation_summary="Create WA App",
        operation_id="create_wa_app",
        tags=["WhatsApp Apps (v2)"],
        request_body=WAAppCreateSerializer,
        responses={
            201: openapi.Response(description="WA App created successfully", schema=WAAppSerializer()),
            400: openapi.Response(description="Validation error"),
            401: openapi.Response(description="Authentication required"),
        },
    )
    def create(self, request, *args, **kwargs):
        """Create a new WhatsApp Business App connection."""
        return super().create(request, *args, **kwargs)

    @swagger_auto_schema(
        operation_description="Retrieve a specific WhatsApp Business App by ID",
        operation_summary="Get WA App",
        operation_id="retrieve_wa_app",
        tags=["WhatsApp Apps (v2)"],
        responses={
            200: openapi.Response(description="WA App details", schema=WAAppSerializer()),
            401: openapi.Response(description="Authentication required"),
            404: openapi.Response(description="WA App not found"),
        },
    )
    def retrieve(self, request, *args, **kwargs):
        """Retrieve a specific WhatsApp Business App by ID."""
        return super().retrieve(request, *args, **kwargs)

    @swagger_auto_schema(
        operation_description="Partially update a WhatsApp Business App",
        operation_summary="Update WA App",
        operation_id="partial_update_wa_app",
        tags=["WhatsApp Apps (v2)"],
        request_body=WAAppSerializer,
        responses={
            200: openapi.Response(description="WA App updated successfully", schema=WAAppSerializer()),
            400: openapi.Response(description="Validation error"),
            401: openapi.Response(description="Authentication required"),
            404: openapi.Response(description="WA App not found"),
        },
    )
    def partial_update(self, request, *args, **kwargs):
        """Partially update a WhatsApp Business App."""
        return super().partial_update(request, *args, **kwargs)

    @swagger_auto_schema(
        operation_description=(
            "Delete a WhatsApp Business App connection. This is a destructive operation restricted to OWNER only."
        ),
        operation_summary="Delete WA App",
        operation_id="delete_wa_app",
        tags=["WhatsApp Apps (v2)"],
        responses={
            204: openapi.Response(description="WA App deleted successfully"),
            401: openapi.Response(description="Authentication required"),
            403: openapi.Response(description="Only OWNER can delete WA apps"),
            404: openapi.Response(description="WA App not found"),
        },
    )
    def destroy(self, request, *args, **kwargs):
        """Delete a WhatsApp Business App (OWNER only via wa_app.delete)."""
        return super().destroy(request, *args, **kwargs)

    @swagger_auto_schema(
        operation_description="Get messaging quota information for a WA App",
        operation_summary="Get WA App Quota",
        operation_id="get_wa_app_quota",
        tags=["WhatsApp Apps (v2)"],
        responses={
            200: openapi.Response(
                description="Quota information",
                schema=openapi.Schema(
                    type=openapi.TYPE_OBJECT,
                    properties={
                        "daily_limit": openapi.Schema(type=openapi.TYPE_INTEGER),
                        "messages_sent_today": openapi.Schema(type=openapi.TYPE_INTEGER),
                        "remaining_quota": openapi.Schema(type=openapi.TYPE_INTEGER),
                        "tier": openapi.Schema(type=openapi.TYPE_STRING),
                    },
                ),
            ),
            401: openapi.Response(description="Authentication required"),
            404: openapi.Response(description="WA App not found"),
        },
    )
    @action(detail=True, methods=["get"], url_path="quota")
    def quota(self, request, pk=None):
        """Get messaging quota information for a WA App."""
        wa_app = self.get_object()
        return Response(
            {
                "daily_limit": wa_app.daily_limit,
                "messages_sent_today": wa_app.messages_sent_today,
                "remaining_quota": max(0, wa_app.daily_limit - wa_app.messages_sent_today),
                "tier": wa_app.tier,
            }
        )

    @swagger_auto_schema(
        operation_description="Reset daily message counter (admin only)",
        operation_summary="Reset Daily Counter",
        operation_id="reset_wa_app_counter",
        tags=["WhatsApp Apps (v2)"],
        responses={
            200: openapi.Response(description="Counter reset successfully"),
            401: openapi.Response(description="Authentication required"),
            403: openapi.Response(description="Admin access required"),
            404: openapi.Response(description="WA App not found"),
        },
    )
    @action(detail=True, methods=["post"], url_path="reset-counter")
    def reset_counter(self, request, pk=None):
        """Reset daily message counter."""
        if not request.user.is_superuser:
            return Response({"error": "Admin access required"}, status=status.HTTP_403_FORBIDDEN)

        wa_app = self.get_object()
        wa_app.messages_sent_today = 0
        wa_app.save(update_fields=["messages_sent_today"])

        return Response({"message": "Daily counter reset successfully"})

    @swagger_auto_schema(
        operation_description=(
            "Which routes to a working WhatsApp app this deployment offers, and whether the "
            "caller may take them. Read this before rendering any onboarding entry point: a "
            "deployment can switch Gupshup's Embedded Signup off, and a client that decides "
            "for itself which options exist will offer a button the server answers 403 to. "
            "Requires wa_app.view so every role can be told what the options are; "
            "'can_manage' says whether this caller may actually act on them."
        ),
        operation_summary="WhatsApp Onboarding Options",
        operation_id="wa_app_onboarding_options",
        tags=["WhatsApp Apps (v2)"],
        responses={
            200: openapi.Response(
                description="Available onboarding routes",
                schema=openapi.Schema(
                    type=openapi.TYPE_OBJECT,
                    properties={
                        "routes": openapi.Schema(
                            type=openapi.TYPE_ARRAY,
                            items=openapi.Schema(
                                type=openapi.TYPE_OBJECT,
                                properties={
                                    "key": openapi.Schema(type=openapi.TYPE_STRING),
                                    "available": openapi.Schema(type=openapi.TYPE_BOOLEAN),
                                    "requires_permission": openapi.Schema(type=openapi.TYPE_STRING),
                                    "reason": openapi.Schema(type=openapi.TYPE_STRING, x_nullable=True),
                                },
                            ),
                        ),
                        "can_manage": openapi.Schema(type=openapi.TYPE_BOOLEAN),
                    },
                ),
            ),
            401: openapi.Response(description="Authentication required"),
            403: openapi.Response(description="Permission denied"),
        },
    )
    @action(detail=False, methods=["get"], url_path="onboarding-options", url_name="onboarding-options")
    def onboarding_options(self, request):
        """What onboarding this deployment offers, and whether this caller may act.

        A list route, not a detail one: it is asked *before* any app exists,
        which is the only moment onboarding matters.
        """
        from tenants.services.onboarding_routes import onboarding_routes

        return Response(
            {
                "routes": onboarding_routes(),
                "can_manage": self._can_manage_apps(request),
            }
        )

    def _can_manage_apps(self, request):
        """Whether this caller could actually create an app, answered honestly.

        This exists because of #310's live bug, in which a screen was gated on
        ``wa_app.view`` while the endpoint behind it required ``wa_app.manage``:
        manager, agent and viewer each saw the link, clicked it, and were refused
        with nothing to act on. Recomputing the same rule in the client would
        reproduce it the first time the two drift, so the server says.

        Three answers, in the order the permission layer itself resolves them,
        so this cannot disagree with what the write will actually do:

        * an impersonated session is read-only (#300), whatever else is true;
        * a superuser bypasses RBAC — including the platform operator of #353,
          who holds no membership and is nonetheless the caller this whole
          screen is for;
        * everyone else is judged by their role's ``wa_app.manage`` grant.

        The impersonation test is neither of the two obvious helpers, and both
        exclusions are deliberate.

        Not ``impersonation_write_denial``, which is what the permission class
        uses: that answers "should THIS request be refused", and this request is
        a GET — a safe method, so nothing is refused, so the denial is empty and
        the superuser bypass below would advertise a create button to a session
        that cannot create. The question here is about a *different* request
        than the one being served: "if this caller posted, would it work?"

        Not ``request_is_impersonated`` either, because it falls back to
        ``request.auth`` when the claim is absent from the user — and reading
        ``request.auth`` on a DRF ``Request`` that has not been authenticated
        *runs* authentication, which with no authenticators replaces
        ``request.user`` with ``AnonymousUser``. ``impersonated_tenant_id``
        documents that hazard and avoids it for the same reason. Over HTTP the
        user is always resolved by the time this runs, so the fallback buys
        nothing here and carries a trap for any caller that reaches this method
        without going through the full stack.

        So the claim is read off ``request.user``, which is where
        ``CustomJWTAuthentication`` stamps it, and nowhere else.
        """
        from tenants.permissions import has_permission
        from users.impersonation import IMPERSONATED_BY_CLAIM

        if getattr(request.user, IMPERSONATED_BY_CLAIM, None):
            return False
        if getattr(request.user, "is_superuser", False):
            return True

        tenant_user = self._get_tenant_user()
        if tenant_user is None or tenant_user.role is None:
            return False
        return has_permission(tenant_user.role, "wa_app.manage")

    @swagger_auto_schema(
        operation_description=(
            "Return the set of capabilities supported by this WA App's BSP adapter. "
            "Capabilities are strings like 'templates', 'subscriptions', 'media_upload'."
        ),
        operation_summary="Get BSP Capabilities",
        operation_id="get_wa_app_capabilities",
        tags=["WhatsApp Apps (v2)"],
        responses={
            200: openapi.Response(
                description="BSP capabilities",
                schema=openapi.Schema(
                    type=openapi.TYPE_OBJECT,
                    properties={
                        "provider": openapi.Schema(type=openapi.TYPE_STRING),
                        "capabilities": openapi.Schema(
                            type=openapi.TYPE_ARRAY,
                            items=openapi.Schema(type=openapi.TYPE_STRING),
                        ),
                    },
                ),
            ),
            401: openapi.Response(description="Authentication required"),
            404: openapi.Response(description="WA App not found"),
        },
    )
    @action(detail=True, methods=["get"], url_path="capabilities")
    def capabilities(self, request, pk=None):
        """Return the capability set for this app's BSP adapter."""
        from wa.adapters import get_bsp_adapter

        wa_app = self.get_object()
        adapter = get_bsp_adapter(wa_app)

        return Response(
            {
                "provider": adapter.PROVIDER_NAME,
                "capabilities": sorted(adapter.CAPABILITIES),
            }
        )

    @swagger_auto_schema(
        operation_description=(
            "Check this app's stored META credentials against META, without changing anything. "
            "Three checks: the access token can read the WABA, phone_number_id is one of that WABA's "
            "numbers, and the WABA is subscribed to an app (to the configured meta_app_id, when one "
            "is set). Returns 200 when every check passes and 400 with one error per failing check, "
            "keyed on the field to correct. Re-runnable as often as needed — it reads what is stored "
            "and no credential has to be re-entered."
        ),
        operation_summary="Preflight META Credentials",
        operation_id="preflight_wa_app",
        tags=["WhatsApp Apps (v2)"],
        request_body=no_body,
        responses={
            200: openapi.Response(
                description="Every check passed",
                schema=openapi.Schema(
                    type=openapi.TYPE_OBJECT,
                    properties={
                        "ok": openapi.Schema(type=openapi.TYPE_BOOLEAN),
                        "token_source": openapi.Schema(
                            type=openapi.TYPE_STRING,
                            enum=["app", "deployment", "none"],
                            description=(
                                "Whose access token was used: the app's own, or the deployment-wide "
                                "META_PERM_TOKEN it still falls back to."
                            ),
                        ),
                        "checks": openapi.Schema(
                            type=openapi.TYPE_ARRAY,
                            items=openapi.Schema(
                                type=openapi.TYPE_OBJECT,
                                properties={
                                    "check": openapi.Schema(type=openapi.TYPE_STRING),
                                    "passed": openapi.Schema(type=openapi.TYPE_BOOLEAN),
                                    "field": openapi.Schema(type=openapi.TYPE_STRING),
                                    "detail": openapi.Schema(type=openapi.TYPE_STRING),
                                },
                            ),
                        ),
                        "observations": openapi.Schema(
                            type=openapi.TYPE_OBJECT,
                            description="What META reported in passing — the WABA name, the subscribed app ids.",
                        ),
                    },
                ),
            ),
            400: openapi.Response(description="One or more checks failed, or the app is not a META app"),
            401: openapi.Response(description="Authentication required"),
            403: openapi.Response(description="Permission denied"),
            404: openapi.Response(description="WA App not found"),
        },
    )
    @action(detail=True, methods=["post"], url_path="reveal-credential")
    def reveal_credential(self, request, pk=None):
        """Return one stored credential in plaintext, and record that it happened.

        A deliberate reversal of #289, which encrypted these columns so the
        plaintext left the database for a Graph call and nothing else. Asked for
        because an operator holding a credential they cannot see cannot tell a
        working one from a wrong one, and the masked hint (#370) answers "is one
        set" without answering "is it this".

        Three things make it defensible rather than a hole:

        * **POST, not GET.** A secret in a query string lands in access logs,
          browser history and any proxy in between; a body does not.
        * **Audited before it is returned.** The row is written first, so a
          reveal that could not be recorded does not happen. A META access token
          can send as the tenant, read their message history and rewrite their
          templates — if it is readable, "who read it, and when" has to be
          answerable.
        * **A whitelist of two fields.** A column added later is not readable by
          default, which is the direction #346 set for writes and matters more
          for reads of secrets.

        ``no-store`` because a credential must not sit in a shared cache.
        Nothing here is logged: the value is in the response body and the audit
        row names the field, never its contents.
        """
        from tenants.models import WACredentialReveal

        wa_app = self.get_object()
        field = str(request.data.get("field") or "")
        allowed = {choice for choice, _ in WACredentialReveal.FIELD_CHOICES}
        if field not in allowed:
            return Response(
                {"field": [f"Unknown credential. Expected one of: {', '.join(sorted(allowed))}."]},
                status=status.HTTP_400_BAD_REQUEST,
            )

        value = getattr(wa_app, field, "") or ""
        if not value:
            # Not an error: "nothing is stored" is a real answer, and the caller
            # needs to tell it apart from "stored but empty-looking".
            return Response({"field": field, "value": "", "is_set": False})

        actor = request.user
        WACredentialReveal.objects.create(
            wa_app=wa_app,
            tenant_name=getattr(wa_app.tenant, "name", "") or "",
            field=field,
            actor=actor if getattr(actor, "pk", None) else None,
            actor_username=getattr(actor, "username", "") or "",
        )

        response = Response({"field": field, "value": value, "is_set": True})
        response["Cache-Control"] = "no-store"
        return response

    @action(detail=True, methods=["post"], url_path="preflight")
    def preflight(self, request, pk=None):
        """Re-run the META credential checks against META on demand (#311).

        A preflight at create time catches the typo that was made that day. This
        exists because credentials go stale afterwards for reasons nothing local
        can see: a token is revoked, a WABA is moved between portfolios, someone
        unsubscribes the app. POST rather than GET because it makes outbound
        calls on the caller's behalf, and it is the only write-shaped thing here
        that writes nothing — the app is not touched, so a failing preflight
        never degrades a working app.

        Gated on ``wa_app.manage``, the same gate as the writes that stored the
        credentials: the report names the WABA and the subscribed app ids, which
        are the identifiers #251 keeps away from lower roles.
        """
        from wa.services import meta_preflight

        wa_app = self.get_object()

        if wa_app.bsp != BSPChoices.META:
            # The checks are Graph-shaped; claiming to have verified a Gupshup
            # app by not calling Meta would be worse than declining.
            return Response(
                {"bsp": f"Preflight checks META credentials; this app's BSP is {wa_app.bsp}."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        report = meta_preflight.run_meta_preflight(wa_app)
        body = report.as_dict()
        if report.ok:
            return Response(body)

        # Field errors, in the shape DRF raises them, plus the full report — the
        # passing checks are how an operator tells "wrong number id" from
        # "nothing about this app works".
        payload = dict(report.as_field_errors())
        payload["preflight"] = body
        return Response(payload, status=status.HTTP_400_BAD_REQUEST)

    @swagger_auto_schema(
        operation_description=(
            "Return the webhook setup pair for this app: the callback URL to paste into the client's own "
            "BSP dashboard, and the verify token to paste beside it. The URL carries the app's opaque "
            "webhook identifier, so deliveries to it are attributed from the URL rather than from the "
            "request body. Treat the response as a credential — whoever holds the URL can address this "
            "app's receiver."
        ),
        operation_summary="Get Webhook Setup",
        operation_id="get_wa_app_webhook_setup",
        tags=["WhatsApp Apps (v2)"],
        responses={
            200: openapi.Response(
                description="Webhook setup pair",
                schema=openapi.Schema(
                    type=openapi.TYPE_OBJECT,
                    properties={
                        "wa_app": openapi.Schema(type=openapi.TYPE_STRING),
                        "bsp": openapi.Schema(type=openapi.TYPE_STRING),
                        "callback_url": openapi.Schema(
                            type=openapi.TYPE_STRING,
                            description=(
                                "Register this exact URL with the BSP. It is also the URL this "
                                "deployment registers itself when webhooks are refreshed (#334)."
                            ),
                        ),
                        "identifier_hint": openapi.Schema(
                            type=openapi.TYPE_STRING,
                            description="Truncated identifier, safe to show in lists and logs.",
                        ),
                        "verify_token": openapi.Schema(type=openapi.TYPE_STRING),
                        "verify_token_scope": openapi.Schema(
                            type=openapi.TYPE_STRING,
                            enum=["app", "deployment", "none"],
                            description=(
                                "Whose token this is, and therefore what the handshake on this app's "
                                "own callback URL will check (#307). 'app' is this app's own token: no "
                                "other app's token completes its handshake. 'deployment' means this "
                                "app has none of its own and the instance-wide setting is what the "
                                "handshake checks — one value shared by every client on the instance. "
                                "'none' means neither is configured, so the token check is skipped."
                            ),
                        ),
                        "verify_token_configured": openapi.Schema(type=openapi.TYPE_BOOLEAN),
                    },
                ),
            ),
            401: openapi.Response(description="Authentication required"),
            403: openapi.Response(description="Permission denied"),
            404: openapi.Response(description="WA App not found"),
        },
    )
    @action(detail=True, methods=["get"], url_path="webhook-setup")
    def webhook_setup(self, request, pk=None):
        """The callback URL and verify token a client configures (#310).

        Gated on ``wa_app.manage`` rather than ``wa_app.view``: the URL contains
        the app's webhook identifier, and anyone holding it can POST at the
        app's receiver. That is a setup credential, so it belongs with the roles
        that do setup (owner/admin) and not with every role that can read the
        app list — which is the same line #251 drew for the BSP identifiers.

        ``request`` is passed down only as a fallback for building an absolute
        URL; ``DEFAULT_WEBHOOK_BASE_URL`` wins when configured, because that is
        the deployment's own statement of where it lives and the ``Host`` header
        is not.
        """
        from wa.services import webhook_identity

        wa_app = self.get_object()
        return Response(webhook_identity.webhook_setup(wa_app, request=request))

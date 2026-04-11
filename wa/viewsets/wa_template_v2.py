"""
WATemplate V2 ViewSet - BSP Agnostic Template Management

Provides CRUD operations for WhatsApp Message Templates using the canonical v2 model.
Frontend uses this to manage message templates.
"""

from django_filters import rest_framework as filters
from drf_yasg import openapi
from drf_yasg.utils import swagger_auto_schema
from rest_framework import status
from rest_framework.decorators import action
from rest_framework.parsers import MultiPartParser
from rest_framework.response import Response

from abstract.viewsets.base import BaseTenantModelViewSet
from tenants.models import TenantMedia, TenantWAApp
from wa.adapters import get_bsp_adapter
from wa.models import ButtonType, TemplateCategory, TemplateStatus, TemplateType, WATemplate
from wa.serializers import WATemplateV2ListSerializer, WATemplateV2Serializer


class WATemplateV2Filter(filters.FilterSet):
    """Filter for WATemplate v2 listing."""

    wa_app = filters.NumberFilter(field_name="wa_app__id")
    category = filters.ChoiceFilter(choices=TemplateCategory.choices)
    status = filters.ChoiceFilter(choices=TemplateStatus.choices)
    template_type = filters.ChoiceFilter(choices=TemplateType.choices)
    language_code = filters.CharFilter(lookup_expr="iexact")
    is_active = filters.BooleanFilter()
    needs_sync = filters.BooleanFilter()

    # Button filters
    has_buttons = filters.BooleanFilter(method="filter_has_buttons")
    has_quick_reply = filters.BooleanFilter(method="filter_has_quick_reply")
    has_url_button = filters.BooleanFilter(method="filter_has_url_button")
    has_call_button = filters.BooleanFilter(method="filter_has_call_button")

    def filter_has_buttons(self, queryset, name, value):
        if value:
            return queryset.exclude(buttons__isnull=True).exclude(buttons=[])
        return queryset.filter(buttons__isnull=True) | queryset.filter(buttons=[])

    def filter_has_quick_reply(self, queryset, name, value):
        if value:
            return queryset.filter(buttons__contains=[{"type": "QUICK_REPLY"}])
        return queryset

    def filter_has_url_button(self, queryset, name, value):
        if value:
            return queryset.filter(buttons__contains=[{"type": "URL"}])
        return queryset

    def filter_has_call_button(self, queryset, name, value):
        if value:
            return queryset.filter(buttons__contains=[{"type": "PHONE_NUMBER"}])
        return queryset

    class Meta:
        model = WATemplate
        fields = [
            "wa_app",
            "category",
            "status",
            "template_type",
            "language_code",
            "is_active",
            "needs_sync",
            "number",
        ]


class WATemplateV2ViewSet(BaseTenantModelViewSet):
    """
    ViewSet for managing WhatsApp Message Templates (v2).

    Provides endpoints to:
    - List templates with advanced filtering
    - Create new templates (queued for BSP sync)
    - Update template content
    - Sync templates with BSP
    - Preview template rendering

    All operations are tenant-scoped through wa_app relationship.
    """

    queryset = WATemplate.objects.select_related("wa_app").all()
    serializer_class = WATemplateV2Serializer
    filterset_class = WATemplateV2Filter
    search_fields = ["name", "element_name", "content", "header", "footer"]
    ordering_fields = ["created_at", "updated_at", "name", "element_name", "status"]
    ordering = ["-created_at"]
    required_permissions = {
        "list": "template.view",
        "retrieve": "template.view",
        "create": "template.create",
        "partial_update": "template.edit",
        "sync": "template.submit",
        "sync_from_bsp": "template.submit",
        "upload_media": "template.create",
        "preview": "template.view",
        "meta_payload": "template.view",
        "categories": "template.view",
        "types": "template.view",
        "button_types": "template.view",
        "default": "template.view",
    }

    def get_serializer_class(self):
        """Use list serializer for list action."""
        if self.action == "list":
            return WATemplateV2ListSerializer
        return WATemplateV2Serializer

    def get_queryset(self):
        """
        Custom queryset to filter by tenant through wa_app relationship.
        Prefetches tenant_media and card_media for efficient serialization.
        """
        queryset = super().get_queryset().select_related("tenant_media").prefetch_related("card_media")
        user = self.request.user

        if user.is_superuser:
            return queryset

        # Filter through wa_app -> tenant -> tenant_users -> user
        return queryset.filter(wa_app__tenant__tenant_users__user=user)

    @swagger_auto_schema(
        operation_description="List all WhatsApp templates (v2) for the tenant",
        operation_summary="List Templates",
        operation_id="list_wa_templates_v2",
        tags=["WhatsApp Templates (v2)"],
        manual_parameters=[
            openapi.Parameter(
                "wa_app",
                openapi.IN_QUERY,
                description="Filter by WA App ID",
                type=openapi.TYPE_STRING,
                format="uuid",
                required=False,
            ),
            openapi.Parameter(
                "category",
                openapi.IN_QUERY,
                description="Filter by template category",
                type=openapi.TYPE_STRING,
                enum=["AUTHENTICATION", "MARKETING", "UTILITY"],
                required=False,
            ),
            openapi.Parameter(
                "status",
                openapi.IN_QUERY,
                description="Filter by template status",
                type=openapi.TYPE_STRING,
                enum=["DRAFT", "PENDING", "APPROVED", "REJECTED", "PAUSED", "DISABLED"],
                required=False,
            ),
            openapi.Parameter(
                "template_type",
                openapi.IN_QUERY,
                description="Filter by template type",
                type=openapi.TYPE_STRING,
                enum=["TEXT", "IMAGE", "VIDEO", "DOCUMENT", "LOCATION", "AUDIO", "CAROUSEL", "CATALOG", "PRODUCT"],
                required=False,
            ),
            openapi.Parameter(
                "language_code",
                openapi.IN_QUERY,
                description="Filter by language code (e.g., en, en_US)",
                type=openapi.TYPE_STRING,
                required=False,
            ),
            openapi.Parameter(
                "has_buttons",
                openapi.IN_QUERY,
                description="Filter templates with buttons",
                type=openapi.TYPE_BOOLEAN,
                required=False,
            ),
            openapi.Parameter(
                "needs_sync",
                openapi.IN_QUERY,
                description="Filter templates needing BSP sync",
                type=openapi.TYPE_BOOLEAN,
                required=False,
            ),
            openapi.Parameter(
                "search",
                openapi.IN_QUERY,
                description="Search in name, element_name, content, header, footer",
                type=openapi.TYPE_STRING,
                required=False,
            ),
            openapi.Parameter(
                "ordering",
                openapi.IN_QUERY,
                description="Order results by field",
                type=openapi.TYPE_STRING,
                enum=["created_at", "-created_at", "name", "-name", "status", "-status"],
                required=False,
            ),
        ],
        responses={
            200: openapi.Response(description="List of templates", schema=WATemplateV2ListSerializer(many=True)),
            401: openapi.Response(description="Authentication required"),
        },
    )
    def list(self, request, *args, **kwargs):
        """List all WhatsApp templates for the tenant."""
        return super().list(request, *args, **kwargs)

    @swagger_auto_schema(
        operation_description="Create a new WhatsApp template",
        operation_summary="Create Template",
        operation_id="create_wa_template_v2",
        tags=["WhatsApp Templates (v2)"],
        request_body=WATemplateV2Serializer,
        responses={
            201: openapi.Response(description="Template created successfully", schema=WATemplateV2Serializer()),
            400: openapi.Response(description="Validation error"),
            401: openapi.Response(description="Authentication required"),
        },
    )
    def create(self, request, *args, **kwargs):
        """Create a new WhatsApp template."""
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        # Step 1 — persist as DRAFT (always saved regardless of BSP outcome).
        template = serializer.save(
            status=TemplateStatus.DRAFT,
            needs_sync=True,
        )

        # Step 1b — link carousel card TenantMedia records to card_media M2M.
        # The FE uploads card images *before* template creation (creating
        # TenantMedia records with card_index + media_id set), then sends
        # only the media_handle string per card.  We match those records
        # back to this template so the preview can resolve media URLs.
        if template.cards:
            tenant = template.wa_app.tenant
            for i, card in enumerate(template.cards):
                media_handle = card.get("media_handle")
                if media_handle:
                    tm = (
                        TenantMedia.objects.filter(
                            tenant=tenant,
                            card_index=i,
                            media_id=media_handle,
                        )
                        .order_by("-created_at")
                        .first()
                    )
                    if tm:
                        template.card_media.add(tm)

        # Step 2 — submit to BSP via adapter (META Direct / Gupshup / …).
        try:
            adapter = get_bsp_adapter(template.wa_app)
            result = adapter.submit_template(template)
            # Refresh from DB — adapter may have updated status / meta_template_id.
            template.refresh_from_db()
            # If adapter returned failure without raising, schedule retry (#386).
            if not result.success:
                from wa.tasks import retry_submit_template

                retry_submit_template.apply_async(
                    (str(template.id),),
                    countdown=60,
                )
        except NotImplementedError as exc:
            # BSP not yet supported — template stays as DRAFT, needs_sync=True.
            template.error_message = str(exc)
            template.save(update_fields=["error_message"])
        except Exception as exc:
            template.error_message = f"BSP adapter error: {exc}"
            template.save(update_fields=["error_message"])
            # Schedule auto-retry with exponential backoff (60s → 120s → 240s)
            from wa.tasks import retry_submit_template

            retry_submit_template.apply_async(
                (str(template.id),),
                countdown=60,
            )

        # Fire template_submitted notification
        try:
            from notifications.signals import create_template_submitted_notification

            create_template_submitted_notification(template)
        except Exception:
            pass

        return Response(WATemplateV2Serializer(template).data, status=status.HTTP_201_CREATED)

    @swagger_auto_schema(
        operation_description="Retrieve a specific template by ID",
        operation_summary="Get Template",
        operation_id="retrieve_wa_template_v2",
        tags=["WhatsApp Templates (v2)"],
        responses={
            200: openapi.Response(description="Template details", schema=WATemplateV2Serializer()),
            401: openapi.Response(description="Authentication required"),
            404: openapi.Response(description="Template not found"),
        },
    )
    def retrieve(self, request, *args, **kwargs):
        """Retrieve a specific template by ID."""
        return super().retrieve(request, *args, **kwargs)

    @swagger_auto_schema(
        operation_description="Partially update a template",
        operation_summary="Update Template",
        operation_id="partial_update_wa_template_v2",
        tags=["WhatsApp Templates (v2)"],
        request_body=WATemplateV2Serializer,
        responses={
            200: openapi.Response(description="Template updated successfully", schema=WATemplateV2Serializer()),
            400: openapi.Response(description="Validation error"),
            401: openapi.Response(description="Authentication required"),
            404: openapi.Response(description="Template not found"),
        },
    )
    def partial_update(self, request, *args, **kwargs):
        """Partially update a template."""
        instance = self.get_object()
        serializer = self.get_serializer(instance, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)

        # Mark for re-sync if content changed
        content_fields = ["content", "header", "footer", "buttons", "cards"]
        if any(field in request.data for field in content_fields):
            serializer.save(needs_sync=True)
        else:
            serializer.save()

        return Response(serializer.data)

    @swagger_auto_schema(
        operation_description="Trigger sync of template to BSP",
        operation_summary="Sync Template",
        operation_id="sync_wa_template_v2",
        tags=["WhatsApp Templates (v2)"],
        responses={
            200: openapi.Response(
                description="Template queued for sync",
                schema=openapi.Schema(
                    type=openapi.TYPE_OBJECT,
                    properties={
                        "message": openapi.Schema(type=openapi.TYPE_STRING),
                        "status": openapi.Schema(type=openapi.TYPE_STRING),
                    },
                ),
            ),
            401: openapi.Response(description="Authentication required"),
            404: openapi.Response(description="Template not found"),
        },
    )
    @action(detail=True, methods=["post"], url_path="sync")
    def sync(self, request, pk=None):
        """Submit template to BSP via adapter."""
        template = self.get_object()

        try:
            adapter = get_bsp_adapter(template.wa_app)
            result = adapter.submit_template(template)
            template.refresh_from_db()

            if result.success:
                return Response(
                    {
                        "message": f"Template submitted to {result.provider}",
                        "status": template.status,
                        "needs_sync": template.needs_sync,
                        "meta_template_id": template.meta_template_id,
                    }
                )
            else:
                return Response(
                    {
                        "message": f"Submission to {result.provider} failed",
                        "status": template.status,
                        "needs_sync": template.needs_sync,
                        "error": result.error_message,
                    },
                    status=status.HTTP_502_BAD_GATEWAY,
                )
        except NotImplementedError as exc:
            return Response(
                {
                    "message": str(exc),
                    "status": template.status,
                    "needs_sync": True,
                },
                status=status.HTTP_501_NOT_IMPLEMENTED,
            )
        except Exception as exc:
            return Response(
                {
                    "message": f"Adapter error: {exc}",
                    "status": template.status,
                    "needs_sync": True,
                },
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

    # ── Sync from BSP ────────────────────────────────────────────────────

    @swagger_auto_schema(
        operation_description=(
            "Bulk-sync all templates from the BSP (e.g. Gupshup) into the local "
            "database.\n\n"
            "Templates are matched by ``(wa_app, element_name, language_code)``.\n\n"
            "- **New** templates are created with all mapped fields.\n"
            "- **Changed** templates are updated (status, content, buttons, etc.).\n"
            "- **Unchanged** templates are skipped.\n\n"
            "Safe to call multiple times — fully idempotent."
        ),
        operation_summary="Sync Templates from BSP",
        operation_id="sync_from_bsp_wa_template_v2",
        tags=["WhatsApp Templates (v2)"],
        request_body=openapi.Schema(
            type=openapi.TYPE_OBJECT,
            required=["wa_app_id"],
            properties={
                "wa_app_id": openapi.Schema(
                    type=openapi.TYPE_INTEGER,
                    description="ID of the TenantWAApp to sync templates for",
                ),
            },
        ),
        responses={
            200: openapi.Response(
                description="Sync summary",
                schema=openapi.Schema(
                    type=openapi.TYPE_OBJECT,
                    properties={
                        "created": openapi.Schema(type=openapi.TYPE_INTEGER),
                        "updated": openapi.Schema(type=openapi.TYPE_INTEGER),
                        "skipped": openapi.Schema(type=openapi.TYPE_INTEGER),
                        "failed": openapi.Schema(type=openapi.TYPE_INTEGER),
                        "errors": openapi.Schema(
                            type=openapi.TYPE_ARRAY,
                            items=openapi.Schema(type=openapi.TYPE_STRING),
                        ),
                        "total_from_bsp": openapi.Schema(type=openapi.TYPE_INTEGER),
                    },
                ),
            ),
            400: openapi.Response(description="Missing wa_app_id"),
            404: openapi.Response(description="WAApp not found"),
        },
    )
    @action(detail=False, methods=["post"], url_path="sync-from-bsp")
    def sync_from_bsp(self, request):
        """Bulk-sync all templates from the BSP into the local database."""
        from wa.services.template_sync import sync_templates_from_bsp

        wa_app_id = request.data.get("wa_app_id")
        if not wa_app_id:
            return Response(
                {"message": "wa_app_id is required"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            wa_app = TenantWAApp.objects.get(pk=wa_app_id)
        except TenantWAApp.DoesNotExist:
            return Response(
                {"message": f"WAApp with id={wa_app_id} not found"},
                status=status.HTTP_404_NOT_FOUND,
            )

        # Verify user has access to this wa_app's tenant
        if not request.user.is_superuser:
            has_access = wa_app.tenant.tenant_users.filter(user=request.user).exists()
            if not has_access:
                return Response(
                    {"message": "You do not have access to this WAApp"},
                    status=status.HTTP_403_FORBIDDEN,
                )

        dry_run = str(request.data.get("dry_run", "")).lower() in ("true", "1", "yes")
        summary = sync_templates_from_bsp(wa_app, dry_run=dry_run)
        return Response(summary, status=status.HTTP_200_OK)

    # ── Media upload ─────────────────────────────────────────────────────

    @swagger_auto_schema(
        operation_description=(
            "Upload a media file to the BSP and store the returned handle ID\n\n"
            "**For IMAGE / VIDEO / DOCUMENT templates:** the handle is saved to "
            "``media_handle`` on the template itself.\n\n"
            "**For CAROUSEL templates:** pass ``card_index`` (0-based) to store "
            "the handle in the matching card's ``media_handle``.\n\n"
            "After uploading, call ``/sync`` to submit the template."
        ),
        operation_summary="Upload Media",
        operation_id="upload_media_wa_template_v2",
        tags=["WhatsApp Templates (v2)"],
        manual_parameters=[
            openapi.Parameter(
                "file",
                openapi.IN_FORM,
                description="Media file (image, video, or document)",
                type=openapi.TYPE_FILE,
                required=True,
            ),
            openapi.Parameter(
                "card_index",
                openapi.IN_FORM,
                description="0-based card index for CAROUSEL templates",
                type=openapi.TYPE_INTEGER,
                required=False,
            ),
        ],
        responses={
            200: openapi.Response(
                description="Media uploaded, handle stored",
                schema=openapi.Schema(
                    type=openapi.TYPE_OBJECT,
                    properties={
                        "handle_id": openapi.Schema(type=openapi.TYPE_STRING),
                        "provider": openapi.Schema(type=openapi.TYPE_STRING),
                        "stored_on": openapi.Schema(
                            type=openapi.TYPE_STRING,
                            description="'template' or 'card:<index>'",
                        ),
                    },
                ),
            ),
            400: openapi.Response(description="No file provided"),
            502: openapi.Response(description="BSP upload failed"),
        },
    )
    @action(detail=True, methods=["post"], url_path="upload-media", parser_classes=[MultiPartParser])
    def upload_media(self, request, pk=None):
        """
        Upload a media file to the BSP and store the handle on the template.

        1. Upload file → BSP → handle_id
        2. Save handle_id on template.media_handle (or card.media_handle)
        3. Return the handle_id
        """
        template = self.get_object()
        uploaded_file = request.FILES.get("file")

        if not uploaded_file:
            return Response(
                {"error": 'No file provided. Send a file in the "file" form field.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # Upload via BSP adapter
        try:
            adapter = get_bsp_adapter(template.wa_app)

            if not adapter.supports("media_upload"):
                return Response(
                    {
                        "error": (f"{adapter.PROVIDER_NAME} does not support media upload via API."),
                    },
                    status=status.HTTP_501_NOT_IMPLEMENTED,
                )

            result = adapter.upload_media(
                file_obj=uploaded_file,
                filename=uploaded_file.name,
                file_type=uploaded_file.content_type,
            )
        except NotImplementedError as exc:
            return Response(
                {"error": str(exc)},
                status=status.HTTP_501_NOT_IMPLEMENTED,
            )
        except Exception as exc:
            return Response(
                {"error": f"Adapter error: {exc}"},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

        if not result.success:
            return Response(
                {"error": result.error_message, "raw_response": result.raw_response},
                status=status.HTTP_502_BAD_GATEWAY,
            )

        handle_id = result.data.get("handle_id")
        card_index = request.data.get("card_index")
        stored_on = "template"

        # Persist a local TenantMedia record so the frontend can preview the file
        tenant = template.wa_app.tenant
        tenant_media = TenantMedia.objects.create(
            tenant=tenant,
            media=uploaded_file,
            card_index=int(card_index) if card_index is not None else None,
            wa_handle_id={"handleId": handle_id} if handle_id else None,
        )

        if card_index is not None:
            # Store on a specific carousel card
            card_index = int(card_index)
            cards = template.cards or []
            if card_index < 0 or card_index >= len(cards):
                return Response(
                    {"error": f"card_index {card_index} out of range (template has {len(cards)} cards)"},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            cards[card_index]["media_handle"] = handle_id
            template.cards = cards
            template.save(update_fields=["cards"])
            # Replace any existing card_media at this index
            template.card_media.filter(card_index=card_index).delete()
            template.card_media.add(tenant_media)
            stored_on = f"card:{card_index}"
        else:
            # Store on the template itself
            template.media_handle = handle_id
            template.tenant_media = tenant_media
            template.save(update_fields=["media_handle", "tenant_media"])

        media_url = request.build_absolute_uri(tenant_media.media.url)

        return Response(
            {
                "handle_id": handle_id,
                "provider": result.provider,
                "stored_on": stored_on,
                "tenant_media_id": str(tenant_media.id),
                "tenant_media_url": media_url,
            }
        )

    @swagger_auto_schema(
        operation_description="Preview template with sample data",
        operation_summary="Preview Template",
        operation_id="preview_wa_template_v2",
        tags=["WhatsApp Templates (v2)"],
        request_body=openapi.Schema(
            type=openapi.TYPE_OBJECT,
            properties={
                "params": openapi.Schema(
                    type=openapi.TYPE_OBJECT,
                    description="Parameter values for placeholders",
                    additional_properties=openapi.Schema(type=openapi.TYPE_STRING),
                ),
            },
        ),
        responses={
            200: openapi.Response(
                description="Rendered template preview",
                schema=openapi.Schema(
                    type=openapi.TYPE_OBJECT,
                    properties={
                        "header": openapi.Schema(type=openapi.TYPE_STRING),
                        "body": openapi.Schema(type=openapi.TYPE_STRING),
                        "footer": openapi.Schema(type=openapi.TYPE_STRING),
                        "buttons": openapi.Schema(
                            type=openapi.TYPE_ARRAY, items=openapi.Schema(type=openapi.TYPE_OBJECT)
                        ),
                    },
                ),
            ),
            401: openapi.Response(description="Authentication required"),
            404: openapi.Response(description="Template not found"),
        },
    )
    @action(detail=True, methods=["post"], url_path="preview")
    def preview(self, request, pk=None):
        """Preview template with sample data."""

        template = self.get_object()
        params = request.data.get("params", {})

        def replace_placeholders(text, params):
            if not text:
                return text

            # Replace named placeholders {{name}} with values
            for key, value in params.items():
                text = text.replace(f"{{{{{key}}}}}", str(value))

            # Replace numbered placeholders {{1}}, {{2}} etc.
            for i in range(1, 10):
                text = text.replace(f"{{{{{i}}}}}", params.get(str(i), f"[{i}]"))

            return text

        return Response(
            {
                "header": replace_placeholders(template.header, params),
                "body": replace_placeholders(template.content, params),
                "footer": template.footer,
                "buttons": template.buttons or [],
            }
        )

    @swagger_auto_schema(
        operation_description="Get META API payload for template submission",
        operation_summary="Get META Payload",
        operation_id="meta_payload_wa_template_v2",
        tags=["WhatsApp Templates (v2)"],
        responses={
            200: openapi.Response(
                description="META API payload",
                schema=openapi.Schema(
                    type=openapi.TYPE_OBJECT, description="Template payload formatted for META Graph API"
                ),
            ),
            401: openapi.Response(description="Authentication required"),
            404: openapi.Response(description="Template not found"),
        },
    )
    @action(detail=True, methods=["get"], url_path="meta-payload")
    def meta_payload(self, request, pk=None):
        """Get META API payload for template submission."""
        template = self.get_object()
        return Response(template.to_meta_payload())

    @swagger_auto_schema(
        operation_description="Get available template categories",
        operation_summary="Get Categories",
        operation_id="categories_wa_template_v2",
        tags=["WhatsApp Templates (v2)"],
        responses={
            200: openapi.Response(
                description="Available template categories",
                schema=openapi.Schema(
                    type=openapi.TYPE_ARRAY,
                    items=openapi.Schema(
                        type=openapi.TYPE_OBJECT,
                        properties={
                            "value": openapi.Schema(type=openapi.TYPE_STRING),
                            "label": openapi.Schema(type=openapi.TYPE_STRING),
                        },
                    ),
                ),
            ),
        },
    )
    @action(detail=False, methods=["get"], url_path="categories")
    def categories(self, request):
        """Get available template categories."""
        return Response([{"value": choice[0], "label": choice[1]} for choice in TemplateCategory.choices])

    @swagger_auto_schema(
        operation_description="Get available template types",
        operation_summary="Get Template Types",
        operation_id="types_wa_template_v2",
        tags=["WhatsApp Templates (v2)"],
        responses={
            200: openapi.Response(
                description="Available template types",
                schema=openapi.Schema(
                    type=openapi.TYPE_ARRAY,
                    items=openapi.Schema(
                        type=openapi.TYPE_OBJECT,
                        properties={
                            "value": openapi.Schema(type=openapi.TYPE_STRING),
                            "label": openapi.Schema(type=openapi.TYPE_STRING),
                        },
                    ),
                ),
            ),
        },
    )
    @action(detail=False, methods=["get"], url_path="types")
    def types(self, request):
        """Get available template types.

        Returns each type with an ``enabled`` flag.  CATALOG and PRODUCT are
        disabled when the tenant's WhatsApp Business Account does not have
        Meta Commerce Manager enabled.
        """
        # Determine commerce-manager status for the requesting user's WA app.
        commerce_enabled = False
        COMMERCE_TYPES = {"CATALOG", "PRODUCT"}
        COMMERCE_DISABLED_REASON = "Available after Meta Commerce Manager onboarding is complete."

        try:
            wa_app = TenantWAApp.objects.filter(tenant__tenant_users__user=request.user).first()
            if wa_app:
                commerce_enabled = wa_app.is_commerce_manager_enabled
        except Exception:
            commerce_enabled = False

        result = []
        for value, label in TemplateType.choices:
            entry: dict = {"value": value, "label": label, "enabled": True}
            if value in COMMERCE_TYPES and not commerce_enabled:
                entry["enabled"] = False
                entry["disabled_reason"] = COMMERCE_DISABLED_REASON
            result.append(entry)

        return Response(result)

    # ── All button definitions with examples ──────────────────────────────
    _ALL_BUTTONS = {
        "QUICK_REPLY": {
            "type": ButtonType.QUICK_REPLY,
            "label": "Quick Reply",
            "example": {"type": "QUICK_REPLY", "text": "Yes, I agree"},
        },
        "URL": {
            "type": ButtonType.URL,
            "label": "URL",
            "example": {"type": "URL", "text": "Visit Website", "url": "https://example.com/{{1}}"},
        },
        "PHONE_NUMBER": {
            "type": ButtonType.PHONE_NUMBER,
            "label": "Phone Number",
            "example": {"type": "PHONE_NUMBER", "text": "Call Support", "phone_number": "+1234567890"},
        },
        "COPY_CODE": {
            "type": ButtonType.COPY_CODE,
            "label": "Copy Code",
            "example": {"type": "COPY_CODE", "text": "Copy code"},
        },
        "FLOW": {
            "type": ButtonType.FLOW,
            "label": "Flow",
            "example": {"type": "FLOW", "text": "Start Flow"},
        },
        "CALL_REQUEST": {
            "type": ButtonType.CALL_REQUEST,
            "label": "Call Request",
            "example": {"type": "CALL_REQUEST", "text": "Request a call"},
        },
        "CATALOG": {
            "type": ButtonType.CATALOG,
            "label": "View Catalog",
            "example": {"type": "CATALOG", "text": "View catalog"},
        },
        "OTP": {
            "type": ButtonType.OTP,
            "label": "OTP",
            "example": {"type": "OTP", "otp_type": "copy_code", "text": "Copy Code"},
        },
    }

    # ── Button rules per (category, sub_category) ────────────────────────
    # Keys: allowed button type names.  'card_buttons' for card-level types.
    _BUTTON_RULES = {
        # ── MARKETING ─────────────────────────────────────────────────
        ("MARKETING", "STANDARD"): {
            "template_buttons": ["QUICK_REPLY", "URL", "PHONE_NUMBER", "CALL_REQUEST"],
            "max_total": 10,
        },
        ("MARKETING", "CAROUSEL"): {
            "template_buttons": [],  # no template-level buttons
            "card_buttons": ["QUICK_REPLY", "URL", "PHONE_NUMBER"],
            "max_card_buttons": 2,
            "max_total": 0,
        },
        ("MARKETING", "CATALOG"): {
            "template_buttons": ["CATALOG", "URL", "PHONE_NUMBER", "QUICK_REPLY"],
            "required": ["CATALOG"],
            "max_total": 10,
        },
        ("MARKETING", "COUPON_CODE"): {
            "template_buttons": ["COPY_CODE", "QUICK_REPLY"],
            "required": ["COPY_CODE"],
            "max_total": 4,
        },
        ("MARKETING", "LIMITED_TIME_OFFER"): {
            "template_buttons": ["COPY_CODE", "URL"],
            "required": ["COPY_CODE"],
            "max_total": 3,
        },
        ("MARKETING", "MPM"): {
            "template_buttons": [],
            "max_total": 0,
        },
        ("MARKETING", "SPM"): {
            "template_buttons": [],
            "max_total": 0,
        },
        ("MARKETING", "PRODUCT_CARD_CAROUSEL"): {
            "template_buttons": [],
            "card_buttons": ["QUICK_REPLY", "URL"],
            "max_card_buttons": 2,
            "max_total": 0,
        },
        # ── UTILITY ───────────────────────────────────────────────────
        ("UTILITY", "STANDARD"): {
            "template_buttons": ["QUICK_REPLY", "URL", "PHONE_NUMBER", "COPY_CODE", "FLOW", "CALL_REQUEST"],
            "max_total": 10,
        },
        ("UTILITY", "ORDER_STATUS"): {
            "template_buttons": ["URL", "QUICK_REPLY", "PHONE_NUMBER"],
            "max_total": 3,
        },
        ("UTILITY", "CHECKOUT_BUTTON"): {
            "template_buttons": ["URL", "QUICK_REPLY", "COPY_CODE"],
            "max_total": 3,
        },
        # ── AUTHENTICATION ────────────────────────────────────────────
        ("AUTHENTICATION", "STANDARD"): {
            "template_buttons": ["OTP"],
            "required": ["OTP"],
            "max_total": 1,
        },
    }

    @swagger_auto_schema(
        operation_description=(
            "Get available button types for templates.\n\n"
            "**Query parameters:**\n"
            "- `category` (optional): MARKETING, UTILITY, or AUTHENTICATION\n"
            "- `sub_category` (optional): e.g. STANDARD, CAROUSEL, CATALOG, etc.\n\n"
            "When no params are supplied, returns **all** button types.\n"
            "When category/sub_category are supplied, returns only the allowed types "
            "for that combination, including card-level buttons for CAROUSEL types."
        ),
        operation_summary="Get Button Types",
        operation_id="button_types_wa_template_v2",
        tags=["WhatsApp Templates (v2)"],
        manual_parameters=[
            openapi.Parameter(
                "category",
                openapi.IN_QUERY,
                type=openapi.TYPE_STRING,
                description="Template category (MARKETING, UTILITY, AUTHENTICATION)",
                required=False,
            ),
            openapi.Parameter(
                "sub_category",
                openapi.IN_QUERY,
                type=openapi.TYPE_STRING,
                description="Template sub-category (STANDARD, CAROUSEL, CATALOG, etc.)",
                required=False,
            ),
        ],
        responses={
            200: openapi.Response(
                description="Available button types with examples",
                schema=openapi.Schema(
                    type=openapi.TYPE_OBJECT,
                    properties={
                        "template_buttons": openapi.Schema(
                            type=openapi.TYPE_ARRAY,
                            items=openapi.Schema(type=openapi.TYPE_OBJECT),
                        ),
                        "card_buttons": openapi.Schema(
                            type=openapi.TYPE_ARRAY,
                            items=openapi.Schema(type=openapi.TYPE_OBJECT),
                            description="Only present for CAROUSEL / PRODUCT_CARD_CAROUSEL",
                        ),
                        "max_total": openapi.Schema(type=openapi.TYPE_INTEGER),
                        "max_card_buttons": openapi.Schema(type=openapi.TYPE_INTEGER),
                        "required": openapi.Schema(
                            type=openapi.TYPE_ARRAY,
                            items=openapi.Schema(type=openapi.TYPE_STRING),
                        ),
                    },
                ),
            ),
        },
    )
    @action(detail=False, methods=["get"], url_path="button-types")
    def button_types(self, request):
        """
        Get available button types with examples.

        Without query params → returns every button type (flat list for
        backward compatibility).

        With ?category=X  or  ?category=X&sub_category=Y → returns a
        structured response with template_buttons, card_buttons (if
        applicable), max_total, required, etc.
        """
        category = (request.query_params.get("category") or "").upper()
        sub_category = (request.query_params.get("sub_category") or "STANDARD").upper()

        # ── No category filter → flat list of all types (backward compat) ─
        if not category:
            return Response(list(self._ALL_BUTTONS.values()))

        # ── Look up rules ─────────────────────────────────────────────────
        rules = self._BUTTON_RULES.get((category, sub_category))
        if rules is None:
            # Fallback: try (category, STANDARD)
            rules = self._BUTTON_RULES.get((category, "STANDARD"))
        if rules is None:
            return Response(
                {"error": f"Unknown category/sub_category: {category}/{sub_category}"},
                status=400,
            )

        # Build template-level button list
        template_buttons = [
            self._ALL_BUTTONS[bt] for bt in rules.get("template_buttons", []) if bt in self._ALL_BUTTONS
        ]

        result = {
            "template_buttons": template_buttons,
            "max_total": rules.get("max_total", 10),
        }

        # Card-level buttons (CAROUSEL, PRODUCT_CARD_CAROUSEL)
        if "card_buttons" in rules:
            result["card_buttons"] = [self._ALL_BUTTONS[bt] for bt in rules["card_buttons"] if bt in self._ALL_BUTTONS]
            result["max_card_buttons"] = rules.get("max_card_buttons", 2)

        # Required button types
        if "required" in rules:
            result["required"] = rules["required"]

        return Response(result)

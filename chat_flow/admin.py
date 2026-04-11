import json
import logging

from django import forms
from django.contrib import admin, messages
from django.db.models import Count, Q
from django.shortcuts import redirect, render
from django.urls import path, reverse
from django.utils.html import format_html

from .models import ChatFlow, ChatFlowEdge, ChatFlowNode, UserChatFlowSession

logger = logging.getLogger(__name__)


# =============================================================================
# Forms for admin actions
# =============================================================================


class ContactChoiceField(forms.ModelChoiceField):
    """Shows phone + name in the dropdown instead of 'TenantContact object (N)'."""

    def label_from_instance(self, obj):
        name = f"{obj.first_name} {obj.last_name}".strip()
        if name:
            return f"{obj.phone} — {name}"
        return str(obj.phone)


class StartTestSessionForm(forms.Form):
    """Form displayed on the intermediate page to pick a contact."""

    contact = ContactChoiceField(
        queryset=None,  # set dynamically
        label="Contact",
        help_text="Select the contact to attach to this flow. A new session will be created at the start node.",
    )
    context_json = forms.CharField(
        required=False,
        widget=forms.Textarea(attrs={"rows": 3, "cols": 60, "placeholder": '{"key": "value"}'}),
        label="Initial context (JSON)",
        help_text="Optional JSON context data to pass into the session.",
    )

    def clean_context_json(self):
        raw = self.cleaned_data.get("context_json", "").strip()
        if not raw:
            return {}
        try:
            data = json.loads(raw)
            if not isinstance(data, dict):
                raise forms.ValidationError("Context must be a JSON object (dict).")
            return data
        except json.JSONDecodeError as e:
            raise forms.ValidationError(f"Invalid JSON: {e}")


# =============================================================================
# Inline admins
# =============================================================================


class ChatFlowNodeInline(admin.TabularInline):
    model = ChatFlowNode
    extra = 0
    show_change_link = True
    fields = ("node_id", "node_type", "template", "position_x", "position_y", "node_summary")
    readonly_fields = ("node_summary",)

    def node_summary(self, obj):
        """Quick summary of the node's config."""
        if not obj.pk:
            return "-"
        data = obj.node_data or {}
        parts = []
        # Template name
        if obj.template:
            parts.append(f"📋 {obj.template.element_name or obj.template_id}")
        # Buttons count
        buttons = data.get("buttons", [])
        if buttons:
            parts.append(f"🔘 {len(buttons)} btn")
        # Message content preview
        body = data.get("message_content") or data.get("body") or ""
        if body:
            parts.append(f'💬 "{body[:40]}…"' if len(body) > 40 else f'💬 "{body}"')
        return " · ".join(parts) if parts else "—"

    node_summary.short_description = "Summary"


class ChatFlowEdgeInline(admin.TabularInline):
    model = ChatFlowEdge
    fk_name = "flow"
    extra = 0
    show_change_link = True
    fields = ("edge_id", "source_node", "target_node", "button_text", "button_type", "edge_routing")
    readonly_fields = ("edge_routing",)

    def edge_routing(self, obj):
        if not obj.pk:
            return "-"
        data = obj.edge_data or {}
        handle = data.get("sourceHandle", "")
        el_type = data.get("element_type", "")
        if el_type == "ROUTING":
            label = data.get("routing_label", handle)
            return format_html('<span style="color:#0369a1;">🔀 {}</span>', label)
        if el_type == "PASSTHROUGH":
            return format_html('<span style="color:#6b7280;">⏩ passthrough</span>')
        return handle or "—"

    edge_routing.short_description = "Routing"


# =============================================================================
# Helpers: validate a ChatFlow from its stored flow_data
# =============================================================================


def _run_validation(flow: ChatFlow):
    """Run the rule-engine validator on a flow's flow_data and return the result."""
    try:
        from .rules import FlowValidatorService

        validator = FlowValidatorService()
        return validator.validate(flow.flow_data or {})
    except Exception:
        logger.exception("Admin validation failed for flow %s", flow.pk)
        return None


# =============================================================================
# ChatFlow Admin
# =============================================================================


@admin.register(ChatFlow)
class ChatFlowAdmin(admin.ModelAdmin):
    list_display = (
        "name",
        "tenant",
        "is_active",
        "node_count",
        "edge_count",
        "health_badge",
        "session_count",
        "active_session_count",
        "created_at",
        "updated_at",
    )
    list_filter = ("is_active", "tenant", "created_at")
    search_fields = ("name", "description", "start_template__element_name")
    readonly_fields = (
        "created_at",
        "updated_at",
        "health_report",
        "flow_graph_preview",
        "node_count",
        "edge_count",
        "session_count",
        "active_session_count",
        "test_session_link",
    )
    actions = ["validate_selected_flows", "activate_flows", "deactivate_flows", "start_test_session"]

    # ------------------------------------------------------------------
    # Custom URLs for test-session intermediate page
    # ------------------------------------------------------------------

    def get_urls(self):
        urls = super().get_urls()
        custom = [
            path(
                "<int:flow_id>/start-test-session/",
                self.admin_site.admin_view(self.start_test_session_view),
                name="chatflow_start_test_session",
            ),
        ]
        return custom + urls

    fieldsets = (
        (
            None,
            {
                "fields": ("name", "description", "created_by", "tenant"),
            },
        ),
        (
            "Flow Configuration",
            {
                "fields": ("start_template", "is_active"),
            },
        ),
        (
            "🩺 Health & Validation",
            {
                "fields": ("health_report",),
                "description": "Runs all validation rules against the saved flow_data and reports errors/warnings.",
            },
        ),
        (
            "🧪 Testing",
            {
                "fields": ("test_session_link",),
                "description": "Start a test session by attaching a contact to this flow.",
            },
        ),
        (
            "📊 Flow Graph Preview",
            {
                "fields": ("flow_graph_preview",),
                "classes": ("collapse",),
                "description": "A text-based summary of nodes and edges stored in flow_data.",
            },
        ),
        (
            "Raw Flow Data",
            {
                "fields": ("flow_data",),
                "classes": ("collapse",),
            },
        ),
        (
            "Timestamps",
            {
                "fields": ("created_at", "updated_at"),
                "classes": ("collapse",),
            },
        ),
    )

    inlines = [ChatFlowNodeInline, ChatFlowEdgeInline]

    def get_queryset(self, request):
        qs = super().get_queryset(request)
        return qs.annotate(
            _node_count=Count("nodes", distinct=True),
            _edge_count=Count("edges", distinct=True),
            _session_count=Count("sessions", distinct=True),
            _active_session_count=Count("sessions", filter=Q(sessions__is_active=True), distinct=True),
        )

    # ------------------------------------------------------------------
    # Computed list columns
    # ------------------------------------------------------------------

    def node_count(self, obj):
        return getattr(obj, "_node_count", obj.nodes.count())

    node_count.short_description = "Nodes"
    node_count.admin_order_field = "_node_count"

    def edge_count(self, obj):
        return getattr(obj, "_edge_count", obj.edges.count())

    edge_count.short_description = "Edges"
    edge_count.admin_order_field = "_edge_count"

    def session_count(self, obj):
        return getattr(obj, "_session_count", obj.sessions.count())

    session_count.short_description = "Sessions"
    session_count.admin_order_field = "_session_count"

    def active_session_count(self, obj):
        return getattr(obj, "_active_session_count", obj.sessions.filter(is_active=True).count())

    active_session_count.short_description = "Active Sessions"
    active_session_count.admin_order_field = "_active_session_count"

    def health_badge(self, obj):
        """Coloured badge in the list view: ✅ / ⚠️ / ❌ / ➖."""
        flow_data = obj.flow_data
        if not flow_data or not flow_data.get("nodes"):
            return format_html('<span title="No flow data">➖</span>')
        result = _run_validation(obj)
        if result is None:
            return format_html('<span title="Validation crashed">💥</span>')
        errs = len(result.errors)
        warns = len(result.warnings)
        if errs:
            return format_html(
                '<span style="color:#dc2626;" title="{} error(s), {} warning(s)">❌ {}</span>',
                errs,
                warns,
                errs,
            )
        if warns:
            return format_html(
                '<span style="color:#d97706;" title="{} warning(s)">⚠️ {}</span>',
                warns,
                warns,
            )
        return format_html('<span style="color:#16a34a;" title="All rules pass">✅</span>')

    health_badge.short_description = "Health"

    # ------------------------------------------------------------------
    # Detail-page readonly fields
    # ------------------------------------------------------------------

    def health_report(self, obj):
        """Full validation report rendered as styled HTML on the detail page."""
        if not obj.pk:
            return "Save the flow first."
        flow_data = obj.flow_data
        if not flow_data or not flow_data.get("nodes"):
            return format_html(
                '<div style="padding:12px;background:#fef9c3;border-radius:8px;">'
                "⚠️ This flow has no flow_data (or no nodes). Nothing to validate.</div>"
            )

        result = _run_validation(obj)
        if result is None:
            return format_html(
                '<div style="padding:12px;background:#fee2e2;border-radius:8px;">'
                "💥 Validation crashed — check server logs.</div>"
            )

        if result.is_valid and not result.warnings:
            return format_html(
                '<div style="padding:12px;background:#dcfce7;border-radius:8px;">'
                "✅ <strong>All rules pass.</strong> No errors or warnings.</div>"
            )

        rows = []
        for v in result.violations:
            sev = v.severity.value if hasattr(v.severity, "value") else v.severity
            if sev == "error":
                icon, colour = "❌", "#dc2626"
            elif sev == "warning":
                icon, colour = "⚠️", "#d97706"
            else:
                icon, colour = "ℹ️", "#2563eb"

            node_info = f" &nbsp;· node <code>{v.node_id}</code>" if v.node_id else ""
            edge_info = f" &nbsp;· edge <code>{v.edge_id}</code>" if v.edge_id else ""
            details_str = ""
            if v.details:
                details_str = f'<br/><small style="color:#6b7280;">{json.dumps(v.details, default=str)[:200]}</small>'

            rows.append(
                f"<tr>"
                f'<td style="padding:6px 8px;white-space:nowrap;">{icon}</td>'
                f'<td style="padding:6px 8px;"><code style="color:{colour};font-weight:600;">{v.rule_id}</code></td>'
                f'<td style="padding:6px 8px;">{v.message}{node_info}{edge_info}{details_str}</td>'
                f"</tr>"
            )

        summary_parts = []
        if result.errors:
            summary_parts.append(f'<span style="color:#dc2626;font-weight:700;">{len(result.errors)} error(s)</span>')
        if result.warnings:
            summary_parts.append(
                f'<span style="color:#d97706;font-weight:700;">{len(result.warnings)} warning(s)</span>'
            )
        if result.info:
            summary_parts.append(f'<span style="color:#2563eb;">{len(result.info)} info</span>')
        summary = " &nbsp;·&nbsp; ".join(summary_parts)

        table = (
            f'<div style="margin-bottom:8px;">{summary}</div>'
            f'<table style="border-collapse:collapse;width:100%;font-size:13px;">'
            f'<thead><tr style="background:#f3f4f6;">'
            f'<th style="padding:6px 8px;text-align:left;width:30px;"></th>'
            f'<th style="padding:6px 8px;text-align:left;width:100px;">Rule</th>'
            f'<th style="padding:6px 8px;text-align:left;">Message</th>'
            f"</tr></thead><tbody>{''.join(rows)}</tbody></table>"
        )

        bg = "#fee2e2" if result.errors else "#fef9c3"
        return format_html(
            '<div style="padding:12px;background:{};border-radius:8px;overflow-x:auto;">{}</div>',
            bg,
            table,
        )

    health_report.short_description = "Validation Report"

    def test_session_link(self, obj):
        """Render a button that links to the 'start test session' intermediate page."""
        if not obj.pk:
            return "Save the flow first."

        url = reverse("admin:chatflow_start_test_session", args=[obj.pk])
        active_sessions = obj.sessions.filter(is_active=True).count()

        parts = [
            f'<a href="{url}" style="'
            f"display:inline-block;padding:8px 20px;background:#7c3aed;color:#fff;"
            f'border-radius:6px;text-decoration:none;font-weight:600;font-size:13px;"'
            f">🧪 Start Test Session</a>",
        ]

        if active_sessions:
            parts.append(
                f'&nbsp;&nbsp;<span style="color:#d97706;font-size:12px;">'
                f"⚡ {active_sessions} active session(s) right now</span>"
            )

        return format_html(" ".join(parts))

    test_session_link.short_description = "Test Flow"

    def flow_graph_preview(self, obj):
        """Text-based summary of the flow graph from flow_data JSON."""
        flow_data = obj.flow_data
        if not flow_data:
            return "No flow data."

        nodes = flow_data.get("nodes", [])
        edges = flow_data.get("edges", [])
        if not nodes:
            return "Empty graph (no nodes)."

        # Build node map
        node_map = {}
        for n in nodes:
            nid = n.get("id", "?")
            data = n.get("data", {})
            ntype = data.get("nodeType", n.get("type", "?"))
            label = data.get("label", ntype)
            node_map[nid] = {"type": ntype, "label": label}

        lines = ['<pre style="font-size:12px;line-height:1.6;max-height:400px;overflow:auto;">']

        # Nodes
        lines.append(f"<strong>NODES ({len(nodes)})</strong>")
        for n in nodes:
            nid = n.get("id", "?")
            info = node_map.get(nid, {})
            ntype = info.get("type", "?")
            label = info.get("label", "?")
            icon = {
                "start": "🟢",
                "end": "🔴",
                "template": "📋",
                "message": "💬",
                "condition": "🔀",
                "delay": "⏱️",
                "api": "🌐",
                "handoff": "🤝",
                "action": "⚡",
            }.get(ntype, "📦")
            lines.append(f'  {icon} [{ntype:>10}]  {label}  <small style="color:#9ca3af;">({nid[:12]}…)</small>')

        # Edges
        lines.append(f"\n<strong>EDGES ({len(edges)})</strong>")
        for e in edges:
            src = e.get("source", "?")
            tgt = e.get("target", "?")
            handle = e.get("sourceHandle", "")
            src_label = node_map.get(src, {}).get("label", src[:12])
            tgt_label = node_map.get(tgt, {}).get("label", tgt[:12])
            handle_txt = f" [{handle}]" if handle else ""
            lines.append(f"  {src_label}  ──{handle_txt}──▶  {tgt_label}")

        lines.append("</pre>")
        return format_html("\n".join(lines))

    flow_graph_preview.short_description = "Graph Preview"

    # ------------------------------------------------------------------
    # Bulk actions
    # ------------------------------------------------------------------

    @admin.action(description="🩺 Validate selected flows")
    def validate_selected_flows(self, request, queryset):
        ok, err, warn = 0, 0, 0
        for flow in queryset:
            result = _run_validation(flow)
            if result is None:
                err += 1
                continue
            if result.errors:
                err += 1
            elif result.warnings:
                warn += 1
            else:
                ok += 1
        self.message_user(
            request,
            f"Validated {queryset.count()} flow(s): ✅ {ok} healthy · ⚠️ {warn} warnings · ❌ {err} errors.",
            messages.SUCCESS if err == 0 else messages.WARNING,
        )

    @admin.action(description="✅ Activate selected flows")
    def activate_flows(self, request, queryset):
        updated = queryset.update(is_active=True)
        self.message_user(request, f"{updated} flow(s) activated.", messages.SUCCESS)

    @admin.action(description="⛔ Deactivate selected flows")
    def deactivate_flows(self, request, queryset):
        from django.utils import timezone

        now = timezone.now()
        total_sessions_ended = 0
        for flow in queryset:
            ended = UserChatFlowSession.objects.filter(flow=flow, is_active=True).update(
                is_active=False, ended_at=now, cancellation_reason="Bulk deactivated via admin"
            )
            total_sessions_ended += ended
        updated = queryset.update(is_active=False)
        self.message_user(
            request,
            f"{updated} flow(s) deactivated, {total_sessions_ended} active session(s) ended.",
            messages.WARNING,
        )

    @admin.action(description="🧪 Start test session (attach contact)")
    def start_test_session(self, request, queryset):
        """Redirect to intermediate page to pick a contact for each selected flow."""
        if queryset.count() != 1:
            self.message_user(request, "Please select exactly ONE flow to start a test session.", messages.ERROR)
            return
        flow = queryset.first()
        return redirect(reverse("admin:chatflow_start_test_session", args=[flow.pk]))

    # ------------------------------------------------------------------
    # Test-session intermediate view
    # ------------------------------------------------------------------

    def start_test_session_view(self, request, flow_id):
        """Intermediate page: pick a contact → create a session at the start node."""
        from contacts.models import TenantContact

        flow = ChatFlow.objects.select_related("tenant", "start_template").get(pk=flow_id)

        # Scope contacts to the same tenant
        contacts_qs = TenantContact.objects.filter(tenant=flow.tenant).order_by("phone")

        if request.method == "POST":
            form = StartTestSessionForm(request.POST)
            form.fields["contact"].queryset = contacts_qs

            if form.is_valid():
                contact = form.cleaned_data["contact"]
                context_data = form.cleaned_data["context_json"]

                # --- Validate the flow first ---
                result = _run_validation(flow)
                has_errors = result and result.errors

                if has_errors:
                    self.message_user(
                        request,
                        f'⚠️ Flow "{flow.name}" has {len(result.errors)} validation error(s). '
                        f"Session created anyway — but the flow may not execute correctly.",
                        messages.WARNING,
                    )

                # --- Create the session ---
                try:
                    from .services.graph_executor import start_flow_for_contact

                    state = start_flow_for_contact(
                        flow_id=flow.pk,
                        contact_id=contact.pk,
                        context=context_data,
                    )

                    current_node = state.get("current_node_id", "?")
                    awaiting = state.get("awaiting_input", False)
                    error = state.get("error")

                    if error:
                        self.message_user(
                            request,
                            f"❌ Session started but hit an error: {error}",
                            messages.ERROR,
                        )
                    else:
                        self.message_user(
                            request,
                            f"✅ Test session started! Contact {contact.phone} is now at node "
                            f"'{current_node}' · awaiting input: {awaiting}",
                            messages.SUCCESS,
                        )

                except Exception as exc:
                    logger.exception("Failed to start test session for flow %s", flow.pk)
                    self.message_user(
                        request,
                        f"💥 Failed to start session: {exc}",
                        messages.ERROR,
                    )

                # Redirect back to the flow's change page
                return redirect(reverse("admin:chat_flow_chatflow_change", args=[flow.pk]))
        else:
            form = StartTestSessionForm()
            form.fields["contact"].queryset = contacts_qs

        # --- Find the start node label for display ---
        start_node_label = "—"
        flow_data = flow.flow_data or {}
        for n in flow_data.get("nodes", []):
            nd = n.get("data", {})
            if nd.get("nodeType", n.get("type")) == "start":
                start_node_label = nd.get("label", n.get("id", "start"))
                break

        context = {
            **self.admin_site.each_context(request),
            "title": f"Start test session — {flow.name}",
            "flow": flow,
            "form": form,
            "start_node_label": start_node_label,
            "opts": self.model._meta,
            "has_view_permission": True,
        }

        return render(request, "admin/chat_flow/start_test_session.html", context)


# =============================================================================
# ChatFlowNode Admin
# =============================================================================


@admin.register(ChatFlowNode)
class ChatFlowNodeAdmin(admin.ModelAdmin):
    list_display = (
        "node_id_short",
        "flow",
        "node_type_badge",
        "template_name",
        "outgoing_edge_count",
        "incoming_edge_count",
        "position_x",
        "position_y",
    )
    list_filter = ("node_type", "flow")
    search_fields = ("node_id", "flow__name", "template__element_name")

    fieldsets = (
        (
            None,
            {
                "fields": ("flow", "node_id", "node_type", "template"),
            },
        ),
        (
            "Position",
            {
                "fields": ("position_x", "position_y"),
            },
        ),
        (
            "Configuration",
            {
                "fields": ("node_data",),
                "classes": ("collapse",),
            },
        ),
    )

    def get_queryset(self, request):
        qs = super().get_queryset(request)
        return qs.annotate(
            _out=Count("outgoing_edges", distinct=True),
            _in=Count("incoming_edges", distinct=True),
        )

    def node_id_short(self, obj):
        nid = obj.node_id or ""
        return nid[:20] + "…" if len(nid) > 20 else nid

    node_id_short.short_description = "Node ID"
    node_id_short.admin_order_field = "node_id"

    def node_type_badge(self, obj):
        colours = {
            "start": "#16a34a",
            "end": "#dc2626",
            "template": "#7c3aed",
            "message": "#2563eb",
            "condition": "#d97706",
            "delay": "#6b7280",
            "api": "#0891b2",
            "handoff": "#db2777",
            "action": "#ea580c",
        }
        c = colours.get(obj.node_type, "#6b7280")
        return format_html(
            '<span style="background:{};color:#fff;padding:2px 8px;border-radius:4px;font-size:11px;">{}</span>',
            c,
            obj.node_type,
        )

    node_type_badge.short_description = "Type"
    node_type_badge.admin_order_field = "node_type"

    def template_name(self, obj):
        if obj.template:
            return obj.template.element_name or str(obj.template_id)[:12]
        return "—"

    template_name.short_description = "Template"

    def outgoing_edge_count(self, obj):
        return getattr(obj, "_out", obj.outgoing_edges.count())

    outgoing_edge_count.short_description = "Out ➡"
    outgoing_edge_count.admin_order_field = "_out"

    def incoming_edge_count(self, obj):
        return getattr(obj, "_in", obj.incoming_edges.count())

    incoming_edge_count.short_description = "⬅ In"
    incoming_edge_count.admin_order_field = "_in"


# =============================================================================
# ChatFlowEdge Admin
# =============================================================================


@admin.register(ChatFlowEdge)
class ChatFlowEdgeAdmin(admin.ModelAdmin):
    list_display = (
        "edge_id_short",
        "flow",
        "source_label",
        "target_label",
        "button_text",
        "routing_type",
    )
    list_filter = ("button_type", "flow")
    search_fields = ("edge_id", "flow__name", "button_text")

    fieldsets = (
        (
            None,
            {
                "fields": ("flow", "edge_id"),
            },
        ),
        (
            "Connection",
            {
                "fields": ("source_node", "target_node"),
            },
        ),
        (
            "Button Mapping",
            {
                "fields": ("button_text", "button_type"),
            },
        ),
        (
            "Configuration",
            {
                "fields": ("edge_data",),
                "classes": ("collapse",),
            },
        ),
    )

    def edge_id_short(self, obj):
        eid = obj.edge_id or ""
        return eid[:30] + "…" if len(eid) > 30 else eid

    edge_id_short.short_description = "Edge ID"
    edge_id_short.admin_order_field = "edge_id"

    def source_label(self, obj):
        return obj.source_node.node_id[:16] if obj.source_node else "—"

    source_label.short_description = "From"

    def target_label(self, obj):
        return obj.target_node.node_id[:16] if obj.target_node else "—"

    target_label.short_description = "To"

    def routing_type(self, obj):
        data = obj.edge_data or {}
        el_type = data.get("element_type", obj.button_type or "?")
        colours = {
            "PASSTHROUGH": ("#6b7280", "⏩"),
            "ROUTING": ("#0369a1", "🔀"),
            "QUICK_REPLY": ("#7c3aed", "🔘"),
        }
        c, icon = colours.get(el_type, ("#6b7280", "·"))
        return format_html(
            '<span style="color:{};">{} {}</span>',
            c,
            icon,
            el_type,
        )

    routing_type.short_description = "Type"


# =============================================================================
# UserChatFlowSession Admin — with visual flow inspector
# =============================================================================


def _build_session_debug_context(session):
    """
    Gather everything needed to render the visual session inspector.

    Returns a dict with:
      flow_nodes, flow_edges, current_node, visited_nodes,
      messages_sent_details, broadcast_statuses, state_error, state_snapshot
    """
    from broadcast.models import Broadcast, BroadcastMessage

    flow = session.flow
    flow_data = flow.flow_data or {}
    ctx_data = session.context_data or {}

    # ---- Build node map from flow_data ----
    nodes_raw = flow_data.get("nodes", [])
    edges_raw = flow_data.get("edges", [])
    node_map = {}
    for n in nodes_raw:
        nid = n.get("id", "?")
        data = n.get("data", {})
        node_map[nid] = {
            "id": nid,
            "type": data.get("nodeType", n.get("type", "?")),
            "label": data.get("label", nid),
            "template_id": data.get("template_id"),
        }

    # ---- Figure out which nodes were visited ----
    messages_sent = ctx_data.get("messages_sent", [])
    current_node_id = session.current_node_id
    state_error = ctx_data.get("error")
    awaiting_input = ctx_data.get("awaiting_input", False)

    # Walk edges from start to reconstruct visited path
    # (best-effort: use current_node_id + messages_sent)
    visited_ids = set()
    # The start node is always visited if there is a session
    for n in nodes_raw:
        nd = n.get("data", {})
        if nd.get("nodeType", n.get("type")) == "start":
            visited_ids.add(n["id"])
            break
    # Any node whose template_id is in messages_sent was visited
    for nid, info in node_map.items():
        tid = info.get("template_id")
        if tid and str(tid) in [str(m) for m in messages_sent]:
            visited_ids.add(nid)
    # current node is always visited
    visited_ids.add(current_node_id)

    # ---- Broadcast / message delivery status ----
    broadcast_details = []
    # Find broadcasts created for this flow's chatflow messages
    chatflow_broadcasts = Broadcast.objects.filter(
        name__startswith="ChatFlow:",
        recipients=session.contact,
        tenant=session.tenant,
    ).order_by("-created_at")[:10]

    for bc in chatflow_broadcasts:
        bm = BroadcastMessage.objects.filter(
            broadcast=bc,
            contact=session.contact,
        ).first()
        broadcast_details.append(
            {
                "broadcast_id": bc.id,
                "broadcast_name": bc.name,
                "broadcast_status": bc.status,
                "task_id": bc.task_id,
                "scheduled_time": bc.scheduled_time,
                "msg_status": bm.status if bm else "—",
                "msg_response": (bm.response or "")[:300] if bm else "",
                "msg_sent_at": bm.sent_at if bm else None,
                "msg_delivered_at": bm.delivered_at if bm else None,
                "msg_failed_at": bm.failed_at if bm else None,
                "msg_message_id": bm.message_id if bm else None,
            }
        )

    # ---- Build ordered node pipeline ----
    # BFS from start node along edges
    adjacency = {}
    for e in edges_raw:
        src = e.get("source")
        tgt = e.get("target")
        if src:
            adjacency.setdefault(src, []).append(tgt)

    pipeline_order = []
    seen = set()
    queue = []
    # find start node
    for n in nodes_raw:
        nd = n.get("data", {})
        if nd.get("nodeType", n.get("type")) == "start":
            queue.append(n["id"])
            break
    while queue:
        nid = queue.pop(0)
        if nid in seen:
            continue
        seen.add(nid)
        pipeline_order.append(nid)
        for tgt in adjacency.get(nid, []):
            if tgt not in seen:
                queue.append(tgt)

    # Add any nodes not reachable from start (orphans)
    for nid in node_map:
        if nid not in seen:
            pipeline_order.append(nid)

    return {
        "node_map": node_map,
        "pipeline_order": pipeline_order,
        "visited_ids": visited_ids,
        "current_node_id": current_node_id,
        "state_error": state_error,
        "awaiting_input": awaiting_input,
        "messages_sent": messages_sent,
        "broadcast_details": broadcast_details,
        "context_snapshot": {k: v for k, v in ctx_data.items() if k not in ("messages_sent",)},
    }


@admin.register(UserChatFlowSession)
class UserChatFlowAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "contact",
        "flow",
        "tenant",
        "current_node_id",
        "status_badge",
        "inspector_link",
        "started_at",
        "ended_at",
    )
    list_filter = ("tenant", "flow", "is_active", "is_complete", "started_at")
    search_fields = ("contact__phone", "flow__name", "current_node_id")
    readonly_fields = (
        "started_at",
        "session_inspector",
    )
    actions = ["end_sessions", "reactivate_sessions"]

    fieldsets = (
        (
            None,
            {
                "fields": ("contact", "flow", "current_node_id", "tenant"),
            },
        ),
        (
            "Status",
            {
                "fields": ("is_active", "is_complete"),
            },
        ),
        (
            "🔍 Session Inspector",
            {
                "fields": ("session_inspector",),
                "description": "Visual flow tracker showing where the session is, what was sent, and any errors.",
            },
        ),
        (
            "Context Data (raw)",
            {
                "fields": ("context_data",),
                "classes": ("collapse",),
            },
        ),
        (
            "Timestamps",
            {
                "fields": ("started_at", "ended_at"),
                "classes": ("collapse",),
            },
        ),
    )

    # ------------------------------------------------------------------
    # Custom URLs
    # ------------------------------------------------------------------
    def get_urls(self):
        urls = super().get_urls()
        custom = [
            path(
                "<int:session_id>/inspect/",
                self.admin_site.admin_view(self.session_inspector_view),
                name="chatflow_session_inspect",
            ),
        ]
        return custom + urls

    # ------------------------------------------------------------------
    # List columns
    # ------------------------------------------------------------------
    def status_badge(self, obj):
        if obj.is_complete:
            return format_html('<span style="color:#16a34a;">✅ Complete</span>')
        if obj.is_active:
            return format_html('<span style="color:#2563eb;">▶️ Active</span>')
        return format_html('<span style="color:#6b7280;">⏹ Ended</span>')

    status_badge.short_description = "Status"

    def inspector_link(self, obj):
        url = reverse("admin:chatflow_session_inspect", args=[obj.pk])
        return format_html('<a href="{}" style="color:#7c3aed;font-weight:600;">🔍 Inspect</a>', url)

    inspector_link.short_description = "Debug"

    # ------------------------------------------------------------------
    # Detail-page readonly: inline inspector
    # ------------------------------------------------------------------
    def session_inspector(self, obj):
        """Render the visual pipeline + broadcast status inline on the detail page."""
        if not obj.pk:
            return "Save the session first."
        try:
            debug = _build_session_debug_context(obj)
        except Exception as exc:
            return format_html(
                '<div style="padding:12px;background:#fee2e2;border-radius:8px;">💥 Inspector crashed: {}</div>',
                str(exc),
            )

        node_map = debug["node_map"]
        pipeline = debug["pipeline_order"]
        visited = debug["visited_ids"]
        current = debug["current_node_id"]
        error = debug["state_error"]
        awaiting = debug["awaiting_input"]
        broadcasts = debug["broadcast_details"]

        type_icons = {
            "start": "🟢",
            "end": "🔴",
            "template": "📋",
            "message": "💬",
            "condition": "🔀",
            "delay": "⏱️",
            "api": "🌐",
            "handoff": "🤝",
            "action": "⚡",
        }

        # ---- Flow pipeline ----
        pipeline_html = []
        for nid in pipeline:
            info = node_map.get(nid, {"type": "?", "label": nid})
            ntype = info["type"]
            label = info["label"]
            icon = type_icons.get(ntype, "📦")
            is_current = nid == current
            is_visited = nid in visited

            if is_current and error:
                bg, border, text_col = "#fee2e2", "#dc2626", "#991b1b"
                badge = "❌ ERROR"
            elif is_current and awaiting:
                bg, border, text_col = "#dbeafe", "#3b82f6", "#1e40af"
                badge = "⏳ WAITING"
            elif is_current:
                bg, border, text_col = "#dbeafe", "#2563eb", "#1e40af"
                badge = "▶️ HERE"
            elif is_visited:
                bg, border, text_col = "#dcfce7", "#16a34a", "#166534"
                badge = "✅"
            else:
                bg, border, text_col = "#f3f4f6", "#d1d5db", "#6b7280"
                badge = ""

            pipeline_html.append(
                f'<div style="display:inline-flex;align-items:center;margin:0 2px;">'
                f'<div style="background:{bg};border:2px solid {border};border-radius:8px;'
                f'padding:8px 14px;min-width:80px;text-align:center;">'
                f'<div style="font-size:18px;">{icon}</div>'
                f'<div style="font-size:11px;font-weight:600;color:{text_col};margin-top:2px;">'
                f"{label[:18]}</div>"
                f'<div style="font-size:10px;color:{text_col};">{ntype}</div>'
                f"{'<div style=font-size:10px;font-weight:700;margin-top:2px;>' + badge + '</div>' if badge else ''}"
                f"</div>"
                f'<span style="color:#9ca3af;font-size:16px;margin:0 2px;">→</span>'
                f"</div>"
            )
        # remove last arrow
        if pipeline_html:
            pipeline_html[-1] = pipeline_html[-1].rsplit("<span", 1)[0] + "</div>"

        # ---- Error callout ----
        error_html = ""
        if error:
            error_html = (
                f'<div style="background:#fee2e2;border:1px solid #fca5a5;border-radius:8px;'
                f'padding:12px;margin-top:16px;">'
                f"<strong>❌ Flow Error:</strong> <code>{error}</code></div>"
            )

        # ---- Broadcast / delivery table ----
        bc_rows = []
        for bc in broadcasts:
            # colour for broadcast status
            bs = bc["broadcast_status"]
            bs_colours = {
                "DRAFT": "#6b7280",
                "QUEUED": "#d97706",
                "SCHEDULED": "#d97706",
                "SENDING": "#2563eb",
                "SENT": "#16a34a",
                "PARTIALLY_SENT": "#d97706",
                "FAILED": "#dc2626",
                "CANCELLED": "#6b7280",
            }
            bs_col = bs_colours.get(bs, "#6b7280")

            ms = bc["msg_status"]
            ms_colours = {
                "PENDING": "#6b7280",
                "QUEUED": "#d97706",
                "SENDING": "#2563eb",
                "SENT": "#16a34a",
                "DELIVERED": "#16a34a",
                "READ": "#059669",
                "FAILED": "#dc2626",
                "BLOCKED": "#dc2626",
            }
            ms_col = ms_colours.get(ms, "#6b7280")

            resp_preview = bc["msg_response"]
            if len(resp_preview) > 120:
                resp_preview = resp_preview[:120] + "…"

            bc_rows.append(
                f"<tr>"
                f'<td style="padding:6px 8px;font-size:12px;">{bc["broadcast_name"][:50]}</td>'
                f'<td style="padding:6px 8px;"><span style="color:{bs_col};font-weight:600;">{bs}</span></td>'
                f'<td style="padding:6px 8px;"><span style="color:{ms_col};font-weight:600;">{ms}</span></td>'
                f'<td style="padding:6px 8px;font-size:11px;color:#6b7280;">'
                f"{bc['msg_sent_at'] or '—'}</td>"
                f'<td style="padding:6px 8px;font-size:11px;color:#6b7280;">'
                f"{bc['msg_delivered_at'] or '—'}</td>"
                f'<td style="padding:6px 8px;font-size:11px;color:#6b7280;">'
                f"{bc['msg_failed_at'] or '—'}</td>"
                f'<td style="padding:6px 8px;font-size:11px;color:#6b7280;">'
                f"{bc['task_id'] or '—'}</td>"
                f'<td style="padding:6px 8px;font-size:11px;color:#9ca3af;">'
                f"{resp_preview or '—'}</td>"
                f"</tr>"
            )

        if bc_rows:
            bc_table = (
                f'<div style="margin-top:20px;">'
                f'<h4 style="margin:0 0 8px;">📡 Broadcast & Delivery Status</h4>'
                f'<table style="border-collapse:collapse;width:100%;font-size:13px;">'
                f'<thead><tr style="background:#f3f4f6;">'
                f'<th style="padding:6px 8px;text-align:left;">Broadcast</th>'
                f'<th style="padding:6px 8px;text-align:left;">BC Status</th>'
                f'<th style="padding:6px 8px;text-align:left;">Msg Status</th>'
                f'<th style="padding:6px 8px;text-align:left;">Sent At</th>'
                f'<th style="padding:6px 8px;text-align:left;">Delivered</th>'
                f'<th style="padding:6px 8px;text-align:left;">Failed</th>'
                f'<th style="padding:6px 8px;text-align:left;">Celery Task</th>'
                f'<th style="padding:6px 8px;text-align:left;">Response</th>'
                f"</tr></thead>"
                f"<tbody>{''.join(bc_rows)}</tbody></table></div>"
            )
        else:
            bc_table = (
                '<div style="margin-top:16px;padding:12px;background:#fef9c3;'
                'border-radius:8px;border:1px solid #fde68a;">'
                "⚠️ <strong>No broadcasts found</strong> for this contact + tenant. "
                "The flow executor may not have reached the template-send step, or "
                "it crashed before creating the Broadcast record.</div>"
            )

        # ---- State snapshot ----
        ctx_snap = debug.get("context_snapshot", {})
        snap_json = json.dumps(ctx_snap, indent=2, default=str)[:2000]

        full_html = (
            f'<div style="padding:16px;background:#fafafa;border-radius:10px;'
            f'border:1px solid #e5e7eb;">'
            # Pipeline
            f'<h4 style="margin:0 0 12px;">🗺️ Flow Pipeline</h4>'
            f'<div style="display:flex;flex-wrap:wrap;align-items:center;gap:0;'
            f'overflow-x:auto;padding-bottom:8px;">{"".join(pipeline_html)}</div>'
            # Error
            f"{error_html}"
            # Broadcasts
            f"{bc_table}"
            # State snapshot
            f'<details style="margin-top:20px;">'
            f'<summary style="cursor:pointer;font-weight:600;font-size:13px;">'
            f"📋 Full State Snapshot</summary>"
            f'<pre style="background:#1e293b;color:#e2e8f0;padding:12px;'
            f"border-radius:8px;font-size:11px;max-height:300px;overflow:auto;"
            f'margin-top:8px;">{snap_json}</pre></details>'
            f"</div>"
        )

        return format_html(full_html)

    session_inspector.short_description = "Session Inspector"

    # ------------------------------------------------------------------
    # Full-page inspector view (linked from list)
    # ------------------------------------------------------------------
    def session_inspector_view(self, request, session_id):
        session = UserChatFlowSession.objects.select_related("flow", "contact", "tenant").get(pk=session_id)

        try:
            debug = _build_session_debug_context(session)
        except Exception as exc:
            debug = None
            debug_error = str(exc)
        else:
            debug_error = None

        context = {
            **self.admin_site.each_context(request),
            "title": f"Session Inspector — #{session.pk}",
            "session": session,
            "debug": debug,
            "debug_error": debug_error,
            "opts": self.model._meta,
            "has_view_permission": True,
        }

        return render(request, "admin/chat_flow/session_inspector.html", context)

    # ------------------------------------------------------------------
    # Bulk actions
    # ------------------------------------------------------------------
    @admin.action(description="⏹ End selected sessions")
    def end_sessions(self, request, queryset):
        from django.utils import timezone

        updated = queryset.filter(is_active=True).update(is_active=False, ended_at=timezone.now())
        self.message_user(request, f"{updated} session(s) ended.", messages.SUCCESS)

    @admin.action(description="▶️ Reactivate selected sessions")
    def reactivate_sessions(self, request, queryset):
        updated = queryset.filter(is_active=False, is_complete=False).update(is_active=True, ended_at=None)
        self.message_user(request, f"{updated} session(s) reactivated.", messages.SUCCESS)

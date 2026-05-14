"""
Tests for chat_flow app:
- #268: ChatFlowNode and ChatFlowEdge viewsets use BaseTenantModelViewSet
- #269: SlugRelatedField source fixes in ChatFlowSerializer and ChatFlowNodeSerializer
"""

from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIClient, APIRequestFactory

from chat_flow.models import ChatFlow, ChatFlowEdge, ChatFlowNode
from chat_flow.serializers import ChatFlowEdgeSerializer, ChatFlowNodeSerializer, ChatFlowSerializer
from chat_flow.viewsets import ChatFlowEdgeViewSet, ChatFlowNodeViewSet
from tenants.models import Tenant, TenantRole, TenantUser, TenantWAApp
from wa.models import WATemplate

User = get_user_model()


def _make_wa_template(tenant, element_name):
    """Helper to create a minimal WATemplate for a tenant."""
    wa_app = TenantWAApp.objects.create(
        tenant=tenant,
        app_name=f"app_{element_name}",
        app_id=f"id_{element_name}",
        app_secret="secret",
        wa_number="+14155550100",
    )
    return WATemplate.objects.create(
        element_name=element_name,
        wa_app=wa_app,
        status="APPROVED",
        category="MARKETING",
    )


class ChatFlowTenantIsolationTests(TestCase):
    """Verify cross-tenant isolation for Node and Edge viewsets."""

    @classmethod
    def setUpTestData(cls):
        # ── Tenant A ─────────────────────────────────────────
        cls.tenant_a = Tenant.objects.create(name="Flow Tenant A")
        cls.role_a = TenantRole.objects.get(tenant=cls.tenant_a, slug="owner")
        cls.user_a = User.objects.create_user(
            username="flow_a",
            email="flow_a@t.com",
            mobile="+910000088801",
            password="testpass123",
        )
        TenantUser.objects.create(
            user=cls.user_a,
            tenant=cls.tenant_a,
            role=cls.role_a,
            is_active=True,
        )
        cls.flow_a = ChatFlow.objects.create(
            name="Flow A",
            tenant=cls.tenant_a,
            flow_data={"nodes": [], "edges": []},
        )
        cls.template_a = _make_wa_template(cls.tenant_a, "tmpl_a")
        cls.node_a1 = ChatFlowNode.objects.create(
            name="Node A1",
            flow=cls.flow_a,
            node_id="a1",
            position_x=0,
            position_y=0,
            template=cls.template_a,
        )
        cls.node_a2 = ChatFlowNode.objects.create(
            name="Node A2",
            flow=cls.flow_a,
            node_id="a2",
            position_x=100,
            position_y=0,
            template=cls.template_a,
        )
        cls.edge_a = ChatFlowEdge.objects.create(
            name="Edge A",
            flow=cls.flow_a,
            edge_id="ea1",
            source_node=cls.node_a1,
            target_node=cls.node_a2,
            button_text="Next",
            button_type="QUICK_REPLY",
        )

        # ── Tenant B ─────────────────────────────────────────
        cls.tenant_b = Tenant.objects.create(name="Flow Tenant B")
        cls.role_b = TenantRole.objects.get(tenant=cls.tenant_b, slug="owner")
        cls.user_b = User.objects.create_user(
            username="flow_b",
            email="flow_b@t.com",
            mobile="+910000088802",
            password="testpass123",
        )
        TenantUser.objects.create(
            user=cls.user_b,
            tenant=cls.tenant_b,
            role=cls.role_b,
            is_active=True,
        )
        cls.flow_b = ChatFlow.objects.create(
            name="Flow B",
            tenant=cls.tenant_b,
            flow_data={"nodes": [], "edges": []},
        )
        cls.template_b = _make_wa_template(cls.tenant_b, "tmpl_b")
        cls.node_b = ChatFlowNode.objects.create(
            name="Node B1",
            flow=cls.flow_b,
            node_id="b1",
            position_x=0,
            position_y=0,
            template=cls.template_b,
        )

        # ── Superuser ────────────────────────────────────────
        cls.superuser = User.objects.create_superuser(
            username="flow_su",
            email="flow_su@t.com",
            mobile="+910000088803",
            password="testpass123",
        )

    def _get_viewset_queryset(self, viewset_cls, user, query_params=None):
        """Instantiate viewset with a fake request and return its queryset."""
        factory = APIRequestFactory()
        django_request = factory.get("/", query_params or {})
        django_request.user = user
        from rest_framework.request import Request

        drf_request = Request(django_request)
        drf_request.user = user
        view = viewset_cls()
        view.request = drf_request
        view.kwargs = {}
        view.format_kwarg = None
        return view.get_queryset()

    # ── Node queryset isolation ──────────────────────────────

    def test_node_queryset_user_a_sees_own(self):
        """User A's queryset contains only their tenant's nodes."""
        qs = self._get_viewset_queryset(ChatFlowNodeViewSet, self.user_a)
        ids = set(qs.values_list("id", flat=True))
        self.assertIn(self.node_a1.id, ids)
        self.assertIn(self.node_a2.id, ids)
        self.assertNotIn(self.node_b.id, ids)

    def test_node_queryset_user_b_sees_own(self):
        """User B's queryset contains only their tenant's nodes."""
        qs = self._get_viewset_queryset(ChatFlowNodeViewSet, self.user_b)
        ids = set(qs.values_list("id", flat=True))
        self.assertIn(self.node_b.id, ids)
        self.assertNotIn(self.node_a1.id, ids)

    def test_node_queryset_superuser_sees_all(self):
        """Superuser queryset contains all tenants' nodes."""
        qs = self._get_viewset_queryset(ChatFlowNodeViewSet, self.superuser)
        ids = set(qs.values_list("id", flat=True))
        self.assertIn(self.node_a1.id, ids)
        self.assertIn(self.node_b.id, ids)

    def test_node_flow_id_filter(self):
        """flow_id query param filters within own tenant."""
        qs = self._get_viewset_queryset(
            ChatFlowNodeViewSet,
            self.user_a,
            {"flow_id": str(self.flow_a.id)},
        )
        ids = set(qs.values_list("id", flat=True))
        self.assertIn(self.node_a1.id, ids)
        self.assertIn(self.node_a2.id, ids)

    # ── Edge queryset isolation ──────────────────────────────

    def test_edge_queryset_user_a_sees_own(self):
        """User A's queryset contains only their tenant's edges."""
        qs = self._get_viewset_queryset(ChatFlowEdgeViewSet, self.user_a)
        ids = set(qs.values_list("id", flat=True))
        self.assertIn(self.edge_a.id, ids)

    def test_edge_queryset_user_b_empty(self):
        """User B's queryset has no edges (Tenant B has none)."""
        qs = self._get_viewset_queryset(ChatFlowEdgeViewSet, self.user_b)
        self.assertEqual(qs.count(), 0)

    def test_edge_queryset_superuser_sees_all(self):
        """Superuser queryset contains all tenants' edges."""
        qs = self._get_viewset_queryset(ChatFlowEdgeViewSet, self.superuser)
        ids = set(qs.values_list("id", flat=True))
        self.assertIn(self.edge_a.id, ids)

    def test_edge_flow_id_filter(self):
        """flow_id query param filters edges within own tenant."""
        qs = self._get_viewset_queryset(
            ChatFlowEdgeViewSet,
            self.user_a,
            {"flow_id": str(self.flow_a.id)},
        )
        ids = set(qs.values_list("id", flat=True))
        self.assertIn(self.edge_a.id, ids)

    # ── API-level cross-tenant blocks ────────────────────────

    def test_node_retrieve_cross_tenant_404(self):
        """User B cannot retrieve Tenant A's node via API."""
        client = APIClient()
        client.force_authenticate(user=self.user_b)
        resp = client.get(f"/chat-flow/nodes/{self.node_a1.id}/")
        self.assertEqual(resp.status_code, 404)

    def test_node_update_cross_tenant_404(self):
        """User B cannot update Tenant A's node via API."""
        client = APIClient()
        client.force_authenticate(user=self.user_b)
        resp = client.patch(
            f"/chat-flow/nodes/{self.node_a1.id}/",
            {"name": "Hacked"},
            format="json",
        )
        self.assertEqual(resp.status_code, 404)

    def test_edge_retrieve_cross_tenant_404(self):
        """User B cannot retrieve Tenant A's edge via API."""
        client = APIClient()
        client.force_authenticate(user=self.user_b)
        resp = client.get(f"/chat-flow/edges/{self.edge_a.id}/")
        self.assertEqual(resp.status_code, 404)

    # ── Manager sanity ───────────────────────────────────────

    def test_node_manager_filter_by_user_tenant(self):
        """ChatFlowNode.objects.filter_by_user_tenant works correctly."""
        qs = ChatFlowNode.objects.filter_by_user_tenant(self.user_a)
        self.assertIn(self.node_a1, qs)
        self.assertNotIn(self.node_b, qs)

    def test_edge_manager_filter_by_user_tenant(self):
        """ChatFlowEdge.objects.filter_by_user_tenant works correctly."""
        qs = ChatFlowEdge.objects.filter_by_user_tenant(self.user_a)
        self.assertIn(self.edge_a, qs)


class SlugRelatedFieldSourceTests(TestCase):
    """#269: Verify SlugRelatedField source= fixes."""

    @classmethod
    def setUpTestData(cls):
        cls.tenant = Tenant.objects.create(name="Slug Tenant")
        cls.role = TenantRole.objects.get(tenant=cls.tenant, slug="owner")
        cls.user = User.objects.create_user(
            username="slug_u",
            email="slug_u@t.com",
            mobile="+910000088810",
            password="testpass123",
        )
        TenantUser.objects.create(
            user=cls.user,
            tenant=cls.tenant,
            role=cls.role,
            is_active=True,
        )
        cls.template = _make_wa_template(cls.tenant, "slug_tmpl")
        cls.flow = ChatFlow.objects.create(
            name="Slug Flow",
            tenant=cls.tenant,
            flow_data={"nodes": [], "edges": []},
            start_template=cls.template,
        )
        cls.node = ChatFlowNode.objects.create(
            name="Slug Node",
            flow=cls.flow,
            node_id="s1",
            position_x=0,
            position_y=0,
            template=cls.template,
        )

    def test_chatflow_serializer_start_template_name(self):
        """ChatFlowSerializer returns start_template_name without error."""
        data = ChatFlowSerializer(self.flow).data
        self.assertEqual(data["start_template_name"], "slug_tmpl")

    def test_chatflow_serializer_null_start_template(self):
        """start_template_name is None when start_template is null."""
        flow = ChatFlow.objects.create(
            name="No Start",
            tenant=self.tenant,
            flow_data={"nodes": [], "edges": []},
        )
        data = ChatFlowSerializer(flow).data
        self.assertIsNone(data["start_template_name"])

    def test_node_serializer_template_name(self):
        """ChatFlowNodeSerializer returns template_name without error."""
        data = ChatFlowNodeSerializer(self.node).data
        self.assertEqual(data["template_name"], "slug_tmpl")

    def test_node_serializer_template_buttons(self):
        """template_buttons reads through template FK correctly."""
        data = ChatFlowNodeSerializer(self.node).data
        # buttons is None since template was created without buttons
        self.assertIsNone(data["template_buttons"])


class EdgeSlugRelatedFieldTests(TestCase):
    """#270: Verify ChatFlowEdgeSerializer source= fixes."""

    @classmethod
    def setUpTestData(cls):
        cls.tenant = Tenant.objects.create(name="EdgeSlug Tenant")
        cls.role = TenantRole.objects.get(tenant=cls.tenant, slug="owner")
        cls.user = User.objects.create_user(
            username="eslug_u",
            email="eslug_u@t.com",
            mobile="+910000088820",
            password="testpass123",
        )
        TenantUser.objects.create(
            user=cls.user,
            tenant=cls.tenant,
            role=cls.role,
            is_active=True,
        )
        cls.template = _make_wa_template(cls.tenant, "edge_tmpl")
        cls.flow = ChatFlow.objects.create(
            name="Edge Flow",
            tenant=cls.tenant,
            flow_data={"nodes": [], "edges": []},
        )
        cls.node_src = ChatFlowNode.objects.create(
            name="Src",
            flow=cls.flow,
            node_id="esrc",
            position_x=0,
            position_y=0,
            template=cls.template,
        )
        cls.node_tgt = ChatFlowNode.objects.create(
            name="Tgt",
            flow=cls.flow,
            node_id="etgt",
            position_x=100,
            position_y=0,
            template=cls.template,
        )
        cls.edge = ChatFlowEdge.objects.create(
            name="Test Edge",
            flow=cls.flow,
            edge_id="te1",
            source_node=cls.node_src,
            target_node=cls.node_tgt,
            button_text="Go",
            button_type="QUICK_REPLY",
        )

    def test_source_template_name(self):
        """source_template_name returns the source node's template element_name."""
        data = ChatFlowEdgeSerializer(self.edge).data
        self.assertEqual(data["source_template_name"], "edge_tmpl")

    def test_target_template_name(self):
        """target_template_name returns the target node's template element_name."""
        data = ChatFlowEdgeSerializer(self.edge).data
        self.assertEqual(data["target_template_name"], "edge_tmpl")

    def test_edge_serializer_has_button_text(self):
        """Edge serializer includes button_text field."""
        data = ChatFlowEdgeSerializer(self.edge).data
        self.assertEqual(data["button_text"], "Go")


class ChatFlowNodeStrTests(TestCase):
    """#272: ChatFlowNode.__str__ should not have a stray closing parenthesis."""

    @classmethod
    def setUpTestData(cls):
        cls.tenant = Tenant.objects.create(name="Str Tenant")
        cls.flow = ChatFlow.objects.create(
            name="Pizza Flow",
            tenant=cls.tenant,
            flow_data={"nodes": [], "edges": []},
        )
        cls.node = ChatFlowNode.objects.create(
            name="Welcome",
            flow=cls.flow,
            node_id="welcome-node",
            position_x=0,
            position_y=0,
        )

    def test_str_no_trailing_paren(self):
        """__str__ should be 'Pizza Flow - welcome-node' with no stray ')'."""
        result = str(self.node)
        self.assertEqual(result, "Pizza Flow - welcome-node")
        self.assertFalse(result.endswith(")"))

    def test_str_format(self):
        """__str__ uses flow name and node_id separated by ' - '."""
        result = str(self.node)
        self.assertIn(" - ", result)
        self.assertIn(self.flow.name, result)
        self.assertIn(self.node.node_id, result)


class TemplateButtonsNullTests(TestCase):
    """#273: template_buttons should return [] when template is null."""

    @classmethod
    def setUpTestData(cls):
        cls.tenant = Tenant.objects.create(name="Btns Tenant")
        cls.template = _make_wa_template(cls.tenant, "btns_tmpl")
        cls.flow = ChatFlow.objects.create(
            name="Btns Flow",
            tenant=cls.tenant,
            flow_data={"nodes": [], "edges": []},
        )
        # Node WITHOUT template (start/end/delay type)
        cls.node_no_tmpl = ChatFlowNode.objects.create(
            name="Start",
            flow=cls.flow,
            node_id="start",
            position_x=0,
            position_y=0,
            template=None,
        )
        # Node WITH template
        cls.node_with_tmpl = ChatFlowNode.objects.create(
            name="Tmpl",
            flow=cls.flow,
            node_id="tmpl",
            position_x=100,
            position_y=0,
            template=cls.template,
        )

    def test_null_template_returns_empty_list(self):
        """Serializing a node with null template returns [] for template_buttons."""
        data = ChatFlowNodeSerializer(self.node_no_tmpl).data
        self.assertEqual(data["template_buttons"], [])

    def test_with_template_returns_buttons(self):
        """Serializing a node with a template returns its buttons."""
        data = ChatFlowNodeSerializer(self.node_with_tmpl).data
        self.assertEqual(data["template_buttons"], self.template.buttons)

    def test_null_template_does_not_crash(self):
        """Serializing a node with null template should not raise any exception."""
        try:
            ChatFlowNodeSerializer(self.node_no_tmpl).data
        except Exception as e:
            self.fail(f"Serialization raised {type(e).__name__}: {e}")


class SendTemplateMessageOrderTests(TestCase):
    """#274: BroadcastMessage must be created before Broadcast transitions to QUEUED."""

    @classmethod
    def setUpTestData(cls):
        cls.tenant = Tenant.objects.create(name="BM Order Tenant")
        cls.role = TenantRole.objects.get(tenant=cls.tenant, slug="owner")
        cls.user = User.objects.create_user(
            username="bm_order_u",
            email="bm_order@t.com",
            mobile="+910000088830",
            password="testpass123",
        )
        TenantUser.objects.create(
            user=cls.user,
            tenant=cls.tenant,
            role=cls.role,
            is_active=True,
        )
        cls.wa_app = TenantWAApp.objects.create(
            tenant=cls.tenant,
            app_name="bm_app",
            app_id="bm_id",
            app_secret="secret",
            wa_number="+14155550200",
        )
        from message_templates.models import TemplateNumber

        cls.template_number = TemplateNumber.objects.create(name="bm_tn")
        cls.template = WATemplate.objects.create(
            element_name="bm_tmpl",
            wa_app=cls.wa_app,
            status="APPROVED",
            category="MARKETING",
            number=cls.template_number,
        )
        from contacts.models import TenantContact

        cls.contact = TenantContact.objects.create(
            tenant=cls.tenant,
            phone="+14155550201",
        )

    @staticmethod
    def _patch_signal_and_call(template_id, contact_id):
        """
        Call send_template_message with the broadcast scheduling signal
        disconnected, so we can inspect the ordering of DB writes without
        side-effects (Celery tasks, credit deduction, etc.).
        """
        from unittest.mock import patch

        from chat_flow.services.graph_executor import send_template_message

        with patch(
            "broadcast.signals.handle_broadcast_scheduling",
            side_effect=lambda *a, **kw: None,
        ):
            return send_template_message(template_id, contact_id)

    def test_broadcast_message_created_before_queued(self):
        """BroadcastMessage row exists before Broadcast moves to QUEUED."""
        from unittest.mock import patch

        from broadcast.models import Broadcast, BroadcastMessage
        from chat_flow.services.graph_executor import send_template_message

        bm_exists_at_save = {}

        original_save = Broadcast.save

        def spy_save(self_bc, *args, **kwargs):
            update_fields = kwargs.get("update_fields") or []
            # Capture whether BroadcastMessage exists at the moment
            # the broadcast transitions to QUEUED via save(update_fields=['status', ...])
            if "status" in update_fields and self_bc.status == "QUEUED":
                bm_exists_at_save["exists"] = BroadcastMessage.objects.filter(broadcast=self_bc).exists()
            return original_save(self_bc, *args, **kwargs)

        with (
            patch.object(Broadcast, "save", spy_save),
            patch("broadcast.signals.handle_broadcast_scheduling", side_effect=lambda *a, **kw: None),
        ):
            result = send_template_message(self.template.id, self.contact.id)

        self.assertTrue(result["success"], result.get("error"))
        self.assertTrue(
            bm_exists_at_save.get("exists"), "BroadcastMessage must exist BEFORE Broadcast.save(status=QUEUED)"
        )

    def test_no_duplicate_broadcast_messages(self):
        """Only one BroadcastMessage should exist per broadcast+contact."""
        from unittest.mock import patch

        from broadcast.models import BroadcastMessage
        from chat_flow.services.graph_executor import send_template_message

        with patch("broadcast.signals.handle_broadcast_scheduling", side_effect=lambda *a, **kw: None):
            result = send_template_message(self.template.id, self.contact.id)

        self.assertTrue(result["success"], result.get("error"))
        count = BroadcastMessage.objects.filter(
            broadcast_id=result["broadcast_id"],
            contact=self.contact,
        ).count()
        self.assertEqual(count, 1, f"Expected 1 BroadcastMessage, got {count}")

    def test_result_contains_broadcast_message_id(self):
        """send_template_message returns a valid broadcast_message_id."""
        from unittest.mock import patch

        from broadcast.models import BroadcastMessage
        from chat_flow.services.graph_executor import send_template_message

        with patch("broadcast.signals.handle_broadcast_scheduling", side_effect=lambda *a, **kw: None):
            result = send_template_message(self.template.id, self.contact.id)

        self.assertTrue(result["success"], result.get("error"))
        self.assertIsNotNone(result["broadcast_message_id"])
        self.assertTrue(BroadcastMessage.objects.filter(id=result["broadcast_message_id"]).exists())


class SessionStoreDBFallbackTests(TestCase):
    """#276: get_session_state and reset_session should use DB, not just in-memory dict."""

    @classmethod
    def setUpTestData(cls):
        cls.tenant = Tenant.objects.create(name="Session Tenant")
        cls.role = TenantRole.objects.get(tenant=cls.tenant, slug="owner")
        cls.user = User.objects.create_user(
            username="sess_u",
            email="sess_u@t.com",
            mobile="+910000088840",
            password="testpass123",
        )
        TenantUser.objects.create(
            user=cls.user,
            tenant=cls.tenant,
            role=cls.role,
            is_active=True,
        )
        cls.flow = ChatFlow.objects.create(
            name="Sess Flow",
            tenant=cls.tenant,
            flow_data={"nodes": [], "edges": []},
        )
        from contacts.models import TenantContact

        cls.contact = TenantContact.objects.create(
            tenant=cls.tenant,
            phone="+14155550300",
        )

    def _create_db_session(self, node_id="welcome", is_active=True, is_complete=False):
        """Helper: create a UserChatFlowSession directly in DB."""
        from chat_flow.models import UserChatFlowSession

        return UserChatFlowSession.objects.create(
            contact=self.contact,
            flow=self.flow,
            current_node_id=node_id,
            is_active=is_active,
            is_complete=is_complete,
            context_data={
                "flow_id": self.flow.id,
                "contact_id": self.contact.id,
                "current_node_id": node_id,
                "user_input": None,
                "messages_sent": [],
                "context": {},
                "is_complete": is_complete,
                "awaiting_input": True,
                "error": None,
            },
        )

    def test_get_session_state_falls_back_to_db(self):
        """get_session_state returns DB session when memory is empty."""
        from chat_flow.services.graph_executor import ChatFlowExecutor, _session_store

        session = self._create_db_session("node-a")

        executor = ChatFlowExecutor.__new__(ChatFlowExecutor)
        executor.flow_id = self.flow.id

        thread_id = f"flow_{self.flow.id}_contact_{self.contact.id}"
        _session_store.pop(thread_id, None)  # ensure memory is empty

        state = executor.get_session_state(self.contact.id)
        self.assertIsNotNone(state, "Should have loaded from DB")
        self.assertEqual(state["current_node_id"], "node-a")

        # Cleanup
        _session_store.pop(thread_id, None)
        session.delete()

    def test_get_session_state_returns_none_when_no_session(self):
        """get_session_state returns None when neither memory nor DB has a session."""
        from chat_flow.services.graph_executor import ChatFlowExecutor, _session_store

        executor = ChatFlowExecutor.__new__(ChatFlowExecutor)
        executor.flow_id = self.flow.id

        thread_id = f"flow_{self.flow.id}_contact_{self.contact.id}"
        _session_store.pop(thread_id, None)

        state = executor.get_session_state(self.contact.id)
        self.assertIsNone(state)

    def test_get_session_state_prefers_memory(self):
        """get_session_state returns memory state when available (no DB query)."""
        from chat_flow.services.graph_executor import ChatFlowExecutor, _session_store

        executor = ChatFlowExecutor.__new__(ChatFlowExecutor)
        executor.flow_id = self.flow.id

        thread_id = f"flow_{self.flow.id}_contact_{self.contact.id}"
        memory_state = {
            "flow_id": self.flow.id,
            "current_node_id": "from-memory",
            "contact_id": self.contact.id,
            "is_complete": False,
        }
        _session_store[thread_id] = memory_state

        state = executor.get_session_state(self.contact.id)
        self.assertEqual(state["current_node_id"], "from-memory")

        # Cleanup
        _session_store.pop(thread_id, None)

    def test_reset_session_deactivates_db_session(self):
        """reset_session should set is_active=False on the DB session."""
        from chat_flow.services.graph_executor import ChatFlowExecutor, _session_store

        session = self._create_db_session("node-x")
        self.assertTrue(session.is_active)

        executor = ChatFlowExecutor.__new__(ChatFlowExecutor)
        executor.flow_id = self.flow.id

        thread_id = f"flow_{self.flow.id}_contact_{self.contact.id}"
        _session_store[thread_id] = {"dummy": True}

        result = executor.reset_session(self.contact.id)
        self.assertTrue(result)

        # Memory should be cleared
        self.assertNotIn(thread_id, _session_store)

        # DB should be deactivated
        session.refresh_from_db()
        self.assertFalse(session.is_active)
        self.assertIsNotNone(session.ended_at)

        session.delete()

    def test_reset_session_db_only(self):
        """reset_session works even when only the DB session exists (memory empty)."""
        from chat_flow.services.graph_executor import ChatFlowExecutor, _session_store

        session = self._create_db_session("node-y")

        executor = ChatFlowExecutor.__new__(ChatFlowExecutor)
        executor.flow_id = self.flow.id

        thread_id = f"flow_{self.flow.id}_contact_{self.contact.id}"
        _session_store.pop(thread_id, None)

        result = executor.reset_session(self.contact.id)
        self.assertTrue(result, "Should return True when DB session exists")

        session.refresh_from_db()
        self.assertFalse(session.is_active)

        session.delete()

    def test_reset_session_returns_false_when_nothing_exists(self):
        """reset_session returns False when neither memory nor DB has a session."""
        from chat_flow.services.graph_executor import ChatFlowExecutor, _session_store

        executor = ChatFlowExecutor.__new__(ChatFlowExecutor)
        executor.flow_id = self.flow.id

        thread_id = f"flow_{self.flow.id}_contact_{self.contact.id}"
        _session_store.pop(thread_id, None)

        result = executor.reset_session(self.contact.id)
        self.assertFalse(result)


class SessionTenantFKTests(TestCase):
    """Tests for ticket #277 — UserChatFlowSession.tenant FK."""

    @classmethod
    def setUpTestData(cls):
        from contacts.models import TenantContact
        from tenants.models import Tenant

        cls.tenant = Tenant.objects.create(name="Tenant FK Test")
        cls.flow = ChatFlow.objects.create(
            name="Tenant FK Flow",
            tenant=cls.tenant,
            flow_data={"nodes": [], "edges": []},
        )
        cls.contact = TenantContact.objects.create(
            phone="9199900277",
            tenant=cls.tenant,
        )

    def test_save_session_to_db_sets_tenant(self):
        """save_session_to_db populates tenant from flow.tenant."""
        from chat_flow.services.graph_executor import save_session_to_db

        state = {
            "flow_id": self.flow.id,
            "contact_id": self.contact.id,
            "current_node_id": "start",
            "is_complete": False,
            "user_input": None,
            "messages_sent": [],
            "context": {},
            "awaiting_input": False,
            "error": None,
        }
        session = save_session_to_db(state)
        self.assertEqual(session.tenant_id, self.tenant.id)

    def test_session_queryable_by_tenant(self):
        """Sessions can be filtered directly by tenant FK."""
        from chat_flow.models import UserChatFlowSession
        from chat_flow.services.graph_executor import save_session_to_db

        state = {
            "flow_id": self.flow.id,
            "contact_id": self.contact.id,
            "current_node_id": "node-1",
            "is_complete": False,
            "user_input": None,
            "messages_sent": [],
            "context": {},
            "awaiting_input": False,
            "error": None,
        }
        save_session_to_db(state)

        qs = UserChatFlowSession.objects.filter(tenant=self.tenant, is_active=True)
        self.assertEqual(qs.count(), 1)
        self.assertEqual(qs.first().current_node_id, "node-1")

    def test_session_tenant_null_when_flow_has_no_tenant(self):
        """If flow has no tenant, session.tenant is None (no crash)."""
        from chat_flow.services.graph_executor import save_session_to_db

        flow_no_tenant = ChatFlow.objects.create(
            name="No Tenant Flow",
            tenant=None,
            flow_data={"nodes": [], "edges": []},
        )
        state = {
            "flow_id": flow_no_tenant.id,
            "contact_id": self.contact.id,
            "current_node_id": "start",
            "is_complete": False,
            "user_input": None,
            "messages_sent": [],
            "context": {},
            "awaiting_input": False,
            "error": None,
        }
        session = save_session_to_db(state)
        self.assertIsNone(session.tenant_id)

    def test_session_update_preserves_tenant(self):
        """Moving to a new node deactivates old session, new session keeps tenant."""
        from chat_flow.models import UserChatFlowSession
        from chat_flow.services.graph_executor import save_session_to_db

        state = {
            "flow_id": self.flow.id,
            "contact_id": self.contact.id,
            "current_node_id": "node-a",
            "is_complete": False,
            "user_input": None,
            "messages_sent": [],
            "context": {},
            "awaiting_input": False,
            "error": None,
        }
        session1 = save_session_to_db(state)

        state["current_node_id"] = "node-b"
        session2 = save_session_to_db(state)

        # Old session deactivated, new one created
        session1.refresh_from_db()
        self.assertFalse(session1.is_active)
        # New active session has tenant set
        self.assertEqual(session2.tenant_id, self.tenant.id)
        self.assertEqual(session2.current_node_id, "node-b")
        # Only one active session per contact+flow
        active = UserChatFlowSession.objects.filter(contact=self.contact, flow=self.flow, is_active=True)
        self.assertEqual(active.count(), 1)


# =============================================================================
# Node-type registry tests (#157)
# =============================================================================

from chat_flow.node_registry import (
    NodeTypeSpec,
    _REGISTRY,
    get_node_type,
    list_node_types_for_platform,
    register_node_type,
    unregister_node_type,
    validate_flow_for_platform,
)
from jina_connect.platform_choices import PlatformChoices


class NodeRegistryTests(TestCase):
    """Unit tests for the chat_flow node-type registry."""

    def setUp(self):
        self._registry_snapshot = dict(_REGISTRY)
        _REGISTRY.clear()

    def tearDown(self):
        _REGISTRY.clear()
        _REGISTRY.update(self._registry_snapshot)

    def test_register_and_lookup(self):
        spec = NodeTypeSpec(
            type_id="t.foo",
            display_name="Foo",
            description="A foo",
            supported_platforms=frozenset(["VOICE"]),
            required_data_fields=frozenset(),
        )
        register_node_type(spec)
        self.assertIs(get_node_type("t.foo"), spec)

    def test_register_duplicate_raises(self):
        spec = NodeTypeSpec(
            type_id="t.dup",
            display_name="Dup",
            description="",
            supported_platforms=frozenset(),
            required_data_fields=frozenset(),
        )
        register_node_type(spec)
        with self.assertRaises(ValueError):
            register_node_type(spec)

    def test_unknown_type_returns_none(self):
        self.assertIsNone(get_node_type("does.not.exist"))

    def test_list_for_platform_filters_correctly(self):
        voice_spec = NodeTypeSpec(
            type_id="v.play",
            display_name="Play",
            description="",
            supported_platforms=frozenset(["VOICE"]),
            required_data_fields=frozenset(),
        )
        wa_spec = NodeTypeSpec(
            type_id="w.send",
            display_name="Send",
            description="",
            supported_platforms=frozenset(["WHATSAPP"]),
            required_data_fields=frozenset(),
        )
        agnostic = NodeTypeSpec(
            type_id="c.if",
            display_name="If",
            description="",
            supported_platforms=frozenset(),
            required_data_fields=frozenset(),
        )
        register_node_type(voice_spec)
        register_node_type(wa_spec)
        register_node_type(agnostic)

        for_voice = list_node_types_for_platform("VOICE")
        self.assertIn(voice_spec, for_voice)
        self.assertIn(agnostic, for_voice)
        self.assertNotIn(wa_spec, for_voice)

    def test_unregister_removes_spec(self):
        spec = NodeTypeSpec(
            type_id="t.rm",
            display_name="Rm",
            description="",
            supported_platforms=frozenset(),
            required_data_fields=frozenset(),
        )
        register_node_type(spec)
        unregister_node_type("t.rm")
        self.assertIsNone(get_node_type("t.rm"))


class ValidateFlowForPlatformTests(TestCase):
    """Validation rules for ``validate_flow_for_platform``."""

    def setUp(self):
        self._registry_snapshot = dict(_REGISTRY)
        _REGISTRY.clear()

    def tearDown(self):
        _REGISTRY.clear()
        _REGISTRY.update(self._registry_snapshot)

    def test_unknown_types_pass_validation(self):
        """Unknown ``type`` does NOT block a save (just logs a warning)."""
        flow = {"nodes": [{"id": "n1", "type": "totally.unknown", "data": {}}]}
        self.assertEqual(validate_flow_for_platform(flow, "WHATSAPP"), [])

    def test_supported_platform_passes(self):
        register_node_type(NodeTypeSpec(
            type_id="v.play",
            display_name="",
            description="",
            supported_platforms=frozenset(["VOICE"]),
            required_data_fields=frozenset(),
        ))
        flow = {"nodes": [{"id": "n1", "type": "v.play", "data": {}}]}
        self.assertEqual(validate_flow_for_platform(flow, "VOICE"), [])

    def test_unsupported_platform_errors(self):
        register_node_type(NodeTypeSpec(
            type_id="v.play",
            display_name="",
            description="",
            supported_platforms=frozenset(["VOICE"]),
            required_data_fields=frozenset(),
        ))
        flow = {"nodes": [{"id": "n1", "type": "v.play", "data": {}}]}
        errors = validate_flow_for_platform(flow, "WHATSAPP")
        self.assertEqual(len(errors), 1)
        self.assertIn("not supported", errors[0])
        self.assertIn("WHATSAPP", errors[0])

    def test_missing_required_field_errors(self):
        register_node_type(NodeTypeSpec(
            type_id="v.gather",
            display_name="",
            description="",
            supported_platforms=frozenset(["VOICE"]),
            required_data_fields=frozenset(["max_digits", "timeout_seconds"]),
        ))
        flow = {"nodes": [{"id": "n1", "type": "v.gather", "data": {"max_digits": 4}}]}
        errors = validate_flow_for_platform(flow, "VOICE")
        self.assertEqual(len(errors), 1)
        self.assertIn("timeout_seconds", errors[0])

    def test_custom_validator_errors_propagate(self):
        register_node_type(NodeTypeSpec(
            type_id="v.play",
            display_name="",
            description="",
            supported_platforms=frozenset(["VOICE"]),
            required_data_fields=frozenset(),
            validator=lambda d: (
                ["needs audio_url or tts_text"]
                if not d.get("audio_url") and not d.get("tts_text") else []
            ),
        ))
        flow = {"nodes": [{"id": "n1", "type": "v.play", "data": {}}]}
        errors = validate_flow_for_platform(flow, "VOICE")
        self.assertEqual(errors, ["needs audio_url or tts_text"])

    def test_non_dict_flow_data_returns_empty(self):
        """Defensive: None / non-dict flow_data must not crash."""
        self.assertEqual(validate_flow_for_platform(None, "VOICE"), [])
        self.assertEqual(validate_flow_for_platform([], "VOICE"), [])


class ChatFlowPlatformValidationTests(TestCase):
    """``ChatFlow.save()`` rejects flows whose nodes violate the registry."""

    @classmethod
    def setUpTestData(cls):
        cls.tenant = Tenant.objects.create(name="Reg Tenant")

    def setUp(self):
        self._registry_snapshot = dict(_REGISTRY)
        _REGISTRY.clear()
        register_node_type(NodeTypeSpec(
            type_id="v.play",
            display_name="Play",
            description="",
            supported_platforms=frozenset([PlatformChoices.VOICE]),
            required_data_fields=frozenset(),
        ))

    def tearDown(self):
        _REGISTRY.clear()
        _REGISTRY.update(self._registry_snapshot)

    def test_voice_node_on_voice_platform_saves(self):
        flow = ChatFlow(
            name="V flow",
            tenant=self.tenant,
            platform=PlatformChoices.VOICE,
            flow_data={"nodes": [{"id": "n1", "type": "v.play", "data": {}}]},
        )
        flow.save()
        self.assertIsNotNone(flow.pk)

    def test_voice_node_on_whatsapp_platform_rejected(self):
        from django.core.exceptions import ValidationError as DjValidationError

        flow = ChatFlow(
            name="X flow",
            tenant=self.tenant,
            platform=PlatformChoices.WHATSAPP,
            flow_data={"nodes": [{"id": "n1", "type": "v.play", "data": {}}]},
        )
        with self.assertRaises(DjValidationError):
            flow.save()

    def test_existing_whatsapp_flows_with_empty_nodes_still_save(self):
        """Back-compat: legacy flows with empty nodes pass new validation."""
        flow = ChatFlow.objects.create(
            name="Legacy",
            tenant=self.tenant,
            flow_data={"nodes": [], "edges": []},
        )
        self.assertEqual(flow.platform, PlatformChoices.WHATSAPP)

"""Tests for process_rcs_event_task: inbound, delivery, chatflow routing."""

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from contacts.models import TenantContact
from rcs.models import RCSApp, RCSOutboundMessage, RCSWebhookEvent
from rcs.tasks import _handle_delivery_event, _handle_inbound_message, process_rcs_event_task
from tenants.models import Tenant


@pytest.fixture
def tenant(db):
    return Tenant.objects.create(name="Task Tenant")


@pytest.fixture
def rcs_app(tenant):
    return RCSApp.objects.create(
        tenant=tenant,
        provider="GOOGLE_RBM",
        agent_id="task-agent@rbm.goog",
    )


@pytest.fixture(autouse=True)
def silence_task_delay(monkeypatch):
    monkeypatch.setattr("rcs.tasks.process_rcs_event_task.delay", lambda *a, **kw: None)


def _make_event(rcs_app, event_type="MESSAGE", msg_id="task-msg-1"):
    return RCSWebhookEvent.objects.create(
        tenant=rcs_app.tenant,
        rcs_app=rcs_app,
        provider="GOOGLE_RBM",
        event_type=event_type,
        provider_message_id=msg_id,
        payload={"senderPhoneNumber": "+14155550200", "messageId": msg_id, "text": "hello"},
    )


def _fake_inbound(phone="+14155550200", msg_type="text", text="hello"):
    return SimpleNamespace(
        message_id="task-msg-1",
        sender_phone=phone,
        message_type=msg_type,
        text=text,
        postback_data=None,
        suggestion_text=None,
        location=None,
        file_info=None,
        raw_payload={},
    )


@pytest.mark.django_db
class TestProcessRCSEventTask:
    def test_skips_already_processed_event(self, rcs_app):
        event = _make_event(rcs_app)
        event.is_processed = True
        event.save()

        with patch("rcs.tasks._handle_inbound_message") as mock_handler:
            process_rcs_event_task.run(str(event.pk))
            mock_handler.assert_not_called()

    def test_missing_event_logs_and_returns(self, rcs_app):
        import uuid

        with patch("rcs.tasks.logger") as mock_log:
            process_rcs_event_task.run(str(uuid.uuid4()))
            assert mock_log.error.called

    def test_marks_event_processed_on_success(self, rcs_app, monkeypatch):
        event = _make_event(rcs_app, event_type="MESSAGE")

        monkeypatch.setattr("rcs.tasks._handle_inbound_message", lambda ev: None)
        process_rcs_event_task.run(str(event.pk))

        event.refresh_from_db()
        assert event.is_processed is True
        assert event.processed_at is not None

    def test_increments_retry_count_on_exception(self, rcs_app, monkeypatch):
        event = _make_event(rcs_app, event_type="MESSAGE")

        def _boom(ev):
            raise ValueError("boom")

        monkeypatch.setattr("rcs.tasks._handle_inbound_message", _boom)
        monkeypatch.setattr(process_rcs_event_task, "retry", lambda *a, **kw: RuntimeError("retry"))

        event.is_processed = False
        event.save(update_fields=["is_processed"])

        task = process_rcs_event_task
        with pytest.raises(RuntimeError):
            task.run(str(event.pk))

        event.refresh_from_db()
        assert event.retry_count == 1
        assert "boom" in event.error_message


@pytest.mark.django_db
class TestHandleInboundMessage:
    def test_upserts_contact_and_creates_inbox_message(self, rcs_app, monkeypatch):
        event = _make_event(rcs_app)

        monkeypatch.setattr(
            "rcs.tasks.get_rcs_provider",
            lambda app: SimpleNamespace(parse_inbound_webhook=lambda p: _fake_inbound()),
        )

        created_msgs = []

        def _mock_create_inbox(**kw):
            msg = MagicMock()
            msg.pk = 999
            created_msgs.append(kw)
            return msg

        monkeypatch.setattr("rcs.tasks.create_inbox_message", _mock_create_inbox)

        _handle_inbound_message(event)

        assert TenantContact.objects.filter(tenant=rcs_app.tenant, phone="+14155550200").exists()
        assert len(created_msgs) == 1
        assert created_msgs[0]["direction"].value == "INCOMING" or "INCOMING" in str(created_msgs[0]["direction"])

    def test_routes_to_chatflow_for_text_input(self, rcs_app, monkeypatch):
        event = _make_event(rcs_app)

        monkeypatch.setattr(
            "rcs.tasks.get_rcs_provider",
            lambda app: SimpleNamespace(parse_inbound_webhook=lambda p: _fake_inbound(text="hello chatflow")),
        )
        monkeypatch.setattr("rcs.tasks.create_inbox_message", lambda **kw: MagicMock(pk=1))

        routed = {}

        def _mock_route(contact, user_input):
            routed["input"] = user_input

        monkeypatch.setattr("rcs.tasks._route_to_chatflow", _mock_route)

        _handle_inbound_message(event)
        assert routed.get("input") == "hello chatflow"


@pytest.mark.django_db
class TestHandleDeliveryEvent:
    def test_updates_outbound_status_to_delivered(self, rcs_app, monkeypatch):
        outbound = RCSOutboundMessage.objects.create(
            tenant=rcs_app.tenant,
            rcs_app=rcs_app,
            to_phone="+14155550300",
            provider_message_id="dlr-msg-1",
            status="SENT",
        )
        event = _make_event(rcs_app, event_type="DELIVERED", msg_id="dlr-msg-1")
        event.payload = {"messageId": "dlr-msg-1", "deliveryReceipt": {"status": "DELIVERED"}}
        event.save()

        monkeypatch.setattr(
            "rcs.tasks.get_rcs_provider",
            lambda app: SimpleNamespace(
                parse_event_webhook=lambda p: SimpleNamespace(message_id="dlr-msg-1", event_type="DELIVERED")
            ),
        )

        _handle_delivery_event(event)
        outbound.refresh_from_db()
        assert outbound.status == "DELIVERED"
        assert outbound.delivered_at is not None

    def test_updates_outbound_status_to_read(self, rcs_app, monkeypatch):
        outbound = RCSOutboundMessage.objects.create(
            tenant=rcs_app.tenant,
            rcs_app=rcs_app,
            to_phone="+14155550301",
            provider_message_id="read-msg-1",
            status="DELIVERED",
        )
        event = _make_event(rcs_app, event_type="READ", msg_id="read-msg-1")

        monkeypatch.setattr(
            "rcs.tasks.get_rcs_provider",
            lambda app: SimpleNamespace(
                parse_event_webhook=lambda p: SimpleNamespace(message_id="read-msg-1", event_type="READ")
            ),
        )

        _handle_delivery_event(event)
        outbound.refresh_from_db()
        assert outbound.status == "READ"
        assert outbound.read_at is not None

    def test_unknown_message_id_logs_warning_no_crash(self, rcs_app, monkeypatch):
        event = _make_event(rcs_app, event_type="DELIVERED", msg_id="unknown-dlr")

        monkeypatch.setattr(
            "rcs.tasks.get_rcs_provider",
            lambda app: SimpleNamespace(
                parse_event_webhook=lambda p: SimpleNamespace(message_id="does-not-exist", event_type="DELIVERED")
            ),
        )

        with patch("rcs.tasks.logger") as mock_log:
            _handle_delivery_event(event)
            assert mock_log.warning.called

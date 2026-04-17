"""Tests for scheduled broadcast auto-send (#101), cancel (#102), and broker failure (#23)."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from django.utils import timezone

from broadcast.models import (
    Broadcast,
    BroadcastMessage,
    BroadcastPlatformChoices,
    BroadcastStatusChoices,
    MessageStatusChoices,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def tenant(db):
    from tenants.models import Tenant

    return Tenant.objects.create(name="Test Tenant")


@pytest.fixture()
def user(db):
    from django.contrib.auth import get_user_model

    return get_user_model().objects.create_user(
        username="bcast_test",
        email="bcast@test.com",
        mobile="+919000000001",
        password="testpass123",
    )


@pytest.fixture()
def role(tenant):
    from tenants.models import TenantRole

    role, _ = TenantRole.objects.get_or_create(tenant=tenant, slug="owner", defaults={"name": "Owner", "priority": 100})
    return role


@pytest.fixture()
def tenant_user(tenant, user, role):
    from tenants.models import TenantUser

    return TenantUser.objects.create(tenant=tenant, user=user, role=role, is_active=True)


@pytest.fixture()
def contact(tenant):
    from contacts.models import TenantContact

    return TenantContact.objects.create(tenant=tenant, phone="+919000000099", first_name="Test")


@pytest.fixture()
def scheduled_broadcast(tenant, user, contact):
    """A broadcast in SCHEDULED state with scheduled_time in the past."""
    bc = Broadcast.objects.create(
        tenant=tenant,
        name="Scheduled Campaign",
        scheduled_time=timezone.now() - timezone.timedelta(minutes=5),
        status=BroadcastStatusChoices.SCHEDULED,
        platform=BroadcastPlatformChoices.SMS,
        created_by=user,
    )
    bc.recipients.add(contact)
    return bc


# ---------------------------------------------------------------------------
# process_scheduled_broadcasts tests (#101)
# ---------------------------------------------------------------------------


@pytest.mark.django_db(transaction=True)
class TestProcessScheduledBroadcasts:
    def test_launches_due_broadcast(self, scheduled_broadcast, monkeypatch):
        """Due broadcast transitions SCHEDULED → SENDING and .delay() is called."""
        from broadcast import tasks

        called_with = []
        monkeypatch.setattr(
            tasks.setup_broadcast_task,
            "delay",
            lambda pk: SimpleNamespace(id="fake-task-id") if not called_with.append(pk) else None,
        )

        result = tasks.process_scheduled_broadcasts()

        assert result["launched"] == 1
        assert called_with == [scheduled_broadcast.pk]

        scheduled_broadcast.refresh_from_db()
        assert scheduled_broadcast.status == BroadcastStatusChoices.SENDING
        assert scheduled_broadcast.task_id == "fake-task-id"

    def test_skips_draft_broadcast(self, tenant, user, monkeypatch):
        """A DRAFT broadcast (no scheduled_time due) is NOT launched."""
        from broadcast import tasks

        Broadcast.objects.create(
            tenant=tenant,
            name="Draft",
            status=BroadcastStatusChoices.DRAFT,
            created_by=user,
        )
        monkeypatch.setattr(
            tasks.setup_broadcast_task, "delay", lambda pk: (_ for _ in ()).throw(AssertionError("should not call"))
        )

        result = tasks.process_scheduled_broadcasts()
        assert result["launched"] == 0

    def test_skips_future_broadcast(self, tenant, user, monkeypatch):
        """A SCHEDULED broadcast whose scheduled_time is in the future is NOT launched."""
        from broadcast import tasks

        Broadcast.objects.create(
            tenant=tenant,
            name="Future",
            status=BroadcastStatusChoices.SCHEDULED,
            scheduled_time=timezone.now() + timezone.timedelta(hours=1),
            created_by=user,
        )
        monkeypatch.setattr(
            tasks.setup_broadcast_task, "delay", lambda pk: (_ for _ in ()).throw(AssertionError("should not call"))
        )

        result = tasks.process_scheduled_broadcasts()
        assert result["launched"] == 0

    def test_broker_failure_reverts_to_scheduled(self, scheduled_broadcast, monkeypatch):
        """If .delay() raises, broadcast reverts to SCHEDULED (#23)."""
        from broadcast import tasks

        monkeypatch.setattr(
            tasks.setup_broadcast_task,
            "delay",
            lambda pk: (_ for _ in ()).throw(ConnectionError("broker down")),
        )

        result = tasks.process_scheduled_broadcasts()

        assert result["launched"] == 0
        scheduled_broadcast.refresh_from_db()
        assert scheduled_broadcast.status == BroadcastStatusChoices.SCHEDULED


# ---------------------------------------------------------------------------
# setup_broadcast_task tests
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestSetupBroadcastTask:
    def test_skips_cancelled_broadcast(self, tenant, user):
        """If broadcast was cancelled before the task runs, it skips."""
        from broadcast.tasks import setup_broadcast_task

        bc = Broadcast.objects.create(
            tenant=tenant,
            name="Already Cancelled",
            status=BroadcastStatusChoices.CANCELLED,
            created_by=user,
        )

        result = setup_broadcast_task(bc.pk)
        assert result.get("skipped") is True

    def test_nonexistent_broadcast(self):
        """Task handles missing broadcast gracefully."""
        from broadcast.tasks import setup_broadcast_task

        result = setup_broadcast_task(999999)
        assert "error" in result


# ---------------------------------------------------------------------------
# Cancel action tests (#102)
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestBroadcastCancel:
    def test_cancel_scheduled_broadcast(self, scheduled_broadcast, tenant_user, monkeypatch):
        """Cancelling a SCHEDULED broadcast sets CANCELLED and returns 200."""
        from rest_framework.test import APIClient

        from broadcast import tasks

        monkeypatch.setattr(tasks.cancel_broadcast_task, "delay", lambda task_id: None)

        client = APIClient()
        client.force_authenticate(user=tenant_user.user)

        url = f"/broadcast/{scheduled_broadcast.pk}/cancel/"
        resp = client.post(url, {"reason": "Changed my mind"}, format="json")

        assert resp.status_code == 200
        assert resp.data["detail"] == "Broadcast cancelled."

        scheduled_broadcast.refresh_from_db()
        assert scheduled_broadcast.status == BroadcastStatusChoices.CANCELLED
        assert scheduled_broadcast.reason_for_cancellation == "Changed my mind"

    def test_cancel_sending_broadcast_marks_pending_messages(
        self, scheduled_broadcast, contact, tenant_user, monkeypatch
    ):
        """Cancelling a SENDING broadcast marks PENDING messages as FAILED."""
        from rest_framework.test import APIClient

        from broadcast import tasks

        monkeypatch.setattr(tasks.cancel_broadcast_task, "delay", lambda task_id: None)

        scheduled_broadcast.status = BroadcastStatusChoices.SENDING
        scheduled_broadcast.task_id = "some-task-id"
        scheduled_broadcast.save(update_fields=["status", "task_id"])

        BroadcastMessage.objects.create(
            broadcast=scheduled_broadcast,
            contact=contact,
            status=MessageStatusChoices.PENDING,
        )

        client = APIClient()
        client.force_authenticate(user=tenant_user.user)

        url = f"/broadcast/{scheduled_broadcast.pk}/cancel/"
        resp = client.post(url, format="json")

        assert resp.status_code == 200
        assert resp.data["messages_cancelled"] == 1

        msg = BroadcastMessage.objects.get(broadcast=scheduled_broadcast, contact=contact)
        assert msg.status == MessageStatusChoices.FAILED

    def test_cannot_cancel_sent_broadcast(self, tenant, user, tenant_user, monkeypatch):
        """Attempting to cancel a SENT broadcast returns 400."""
        from rest_framework.test import APIClient

        from broadcast import tasks

        monkeypatch.setattr(tasks.cancel_broadcast_task, "delay", lambda task_id: None)

        bc = Broadcast.objects.create(
            tenant=tenant,
            name="Sent",
            status=BroadcastStatusChoices.SENT,
            created_by=user,
        )

        client = APIClient()
        client.force_authenticate(user=tenant_user.user)

        url = f"/broadcast/{bc.pk}/cancel/"
        resp = client.post(url, format="json")

        assert resp.status_code == 400
        assert "Cannot cancel" in resp.data["detail"]

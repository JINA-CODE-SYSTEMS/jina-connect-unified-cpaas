"""Tests for bulk contact import task (#118) and endpoint (#22)."""

from __future__ import annotations

import io

import pytest
from django.core.files.storage import default_storage
from django.core.files.uploadedfile import SimpleUploadedFile

from contacts.models import ContactSource, ImportJob, TenantContact

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def tenant(db):
    from tenants.models import Tenant

    return Tenant.objects.create(name="Import Tenant")


@pytest.fixture()
def user(db):
    from django.contrib.auth import get_user_model

    return get_user_model().objects.create_user(
        username="import_test",
        email="import@test.com",
        mobile="+919100000001",
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


def _make_csv(rows: list[list[str]]) -> bytes:
    """Build a CSV bytes payload from a list of rows."""
    buf = io.StringIO()
    for row in rows:
        buf.write(",".join(row) + "\n")
    return buf.getvalue().encode("utf-8")


@pytest.fixture()
def csv_file():
    return _make_csv(
        [
            ["phone", "first_name", "last_name", "tag"],
            ["+919200000001", "Alice", "A", "vip"],
            ["+919200000002", "Bob", "B", ""],
            ["+919200000003", "Charlie", "C", "vip"],
        ]
    )


@pytest.fixture()
def import_job(tenant, user, csv_file):
    """Create an ImportJob with a real file in default_storage."""
    path = f"imports/{tenant.pk}/test_import.csv"
    saved = default_storage.save(path, io.BytesIO(csv_file))
    return ImportJob.objects.create(
        tenant=tenant,
        created_by=user,
        file_name="test_import.csv",
        file_path=saved,
        skip_duplicates=True,
    )


# ---------------------------------------------------------------------------
# process_import_job task tests
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestProcessImportJob:
    def test_creates_contacts_from_csv(self, import_job, tenant):
        from contacts.tasks import process_import_job

        process_import_job(import_job.pk)

        import_job.refresh_from_db()
        assert import_job.status == ImportJob.Status.COMPLETED
        assert import_job.created_count == 3
        assert import_job.skipped_count == 0
        assert import_job.total_rows == 3

        contacts = TenantContact.objects.filter(tenant=tenant, source=ContactSource.IMPORT)
        assert contacts.count() == 3

    def test_skips_duplicates(self, import_job, tenant):
        """When skip_duplicates=True, existing phones are skipped."""
        from contacts.tasks import process_import_job

        TenantContact.objects.create(tenant=tenant, phone="+919200000001", first_name="Existing")

        process_import_job(import_job.pk)

        import_job.refresh_from_db()
        assert import_job.created_count == 2
        assert import_job.skipped_count == 1

    def test_handles_missing_job(self):
        """Task handles non-existent job gracefully."""
        from contacts.tasks import process_import_job

        # Should not raise
        process_import_job(999999)

    def test_handles_empty_phone(self, tenant, user):
        """Rows with empty phone are silently skipped."""
        from contacts.tasks import process_import_job

        csv_data = _make_csv(
            [
                ["phone", "first_name"],
                ["", "NoPhone"],
                ["+919200000099", "HasPhone"],
            ]
        )
        path = default_storage.save(f"imports/{tenant.pk}/empty_phone.csv", io.BytesIO(csv_data))
        job = ImportJob.objects.create(tenant=tenant, created_by=user, file_name="empty_phone.csv", file_path=path)

        process_import_job(job.pk)

        job.refresh_from_db()
        assert job.created_count == 1
        assert job.status == ImportJob.Status.COMPLETED

    def test_duplicate_csv_headers_deduplicated(self, tenant, user):
        """CSV with duplicate headers gets _1 suffix (#32)."""
        from contacts.tasks import _parse_csv

        csv_data = b"phone,tag,tag\n+919300000001,a,b\n"
        rows = _parse_csv(csv_data)

        assert len(rows) == 1
        assert "tag" in rows[0]
        assert "tag_1" in rows[0]


# ---------------------------------------------------------------------------
# bulk_import endpoint tests
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestBulkImportEndpoint:
    def test_upload_csv(self, tenant_user, monkeypatch):
        """POST /contacts/v1/bulk-import/ with CSV creates ImportJob and returns 202."""
        from rest_framework.test import APIClient

        from contacts import tasks

        monkeypatch.setattr(tasks.process_import_job, "delay", lambda job_id: None)

        csv_data = _make_csv([["phone", "first_name"], ["+919200000010", "Test"]])

        client = APIClient()
        client.force_authenticate(user=tenant_user.user)

        f = SimpleUploadedFile("contacts.csv", csv_data, content_type="text/csv")
        resp = client.post("/contacts/bulk-import/", {"file": f}, format="multipart")

        assert resp.status_code == 202
        assert "import_job_id" in resp.data

        job = ImportJob.objects.get(pk=resp.data["import_job_id"])
        assert job.tenant == tenant_user.tenant
        assert job.file_name == "contacts.csv"

    def test_rejects_invalid_file_type(self, tenant_user, monkeypatch):
        """Non-CSV/XLSX file is rejected with 400."""
        from rest_framework.test import APIClient

        from contacts import tasks

        monkeypatch.setattr(tasks.process_import_job, "delay", lambda job_id: None)

        client = APIClient()
        client.force_authenticate(user=tenant_user.user)

        f = SimpleUploadedFile("data.json", b"{}", content_type="application/json")
        resp = client.post("/contacts/bulk-import/", {"file": f}, format="multipart")

        assert resp.status_code == 400

    def test_import_status(self, tenant_user, import_job):
        """GET /contacts/v1/import-status/<id>/ returns job details."""
        from rest_framework.test import APIClient

        import_job.status = ImportJob.Status.COMPLETED
        import_job.total_rows = 3
        import_job.created_count = 3
        import_job.save()

        client = APIClient()
        client.force_authenticate(user=tenant_user.user)

        resp = client.get(f"/contacts/import-status/{import_job.pk}/")

        assert resp.status_code == 200
        assert resp.data["status"] == ImportJob.Status.COMPLETED
        assert resp.data["created_count"] == 3

    def test_import_status_cross_tenant_forbidden(self, user, import_job):
        """A user from a different tenant cannot see the import job (#34)."""
        from django.contrib.auth import get_user_model
        from rest_framework.test import APIClient

        from tenants.models import Tenant, TenantRole, TenantUser

        other_tenant = Tenant.objects.create(name="Other Tenant")
        other_role = TenantRole.objects.get(tenant=other_tenant, slug="owner")
        other_user = get_user_model().objects.create_user(
            username="other_user", email="other@test.com", mobile="+919100000099", password="testpass123"
        )
        TenantUser.objects.create(tenant=other_tenant, user=other_user, role=other_role, is_active=True)

        client = APIClient()
        client.force_authenticate(user=other_user)

        resp = client.get(f"/contacts/import-status/{import_job.pk}/")

        assert resp.status_code == 404

    def test_path_traversal_sanitized(self, tenant_user, monkeypatch):
        """Malicious filename is sanitized (#18)."""
        from rest_framework.test import APIClient

        from contacts import tasks

        monkeypatch.setattr(tasks.process_import_job, "delay", lambda job_id: None)

        client = APIClient()
        client.force_authenticate(user=tenant_user.user)

        f = SimpleUploadedFile("../../etc/evil.csv", b"phone\n+91900\n", content_type="text/csv")
        resp = client.post("/contacts/bulk-import/", {"file": f}, format="multipart")

        assert resp.status_code == 202
        job = ImportJob.objects.get(pk=resp.data["import_job_id"])
        # Path should NOT contain '..'
        assert ".." not in job.file_path

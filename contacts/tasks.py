"""Celery tasks for contacts app (#118 — bulk import)."""

from __future__ import annotations

import csv
import io
import logging

from celery import shared_task
from django.core.files.storage import default_storage
from django.utils import timezone

logger = logging.getLogger(__name__)


@shared_task(bind=True, max_retries=1, default_retry_delay=120)
def process_import_job(self, import_job_id: int):
    """Process a bulk contact import job asynchronously (#118).

    Supports CSV and XLSX files. For XLSX, requires openpyxl.
    """
    from contacts.models import ImportJob, TenantContact

    try:
        job = ImportJob.objects.get(pk=import_job_id)
    except ImportJob.DoesNotExist:
        logger.error("[process_import_job] ImportJob %s not found", import_job_id)
        return

    job.status = ImportJob.Status.PROCESSING
    job.save(update_fields=["status"])

    errors = []
    created = 0
    skipped = 0
    total = 0

    try:
        with default_storage.open(job.file_path, "rb") as f:
            file_content = f.read()
        is_xlsx = job.file_name.lower().endswith(".xlsx")

        if is_xlsx:
            rows = _parse_xlsx(file_content)
        else:
            rows = _parse_csv(file_content)

        existing_phones = set()
        if job.skip_duplicates:
            existing_phones = set(
                str(p) for p in TenantContact.objects.filter(tenant=job.tenant).values_list("phone", flat=True)
            )

        contacts_to_create = []

        for row_num, row in enumerate(rows, start=2):
            total += 1
            phone = row.get("phone", "").strip()
            if not phone:
                continue

            # Normalize phone
            if not phone.startswith("+"):
                phone = f"+{phone}"

            if job.skip_duplicates and phone in existing_phones:
                skipped += 1
                continue

            contacts_to_create.append(
                TenantContact(
                    tenant=job.tenant,
                    phone=phone,
                    first_name=(row.get("first_name") or "")[:255],
                    last_name=(row.get("last_name") or "")[:255],
                    tag=(row.get("tag") or job.default_tag or "")[:255],
                    source="IMPORT",
                )
            )
            existing_phones.add(phone)

            # Batch create every 500 rows
            if len(contacts_to_create) >= 500:
                created_objs = TenantContact.objects.bulk_create(contacts_to_create, ignore_conflicts=True)
                created += len(created_objs)
                contacts_to_create = []

        # Final batch
        if contacts_to_create:
            created_objs = TenantContact.objects.bulk_create(contacts_to_create, ignore_conflicts=True)
            created += len(created_objs)

        job.status = ImportJob.Status.COMPLETED
        job.total_rows = total
        job.created_count = created
        job.skipped_count = skipped
        job.error_count = len(errors)
        job.errors = errors[:100]  # Cap stored errors
        job.completed_at = timezone.now()
        job.save()

        # Fire notification
        try:
            from notifications.signals import create_contact_imported_notification

            create_contact_imported_notification(job.tenant, created)
        except Exception:
            pass

        logger.info("[process_import_job] Job %s completed: %d created, %d skipped", job.pk, created, skipped)

    except Exception as exc:
        logger.exception("[process_import_job] Job %s failed", job.pk)
        job.status = ImportJob.Status.FAILED
        job.errors = [{"error": str(exc)}]
        job.completed_at = timezone.now()
        job.save(update_fields=["status", "errors", "completed_at"])
        raise self.retry(exc=exc)


def _parse_csv(content: bytes) -> list[dict]:
    decoded = content.decode("utf-8-sig")
    reader = csv.DictReader(io.StringIO(decoded))
    if reader.fieldnames:
        reader.fieldnames = [n.strip().lower() for n in reader.fieldnames]
    return list(reader)


def _parse_xlsx(content: bytes) -> list[dict]:
    import openpyxl

    wb = openpyxl.load_workbook(io.BytesIO(content), read_only=True, data_only=True)
    ws = wb.active
    rows_iter = ws.iter_rows(values_only=True)
    headers = [str(h).strip().lower() if h else f"col_{i}" for i, h in enumerate(next(rows_iter))]
    result = []
    for row in rows_iter:
        result.append({headers[i]: str(cell) if cell is not None else "" for i, cell in enumerate(row)})
    return result

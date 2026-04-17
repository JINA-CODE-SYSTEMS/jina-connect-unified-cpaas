import csv
import io
import logging

from django.contrib.auth import get_user_model
from django.http import HttpResponse
from django.utils import timezone
from django_filters.rest_framework import DjangoFilterBackend
from rest_framework import serializers, status
from rest_framework.decorators import action
from rest_framework.filters import OrderingFilter
from rest_framework.parsers import FormParser, MultiPartParser
from rest_framework.response import Response
from rest_framework.throttling import UserRateThrottle

from abstract.viewsets.base import BaseTenantModelViewSet
from contacts.filters import TenantContactFilter
from contacts.models import AssigneeTypeChoices, ContactSource, TenantContact
from contacts.serializers import ContactAssignmentSerializer, ContactCSVUploadSerializer, TenantContactSerializer

logger = logging.getLogger(__name__)
User = get_user_model()


class ContactsViewSet(BaseTenantModelViewSet):
    """
    ViewSet for managing TenantContacts.

    Supports keyword search via 'search' parameter that searches across:
    - first_name
    - last_name
    - phone

    Example: /api/contacts/?search=john

    CSV Import/Export:
    - GET /contacts/download-template/ - Download CSV template
    - POST /contacts/upload-csv/ - Upload CSV with contacts

    Assignment Endpoints:
    - POST /contacts/{id}/assign/ - Assign contact to user/bot/chatflow
    - POST /contacts/{id}/unassign/ - Unassign contact

    Assignment history is available via timeline (Event table with TICKET_ASSIGNED type).
    """

    queryset = TenantContact.objects.all()
    serializer_class = TenantContactSerializer
    filterset_class = TenantContactFilter
    filter_backends = [DjangoFilterBackend, OrderingFilter]  # Removed SearchFilter to avoid conflicts
    required_permissions = {
        "list": "contact.view",
        "retrieve": "contact.view",
        "create": "contact.create",
        "partial_update": "contact.edit",
        "assign": "contact.edit",
        "unassign": "contact.edit",
        "close": "contact.edit",
        "reopen": "contact.edit",
        "reserve_keyword_list": "contact.view",
        "contact_tags_list": "contact.view",
        "download_template": "contact.import",
        "upload_csv": "contact.import",
        "bulk_import": "contact.import",
        "import_status": "contact.import",
        "export_csv": "contact.export",
        "dashboard": "analytics.view",
        "default": "contact.view",
    }

    # ── Auto-set tenant on create ──────────────────────────────────────

    def perform_create(self, serializer):
        tenant_user = self._get_tenant_user()
        if not tenant_user:
            raise serializers.ValidationError(
                "Could not determine tenant for this request. Ensure the user has an active tenant membership."
            )
        contact = serializer.save(tenant=tenant_user.tenant)
        try:
            from notifications.signals import create_contact_added_notification

            create_contact_added_notification(contact)
        except Exception:
            pass

    # ── Role-scoped queryset ──────────────────────────────────────────

    def get_role_scoped_queryset(self, queryset, user, tenant_user):
        """
        Agents see only contacts assigned to them.
        """
        return queryset.filter(assigned_to_user=user)

    # ==================== Assignment Endpoints ====================

    @action(detail=True, methods=["post"], url_path="assign")
    def assign(self, request, pk=None):
        """
        Assign a contact to a user, bot, or chatflow.

        Request body:
        {
            "assigned_to_type": "USER" | "BOT" | "CHATFLOW",
            "assigned_to_id": 123,
            "note": "Optional note for the assignee"
        }

        Creates an Event entry (via signal) and broadcasts the change via WebSocket.
        """
        contact = self.get_object()
        serializer = ContactAssignmentSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        assigned_to_type = serializer.validated_data["assigned_to_type"]
        assigned_to_id = serializer.validated_data.get("assigned_to_id")
        note = serializer.validated_data.get("note", "")

        # Get the user being assigned to (if type is USER)
        assigned_to_user = None
        if assigned_to_type == AssigneeTypeChoices.USER and assigned_to_id:
            assigned_to_user = User.objects.filter(id=assigned_to_id).first()

        # Determine who is making the assignment (assigned_by)
        assigned_by_type = AssigneeTypeChoices.USER
        assigned_by_id = request.user.id
        assigned_by_user = request.user

        # Store metadata on instance for the signal to use
        contact._assignment_metadata = {
            "ip_address": self._get_client_ip(request),
            "user_agent": request.META.get("HTTP_USER_AGENT", ""),
        }

        # Update the contact (signal will create the Event and broadcast automatically)
        contact.assigned_to_type = assigned_to_type
        contact.assigned_to_id = assigned_to_id
        contact.assigned_to_user = assigned_to_user
        contact.assigned_at = timezone.now()
        contact.assigned_by_type = assigned_by_type
        contact.assigned_by_id = assigned_by_id
        contact.assigned_by_user = assigned_by_user
        contact.assignment_note = note
        contact.save()

        # Return updated contact
        response_serializer = TenantContactSerializer(contact)
        return Response(
            {
                "status": "success",
                "message": f"Contact assigned to {contact.assigned_to_name}",
                "contact": response_serializer.data,
            },
            status=status.HTTP_200_OK,
        )

    @action(detail=True, methods=["post"], url_path="unassign")
    def unassign(self, request, pk=None):
        """
        Unassign a contact (remove current assignment).

        Request body (optional):
        {
            "note": "Optional reason for unassigning"
        }

        Creates an Event entry (via signal) and broadcasts the change via WebSocket.
        """
        contact = self.get_object()
        note = request.data.get("note", "")

        # Determine who is making the assignment (assigned_by)
        assigned_by_type = AssigneeTypeChoices.USER
        assigned_by_id = request.user.id
        assigned_by_user = request.user

        # Store metadata on instance for the signal to use
        contact._assignment_metadata = {
            "ip_address": self._get_client_ip(request),
            "user_agent": request.META.get("HTTP_USER_AGENT", ""),
        }

        # Update the contact (signal will create the Event and broadcast automatically)
        contact.assigned_to_type = AssigneeTypeChoices.UNASSIGNED
        contact.assigned_to_id = None
        contact.assigned_to_user = None
        contact.assigned_at = timezone.now()
        contact.assigned_by_type = assigned_by_type
        contact.assigned_by_id = assigned_by_id
        contact.assigned_by_user = assigned_by_user
        contact.assignment_note = note
        contact.save()

        # Return updated contact
        response_serializer = TenantContactSerializer(contact)
        return Response(
            {
                "status": "success",
                "message": "Contact unassigned",
                "contact": response_serializer.data,
            },
            status=status.HTTP_200_OK,
        )

    @action(detail=True, methods=["post"], url_path="close")
    def close(self, request, pk=None):
        """
        Close a contact/ticket.

        Request body:
        {
            "reason": "Optional reason for closing"
        }

        Creates a TICKET_CLOSED Event entry (via signal) and broadcasts the change via WebSocket.
        """
        from contacts.models import TicketStatusChoices

        contact = self.get_object()
        reason = request.data.get("reason", "")

        # Check if already closed
        if contact.status == TicketStatusChoices.CLOSED:
            return Response(
                {
                    "status": "error",
                    "message": "Contact is already closed",
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        # Store metadata on instance for the signal to use
        contact._status_changed_by_user = request.user
        contact._status_change_reason = reason

        # Update the status (signal will create the Event and broadcast automatically)
        contact.status = TicketStatusChoices.CLOSED
        contact.save()

        # Return updated contact
        response_serializer = TenantContactSerializer(contact)
        return Response(
            {
                "status": "success",
                "message": "Contact closed",
                "contact": response_serializer.data,
            },
            status=status.HTTP_200_OK,
        )

    @action(detail=True, methods=["post"], url_path="reopen")
    def reopen(self, request, pk=None):
        """
        Reopen a closed contact/ticket.

        Request body:
        {
            "reason": "Optional reason for reopening"
        }

        Creates a TICKET_REOPENED Event entry (via signal) and broadcasts the change via WebSocket.
        """
        from contacts.models import TicketStatusChoices

        contact = self.get_object()
        reason = request.data.get("reason", "")

        # Check if already open
        if contact.status == TicketStatusChoices.OPEN:
            return Response(
                {
                    "status": "error",
                    "message": "Contact is already open",
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        # Store metadata on instance for the signal to use
        contact._status_changed_by_user = request.user
        contact._status_change_reason = reason

        # Update the status (signal will create the Event and broadcast automatically)
        contact.status = TicketStatusChoices.OPEN
        contact.save()

        # Return updated contact
        response_serializer = TenantContactSerializer(contact)
        return Response(
            {
                "status": "success",
                "message": "Contact reopened",
                "contact": response_serializer.data,
            },
            status=status.HTTP_200_OK,
        )

    def _get_client_ip(self, request):
        """Get client IP address from request."""
        x_forwarded_for = request.META.get("HTTP_X_FORWARDED_FOR")
        if x_forwarded_for:
            return x_forwarded_for.split(",")[0].strip()
        return request.META.get("REMOTE_ADDR", "")

    # ==================== Original Endpoints ====================

    @action(detail=False, methods=["get"], url_path="reserve-keyword")
    def reserve_keyword_list(self, request):
        data = TenantContact.RESERVED_VARS
        return Response(data, status=200)

    @action(detail=False, methods=["get"], url_path="contact-tags")
    def contact_tags_list(self, request):
        search = request.query_params.get("search", None)
        tags_qs = (
            TenantContact.objects.filter(tag__isnull=False).exclude(tag="").values_list("tag", flat=True).distinct()
        )

        if search:
            tags_qs = tags_qs.filter(tag__icontains=search)

        tags_list = list(tags_qs)
        page = self.paginate_queryset(tags_list)
        if page is not None:
            return self.get_paginated_response(page)
        return Response(tags_list, status=200)

    @action(detail=False, methods=["get"], url_path="download-template")
    def download_template(self, request):
        """
        Download a CSV template for bulk contact import.

        The template includes:
        - Required columns: phone
        - Optional columns: first_name, last_name, tag
        - Sample data row for reference

        Returns: CSV file download
        """
        response = HttpResponse(content_type="text/csv")
        response["Content-Disposition"] = 'attachment; filename="contacts_template.csv"'

        writer = csv.writer(response)

        # Header row
        writer.writerow(["phone", "first_name", "last_name", "tag"])

        # Sample data rows with instructions
        writer.writerow(["+14155552671", "John", "Doe", "customer"])
        writer.writerow(["+919876543210", "Jane", "Smith", "lead"])
        writer.writerow(["+442071234567", "Bob", "", "partner"])

        # Add instructions as comments (will be ignored during import)
        writer.writerow([])
        writer.writerow(["# Instructions:"])
        writer.writerow(["# - phone: Required. Use international format with country code (e.g., +14155552671)"])
        writer.writerow(["# - first_name: Optional. Contact first name"])
        writer.writerow(["# - last_name: Optional. Contact last name"])
        writer.writerow(["# - tag: Optional. Tag to categorize the contact"])
        writer.writerow(["# - Delete sample rows before uploading your data"])
        writer.writerow(["# - Lines starting with # are ignored"])

        return response

    @action(detail=False, methods=["post"], url_path="upload-csv", parser_classes=[MultiPartParser, FormParser])
    def upload_csv(self, request):
        """
        Upload a CSV file to bulk import contacts.

        Request:
        - file: CSV file with contacts (multipart/form-data)
        - skip_duplicates: Optional boolean (default: true) - Skip contacts with existing phone numbers
        - default_tag: Optional string - Default tag for contacts without a tag

        CSV Format:
        - Required columns: phone
        - Optional columns: first_name, last_name, tag

        Returns:
        - created_count: Number of contacts created
        - skipped_count: Number of contacts skipped (duplicates or invalid)
        - errors: List of errors with row numbers
        """
        serializer = ContactCSVUploadSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        csv_file = serializer.validated_data["file"]
        skip_duplicates = serializer.validated_data.get("skip_duplicates", True)
        default_tag = serializer.validated_data.get("default_tag", "")

        # Get tenant from request
        tenant = self._get_tenant(request)
        if not tenant:
            return Response(
                {"error": "Could not determine tenant for this request"}, status=status.HTTP_400_BAD_REQUEST
            )

        # Process CSV
        result = self._process_csv(csv_file, tenant, skip_duplicates, default_tag, request.user)

        return Response(result, status=status.HTTP_201_CREATED if result["created_count"] > 0 else status.HTTP_200_OK)

    def _get_tenant(self, request):
        """Get tenant from request user."""
        try:
            if hasattr(request.user, "user_tenants") and request.user.user_tenants.exists():
                return request.user.user_tenants.first().tenant
        except Exception:
            pass
        return None

    def _process_csv(self, csv_file, tenant, skip_duplicates, default_tag, user):
        """
        Process the uploaded CSV file and create contacts.

        Returns a dict with created_count, skipped_count, updated_count, and errors.
        """
        result = {"created_count": 0, "skipped_count": 0, "updated_count": 0, "total_rows": 0, "errors": []}

        try:
            # Read CSV file
            decoded_file = csv_file.read().decode("utf-8-sig")  # Handle BOM
            csv_reader = csv.DictReader(io.StringIO(decoded_file))

            # Normalize column names (strip whitespace, lowercase)
            if csv_reader.fieldnames:
                csv_reader.fieldnames = [name.strip().lower() for name in csv_reader.fieldnames]

            # Validate required columns
            if "phone" not in csv_reader.fieldnames:
                result["errors"].append(
                    {
                        "row": 0,
                        "error": "Missing required column 'phone'. Found columns: "
                        + ", ".join(csv_reader.fieldnames or []),
                    }
                )
                return result

            # Get existing phone numbers for this tenant (for duplicate checking)
            existing_phones = set()
            if skip_duplicates:
                existing_phones = set(TenantContact.objects.filter(tenant=tenant).values_list("phone", flat=True))

            contacts_to_create = []

            for row_num, row in enumerate(csv_reader, start=2):  # Start at 2 (1 is header)
                result["total_rows"] += 1

                # Skip empty rows or comment rows
                phone = row.get("phone", "").strip()
                if not phone or phone.startswith("#"):
                    continue

                # Normalize phone number
                if not phone.startswith("+"):
                    phone = "+" + phone

                # Check for duplicates
                if skip_duplicates and phone in existing_phones:
                    result["skipped_count"] += 1
                    continue

                # Validate phone number format
                try:
                    from phonenumber_field.phonenumber import PhoneNumber

                    parsed = PhoneNumber.from_string(phone)
                    if not parsed.is_valid():
                        result["errors"].append(
                            {"row": row_num, "phone": phone, "error": "Invalid phone number format"}
                        )
                        result["skipped_count"] += 1
                        continue
                except Exception as e:
                    result["errors"].append(
                        {"row": row_num, "phone": phone, "error": f"Invalid phone number: {str(e)}"}
                    )
                    result["skipped_count"] += 1
                    continue

                # Create contact object
                contact = TenantContact(
                    phone=phone,
                    first_name=row.get("first_name", "").strip()[:255],
                    last_name=row.get("last_name", "").strip()[:255],
                    tag=row.get("tag", "").strip()[:255] or default_tag,
                    tenant=tenant,
                    source=ContactSource.MANUAL,
                    created_by=user,
                )

                contacts_to_create.append(contact)
                existing_phones.add(phone)  # Prevent duplicates within same file

            # Bulk create contacts
            if contacts_to_create:
                TenantContact.objects.bulk_create(contacts_to_create, ignore_conflicts=True)
                result["created_count"] = len(contacts_to_create)
                try:
                    from notifications.signals import create_contact_imported_notification

                    create_contact_imported_notification(tenant, result["created_count"])
                except Exception:
                    pass

            logger.info(f"CSV import completed: {result['created_count']} created, {result['skipped_count']} skipped")

        except UnicodeDecodeError:
            result["errors"].append(
                {"row": 0, "error": "Invalid file encoding. Please save your CSV file with UTF-8 encoding."}
            )
        except csv.Error as e:
            result["errors"].append({"row": 0, "error": f"CSV parsing error: {str(e)}"})
        except Exception as e:
            logger.exception(f"Error processing CSV: {e}")
            result["errors"].append({"row": 0, "error": f"Unexpected error: {str(e)}"})

        return result

    @action(detail=False, methods=["get"], url_path="export-csv")
    def export_csv(self, request):
        """
        Export all contacts to a CSV file.

        Query Parameters:
        - tag: Optional. Filter contacts by tag
        - search: Optional. Search contacts by name or phone

        Returns: CSV file download with all matching contacts
        """
        # Get filtered queryset
        queryset = self.filter_queryset(self.get_queryset())

        response = HttpResponse(content_type="text/csv")
        response["Content-Disposition"] = 'attachment; filename="contacts_export.csv"'

        writer = csv.writer(response)

        # Header row
        writer.writerow(["phone", "first_name", "last_name", "tag", "source", "created_at"])

        # Data rows
        for contact in queryset:
            writer.writerow(
                [
                    str(contact.phone),
                    contact.first_name,
                    contact.last_name,
                    contact.tag,
                    contact.source,
                    contact.created_at.isoformat() if contact.created_at else "",
                ]
            )

        return response

    @action(detail=False, methods=["get"], url_path="dashboard")
    def dashboard(self, request):
        """
        Get contact growth & engagement dashboard statistics.

        Query Parameters:
            period (str): Time range — 24h, 7d, 30d, 90d (default: 7d)
        """
        from datetime import timedelta

        from django.db.models import Count, Q

        from wa.models import MessageDirection, WAMessage

        # ── Period parsing ──────────────────────────────────────────
        period = request.query_params.get("period", "7d")
        period_days = {"24h": 1, "7d": 7, "30d": 30, "90d": 90}
        if period not in period_days:
            return Response(
                {"error": f"Invalid period. Use one of: {', '.join(period_days.keys())}"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        now = timezone.now()
        days = period_days[period]
        period_start = now - timedelta(days=days)
        prev_period_start = period_start - timedelta(days=days)

        # ── Tenant-scoped base queryset ─────────────────────────────
        tenant_user = self._get_tenant_user()
        if not tenant_user:
            return Response({"error": "Could not determine tenant"}, status=status.HTTP_400_BAD_REQUEST)
        tenant = tenant_user.tenant
        all_contacts = TenantContact.objects.filter(tenant=tenant)
        total_contacts = all_contacts.count()

        # ── New contacts (current vs previous period) ───────────────
        new_contacts = all_contacts.filter(created_at__gte=period_start).count()
        prev_new_contacts = all_contacts.filter(
            created_at__gte=prev_period_start,
            created_at__lt=period_start,
        ).count()
        if prev_new_contacts > 0:
            new_contacts_change_percent = round(((new_contacts - prev_new_contacts) / prev_new_contacts) * 100, 1)
        else:
            new_contacts_change_percent = 100.0 if new_contacts > 0 else 0.0

        # ── Active vs Inactive (activity in last 30 days) ──────────
        thirty_days_ago = now - timedelta(days=30)
        active_contact_ids = (
            WAMessage.objects.filter(
                contact__tenant=tenant,
                created_at__gte=thirty_days_ago,
            )
            .values_list("contact_id", flat=True)
            .distinct()
        )
        active_contacts = all_contacts.filter(id__in=active_contact_ids).count()
        inactive_contacts = total_contacts - active_contacts

        if total_contacts > 0:
            active_percent = round((active_contacts / total_contacts) * 100, 1)
            inactive_percent = round((inactive_contacts / total_contacts) * 100, 1)
        else:
            active_percent = 0.0
            inactive_percent = 0.0

        # ── Top segment (tag with highest engagement rate) ──────────
        top_segment = {"name": None, "contact_count": 0, "engagement_rate": 0.0}
        tag_stats = (
            all_contacts.exclude(tag="")
            .values("tag")
            .annotate(
                contact_count=Count("id", distinct=True),
                engaged_count=Count(
                    "id",
                    filter=Q(
                        wa_messages__direction=MessageDirection.INBOUND,
                        wa_messages__created_at__gte=period_start,
                    ),
                    distinct=True,
                ),
            )
            .filter(contact_count__gte=5)
            .order_by("-contact_count")[:100]
        )
        best_rate = -1.0
        for t in tag_stats:
            rate = round((t["engaged_count"] / t["contact_count"]) * 100, 1)
            if rate > best_rate:
                best_rate = rate
                top_segment = {
                    "name": t["tag"],
                    "contact_count": t["contact_count"],
                    "engagement_rate": rate,
                }

        # ── Funnel metrics (scoped to selected period) ──────────────
        messaged_count = (
            all_contacts.filter(
                wa_messages__direction=MessageDirection.OUTBOUND,
                wa_messages__created_at__gte=period_start,
            )
            .distinct()
            .count()
        )
        engaged_count = (
            all_contacts.filter(
                wa_messages__direction=MessageDirection.INBOUND,
                wa_messages__created_at__gte=period_start,
            )
            .distinct()
            .count()
        )

        return Response(
            {
                "new_contacts": new_contacts,
                "new_contacts_change_percent": new_contacts_change_percent,
                "total_contacts": total_contacts,
                "active_contacts": active_contacts,
                "inactive_contacts": inactive_contacts,
                "active_percent": active_percent,
                "inactive_percent": inactive_percent,
                "top_segment": top_segment,
                "funnel": {
                    "total_reach": total_contacts,
                    "messaged": messaged_count,
                    "engaged": engaged_count,
                    "converted": engaged_count,
                },
                "period": period,
            }
        )

    @action(
        detail=False,
        methods=["post"],
        url_path="bulk-import",
        parser_classes=[MultiPartParser, FormParser],
        throttle_classes=[UserRateThrottle],
    )
    def bulk_import(self, request):
        """Upload CSV or XLSX file for async bulk contact import (#118).

        Returns an ImportJob ID that can be polled via ``import-status/<id>/``.
        """
        from contacts.models import ImportJob
        from contacts.tasks import process_import_job

        file_obj = request.FILES.get("file")
        if not file_obj:
            return Response({"error": "No file provided"}, status=status.HTTP_400_BAD_REQUEST)

        name = file_obj.name.lower()
        if not (name.endswith(".csv") or name.endswith(".xlsx")):
            return Response({"error": "Only .csv and .xlsx files are supported"}, status=status.HTTP_400_BAD_REQUEST)

        if file_obj.size > 10 * 1024 * 1024:
            return Response({"error": "File size exceeds 10 MB limit"}, status=status.HTTP_400_BAD_REQUEST)

        tenant_user = self._get_tenant_user()
        if not tenant_user:
            return Response({"error": "Could not determine tenant"}, status=status.HTTP_400_BAD_REQUEST)
        tenant = tenant_user.tenant

        # Save file to default storage
        from django.core.files.storage import default_storage
        from django.utils.text import get_valid_filename

        safe_name = get_valid_filename(file_obj.name)
        path = f"imports/{tenant.pk}/{safe_name}"
        saved_path = default_storage.save(path, file_obj)

        job = ImportJob.objects.create(
            tenant=tenant,
            created_by=request.user,
            file_name=file_obj.name,
            file_path=saved_path,
            skip_duplicates=request.data.get("skip_duplicates", "true").lower() in ("true", "1", "yes"),
            default_tag=request.data.get("default_tag", ""),
        )

        process_import_job.delay(job.pk)

        return Response(
            {"import_job_id": job.pk, "status": job.status, "file_name": job.file_name},
            status=status.HTTP_202_ACCEPTED,
        )

    @action(detail=False, methods=["get"], url_path=r"import-status/(?P<job_id>\d+)")
    def import_status(self, request, job_id=None):
        """Check the status of a bulk import job (#118)."""
        from contacts.models import ImportJob

        try:
            job = ImportJob.objects.get(pk=job_id, tenant__tenant_users__user=request.user)
        except ImportJob.DoesNotExist:
            return Response({"error": "Import job not found"}, status=status.HTTP_404_NOT_FOUND)

        return Response(
            {
                "id": job.pk,
                "status": job.status,
                "file_name": job.file_name,
                "total_rows": job.total_rows,
                "created_count": job.created_count,
                "skipped_count": job.skipped_count,
                "error_count": job.error_count,
                "errors": job.errors[:20],
                "created_at": job.created_at.isoformat(),
                "completed_at": job.completed_at.isoformat() if job.completed_at else None,
            }
        )

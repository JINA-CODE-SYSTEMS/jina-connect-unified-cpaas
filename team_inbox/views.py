import io
import logging
import re
from datetime import timedelta

from django.http import HttpResponse
from django.utils import timezone
from django.utils.dateparse import parse_date
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import (Flowable, Paragraph, SimpleDocTemplate,
                                 Spacer)
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from contacts.models import TenantContact
from team_inbox.models import Messages, MessageDirectionChoices, AuthorChoices
from tenants.permission_classes import TenantRolePermission

logger = logging.getLogger(__name__)

MAX_DATE_RANGE_DAYS = 90


def _sanitize_filename(name):
    """Remove characters unsafe for filenames."""
    return re.sub(r'[^\w\s\-]', '', name).strip().replace(' ', '-')


def _extract_message_text(content):
    """
    Extract human-readable text from the Messages.content JSONField.

    Supported structures (see validators.py):
      text   → body.text
      image  → image.caption or "[Image]"
      video  → video.caption or "[Video]"
      audio  → "[Audio]"
      document → document.caption or document.filename or "[Document]"
      cards  → body.text from each card summarised
      other  → body.text fallback or "[Message]"
    """
    if not isinstance(content, dict):
        return "[Message]"

    msg_type = content.get("type", "text")

    # Body text is the most common field
    body_text = ""
    body = content.get("body")
    if isinstance(body, dict):
        body_text = body.get("text", "")

    if msg_type == "text":
        return body_text or "[Empty message]"

    if msg_type == "image":
        img = content.get("image", {})
        caption = img.get("caption", "")
        return f"[Image] {caption}".strip() if caption else "[Image]"

    if msg_type == "video":
        vid = content.get("video", {})
        caption = vid.get("caption", "")
        return f"[Video] {caption}".strip() if caption else "[Video]"

    if msg_type == "audio":
        return "[Audio]"

    if msg_type == "document":
        doc = content.get("document", {})
        caption = doc.get("caption", "")
        filename = doc.get("filename", "")
        label = caption or filename or ""
        return f"[Document] {label}".strip() if label else "[Document]"

    if msg_type == "cards":
        cards = content.get("cards", [])
        if cards:
            summaries = []
            for i, card in enumerate(cards, 1):
                card_body = card.get("body", {}).get("text", "")
                summaries.append(f"Card {i}: {card_body}" if card_body else f"Card {i}")
            return "[Cards] " + " | ".join(summaries)
        return "[Cards]"

    # Fallback for interactive / unknown
    if body_text:
        return body_text
    return f"[{msg_type.capitalize()}]"


def _sender_label(msg):
    """Return a display name for the message sender."""
    if msg.author == AuthorChoices.CONTACT:
        if msg.contact:
            name = f"{msg.contact.first_name or ''} {msg.contact.last_name or ''}".strip()
            return name or str(msg.contact.phone)
        return "Contact"
    if msg.author == AuthorChoices.USER:
        if msg.tenant_user:
            return msg.tenant_user.get_full_name() or msg.tenant_user.username
        return "Agent"
    if msg.author == AuthorChoices.BOT:
        return "Bot"
    return "System"


class HLine(Flowable):
    """A thin horizontal rule for visual separation."""

    def __init__(self, width=0, color=colors.HexColor("#E5E7EB"), thickness=0.5):
        super().__init__()
        self.width = width
        self.color = color
        self.thickness = thickness

    def draw(self):
        self.canv.setStrokeColor(self.color)
        self.canv.setLineWidth(self.thickness)
        self.canv.line(0, 0, self.width, 0)

    def wrap(self, availWidth, availHeight):
        self.width = availWidth
        return (availWidth, self.thickness + 2)


class ExportPDFView(APIView):
    """
    POST /team-inbox/api/export-pdf/

    Generate and return a PDF of chat messages for a contact within a date range.
    """
    permission_classes = [IsAuthenticated, TenantRolePermission]
    required_permissions = {
        "post": "inbox.view",
    }

    def post(self, request, *args, **kwargs):
        chat_id = request.data.get("chat_id")
        contact_name = request.data.get("contact_name", "")
        start_date_str = request.data.get("start_date")
        end_date_str = request.data.get("end_date")

        # --- Validation ---
        if not chat_id or not start_date_str or not end_date_str:
            return Response(
                {"error": "chat_id, start_date, and end_date are required."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        start_date = parse_date(start_date_str)
        end_date = parse_date(end_date_str)
        if not start_date or not end_date:
            return Response(
                {"error": "Invalid date format. Use YYYY-MM-DD."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        if start_date > end_date:
            return Response(
                {"error": "start_date must be before end_date."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        if (end_date - start_date).days > MAX_DATE_RANGE_DAYS:
            return Response(
                {"error": f"Date range must not exceed {MAX_DATE_RANGE_DAYS} days."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # --- Tenant scoping ---
        user = request.user
        try:
            if user.is_superuser:
                contact = TenantContact.objects.get(pk=chat_id)
            else:
                contact = TenantContact.objects.filter(
                    pk=chat_id,
                    tenant__tenant_users__user=user,
                ).first()
                if not contact:
                    return Response(
                        {"error": "Contact not found or access denied."},
                        status=status.HTTP_404_NOT_FOUND,
                    )
        except TenantContact.DoesNotExist:
            return Response(
                {"error": "Contact not found."},
                status=status.HTTP_404_NOT_FOUND,
            )

        # --- Query messages ---
        # end_date is inclusive, so query up to end_date + 1 day
        end_datetime = timezone.make_aware(
            timezone.datetime.combine(end_date + timedelta(days=1), timezone.datetime.min.time())
        )
        start_datetime = timezone.make_aware(
            timezone.datetime.combine(start_date, timezone.datetime.min.time())
        )

        messages = (
            Messages.objects.filter(
                contact_id=chat_id,
                timestamp__gte=start_datetime,
                timestamp__lt=end_datetime,
            )
            .select_related("contact", "tenant_user")
            .order_by("timestamp")
        )

        if not messages.exists():
            return Response(
                {"error": "No messages found in the selected date range."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # --- Build PDF ---
        buf = io.BytesIO()
        doc = SimpleDocTemplate(
            buf,
            pagesize=A4,
            topMargin=20 * mm,
            bottomMargin=20 * mm,
            leftMargin=15 * mm,
            rightMargin=15 * mm,
        )

        styles = getSampleStyleSheet()
        story = []

        # Custom styles
        title_style = ParagraphStyle(
            "ExportTitle",
            parent=styles["Heading1"],
            fontSize=16,
            spaceAfter=4,
            textColor=colors.HexColor("#1F2937"),
        )
        subtitle_style = ParagraphStyle(
            "ExportSubtitle",
            parent=styles["Normal"],
            fontSize=10,
            textColor=colors.HexColor("#6B7280"),
            spaceAfter=12,
        )
        sender_style = ParagraphStyle(
            "SenderName",
            parent=styles["Normal"],
            fontSize=9,
            textColor=colors.HexColor("#374151"),
            fontName="Helvetica-Bold",
        )
        msg_style_in = ParagraphStyle(
            "MsgIn",
            parent=styles["Normal"],
            fontSize=10,
            textColor=colors.HexColor("#1F2937"),
            leftIndent=0,
            spaceBefore=2,
            spaceAfter=2,
        )
        msg_style_out = ParagraphStyle(
            "MsgOut",
            parent=styles["Normal"],
            fontSize=10,
            textColor=colors.HexColor("#1E40AF"),
            leftIndent=0,
            spaceBefore=2,
            spaceAfter=2,
        )
        footer_style = ParagraphStyle(
            "Footer",
            parent=styles["Normal"],
            fontSize=7,
            textColor=colors.HexColor("#9CA3AF"),
            alignment=1,
        )

        # Header
        display_name = contact_name or contact.full_name or str(contact.phone)
        phone = str(contact.phone) if contact.phone else ""
        story.append(Paragraph(f"Chat Export: {display_name}", title_style))
        story.append(
            Paragraph(
                f"{phone} &bull; {start_date_str} to {end_date_str}",
                subtitle_style,
            )
        )
        story.append(HLine())
        story.append(Spacer(1, 6 * mm))

        # Messages
        for msg in messages.iterator():
            sender = _sender_label(msg)
            direction = "\u2192" if msg.direction == MessageDirectionChoices.OUTGOING else "\u2190"
            ts = msg.timestamp.strftime("%b %d, %Y %I:%M %p") if msg.timestamp else ""
            text = _extract_message_text(msg.content)

            is_outgoing = msg.direction == MessageDirectionChoices.OUTGOING
            current_msg_style = msg_style_out if is_outgoing else msg_style_in

            # Sender + direction + time
            story.append(
                Paragraph(
                    f"{direction} <b>{sender}</b> &nbsp; <font size=7 color='#9CA3AF'>{ts}</font>",
                    sender_style,
                )
            )
            # Message text (escape XML entities for reportlab)
            safe_text = (
                text.replace("&", "&amp;")
                .replace("<", "&lt;")
                .replace(">", "&gt;")
            )
            story.append(Paragraph(safe_text, current_msg_style))
            story.append(Spacer(1, 3 * mm))

        # Footer
        story.append(Spacer(1, 8 * mm))
        story.append(HLine())
        story.append(Spacer(1, 2 * mm))
        now = timezone.now().strftime("%b %d, %Y %I:%M %p")
        story.append(Paragraph(f"Generated by Jina Connect &bull; {now}", footer_style))

        doc.build(story)

        # --- Response ---
        buf.seek(0)
        safe_name = _sanitize_filename(display_name)
        filename = f"chat-{safe_name}-{start_date_str}-to-{end_date_str}.pdf"

        response = HttpResponse(buf.read(), content_type="application/pdf")
        response["Content-Disposition"] = f'attachment; filename="{filename}"'
        return response

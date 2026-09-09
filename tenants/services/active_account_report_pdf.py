"""PDF rendering for the Active Customer Account report (Cl. 4.2).

The PDF is a contractual artefact: a partner reads it to check an invoice.
So it states its own basis — that accounts are counted whether or not they
send messages, that partial months are pro-rated by day, and which timezone
decides a day boundary — rather than presenting bare numbers the reader has
to take on trust.
"""

from io import BytesIO

from django.conf import settings
from django.utils import timezone
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from tenants.services.active_account_report import ActiveAccountReport

DATE_FORMAT = "%d %b %Y"


def _basis_note() -> str:
    return (
        "An active customer account is any account onboarded on this deployment, whether or not it "
        "sent messages during the period. Accounts present for part of the month are counted pro rata "
        "by day, inclusive of both the onboarding and archival dates. Day boundaries follow "
        f"{settings.TIME_ZONE}."
    )


def render_active_account_report_pdf(report: ActiveAccountReport, partner_name: str | None = None) -> bytes:
    """Render the report to PDF bytes."""
    partner_name = partner_name or settings.PARTNER_NAME

    buffer = BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        topMargin=20 * mm,
        bottomMargin=20 * mm,
        leftMargin=15 * mm,
        rightMargin=15 * mm,
        title=f"Active Customer Account Report - {report.label}",
    )

    styles = getSampleStyleSheet()
    small = ParagraphStyle(
        "small", parent=styles["Normal"], fontSize=8, leading=11, textColor=colors.HexColor("#555555")
    )

    story = [
        Paragraph("Active Customer Account Report", styles["Title"]),
        Paragraph(f"{partner_name} &mdash; {report.label}", styles["Heading2"]),
        Spacer(1, 4 * mm),
        Paragraph(
            f"Period: {report.period_start.strftime(DATE_FORMAT)} to {report.period_end.strftime(DATE_FORMAT)} "
            f"({report.days_in_month} days)",
            styles["Normal"],
        ),
        Spacer(1, 6 * mm),
    ]

    summary = Table(
        [
            ["Accounts in period", str(report.account_count)],
            ["Full month", str(report.full_month_count)],
            ["Partial month", str(report.partial_month_count)],
            ["Billable accounts (pro rata)", f"{report.billable_accounts}"],
        ],
        colWidths=[70 * mm, 30 * mm],
        hAlign="LEFT",
    )
    summary.setStyle(
        TableStyle(
            [
                ("FONTSIZE", (0, 0), (-1, -1), 9),
                ("ALIGN", (1, 0), (1, -1), "RIGHT"),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
                ("LINEBELOW", (0, -2), (-1, -2), 0.4, colors.HexColor("#cccccc")),
                ("FONTNAME", (0, -1), (-1, -1), "Helvetica-Bold"),
            ]
        )
    )
    story += [summary, Spacer(1, 8 * mm)]

    rows = [["#", "Account", "Onboarded", "Archived", "Days", "Pro rata"]]
    for index, line in enumerate(report.lines, start=1):
        rows.append(
            [
                str(index),
                line.name,
                line.onboarded_on.strftime(DATE_FORMAT),
                line.archived_on.strftime(DATE_FORMAT) if line.archived_on else "—",
                f"{line.active_days}/{line.days_in_month}",
                f"{line.prorata}",
            ]
        )

    if not report.lines:
        rows.append(["", "No accounts in this period", "", "", "", ""])

    table = Table(rows, colWidths=[10 * mm, 60 * mm, 28 * mm, 28 * mm, 28 * mm, 22 * mm], repeatRows=1)
    table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#f0f0f0")),
                ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                ("FONTSIZE", (0, 0), (-1, -1), 8),
                ("ALIGN", (4, 0), (-1, -1), "RIGHT"),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("GRID", (0, 0), (-1, -1), 0.3, colors.HexColor("#dddddd")),
                ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#fafafa")]),
                ("TOPPADDING", (0, 0), (-1, -1), 3),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
            ]
        )
    )
    story += [table, Spacer(1, 8 * mm), Paragraph(_basis_note(), small)]

    generated = timezone.localtime(timezone.now()).strftime("%d %b %Y %H:%M %Z")
    story += [Spacer(1, 3 * mm), Paragraph(f"Generated {generated}.", small)]

    doc.build(story)
    return buffer.getvalue()

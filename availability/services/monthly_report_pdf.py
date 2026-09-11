"""PDF rendering for the monthly availability report (Cl. 5.4).

A partner reads this to decide whether a service credit is owed, so it has
to state the things that change the number rather than presenting a bare
percentage:

* that planned maintenance is excluded from both sides of the fraction
* that downtime is summed across probe targets rather than maxed, which
  biases the figure against us
* that a failed probe counts as one whole interval, so an outage shorter
  than the interval is invisible and a longer one rounds up
* whether the month's data is complete, prominently, when it is not

The last matters most. A report computed from partial data can look
excellent precisely because the monitoring failed, and a reader must not
have to infer that from a coverage column.
"""

from io import BytesIO

from django.conf import settings
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from availability.services.monthly_report import AvailabilityReport

DATE_FORMAT = "%d %b %Y"


def _hours(seconds: int) -> str:
    if seconds == 0:
        return "none"
    hours, remainder = divmod(seconds, 3600)
    minutes = remainder // 60
    if hours:
        return f"{hours}h {minutes:02d}m"
    return f"{minutes}m"


def _basis_note() -> str:
    return (
        "Availability is measured by external synthetic probes and expressed as "
        "(period − planned maintenance − unplanned downtime) ÷ (period − planned maintenance), so planned "
        "maintenance is excluded from both sides. Downtime is summed across probe targets rather than taking "
        "the greatest, because the platform is unusable if either the API or the web interface is down; where "
        "outages overlap this understates availability rather than overstating it. Each failed probe counts as "
        "one full probe interval, so an outage shorter than the interval is not visible and a longer one is "
        f"rounded up. Period boundaries follow {settings.TIME_ZONE}."
    )


def render_availability_report_pdf(report: AvailabilityReport, partner_name: str | None = None) -> bytes:
    """Render the availability report to PDF bytes."""
    partner_name = partner_name or settings.PARTNER_NAME

    buffer = BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        topMargin=20 * mm,
        bottomMargin=20 * mm,
        leftMargin=15 * mm,
        rightMargin=15 * mm,
        title=f"Availability Report - {report.label}",
    )

    styles = getSampleStyleSheet()
    small = ParagraphStyle(
        "small", parent=styles["Normal"], fontSize=8, leading=11, textColor=colors.HexColor("#555555")
    )
    warn = ParagraphStyle("warn", parent=styles["Normal"], fontSize=9, leading=12, textColor=colors.HexColor("#8a2f2f"))

    story = [
        Paragraph("Service Availability Report", styles["Title"]),
        Paragraph(f"{partner_name} &mdash; {report.label}", styles["Heading2"]),
        Spacer(1, 4 * mm),
        Paragraph(
            f"Period: {report.period_start.strftime(DATE_FORMAT)} to {report.period_end.strftime(DATE_FORMAT)}",
            styles["Normal"],
        ),
        Spacer(1, 6 * mm),
    ]

    # Incomplete data goes above the headline figure, not below it. A reader
    # should not reach the percentage before learning it is unreliable.
    if not report.is_complete:
        story += [
            Paragraph(
                f"<b>Incomplete data.</b> {report.missing_days} of {report.days_expected} days have no "
                f"monitoring record for every probe target. Days without data are not counted as available, "
                f"and the figure below is computed only from the days that were measured.",
                warn,
            ),
            Spacer(1, 5 * mm),
        ]

    verdict = "Met" if report.meets_commitment else "Not met"
    summary = Table(
        [
            ["Commitment", f"{report.commitment}%"],
            ["Measured availability", f"{report.availability}%"],
            ["Planned maintenance excluded", _hours(report.maintenance_seconds)],
            ["Unplanned downtime", _hours(report.downtime_seconds)],
            ["Days with complete data", f"{report.days_covered}/{report.days_expected}"],
            ["Commitment", verdict],
        ],
        colWidths=[70 * mm, 40 * mm],
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
                (
                    "TEXTCOLOR",
                    (1, -1),
                    (1, -1),
                    colors.HexColor("#1a7a52") if report.meets_commitment else colors.HexColor("#8a2f2f"),
                ),
            ]
        )
    )
    story += [summary, Spacer(1, 8 * mm)]

    rows = [["Probe target", "Checks", "Failed", "Downtime", "Availability"]]
    for target in report.targets:
        rows.append(
            [
                target.label,
                str(target.total_checks),
                str(target.failed_checks),
                _hours(target.downtime_seconds),
                f"{target.availability}%",
            ]
        )

    table = Table(rows, colWidths=[50 * mm, 28 * mm, 25 * mm, 30 * mm, 30 * mm], repeatRows=1)
    table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#f0f0f0")),
                ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                ("FONTSIZE", (0, 0), (-1, -1), 8),
                ("ALIGN", (1, 0), (-1, -1), "RIGHT"),
                ("GRID", (0, 0), (-1, -1), 0.3, colors.HexColor("#dddddd")),
                ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#fafafa")]),
                ("TOPPADDING", (0, 0), (-1, -1), 3),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
            ]
        )
    )
    story += [table, Spacer(1, 8 * mm), Paragraph(_basis_note(), small)]

    doc.build(story)
    return buffer.getvalue()

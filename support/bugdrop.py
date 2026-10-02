"""Reading a BugDrop issue body.

BugDrop writes a fixed Markdown layout (``formatIssueBody`` in its worker):

    ## Submitted by
    **Piyush** (piyush@example.com)

    ## Description
    What the reporter typed.

    ## Screenshot
    ...
    <details><summary>System Info</summary>
    | **Page** | https://app.example.com/host/orgManagement |
    ...

Every section is optional except the system-info table. Parsing is forgiving on
purpose: a layout change upstream should leave a ticket with less detail, not
drop the report.
"""

import html
import re
from dataclasses import dataclass
from urllib.parse import urlparse

BUGDROP_LABEL = "bugdrop"

_SECTION = re.compile(r"^##\s+(.+?)\s*$", re.MULTILINE)
_PAGE = re.compile(r"^\|\s*\*\*Page\*\*\s*\|\s*(\S+?)\s*\|", re.MULTILINE)
_EMAIL_TAIL = re.compile(r"\(([^()\s]+@[^()\s]+)\)\s*$")


@dataclass(frozen=True)
class BugDropReport:
    description: str = ""
    page_url: str = ""
    reporter_name: str = ""
    reporter_email: str = ""

    @property
    def page_host(self) -> str:
        return (urlparse(self.page_url).hostname or "").lower()


def _sections(body: str) -> dict[str, str]:
    """``## Heading`` → the text up to the next heading or the system-info block."""
    out: dict[str, str] = {}
    matches = list(_SECTION.finditer(body))
    for i, match in enumerate(matches):
        end = matches[i + 1].start() if i + 1 < len(matches) else len(body)
        text = body[match.end() : end]
        # The system-info <details> block and BugDrop's footer follow the last
        # section without a heading of their own.
        text = re.split(r"^<details>|^---\s*$", text, maxsplit=1, flags=re.MULTILINE)[0]
        out[match.group(1).strip().lower()] = text.strip()
    return out


def _unescape(value: str) -> str:
    # BugDrop escapes Markdown with backslashes and HTML with entities.
    return html.unescape(re.sub(r"\\(.)", r"\1", value))


def _submitter(text: str) -> tuple[str, str]:
    line = text.strip().splitlines()[0] if text.strip() else ""
    email = ""
    match = _EMAIL_TAIL.search(line)
    if match:
        email = _unescape(match.group(1))
        line = line[: match.start()]
    # **Name**, or **`name@with-at`** — BugDrop code-spans a name containing "@"
    # so GitHub does not turn it into a mention.
    name = line.strip().strip("*").strip().strip("`").strip()
    return _unescape(name), email


def parse(body: str | None) -> BugDropReport:
    body = body or ""
    sections = _sections(body)
    name, email = _submitter(sections.get("submitted by", ""))
    page = _PAGE.search(body)
    return BugDropReport(
        description=sections.get("description", ""),
        page_url=page.group(1) if page else "",
        reporter_name=name,
        reporter_email=email,
    )


def is_bugdrop_issue(issue: dict) -> bool:
    return any((label.get("name") or "").lower() == BUGDROP_LABEL for label in issue.get("labels") or [])

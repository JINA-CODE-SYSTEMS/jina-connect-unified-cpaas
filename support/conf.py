"""Deployment settings for the support module, read in one place.

Everything is per deployment and comes from the environment. This repository is
public, so nothing here names a partner, a repository or a developer.
"""

import hashlib
from urllib.parse import urlparse

from django.conf import settings

# Used for any GitHub login without an entry in ``SUPPORT_AGENT_NAMES``. The
# pick is a hash of the login, so a developer keeps the same name on every
# ticket without anyone having to configure it.
DEFAULT_AGENT_NAMES = (
    "Ananya",
    "Rohan",
    "Priya",
    "Arjun",
    "Kavya",
    "Vikram",
    "Meera",
    "Aditya",
    "Ishita",
    "Karan",
)


def repo() -> str:
    return settings.SUPPORT_GITHUB_REPO.strip()


def is_configured() -> bool:
    """A repository to mirror, a token to act on it, and a secret to trust its webhooks."""
    return bool(repo() and settings.SUPPORT_GITHUB_TOKEN and settings.SUPPORT_GITHUB_WEBHOOK_SECRET)


def report_hosts() -> frozenset[str]:
    """Hostnames whose BugDrop reports belong to this deployment.

    Several deployments can file into one repository, and every one of them
    receives every webhook. A report is ours when the page it was filed from is
    on one of these hosts — the frontend's own host unless configured.
    """
    configured = {h.strip().lower() for h in settings.SUPPORT_REPORT_HOSTS.split(",") if h.strip()}
    if configured:
        return frozenset(configured)
    host = (urlparse(settings.FRONTEND_URL).hostname or "").lower()
    return frozenset({host} if host else set())


def agent_names() -> dict[str, str]:
    """``login:Name,login:Name`` → {login: Name}. Logins compare case-insensitively."""
    out = {}
    for pair in settings.SUPPORT_AGENT_NAMES.split(","):
        login, sep, name = pair.partition(":")
        if sep and login.strip() and name.strip():
            out[login.strip().lower()] = name.strip()
    return out


def agent_name(login: str | None) -> str:
    """The name a customer sees for this GitHub login. Never the login itself."""
    if not login:
        return ""
    mapped = agent_names().get(login.lower())
    if mapped:
        return mapped
    digest = hashlib.sha256(login.lower().encode()).digest()
    return DEFAULT_AGENT_NAMES[digest[0] % len(DEFAULT_AGENT_NAMES)]


def resolved_label() -> str:
    return settings.SUPPORT_RESOLVED_LABEL


def reply_prefix() -> str:
    return settings.SUPPORT_REPLY_PREFIX


def auto_close_hours() -> int:
    return settings.SUPPORT_AUTO_CLOSE_HOURS

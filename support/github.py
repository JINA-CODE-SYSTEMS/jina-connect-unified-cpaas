"""The few GitHub calls the support module makes, with the deployment's token.

The token needs Issues read/write on ``SUPPORT_GITHUB_REPO`` and nothing else —
a fine-grained personal access token scoped to that one repository is enough.
"""

import hashlib
import hmac
import logging

import requests
from django.conf import settings

from support import conf

logger = logging.getLogger(__name__)

API = "https://api.github.com"
TIMEOUT = 15


class GitHubError(Exception):
    """A GitHub call failed. The message is safe to log, not to show a customer."""


def verify_signature(body: bytes, header: str | None) -> bool:
    """``X-Hub-Signature-256`` is ``sha256=<hex HMAC of the raw body>``."""
    secret = settings.SUPPORT_GITHUB_WEBHOOK_SECRET
    if not secret or not header or not header.startswith("sha256="):
        return False
    expected = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, header.removeprefix("sha256="))


def _request(method: str, path: str, **kwargs):
    url = f"{API}/repos/{conf.repo()}{path}"
    headers = {
        "Authorization": f"Bearer {settings.SUPPORT_GITHUB_TOKEN}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    try:
        response = requests.request(method, url, headers=headers, timeout=TIMEOUT, **kwargs)
    except requests.RequestException as exc:
        raise GitHubError(f"{method} {path}: {exc}") from exc
    # Removing a label the issue does not carry is not a failure worth surfacing.
    if response.status_code == 404 and method == "DELETE" and "/labels/" in path:
        return None
    if response.status_code >= 400:
        raise GitHubError(f"{method} {path}: HTTP {response.status_code} {response.text[:300]}")
    return response.json() if response.content else None


def comment(number: int, body: str) -> dict:
    return _request("POST", f"/issues/{number}/comments", json={"body": body})


def set_state(number: int, state: str, state_reason: str | None = None) -> dict:
    payload = {"state": state}
    if state_reason:
        payload["state_reason"] = state_reason
    return _request("PATCH", f"/issues/{number}", json=payload)


def remove_label(number: int, label: str) -> None:
    _request("DELETE", f"/issues/{number}/labels/{requests.utils.quote(label, safe='')}")


def list_issues(label: str, state: str = "all", per_page: int = 100, max_pages: int = 10) -> list[dict]:
    issues: list[dict] = []
    for page in range(1, max_pages + 1):
        batch = _request("GET", "/issues", params={"labels": label, "state": state, "per_page": per_page, "page": page})
        # The issues endpoint also returns pull requests.
        issues.extend(i for i in batch or [] if "pull_request" not in i)
        if not batch or len(batch) < per_page:
            break
    return issues


def list_comments(number: int) -> list[dict]:
    return _request("GET", f"/issues/{number}/comments", params={"per_page": 100}) or []

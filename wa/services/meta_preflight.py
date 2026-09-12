"""
Preflight the META credentials a client hands over, against META (#311).

A presence check catches an empty field. It does not catch a transposed digit,
and that is the failure that costs real time: a typo in ``waba_id`` produces an
app that looks fully configured, sends nothing and receives nothing, and is
diagnosed days later from the wrong end. The identifiers are copied by hand out
of someone else's Business Manager, so a typo is the expected case rather than
the exotic one.

Four checks: one local precondition, then three questions put to META, each
answered by a Graph call that already has a client in
``wa.utility.apis.meta.waba.WABAAPI``:

==================== ============================================= ===============
check                question                                      reported on
==================== ============================================= ===============
``credentials``      is there a token and a WABA id to try at all?  ``waba_id``
``waba_readable``    can this token read *this* WABA?               ``waba_id``
``phone_number_listed`` is ``phone_number_id`` one of its numbers?  ``phone_number_id``
``app_subscribed``   is the WABA subscribed to the app?             ``meta_app_id``
==================== ============================================= ===============

The last one is the quiet killer: the callback URL and verify token are
configured once per app in the App Dashboard, but every WABA must *additionally*
be subscribed to that app or Meta delivers nothing for it — no messages, no
statuses, no template updates — with no error anywhere to say so.

Two rules this module holds to:

* **It never raises.** ``WAAPI.make_request`` raises a bare ``Exception`` for
  every non-2xx, so a wrong id, an expired token and a Graph outage all arrive
  the same way. Each is caught and turned into a failed check naming the field
  the operator should look at, because the acceptance criterion is a field
  error and never a 500.
* **It never echoes the token.** Provider error text is truncated and the
  resolved token is scrubbed out of it before it goes anywhere near a response
  body or a log line.

This module reads credentials; it does not write them, does not mutate the app
and does not decide when it runs. Callers are ``WAAppSerializer`` (on request,
via ``verify_with_meta``) and the ``preflight`` action on ``WAAppViewSet``
(re-runnable on demand, reading what is already stored).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from dataclasses import field as dataclass_field
from typing import Any, Optional

from django.conf import settings

logger = logging.getLogger(__name__)

#: How much provider error text is worth keeping. Graph's bodies are verbose and
#: the useful part — "Unsupported get request", "Invalid OAuth access token" — is
#: at the front.
_MAX_DETAIL = 300

#: Where the access token came from: the app itself, or the deployment-wide
#: fallback. Reported rather than hidden — an app with no token of its own still
#: works today through ``settings.META_PERM_TOKEN``, and a preflight that
#: silently validated the platform's credential while reporting on the client's
#: would be lying.
#:
#: Named without the word "token" on purpose: bandit's B105 fires on the *name*
#: of anything holding a string literal when the name reads like a credential,
#: and three false positives here would train the eye to skip the real ones.
SOURCE_APP = "app"
SOURCE_DEPLOYMENT = "deployment"
SOURCE_NONE = "none"


@dataclass(frozen=True)
class CheckResult:
    """One preflight question and its answer."""

    name: str
    passed: bool
    #: Serializer field name this failure belongs on, so a caller can raise it
    #: as a field error instead of a flat string.
    field: str
    detail: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {"check": self.name, "passed": self.passed, "field": self.field, "detail": self.detail}


@dataclass(frozen=True)
class PreflightReport:
    """Every check that ran, in the order it ran."""

    checks: tuple[CheckResult, ...] = ()
    token_source: str = SOURCE_NONE
    #: Anything worth showing that is not pass/fail — the subscribed app id Meta
    #: reported, for instance, which is what an operator pastes into
    #: ``meta_app_id``.
    observations: dict[str, Any] = dataclass_field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return all(check.passed for check in self.checks)

    @property
    def failures(self) -> tuple[CheckResult, ...]:
        return tuple(check for check in self.checks if not check.passed)

    def as_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "token_source": self.token_source,
            "checks": [check.as_dict() for check in self.checks],
            "observations": self.observations,
        }

    def as_field_errors(self) -> dict[str, list[str]]:
        """The failures keyed by the field each belongs on.

        Several checks can report on one field, so the values are lists —
        which is the shape DRF raises anyway.
        """
        errors: dict[str, list[str]] = {}
        for check in self.failures:
            errors.setdefault(check.field, []).append(check.detail)
        return errors


@dataclass(frozen=True)
class PreflightSubject:
    """The four values a preflight reads, detached from any row.

    A create request has no row yet, and an update request is a mixture of what
    was sent and what is already stored. Rather than build a half-populated
    ``WAApp`` and risk saving it, callers assemble this.
    """

    waba_id: str = ""
    phone_number_id: str = ""
    meta_app_id: str = ""
    bsp_access_token: str = ""


def _redact(text: str, *secrets: Optional[str]) -> str:
    """Truncate provider text and scrub any credential out of it.

    Graph does not echo the bearer token back, but the exception carrying this
    text was built by string-formatting a request that did hold one, and this
    value is about to be put in a response body. Cheap insurance.
    """
    cleaned = " ".join(str(text or "").split())
    for secret in secrets:
        if secret and len(secret) >= 8:
            cleaned = cleaned.replace(secret, "[redacted]")
    if len(cleaned) > _MAX_DETAIL:
        cleaned = cleaned[:_MAX_DETAIL].rstrip() + "…"
    return cleaned


def resolve_preflight_token(subject: Any) -> tuple[Optional[str], str]:
    """The token a preflight should use, and where it came from.

    Deliberately the same priority the send path uses in
    ``MetaDirectAdapter._resolve_access_token``: the app's own token, then the
    deployment-wide one. Preflighting with a stricter rule than sending would
    report a working app as broken.
    """
    token = getattr(subject, "bsp_access_token", "") or ""
    if token:
        return token, SOURCE_APP

    token = getattr(settings, "META_PERM_TOKEN", "") or ""
    if token:
        return token, SOURCE_DEPLOYMENT

    return None, SOURCE_NONE


def _build_api(token: str, waba_id: str):
    from wa.utility.apis.meta.waba import WABAAPI

    api = WABAAPI(token=token)
    api.waba_id = waba_id
    return api


def run_meta_preflight(subject: Any) -> PreflightReport:
    """Check a META app's credentials against META.

    ``subject`` is anything carrying ``waba_id``, ``phone_number_id``,
    ``meta_app_id`` and ``bsp_access_token`` — a saved ``WAApp``, or a
    :class:`PreflightSubject` assembled from a request that has no row yet.
    Duck-typed rather than taking a primary key for exactly that reason.
    """
    waba_id = str(getattr(subject, "waba_id", "") or "")
    phone_number_id = str(getattr(subject, "phone_number_id", "") or "")
    meta_app_id = str(getattr(subject, "meta_app_id", "") or "")

    token, token_source = resolve_preflight_token(subject)

    checks: list[CheckResult] = []
    observations: dict[str, Any] = {}

    # ── 1. Is there anything to try? ──────────────────────────────────────
    missing = []
    if not waba_id:
        missing.append("waba_id")
    if not token:
        missing.append("an access token")
    if missing:
        checks.append(
            CheckResult(
                name="credentials",
                passed=False,
                field="waba_id" if not waba_id else "bsp_access_token",
                detail=(
                    f"Cannot reach META to check these credentials: {', '.join(missing)} is not set. "
                    "Set the WABA ID and the app's access token, then run the preflight again."
                ),
            )
        )
        return PreflightReport(checks=tuple(checks), token_source=token_source, observations=observations)

    checks.append(
        CheckResult(
            name="credentials",
            passed=True,
            field="waba_id",
            detail=f"Checking WABA {waba_id} with the {token_source} access token.",
        )
    )

    api = _build_api(token, waba_id)

    # ── 2. Can this token read this WABA? ─────────────────────────────────
    # A wrong digit in ``waba_id`` and an expired token both land here, and they
    # are told apart by the provider text rather than guessed at.
    try:
        account = api.get_account_status() or {}
    except Exception as exc:  # noqa: BLE001 — make_request raises bare Exception
        logger.warning("META preflight: WABA %s unreadable", waba_id)
        checks.append(
            CheckResult(
                name="waba_readable",
                passed=False,
                field="waba_id",
                detail=(
                    f"META refused to return WhatsApp Business Account {waba_id} for this access token. "
                    f"Check the WABA ID for a typo and that the token has access to it. META said: "
                    f"{_redact(str(exc), token)}"
                ),
            )
        )
        # The next two calls are the same token against the same node; they would
        # only restate this failure.
        return PreflightReport(checks=tuple(checks), token_source=token_source, observations=observations)

    returned_id = str(account.get("id") or "")
    if returned_id and returned_id != waba_id:
        # Graph follows some aliases, so a 200 is not by itself proof that the id
        # asked for is the id answered.
        checks.append(
            CheckResult(
                name="waba_readable",
                passed=False,
                field="waba_id",
                detail=(
                    f"META answered for WhatsApp Business Account {returned_id}, not {waba_id}. "
                    "Use the id META reports."
                ),
            )
        )
        return PreflightReport(checks=tuple(checks), token_source=token_source, observations=observations)

    if account.get("name") is not None:
        observations["waba_name"] = account.get("name")
    if account.get("account_review_status") is not None:
        observations["account_review_status"] = account.get("account_review_status")

    checks.append(
        CheckResult(
            name="waba_readable",
            passed=True,
            field="waba_id",
            detail=f"The access token can read WhatsApp Business Account {waba_id}.",
        )
    )

    # ── 3. Is phone_number_id one of that WABA's numbers? ─────────────────
    try:
        numbers = api.get_phone_numbers() or {}
    except Exception as exc:  # noqa: BLE001
        checks.append(
            CheckResult(
                name="phone_number_listed",
                passed=False,
                field="phone_number_id",
                detail=(
                    f"Could not list the phone numbers on WABA {waba_id}, so the phone number ID "
                    f"cannot be confirmed. META said: {_redact(str(exc), token)}"
                ),
            )
        )
    else:
        rows = [row for row in (numbers.get("data") or []) if isinstance(row, dict)]
        available = [str(row.get("id") or "") for row in rows if row.get("id")]
        # The count, not the ids: on a shared WABA the other numbers are another
        # customer's, and the count is all any message below needs.
        observations["waba_phone_number_count"] = len(available)

        if not phone_number_id:
            checks.append(
                CheckResult(
                    name="phone_number_listed",
                    passed=False,
                    field="phone_number_id",
                    detail=(
                        "Phone Number ID is not set, so sends have no number to go out from. "
                        f"META lists {len(available)} number(s) on this WABA."
                    ),
                )
            )
        elif phone_number_id in available:
            checks.append(
                CheckResult(
                    name="phone_number_listed",
                    passed=True,
                    field="phone_number_id",
                    detail=f"Phone number {phone_number_id} is on WABA {waba_id}.",
                )
            )
        else:
            # Deliberately does not print the other numbers: on a shared WABA
            # they belong to someone else.
            checks.append(
                CheckResult(
                    name="phone_number_listed",
                    passed=False,
                    field="phone_number_id",
                    detail=(
                        f"Phone Number ID {phone_number_id} is not one of the {len(available)} "
                        f"number(s) META lists on WABA {waba_id}. Check it for a typo, and that it "
                        "belongs to this WABA rather than another one."
                    ),
                )
            )

    # ── 4. Is the WABA subscribed to the app? ─────────────────────────────
    try:
        subscribed = api.get_subscribed_apps() or {}
    except Exception as exc:  # noqa: BLE001
        checks.append(
            CheckResult(
                name="app_subscribed",
                passed=False,
                field="meta_app_id",
                detail=(
                    f"Could not read the subscribed apps for WABA {waba_id}, so webhook delivery "
                    f"cannot be confirmed. META said: {_redact(str(exc), token)}"
                ),
            )
        )
    else:
        rows = [row for row in (subscribed.get("data") or []) if isinstance(row, dict)]
        subscribed_ids = []
        for row in rows:
            data = row.get("whatsapp_business_api_data")
            if isinstance(data, dict) and data.get("id"):
                subscribed_ids.append(str(data["id"]))
        observations["subscribed_app_ids"] = subscribed_ids

        if not subscribed_ids:
            checks.append(
                CheckResult(
                    name="app_subscribed",
                    passed=False,
                    field="meta_app_id",
                    detail=(
                        f"No app is subscribed to WABA {waba_id}, so META will deliver nothing for it — "
                        "no messages, no statuses, no template updates. Subscribe the app to this WABA."
                    ),
                )
            )
        elif meta_app_id and meta_app_id not in subscribed_ids:
            checks.append(
                CheckResult(
                    name="app_subscribed",
                    passed=False,
                    field="meta_app_id",
                    detail=(
                        f"WABA {waba_id} is subscribed to a different app than {meta_app_id}, so "
                        "deliveries for it go elsewhere. Subscribe this app to the WABA, or correct "
                        "the META App ID."
                    ),
                )
            )
        elif not meta_app_id:
            # ``app_id`` is the *Gupshup* app id by definition, so it is not a
            # stand-in here — comparing it would invent a failure. Report what
            # Meta says instead; it is the value to paste into ``meta_app_id``.
            checks.append(
                CheckResult(
                    name="app_subscribed",
                    passed=True,
                    field="meta_app_id",
                    detail=(
                        f"WABA {waba_id} is subscribed to {len(subscribed_ids)} app(s): "
                        f"{', '.join(subscribed_ids)}. META App ID is not set on this app, so which one "
                        "is ours was not verified — set meta_app_id to check it."
                    ),
                )
            )
        else:
            checks.append(
                CheckResult(
                    name="app_subscribed",
                    passed=True,
                    field="meta_app_id",
                    detail=f"WABA {waba_id} is subscribed to app {meta_app_id}.",
                )
            )

    return PreflightReport(checks=tuple(checks), token_source=token_source, observations=observations)

"""
Gupshup BSP Adapter — submits templates via Gupshup Partner API.

Uses the existing ``wa.utility.apis.gupshup.template_api.TemplateAPI`` HTTP
client under the hood.  Credential resolution:

    1. ``wa_app.bsp_credentials["partner_app_token"]``  (per-app token)
    2. ``wa_app.app_secret``                             (legacy field)

When the WAApp has ``bsp = "GUPSHUP"`` the adapter factory will select this
adapter.

Gupshup's template API uses **form-encoded** (not JSON) payloads and a
different response shape compared to META's Graph API.
"""

from __future__ import annotations

import json
import logging
import re
from typing import TYPE_CHECKING, Optional

from django.db import IntegrityError
from django.utils import timezone
from pydantic import ValidationError

from wa.adapters.base import AdapterResult, BaseBSPAdapter
from wa.adapters.channel_base import Capabilities
from wa.models import TemplateStatus

# Silk profiling — only active when DEBUG is on and silk is installed.
try:
    from silk.profiling.profiler import silk_profile
except (ImportError, RuntimeError):

    def silk_profile(name=""):  # noqa: F811 — no-op fallback
        def decorator(func):
            return func

        return decorator


if TYPE_CHECKING:
    from wa.models import WASubscription, WATemplate

logger = logging.getLogger(__name__)


class GupshupAdapter(BaseBSPAdapter):
    """
    Adapter for the Gupshup WhatsApp Business Solution Provider.

    Responsibilities:
    - Build a ``TemplateAPI`` client from the WAApp's credentials.
    - Convert a ``WATemplate`` to a Gupshup form-encoded payload.
    - Call ``TemplateAPI.apply_for_template()`` to create templates.
    - Call ``TemplateAPI.get_template_status()`` to poll status.
    - Call ``TemplateAPI`` to delete templates.
    - Map Gupshup's response back onto canonical model fields.
    """

    PROVIDER_NAME = "gupshup"
    CAPABILITIES = frozenset({"templates", "subscriptions", "media_upload"})
    capabilities = Capabilities(
        supports_text=True,
        supports_media=True,
        supports_keyboards=True,
        supports_templates=True,
        supports_template_buttons=True,
        supports_reactions=True,
        supports_typing_indicator=True,
    )

    # ── credential helpers ────────────────────────────────────────────────

    def _resolve_partner_token(self) -> Optional[str]:
        """
        Resolve the Gupshup partner app token.

        Priority:
        1. ``wa_app.bsp_credentials["partner_app_token"]``
        2. ``wa_app.app_secret``  (legacy — many tenants store it here)
        """
        creds = self.wa_app.bsp_credentials or {}
        token = creds.get("partner_app_token")
        if token:
            return token

        # Legacy fallback
        if self.wa_app.app_secret:
            return self.wa_app.app_secret

        return None

    def _resolve_app_id(self) -> Optional[str]:
        """Return the Gupshup app ID stored on the WAApp."""
        return self.wa_app.app_id or None

    def _get_template_api(self):
        """
        Build a configured Gupshup ``TemplateAPI`` instance.

        Raises ``ValueError`` when credentials are missing.
        """
        from wa.utility.apis.gupshup.template_api import TemplateAPI

        token = self._resolve_partner_token()
        if not token:
            raise ValueError(
                "Gupshup partner token not configured. Set "
                "bsp_credentials.partner_app_token on the WAApp or use app_secret."
            )

        app_id = self._resolve_app_id()
        if not app_id:
            raise ValueError("Gupshup app_id not configured on the WAApp.")

        return TemplateAPI(appId=app_id, token=token)

    # ── Payload builder ───────────────────────────────────────────────────

    def _validate_and_build_payload(self, template: "WATemplate") -> dict:
        """
        Validate a template through the appropriate Gupshup Pydantic
        validator, then serialise to the form-encoded payload Gupshup expects.

        1. ``template.to_gupshup_payload()`` builds the raw dict with
           positional placeholders ({{1}}, {{2}}) and all Gupshup fields.
        2. The correct validator (Marketing / Utility / Authentication)
           enforces structural rules (button types, mutual exclusivity, etc.).
        3. Buttons and cards are JSON-stringified for Gupshup's form-encoded API.

        Raises ``ValueError`` or ``pydantic.ValidationError`` on bad data.
        """
        from wa.utility.validators.gupshup.authentication_template_validator import AuthTemplateValidator
        from wa.utility.validators.gupshup.marketing_validator import MarketingTemplateValidator
        from wa.utility.validators.gupshup.utility_template_validator import UtilityTemplateValidator

        # A — canonical model → Gupshup-shaped dict (named → positional)
        raw = template.to_gupshup_payload()

        # B — pick validator by category
        category = (raw.get("category") or "").upper()
        validator_map = {
            "MARKETING": MarketingTemplateValidator,
            "UTILITY": UtilityTemplateValidator,
            "AUTHENTICATION": AuthTemplateValidator,
        }
        validator_cls = validator_map.get(category)
        if validator_cls is None:
            raise ValueError(
                f"Unsupported template category for Gupshup: '{category}'. "
                f"Expected one of {list(validator_map.keys())}."
            )

        # B2 — ORDER_DETAILS handling
        #     Gupshup does NOT accept templateType=ORDER_DETAILS.
        #     Instead, template type stays TEXT/IMAGE and the flag
        #     ``sendAsOrderDetails=true`` tells Gupshup to auto-generate
        #     the ORDER_DETAILS button server-side.
        template_type_raw = (raw.get("templateType") or "TEXT").upper()
        if template_type_raw == "ORDER_DETAILS":
            # Determine effective type: IMAGE if there's media, else TEXT
            has_media = bool(raw.get("exampleMedia"))
            raw["templateType"] = "IMAGE" if has_media else "TEXT"
            raw["sendAsOrderDetails"] = True
            # Remove buttons — Gupshup generates the ORDER_DETAILS button
            raw.pop("buttons", None)
            template_type_raw = raw["templateType"]

        # B3 — reject template types not supported by Gupshup / WhatsApp
        _SUPPORTED_TEMPLATE_TYPES = {
            "TEXT",
            "IMAGE",
            "VIDEO",
            "DOCUMENT",
            "CAROUSEL",
            "LOCATION",
            "CATALOG",
        }
        if template_type_raw not in _SUPPORTED_TEMPLATE_TYPES:
            raise ValueError(
                f"Unsupported template type for Gupshup: '{template_type_raw}'. "
                f"Supported types: {sorted(_SUPPORTED_TEMPLATE_TYPES)}."
            )

        # C — validate (raises pydantic.ValidationError on failure)
        validator_cls(**raw)

        # D — Transform COPY_CODE buttons to Gupshup/Meta format
        #     Our model uses {type: COPY_CODE, text: ..., coupon_code: ...}
        #     Gupshup expects  {type: COPY_CODE, example: "<code>"} — no text.
        if raw.get("buttons") and isinstance(raw["buttons"], list):
            for btn in raw["buttons"]:
                if isinstance(btn, dict) and btn.get("type") == "COPY_CODE":
                    coupon = btn.pop("coupon_code", "") or ""
                    btn.pop("text", None)
                    btn["example"] = coupon

        # E — JSON-stringify buttons & cards for form-encoded API
        if raw.get("buttons") and not isinstance(raw["buttons"], str):
            raw["buttons"] = json.dumps(raw["buttons"])
        if raw.get("cards") and not isinstance(raw["cards"], str):
            raw["cards"] = json.dumps(raw["cards"])

        # E — Log CAROUSEL-specific payload details for debugging
        template_type = (raw.get("templateType") or "").upper()
        if template_type == "CAROUSEL":
            self._log(
                "info",
                f"[CAROUSEL DEBUG] content={raw.get('content')!r}, "
                f"example={raw.get('example')!r}, "
                f"has_cards={bool(raw.get('cards'))}, "
                f"has_buttons={bool(raw.get('buttons'))}, "
                f"has_header={bool(raw.get('header'))}, "
                f"has_footer={bool(raw.get('footer'))}",
            )

        return raw

    # ── Media operations ─────────────────────────────────────────────────

    @silk_profile(name="adapter.gupshup.upload_media")
    def upload_media(
        self,
        file_obj,
        filename: str,
        file_type: Optional[str] = None,
    ) -> AdapterResult:
        """
        Upload a media file to Gupshup and return a permanent handle ID.

        Endpoint: ``POST /partner/app/{appId}/upload/media``

        The returned ``handle_id`` can be stored as
        ``WATemplate.media_handle`` or inside a carousel card's
        ``media_handle`` field.
        """
        self._log("info", f"upload_media START — filename={filename}, file_type={file_type}")

        try:
            api = self._get_template_api()
        except ValueError as exc:
            self._log("error", f"upload_media credential error — {exc}")
            return AdapterResult(
                success=False,
                provider=self.PROVIDER_NAME,
                error_message=str(exc),
            )

        try:
            response = api.upload_media_from_file_object(
                file_obj=file_obj,
                filename=filename,
                file_type=file_type,
            )
            self._log("info", f"upload_media response — {response}")
        except Exception as exc:
            error_msg = f"Gupshup media upload failed: {exc}"
            self._log("error", error_msg, exc_info=True)
            return AdapterResult(
                success=False,
                provider=self.PROVIDER_NAME,
                error_message=error_msg,
            )

        # Gupshup returns {"status": "success", "handleId": {"message": "<id>"}}
        # or a flat  {"handleId": "<id>"}
        handle_id = None
        if isinstance(response, dict):
            raw = response.get("handleId", response.get("handle_id"))
            if isinstance(raw, dict):
                handle_id = raw.get("message") or raw.get("handleId")
            elif isinstance(raw, str):
                handle_id = raw

        if not handle_id:
            return AdapterResult(
                success=False,
                provider=self.PROVIDER_NAME,
                error_message=f"No handle_id in Gupshup response: {response}",
                raw_response=response,
            )

        self._log("info", f"upload_media SUCCESS — handle_id={handle_id}")
        return AdapterResult(
            success=True,
            provider=self.PROVIDER_NAME,
            data={"handle_id": handle_id},
            raw_response=response,
        )

    # ── Duplicate element_name helpers ───────────────────────────────────

    # Maximum number of suffix attempts before giving up.
    # Set higher than 5 to handle data-migration scenarios where several
    # sequential _vN names may already exist on Gupshup's servers.
    _MAX_ELEMENT_NAME_RETRIES = 10

    # Matches a trailing ``_v<digits>`` suffix, e.g. ``_v2``, ``_v13``.
    _ELEMENT_NAME_SUFFIX_RE = re.compile(r"_v(\d+)$")

    # Error sub-string Gupshup returns when the element_name is taken.
    _DUPLICATE_ERROR_FRAGMENT = "already exists with same namespace and elementname and languagecode"

    @staticmethod
    def _next_element_name(current: str) -> str:
        """
        Generate the next element_name candidate by bumping a ``_vN`` suffix.

        ``order_confirm``   → ``order_confirm_v2``
        ``order_confirm_v2`` → ``order_confirm_v3``
        ``order_confirm_v99`` → ``order_confirm_v100``
        """
        m = GupshupAdapter._ELEMENT_NAME_SUFFIX_RE.search(current)
        if m:
            n = int(m.group(1)) + 1
            return current[: m.start()] + f"_v{n}"
        return current + "_v2"

    def _persist_element_name(self, template: "WATemplate", new_name: str) -> bool:
        """
        Atomically update ``element_name`` on the template.

        Returns ``True`` on success, ``False`` if the new name collides with
        an existing row (Django ``unique_together`` constraint).
        """
        old_name = template.element_name
        template.element_name = new_name
        try:
            template.save(update_fields=["element_name"])
            self._log(
                "info",
                f"element_name updated: {old_name!r} → {new_name!r}",
            )
            return True
        except IntegrityError:
            # Another template already owns this (wa_app, element_name,
            # language_code) combo in our own DB.
            template.element_name = old_name  # rollback in-memory
            self._log(
                "warning",
                f"element_name {new_name!r} already taken locally, skipping",
            )
            return False

    # ── Template operations ───────────────────────────────────────────────

    @silk_profile(name="adapter.gupshup.submit_template")
    def submit_template(self, template: "WATemplate") -> AdapterResult:
        """
        Submit *template* to Gupshup's Partner API for META approval.

        On success the template is moved to ``PENDING`` and the Gupshup
        template ID is stored in ``bsp_template_id``.  The actual META
        template ID (assigned later by META) is populated during
        ``get_template_status()`` once Gupshup returns it.

        **Duplicate handling:** if Gupshup responds with *"Template Already
        exists with same namespace and elementName and languageCode"*, the
        adapter automatically appends / bumps a ``_vN`` suffix on the
        ``element_name`` and retries (up to ``_MAX_ELEMENT_NAME_RETRIES``
        times).  The local ``WATemplate.element_name`` is updated to match
        so the DB stays in sync with what Gupshup accepted.
        """
        self._log(
            "info",
            f"[STEP 1/5] submit_template START — element_name={template.element_name}, wa_app_id={template.wa_app_id}",
        )

        # Step 2: Resolve credentials
        try:
            api = self._get_template_api()
            self._log(
                "info",
                f"[STEP 2/5] Credentials resolved — app_id={api.appId}, token={'***' + self._resolve_partner_token()[-4:] if self._resolve_partner_token() else 'NONE'}",
            )
        except ValueError as exc:
            self._log("error", f"[STEP 2/5] Credential resolution FAILED — {exc}")
            template.error_message = str(exc)
            template.save(update_fields=["error_message"])
            return AdapterResult(
                success=False,
                provider=self.PROVIDER_NAME,
                error_message=str(exc),
            )

        # Step 3 + 4: Build payload and call API (with duplicate retry loop)
        last_error_msg: Optional[str] = None
        last_response: Optional[dict] = None

        for attempt in range(1, self._MAX_ELEMENT_NAME_RETRIES + 1):
            # Step 3: Validate & build payload
            try:
                payload = self._validate_and_build_payload(template)
                self._log(
                    "info",
                    f"[STEP 3/5] Payload validated & built (attempt {attempt}) — elementName={payload.get('elementName')}, category={payload.get('category')}, templateType={payload.get('templateType')}",
                )
                self._log("debug", f"[STEP 3/5] Full payload: {payload}")
            except (ValueError, ValidationError) as exc:
                error_msg = f"Gupshup payload validation failed: {exc}"
                self._log("error", f"[STEP 3/5] Payload validation FAILED — {exc}")
                template.error_message = error_msg
                template.save(update_fields=["error_message"])
                return AdapterResult(
                    success=False,
                    provider=self.PROVIDER_NAME,
                    error_message=error_msg,
                )

            # Step 4: Call Gupshup API
            try:
                self._log(
                    "info",
                    f"[STEP 4/5] Calling Gupshup Partner API (attempt {attempt}) — POST /partner/app/{api.appId}/templates",
                )
                response = api.apply_for_template(payload)
                self._log(
                    "info",
                    f"[STEP 4/5] Gupshup responded — keys={list(response.keys()) if isinstance(response, dict) else type(response)}",
                )
                self._log("debug", f"[STEP 4/5] Full response: {response}")
            except Exception as exc:
                error_msg = f"Gupshup API call failed: {exc}"
                self._log("error", f"[STEP 4/5] Gupshup API call FAILED — {exc}", exc_info=True)
                template.error_message = error_msg
                template.save(update_fields=["error_message"])
                return AdapterResult(
                    success=False,
                    provider=self.PROVIDER_NAME,
                    error_message=error_msg,
                )

            # Check for duplicate element_name error
            status_str = (response.get("status") or "").lower()
            resp_message = response.get("message", "")

            if status_str == "error" and self._DUPLICATE_ERROR_FRAGMENT in resp_message.lower():
                # ── Duplicate detected — try next element_name suffix ─────
                new_name = self._next_element_name(template.element_name)
                self._log(
                    "warning",
                    f"[STEP 4/5] Duplicate element_name detected (attempt {attempt}). "
                    f"Retrying: {template.element_name!r} → {new_name!r}",
                )
                if not self._persist_element_name(template, new_name):
                    # Local DB already has this name — keep bumping
                    for _ in range(self._MAX_ELEMENT_NAME_RETRIES):
                        new_name = self._next_element_name(new_name)
                        if self._persist_element_name(template, new_name):
                            break
                    else:
                        error_msg = (
                            f"Could not find an available element_name after "
                            f"{self._MAX_ELEMENT_NAME_RETRIES} attempts. "
                            f"Last tried: {new_name!r}"
                        )
                        template.error_message = error_msg
                        template.save(update_fields=["error_message"])
                        return AdapterResult(
                            success=False,
                            provider=self.PROVIDER_NAME,
                            error_message=error_msg,
                        )
                last_error_msg = resp_message
                last_response = response
                continue  # retry with the new element_name

            # Not a duplicate error — break out of retry loop
            last_error_msg = resp_message if status_str == "error" else None
            last_response = response
            break
        else:
            # Exhausted all retries — still getting duplicates
            error_msg = (
                f"Gupshup element_name conflict after {self._MAX_ELEMENT_NAME_RETRIES} "
                f"retries (current name: {template.element_name!r}). "
                f"This may happen when many versions exist on Gupshup from a "
                f"data migration. Last error: {last_error_msg}"
            )
            self._log("error", f"[STEP 4/5] {error_msg}")
            template.error_message = error_msg
            template.save(update_fields=["error_message"])
            return AdapterResult(
                success=False,
                provider=self.PROVIDER_NAME,
                error_message=error_msg,
                raw_response=last_response,
            )

        # ── Step 5: Interpret the response ────────────────────────────────
        # Gupshup returns:
        #   Success → {"status": "success", "template": {"id": "...", ...}}
        #   Error   → {"status": "error", "message": "..."}
        status = (response.get("status") or "").lower()

        if status == "error" or status != "success":
            error_msg = response.get("message", str(response))
            self._log("warning", f"[STEP 5/5] Gupshup REJECTED — msg={error_msg}")
            template.error_message = error_msg
            template.save(update_fields=["error_message"])
            return AdapterResult(
                success=False,
                provider=self.PROVIDER_NAME,
                error_message=error_msg,
                raw_response=response,
            )

        # Success path — extract Gupshup's own template ID
        template_data = response.get("template", response)
        gs_template_id = template_data.get("id") or template_data.get("templateId") or template_data.get("elementName")

        # Store in bsp_template_id — this is Gupshup's internal ID.
        # meta_template_id will be populated later during get_template_status()
        # when Gupshup returns META's ID after approval.
        template.bsp_template_id = str(gs_template_id) if gs_template_id else None
        template.status = TemplateStatus.PENDING
        template.needs_sync = False
        template.error_message = None
        template.last_synced_at = timezone.now()
        template.save(
            update_fields=[
                "bsp_template_id",
                "status",
                "needs_sync",
                "error_message",
                "last_synced_at",
            ]
        )

        self._log("info", f"[STEP 5/5] submit_template SUCCESS — bsp_template_id={gs_template_id}, status=PENDING")

        return AdapterResult(
            success=True,
            provider=self.PROVIDER_NAME,
            data={
                "bsp_template_id": str(gs_template_id),
                "status": template.status,
            },
            raw_response=response,
        )

    # ──────────────────────────────────────────────────────────────────────

    @silk_profile(name="adapter.gupshup.list_templates")
    def list_templates(self) -> AdapterResult:
        """
        Fetch all templates from Gupshup Partner API for this app.

        Endpoint: ``GET /partner/app/{appId}/templates``

        Returns an ``AdapterResult`` with ``data["templates"]`` containing
        the raw Gupshup template dicts.  The caller (sync service) is
        responsible for mapping these to canonical ``WATemplate`` fields.
        """
        self._log("info", "list_templates START")

        try:
            api = self._get_template_api()
        except ValueError as exc:
            self._log("error", f"list_templates credential error — {exc}")
            return AdapterResult(
                success=False,
                provider=self.PROVIDER_NAME,
                error_message=str(exc),
            )

        try:
            response = api.get_all_templates()
        except Exception as exc:
            self._log("error", f"list_templates API call FAILED — {exc}", exc_info=True)
            return AdapterResult(
                success=False,
                provider=self.PROVIDER_NAME,
                error_message=f"Gupshup API call failed: {exc}",
            )

        if response.get("status", "").lower() == "error":
            error_msg = response.get("message", str(response))
            self._log("warning", f"list_templates Gupshup error — {error_msg}")
            return AdapterResult(
                success=False,
                provider=self.PROVIDER_NAME,
                error_message=error_msg,
                raw_response=response,
            )

        templates = response.get("templates", [])
        self._log("info", f"list_templates SUCCESS — {len(templates)} templates fetched")
        return AdapterResult(
            success=True,
            provider=self.PROVIDER_NAME,
            data={"templates": templates},
            raw_response=response,
        )

    # ──────────────────────────────────────────────────────────────────────

    @silk_profile(name="adapter.gupshup.get_template_status")
    def get_template_status(self, template: "WATemplate") -> AdapterResult:
        """
        Fetch template status from Gupshup Partner API.

        Uses ``bsp_template_id`` (Gupshup's ID) to query the API.  When
        the response contains META's template ID (``meta.id``), it is
        stored in ``meta_template_id`` so both IDs are tracked.
        """
        gs_id = template.bsp_template_id
        self._log(
            "info",
            f"[STEP 1/4] get_template_status START — element_name={template.element_name}, bsp_template_id={gs_id}",
        )

        if not gs_id:
            self._log("warning", "[STEP 1/4] ABORTED — bsp_template_id is not set")
            return AdapterResult(
                success=False,
                provider=self.PROVIDER_NAME,
                error_message="bsp_template_id is not set — template was never submitted to Gupshup.",
            )

        try:
            api = self._get_template_api()
            self._log("info", f"[STEP 2/4] Calling Gupshup — GET template status for {gs_id}")
            response = api.get_template_status(gs_id)
            self._log("debug", f"[STEP 2/4] Response: {response}")
        except Exception as exc:
            self._log("error", f"[STEP 2/4] Gupshup API call FAILED — {exc}", exc_info=True)
            return AdapterResult(
                success=False,
                provider=self.PROVIDER_NAME,
                error_message=f"Gupshup API call failed: {exc}",
            )

        # Check for API-level errors
        if response.get("status", "").lower() == "error":
            error_msg = response.get("message", str(response))
            self._log("warning", f"[STEP 3/4] Gupshup error — {error_msg}")
            return AdapterResult(
                success=False,
                provider=self.PROVIDER_NAME,
                error_message=error_msg,
                raw_response=response,
            )

        # Map Gupshup status to canonical TemplateStatus
        # Gupshup statuses: APPROVED, PENDING, REJECTED, PAUSED, DISABLED, DELETED
        template_data = response.get("template", response)
        gs_status = (template_data.get("status") or "").upper()

        status_map = {
            "APPROVED": TemplateStatus.APPROVED,
            "PENDING": TemplateStatus.PENDING,
            "REJECTED": TemplateStatus.REJECTED,
            "PAUSED": TemplateStatus.PAUSED,
            "DISABLED": TemplateStatus.DISABLED,
            "DELETED": TemplateStatus.DISABLED,
        }
        canonical_status = status_map.get(gs_status, template.status)
        self._log("info", f"[STEP 3/4] Status mapped — gs_status={gs_status} → canonical={canonical_status}")

        # Extract META's actual template ID if Gupshup includes it.
        # Gupshup may return it under meta.id, meta.templateId, or wabaTemplateId.
        meta_section = template_data.get("meta", {})
        meta_id = meta_section.get("id") or meta_section.get("templateId") or template_data.get("wabaTemplateId")

        update_fields = ["status", "rejection_reason", "last_synced_at"]

        # Persist
        template.status = canonical_status
        if meta_id and str(meta_id) != template.meta_template_id:
            template.meta_template_id = str(meta_id)
            update_fields.append("meta_template_id")
            self._log("info", f"[STEP 3/4] META template ID captured — meta_template_id={meta_id}")
        if gs_status == "REJECTED":
            template.rejection_reason = template_data.get("reason") or meta_section.get("reason")
            self._log("warning", f"[STEP 4/4] Template REJECTED — reason={template.rejection_reason}")
        template.last_synced_at = timezone.now()
        template.save(update_fields=update_fields)

        self._log(
            "info",
            f"[STEP 4/4] get_template_status SUCCESS — status={canonical_status}, meta_template_id={template.meta_template_id}",
        )

        return AdapterResult(
            success=True,
            provider=self.PROVIDER_NAME,
            data={
                "status": canonical_status,
                "meta_template_id": template.meta_template_id,
                "bsp_template_id": template.bsp_template_id,
            },
            raw_response=response,
        )

    # ──────────────────────────────────────────────────────────────────────

    @silk_profile(name="adapter.gupshup.delete_template")
    def delete_template(self, template: "WATemplate") -> AdapterResult:
        """
        Delete a template from Gupshup.

        Gupshup Partner API: ``DELETE /partner/app/{appId}/templates/{templateId}``
        """
        self._log("info", f"[STEP 1/4] delete_template START — element_name={template.element_name}")

        try:
            api = self._get_template_api()
            self._log("info", f"[STEP 2/4] Credentials resolved — app_id={api.appId}")
        except ValueError as exc:
            self._log("error", f"[STEP 2/4] Credential resolution FAILED — {exc}")
            return AdapterResult(
                success=False,
                provider=self.PROVIDER_NAME,
                error_message=str(exc),
            )

        if not template.bsp_template_id:
            # Try deleting by element_name via the list → filter approach
            self._log("warning", "[STEP 3/4] No bsp_template_id — attempting delete by element_name")

        try:
            import requests as http

            # Gupshup delete endpoint
            template_id = template.bsp_template_id or template.element_name
            url = f"{api.BASE_URL}{api.appId}/templates/{template_id}"
            headers = {"Authorization": api.token}

            self._log("info", f"[STEP 3/4] Calling Gupshup — DELETE {url}")
            resp = http.delete(url, headers=headers, timeout=30)
            response = resp.json() if resp.text else {}
            self._log("debug", f"[STEP 3/4] Response: {response}")
        except Exception as exc:
            self._log("error", f"[STEP 3/4] Gupshup API call FAILED — {exc}", exc_info=True)
            return AdapterResult(
                success=False,
                provider=self.PROVIDER_NAME,
                error_message=f"Gupshup API call failed: {exc}",
            )

        if response.get("status", "").lower() == "error":
            error_msg = response.get("message", str(response))
            self._log("warning", f"[STEP 4/4] Gupshup error — {error_msg}")
            return AdapterResult(
                success=False,
                provider=self.PROVIDER_NAME,
                error_message=error_msg,
                raw_response=response,
            )

        # Mark locally
        template.status = TemplateStatus.DISABLED
        template.is_active = False
        template.save(update_fields=["status", "is_active"])

        self._log("info", "[STEP 4/4] delete_template SUCCESS — template disabled")

        return AdapterResult(
            success=True,
            provider=self.PROVIDER_NAME,
            data={"deleted": True},
            raw_response=response,
        )

    # ── Webhook Subscription operations ───────────────────────────────────

    def _get_subscription_api(self):
        """
        Build a configured Gupshup ``SubscriptionAPI`` instance.

        Raises ``ValueError`` when credentials are missing.
        """
        from wa.utility.apis.gupshup.subscription_api import SubscriptionAPI

        token = self._resolve_partner_token()
        if not token:
            raise ValueError(
                "Gupshup partner token not configured. Set "
                "bsp_credentials.partner_app_token on the WAApp or use app_secret."
            )

        app_id = self._resolve_app_id()
        if not app_id:
            raise ValueError("Gupshup app_id not configured on the WAApp.")

        return SubscriptionAPI(appId=app_id, token=token)

    def _map_event_types_to_gupshup_modes(self, event_types: list) -> list:
        """
        Map canonical WebhookEventType values to Gupshup subscription modes.

        Our WebhookEventType → Gupshup modes:
            MESSAGE  → ["MESSAGE", "ALL"]
            STATUS   → ["FAILED", "SENT", "DELIVERED", "READ", "ENQUEUED"]
            TEMPLATE → ["TEMPLATE"]
            BILLING  → ["BILLING"]
            ACCOUNT  → ["ACCOUNT"]
        """
        mode_map = {
            "MESSAGE": ["MESSAGE", "ALL"],
            "STATUS": ["FAILED", "SENT", "DELIVERED", "READ", "ENQUEUED"],
            "TEMPLATE": ["TEMPLATE"],
            "BILLING": ["BILLING"],
            "ACCOUNT": ["ACCOUNT"],
        }
        modes = []
        for et in event_types:
            modes.extend(mode_map.get(et, [et]))
        return list(dict.fromkeys(modes))  # dedupe, preserve order

    # Gupshup enforces a maximum of 5 subscriptions per app.
    GUPSHUP_MAX_SUBSCRIPTIONS = 5

    @silk_profile(name="adapter.gupshup.register_webhook")
    def register_webhook(self, subscription: "WASubscription") -> AdapterResult:
        """
        Register a webhook subscription with Gupshup Partner API.

        Gupshup uses ``POST /partner/app/{appId}/subscription`` with
        form-encoded body containing modes, url, tag, version.

        **Limit:** Gupshup allows a maximum of 5 subscriptions per app.
        If the limit is reached, use ``purge_all_webhooks()`` or the
        ``/refresh/`` endpoint first.
        """
        self._log("info", f"[STEP 1/5] register_webhook START — url={subscription.webhook_url}")

        from wa.models import SubscriptionStatus
        from wa.utility.data_model.gupshup.subscription import SubscriptionFormData

        # Step 2: Resolve credentials
        try:
            api = self._get_subscription_api()
            self._log("info", f"[STEP 2/5] Credentials resolved — app_id={api.appId}")
        except ValueError as exc:
            self._log("error", f"[STEP 2/5] Credential resolution FAILED — {exc}")
            subscription.error_message = str(exc)
            subscription.status = SubscriptionStatus.FAILED
            subscription.save(update_fields=["error_message", "status"])
            return AdapterResult(
                success=False,
                provider=self.PROVIDER_NAME,
                error_message=str(exc),
            )

        # Step 3: Check Gupshup subscription limit (max 5)
        try:
            existing = api.get_all_subscriptions()
            existing_count = len(existing.get("subscriptions", []))
            self._log("info", f"[STEP 3/5] Existing subscriptions: {existing_count}/{self.GUPSHUP_MAX_SUBSCRIPTIONS}")
            if existing_count >= self.GUPSHUP_MAX_SUBSCRIPTIONS:
                error_msg = (
                    f"Gupshup subscription limit reached ({existing_count}/{self.GUPSHUP_MAX_SUBSCRIPTIONS}). "
                    f"Use the /refresh/ endpoint to purge old subscriptions first."
                )
                self._log("warning", f"[STEP 3/5] LIMIT REACHED — {error_msg}")
                subscription.error_message = error_msg
                subscription.status = SubscriptionStatus.FAILED
                subscription.save(update_fields=["error_message", "status"])
                return AdapterResult(
                    success=False,
                    provider=self.PROVIDER_NAME,
                    error_message=error_msg,
                )
        except Exception as exc:
            # Non-fatal — if we can't check, proceed and let the create call fail naturally
            self._log("warning", f"[STEP 3/5] Could not check existing count: {exc}")

        # Step 4: Build payload
        modes = self._map_event_types_to_gupshup_modes(subscription.event_types or [])
        if not modes:
            modes = ["MESSAGE", "ALL"]  # sensible default

        form_data = SubscriptionFormData(
            modes=modes,
            tag=f"sub_{subscription.id}_app_{self.wa_app.pk}",
            url=subscription.webhook_url,
        )
        payload = form_data.to_form_data()
        self._log("info", f"[STEP 4/5] Payload built — modes={modes}")
        self._log("debug", f"[STEP 4/5] Full payload: {payload}")

        # Step 5: Call Gupshup API
        try:
            response = api.create_subscription(payload)
            self._log("info", f"[STEP 5/5] Gupshup responded — {response}")
        except Exception as exc:
            error_msg = f"Gupshup subscription API failed: {exc}"
            self._log("error", f"[STEP 5/5] FAILED — {exc}", exc_info=True)
            subscription.error_message = error_msg
            subscription.status = SubscriptionStatus.FAILED
            subscription.save(update_fields=["error_message", "status"])
            return AdapterResult(
                success=False,
                provider=self.PROVIDER_NAME,
                error_message=error_msg,
            )

        # Interpret response
        if response.get("status") == "success":
            gs_sub_id = response.get("subscription", {}).get("id")
            subscription.bsp_subscription_id = str(gs_sub_id) if gs_sub_id else None
            subscription.status = SubscriptionStatus.ACTIVE
            subscription.error_message = None
            subscription.save(
                update_fields=[
                    "bsp_subscription_id",
                    "status",
                    "error_message",
                ]
            )
            self._log("info", f"register_webhook SUCCESS — gs_id={gs_sub_id}")
            return AdapterResult(
                success=True,
                provider=self.PROVIDER_NAME,
                data={"bsp_subscription_id": str(gs_sub_id)},
                raw_response=response,
            )

        error_msg = response.get("message", str(response))
        subscription.error_message = error_msg
        subscription.status = SubscriptionStatus.FAILED
        subscription.save(update_fields=["error_message", "status"])
        self._log("warning", f"register_webhook FAILED — {error_msg}")
        return AdapterResult(
            success=False,
            provider=self.PROVIDER_NAME,
            error_message=error_msg,
            raw_response=response,
        )

    @silk_profile(name="adapter.gupshup.unregister_webhook")
    def unregister_webhook(self, subscription: "WASubscription") -> AdapterResult:
        """
        Delete a webhook subscription from Gupshup.

        ``DELETE /partner/app/{appId}/subscription/{subscriptionId}``
        """
        self._log("info", f"[STEP 1/3] unregister_webhook START — bsp_id={subscription.bsp_subscription_id}")

        from wa.models import SubscriptionStatus

        if not subscription.bsp_subscription_id:
            # Nothing registered on BSP — just mark inactive locally.
            subscription.status = SubscriptionStatus.INACTIVE
            subscription.save(update_fields=["status"])
            self._log("info", "unregister_webhook — no bsp_subscription_id, marked INACTIVE")
            return AdapterResult(
                success=True,
                provider=self.PROVIDER_NAME,
                data={"note": "No BSP subscription to remove."},
            )

        try:
            api = self._get_subscription_api()
            self._log("info", f"[STEP 2/3] Credentials resolved — app_id={api.appId}")
        except ValueError as exc:
            self._log("error", f"[STEP 2/3] Credential resolution FAILED — {exc}")
            return AdapterResult(
                success=False,
                provider=self.PROVIDER_NAME,
                error_message=str(exc),
            )

        try:
            response = api.delete_subscription(subscription.bsp_subscription_id)
            self._log("info", f"[STEP 3/3] Gupshup responded — {response}")
        except Exception as exc:
            self._log("error", f"[STEP 3/3] FAILED — {exc}", exc_info=True)
            return AdapterResult(
                success=False,
                provider=self.PROVIDER_NAME,
                error_message=f"Gupshup delete subscription failed: {exc}",
            )

        subscription.status = SubscriptionStatus.INACTIVE
        subscription.save(update_fields=["status"])

        self._log("info", "unregister_webhook SUCCESS — marked INACTIVE")
        return AdapterResult(
            success=True,
            provider=self.PROVIDER_NAME,
            data={"deleted": True},
            raw_response=response,
        )

    @silk_profile(name="adapter.gupshup.list_webhooks")
    def list_webhooks(self) -> AdapterResult:
        """
        List all webhook subscriptions from Gupshup Partner API.

        ``GET /partner/app/{appId}/subscription``
        """
        self._log("info", "list_webhooks START")

        try:
            api = self._get_subscription_api()
        except ValueError as exc:
            self._log("error", f"Credential resolution FAILED — {exc}")
            return AdapterResult(
                success=False,
                provider=self.PROVIDER_NAME,
                error_message=str(exc),
            )

        try:
            response = api.get_all_subscriptions()
            self._log("debug", f"list_webhooks response: {response}")
        except Exception as exc:
            self._log("error", f"list_webhooks FAILED — {exc}", exc_info=True)
            return AdapterResult(
                success=False,
                provider=self.PROVIDER_NAME,
                error_message=f"Gupshup list subscriptions failed: {exc}",
            )

        # Normalise Gupshup response shape
        raw_subs = response.get("subscriptions", [])
        subscriptions = [
            {
                "id": s.get("id"),
                "url": s.get("url"),
                "events": s.get("modes", []),
                "tag": s.get("tag"),
                "status": s.get("status"),
            }
            for s in raw_subs
        ]

        self._log("info", f"list_webhooks SUCCESS — count={len(subscriptions)}")
        return AdapterResult(
            success=True,
            provider=self.PROVIDER_NAME,
            data={"subscriptions": subscriptions},
            raw_response=response,
        )

    @silk_profile(name="adapter.gupshup.purge_all_webhooks")
    def purge_all_webhooks(self) -> AdapterResult:
        """
        Delete ALL webhook subscriptions on Gupshup for this app.

        Gupshup enforces a maximum of 5 subscriptions per app.
        This method calls ``DELETE /partner/app/{appId}/subscription``
        (without a subscription ID) which removes all subscriptions.
        """
        self._log("info", "purge_all_webhooks START")

        try:
            api = self._get_subscription_api()
        except ValueError as exc:
            self._log("error", f"purge_all_webhooks — credential error: {exc}")
            return AdapterResult(
                success=False,
                provider=self.PROVIDER_NAME,
                error_message=str(exc),
            )

        try:
            response = api.delete_all_subscriptions()
            self._log("info", f"purge_all_webhooks response: {response}")
        except Exception as exc:
            self._log("error", f"purge_all_webhooks FAILED — {exc}", exc_info=True)
            return AdapterResult(
                success=False,
                provider=self.PROVIDER_NAME,
                error_message=f"Gupshup delete all subscriptions failed: {exc}",
            )

        # Mark all local WASubscription records for this app as INACTIVE
        from wa.models import SubscriptionStatus, WASubscription

        deleted_count = (
            WASubscription.objects.filter(
                wa_app=self.wa_app,
            )
            .exclude(
                status=SubscriptionStatus.INACTIVE,
            )
            .update(
                status=SubscriptionStatus.INACTIVE,
                bsp_subscription_id=None,
                error_message="Purged during refresh",
            )
        )

        self._log("info", f"purge_all_webhooks SUCCESS — {deleted_count} local records marked INACTIVE")
        return AdapterResult(
            success=True,
            provider=self.PROVIDER_NAME,
            data={"deleted_count": deleted_count, "purged_on_bsp": True},
            raw_response=response,
        )

    # ── ORDER_DETAILS SEND helpers ────────────────────────────────────────

    @staticmethod
    def validate_order_details_data(order_data: dict) -> dict:
        """
        Validate order data for ORDER_DETAILS template SEND.

        Uses shared models from ``order_models.py`` for structural
        validation, plus arithmetic check:
        ``total_amount = subtotal + tax + shipping - discount``.

        Args:
            order_data: Dict with keys: currency, type, reference_id,
                payment_configuration, total_amount, order, payment_settings.

        Returns:
            The validated order_data dict (unchanged if valid).

        Raises:
            ValueError: On validation failure.
        """
        from wa.utility.data_model.shared.order_models import (
            OrderAmount,
            OrderItem,
            PaymentSettings,
        )

        # Required top-level fields
        required = (
            "currency",
            "type",
            "reference_id",
            "payment_configuration",
            "total_amount",
            "order",
            "payment_settings",
        )
        missing = [f for f in required if f not in order_data]
        if missing:
            raise ValueError(f"Missing required fields: {missing}")

        # Currency must be INR
        if order_data["currency"] != "INR":
            raise ValueError(f"Only INR currency is supported, got '{order_data['currency']}'")

        # Type must be digital-goods or physical-goods
        if order_data["type"] not in ("digital-goods", "physical-goods"):
            raise ValueError(f"type must be 'digital-goods' or 'physical-goods', got '{order_data['type']}'")

        # Validate total_amount structure
        total = order_data["total_amount"]
        OrderAmount(**total)

        # Validate order block
        order = order_data["order"]
        if not order.get("items"):
            raise ValueError("order.items is required and must not be empty")
        for item in order["items"]:
            OrderItem(**item)
        OrderAmount(**order.get("subtotal", {}))

        # Validate payment_settings
        ps_list = order_data["payment_settings"]
        if not ps_list:
            raise ValueError("payment_settings is required and must not be empty")
        for ps in ps_list:
            PaymentSettings(**ps)

        # Arithmetic: total = subtotal + tax + shipping - discount
        subtotal_val = order["subtotal"]["value"]
        tax_val = order.get("tax", {}).get("value", 0)
        shipping_val = order.get("shipping", {}).get("value", 0)
        discount_val = order.get("discount", {}).get("value", 0)
        expected = subtotal_val + tax_val + shipping_val - discount_val
        if total["value"] != expected:
            raise ValueError(
                f"total_amount ({total['value']}) != "
                f"subtotal ({subtotal_val}) + tax ({tax_val}) + "
                f"shipping ({shipping_val}) - discount ({discount_val}) "
                f"= {expected}"
            )

        return order_data

    @staticmethod
    def build_order_details_send_component(order_data: dict) -> dict:
        """
        Build the ORDER_DETAILS button component for a template SEND payload.

        Gupshup uses the same Meta Cloud API format for template sends.
        The component structure is::

            {
                "type": "button",
                "sub_type": "order_details",
                "index": 0,
                "parameters": [{
                    "type": "action",
                    "action": {
                        "order_details": { ... order data ... }
                    }
                }]
            }

        Args:
            order_data: Validated order data dict (call
                ``validate_order_details_data`` first).

        Returns:
            dict: The button component ready for inclusion in
            ``template.components``.
        """
        return {
            "type": "button",
            "sub_type": "order_details",
            "index": 0,
            "parameters": [
                {
                    "type": "action",
                    "action": {
                        "order_details": order_data,
                    },
                }
            ],
        }

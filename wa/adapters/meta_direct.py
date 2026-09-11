"""
META Direct BSP Adapter — submits templates to META's Graph API.

Uses the existing ``wa.utility.apis.meta.template_api.TemplateAPI`` HTTP
client under the hood.  Credential resolution follows the same priority as
``wa.services.meta_template_service``:

    1. ``wa_app.bsp_credentials["access_token"]``  (per-app token)
    2. ``settings.META_PERM_TOKEN``                 (global permanent token)

When the WAApp has ``bsp = "META"`` **or** ``bsp`` is blank/null the adapter
factory will select this adapter.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Optional

from django.conf import settings
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


class MetaDirectAdapter(BaseBSPAdapter):
    """
    Adapter for the META Direct (Graph API) WhatsApp Business platform.

    Responsibilities:
    - Build a ``TemplateAPI`` client from the WAApp's credentials.
    - Convert a ``WATemplate`` to a META payload (``template.to_meta_payload()``).
    - Call ``TemplateAPI.apply_for_template()`` to create templates.
    - Call ``TemplateAPI.get_template_status()`` to poll status.
    - Map META's response back onto canonical model fields.
    """

    PROVIDER_NAME = "meta_direct"
    capabilities = Capabilities(
        supports_text=True,
        supports_media=True,
        supports_keyboards=True,
        supports_templates=True,
        supports_template_buttons=True,
        supports_reactions=True,
        # Dropped rather than implemented (#274). META does expose a typing
        # indicator — the same POST as a read receipt, plus
        # ``typing_indicator`` — but no adapter method sends one, and the
        # team-inbox indicator is agent-to-agent over WebSocket only. #266
        # settled that a flag must match the code behind it; this one
        # pointed at nothing.
        supports_typing_indicator=False,
        # CTWA #192 — Meta Cloud surfaces the full referral payload
        # including ctwa_clid, which is the highest-quality match key
        # for Conversions API.
        supports_ctwa_referral=True,
        supports_ctwa_clid=True,
        # "media_upload" belongs here because ``upload_media`` below is a real
        # Resumable Upload implementation. ``supports()`` consults *only* this
        # frozenset, so omitting it made the viewset return 501 for every media
        # template while the method sat fully implemented — see #266.
        extra=frozenset({"templates", "subscriptions", "media_upload"}),
    )

    # ── CTWA referral parsing (#192) ─────────────────────────────────────

    def parse_referral(self, raw_webhook_payload: dict):
        """Extract CTWA referral from a Meta Cloud inbound webhook.

        Field path: ``entry[].changes[].value.messages[].referral``.
        Returns ``CtwaReferral`` or ``None``. Never raises.
        """
        from wa.adapters.ctwa_referral import CtwaReferral

        if not isinstance(raw_webhook_payload, dict):
            return None
        try:
            entries = raw_webhook_payload.get("entry") or []
            for entry in entries:
                changes = entry.get("changes") or []
                for change in changes:
                    value = change.get("value") or {}
                    messages = value.get("messages") or []
                    for msg in messages:
                        ref = msg.get("referral")
                        if not isinstance(ref, dict):
                            continue
                        source_id = ref.get("source_id") or ref.get("source_ad_id")
                        if not source_id:
                            continue
                        return CtwaReferral(
                            source_type=str(ref.get("source_type") or "ad"),
                            source_id=str(source_id),
                            source_url=str(ref.get("source_url") or ""),
                            headline=str(ref.get("headline") or ""),
                            body=str(ref.get("body") or ""),
                            media_type=str(ref.get("media_type") or ""),
                            media_url=str(ref.get("media_url") or ""),
                            thumbnail_url=str(ref.get("thumbnail_url") or ""),
                            ctwa_clid=str(ref.get("ctwa_clid") or ""),
                        )
        except Exception:  # noqa: BLE001 — never raise from parse_referral
            return None
        return None

    # ── credential helpers ────────────────────────────────────────────────

    def _resolve_access_token(self) -> Optional[str]:
        """
        Resolve the META access token with the same priority used elsewhere.

        1. ``wa_app.bsp_credentials["access_token"]``
        2. ``settings.META_PERM_TOKEN``
        """
        creds = self.wa_app.bsp_credentials or {}
        token = creds.get("access_token")
        if token:
            return token

        token = getattr(settings, "META_PERM_TOKEN", None)
        if token:
            return token

        return None

    def _resolve_waba_id(self) -> Optional[str]:
        """Return the WABA ID stored on the WAApp."""
        return self.wa_app.waba_id or None

    def _get_template_api(self):
        """
        Build a configured ``TemplateAPI`` instance.

        Raises ``ValueError`` when credentials are missing so the caller can
        surface a clear error to the user instead of a cryptic 401.
        """
        from wa.utility.apis.meta.template_api import TemplateAPI

        token = self._resolve_access_token()
        if not token:
            raise ValueError(
                "META access token not configured. Set bsp_credentials.access_token "
                "on the WAApp or META_PERM_TOKEN in settings."
            )

        waba_id = self._resolve_waba_id()
        if not waba_id:
            raise ValueError(
                "WABA ID not configured on the WAApp. Please set wa_app.waba_id before submitting templates."
            )

        api = TemplateAPI(token=token)
        api.waba_id = waba_id
        return api

    def _get_waba_api(self):
        """Build a configured ``WABAAPI`` instance.

        Mirrors ``_get_template_api``: raises ``ValueError`` for missing
        credentials so the caller can report which knob is unset rather than
        surfacing a 401.
        """
        from wa.utility.apis.meta.waba import WABAAPI

        token = self._resolve_access_token()
        if not token:
            raise ValueError(
                "META access token not configured. Set bsp_credentials.access_token "
                "on the WAApp or META_PERM_TOKEN in settings."
            )

        waba_id = self._resolve_waba_id()
        if not waba_id:
            raise ValueError(
                "WABA ID not configured on the WAApp. Please set wa_app.waba_id before "
                "registering webhooks."
            )

        api = WABAAPI(token=token)
        api.waba_id = waba_id
        return api

    # ── Payload validation ─────────────────────────────────────────────

    @staticmethod
    def _has_copy_code_button(template: "WATemplate") -> bool:
        """Check if template has a COPY_CODE button (→ coupon code template)."""
        for btn in template.buttons or []:
            if isinstance(btn, dict) and (btn.get("type") or "").upper() == "COPY_CODE":
                return True
        return False

    def _validate_payload(self, template: "WATemplate", payload: dict) -> None:
        """
        Validate *payload* through the appropriate META Pydantic validator.

        Picks the validator based on ``template.category`` and
        ``template.template_type`` (e.g. CAROUSEL gets its own validator).
        Raises ``pydantic.ValidationError`` on failure so the caller can
        surface a clear message instead of a cryptic META API error.
        """
        from wa.utility.validators.meta_direct.create.authentication_template_request import (
            AuthenticationTemplateRequestValidator,
        )
        from wa.utility.validators.meta_direct.create.carousel_template_request import (
            CarouselTemplateRequestValidator,
        )
        from wa.utility.validators.meta_direct.create.catalog_template_request import (
            CatalogTemplateRequestValidator,
        )
        from wa.utility.validators.meta_direct.create.checkout_template_request import (
            CheckoutTemplateRequestValidator,
        )
        from wa.utility.validators.meta_direct.create.coupon_code_template_request import (
            CouponCodeTemplateRequestValidator,
        )
        from wa.utility.validators.meta_direct.create.lto_template_request import (
            LTOTemplateRequestValidator,
        )
        from wa.utility.validators.meta_direct.create.marketing_template_request import (
            MarketingTemplateRequestValidator,
        )
        from wa.utility.validators.meta_direct.create.utility_template_request import (
            UtilityTemplateRequestValidator,
        )

        category = (template.category or "").upper()
        ttype = (template.template_type or "").upper()

        # Carousel / Catalog / Order Details get their own validators regardless of category
        if ttype == "CAROUSEL":
            validator_cls = CarouselTemplateRequestValidator
        elif ttype == "CATALOG":
            validator_cls = CatalogTemplateRequestValidator
        elif ttype == "ORDER_DETAILS":
            validator_cls = CheckoutTemplateRequestValidator
        elif getattr(template, "is_lto", False):
            validator_cls = LTOTemplateRequestValidator
        elif self._has_copy_code_button(template):
            validator_cls = CouponCodeTemplateRequestValidator
        else:
            validator_map = {
                "MARKETING": MarketingTemplateRequestValidator,
                "UTILITY": UtilityTemplateRequestValidator,
                "AUTHENTICATION": AuthenticationTemplateRequestValidator,
            }
            validator_cls = validator_map.get(category)

        if validator_cls is None:
            self._log("warning", f"No META validator for category={category}, type={ttype} — skipping validation")
            return

        # Pydantic v2 coerces nested dicts into model instances in-place,
        # so validate on a copy to keep the original JSON-serialisable.
        import copy

        validator_cls(**copy.deepcopy(payload))
        self._log("info", f"Payload validated via {validator_cls.__name__}")

    @staticmethod
    def _get_send_validator_class(template_type: str):
        """
        Return the SEND-side Pydantic validator for *template_type*.

        Maps template types to their corresponding SEND validator classes.
        Returns ``None`` for unknown types (caller decides how to proceed).
        """
        ttype = (template_type or "").upper()

        # Lazy imports — keeps the module lightweight when no send
        # validation is needed.
        if ttype == "ORDER_DETAILS":
            from wa.utility.validators.meta_direct.send.template.checkout_template_send_request import (
                CheckoutTemplateSendRequestValidator,
            )

            return CheckoutTemplateSendRequestValidator
        elif ttype == "CAROUSEL":
            from wa.utility.validators.meta_direct.send.template.carousel_template_send_request import (
                CarouselTemplateSendRequestValidator,
            )

            return CarouselTemplateSendRequestValidator
        elif ttype == "CATALOG":
            from wa.utility.validators.meta_direct.send.template.catalog_template_send_request import (
                CatalogTemplateSendRequestValidator,
            )

            return CatalogTemplateSendRequestValidator
        elif ttype == "ORDER_STATUS":
            from wa.utility.validators.meta_direct.send.template.order_status_template_send_request import (
                OrderStatusTemplateSendRequestValidator,
            )

            return OrderStatusTemplateSendRequestValidator
        return None

    def _validate_send_payload(self, template_type: str, payload: dict) -> None:
        """
        Validate a SEND *payload* through the appropriate Pydantic validator.

        Complements ``_validate_payload`` (which covers CREATE). Call this
        before sending a template message to Meta Cloud API.

        Raises ``pydantic.ValidationError`` on failure.
        """
        validator_cls = self._get_send_validator_class(template_type)

        if validator_cls is None:
            self._log(
                "debug",
                f"No META SEND validator for type={template_type} — skipping",
            )
            return

        import copy

        validator_cls(**copy.deepcopy(payload))
        self._log("info", f"Send payload validated via {validator_cls.__name__}")

    # ── Template operations ───────────────────────────────────────────────

    @silk_profile(name="adapter.submit_template")
    def submit_template(self, template: "WATemplate") -> AdapterResult:
        """
        Submit *template* to META's Graph API for review.

        On success the template is moved to ``PENDING`` and
        ``meta_template_id`` is stored.  On failure ``error_message`` is
        populated and the status stays unchanged.
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
                f"[STEP 2/5] Credentials resolved — waba_id={api.waba_id}, token={'***' + self._resolve_access_token()[-4:] if self._resolve_access_token() else 'NONE'}",
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

        # Step 3: Build & validate payload
        payload = template.to_meta_payload()
        self._log(
            "info",
            f"[STEP 3/5] Payload built — name={payload.get('name')}, category={payload.get('category')}, components={len(payload.get('components', []))}",
        )
        self._log("debug", f"[STEP 3/5] Full payload: {payload}")

        try:
            self._validate_payload(template, payload)
        except ValidationError as exc:
            error_msg = f"Payload validation failed: {exc}"
            self._log("error", f"[STEP 3/5] Validation FAILED — {exc}")
            template.error_message = error_msg
            template.save(update_fields=["error_message"])
            return AdapterResult(
                success=False,
                provider=self.PROVIDER_NAME,
                error_message=error_msg,
            )

        # Step 4: Call META API
        try:
            self._log("info", f"[STEP 4/5] Calling META Graph API — POST /{api.waba_id}/message_templates")
            response = api.apply_for_template(payload)
            self._log(
                "info",
                f"[STEP 4/5] META responded — keys={list(response.keys()) if isinstance(response, dict) else type(response)}",
            )
            self._log("debug", f"[STEP 4/5] Full response: {response}")
        except Exception as exc:
            error_msg = f"META API call failed: {exc}"
            self._log("error", f"[STEP 4/5] META API call FAILED — {exc}", exc_info=True)
            template.error_message = error_msg
            template.save(update_fields=["error_message"])
            return AdapterResult(
                success=False,
                provider=self.PROVIDER_NAME,
                error_message=error_msg,
            )

        # ── Step 5: Interpret the response ────────────────────────────────
        meta_error = response.get("error")
        if meta_error:
            error_msg = meta_error.get("message", str(meta_error))
            self._log("warning", f"[STEP 5/5] META REJECTED — code={meta_error.get('code')}, msg={error_msg}")
            template.error_message = error_msg
            template.save(update_fields=["error_message"])
            return AdapterResult(
                success=False,
                provider=self.PROVIDER_NAME,
                error_message=error_msg,
                raw_response=response,
            )

        # Success path
        meta_template_id = response.get("id")
        template.meta_template_id = meta_template_id
        template.status = TemplateStatus.PENDING
        template.needs_sync = False
        template.error_message = None
        template.last_synced_at = timezone.now()
        template.save(
            update_fields=[
                "meta_template_id",
                "status",
                "needs_sync",
                "error_message",
                "last_synced_at",
            ]
        )

        self._log("info", f"[STEP 5/5] submit_template SUCCESS — meta_id={meta_template_id}, status=PENDING")

        return AdapterResult(
            success=True,
            provider=self.PROVIDER_NAME,
            data={
                "meta_template_id": meta_template_id,
                "status": template.status,
            },
            raw_response=response,
        )

    # ──────────────────────────────────────────────────────────────────────

    @silk_profile(name="adapter.get_template_status")
    def get_template_status(self, template: "WATemplate") -> AdapterResult:
        """
        Fetch template status from META Graph API.

        Requires ``template.meta_template_id`` to be set (i.e. the template
        was already submitted).
        """
        self._log(
            "info",
            f"[STEP 1/4] get_template_status START — element_name={template.element_name}, meta_id={template.meta_template_id}",
        )

        if not template.meta_template_id:
            self._log("warning", "[STEP 1/4] ABORTED — meta_template_id is not set")
            return AdapterResult(
                success=False,
                provider=self.PROVIDER_NAME,
                error_message="meta_template_id is not set -- template was never submitted.",
            )

        try:
            api = self._get_template_api()
            self._log("info", f"[STEP 2/4] Calling META — GET template status for {template.meta_template_id}")
            response = api.get_template_status(template.meta_template_id)
            self._log("debug", f"[STEP 2/4] Response: {response}")
        except Exception as exc:
            self._log("error", f"[STEP 2/4] META API call FAILED — {exc}", exc_info=True)
            return AdapterResult(
                success=False,
                provider=self.PROVIDER_NAME,
                error_message=f"META API call failed: {exc}",
            )

        meta_error = response.get("error")
        if meta_error:
            self._log("warning", f"[STEP 3/4] META error — {meta_error.get('message', meta_error)}")
            return AdapterResult(
                success=False,
                provider=self.PROVIDER_NAME,
                error_message=meta_error.get("message", str(meta_error)),
                raw_response=response,
            )

        # Map META status string to our canonical TemplateStatus
        meta_status = response.get("status", "").upper()
        status_map = {
            "APPROVED": TemplateStatus.APPROVED,
            "PENDING": TemplateStatus.PENDING,
            "REJECTED": TemplateStatus.REJECTED,
            "PAUSED": TemplateStatus.PAUSED,
            "DISABLED": TemplateStatus.DISABLED,
        }
        canonical_status = status_map.get(meta_status, template.status)
        self._log("info", f"[STEP 3/4] Status mapped — meta_status={meta_status} → canonical={canonical_status}")

        # Persist the refreshed status
        template.status = canonical_status
        if meta_status == "REJECTED":
            template.rejection_reason = response.get(
                "rejected_reason", response.get("quality_score", {}).get("reasons")
            )
            self._log("warning", f"[STEP 4/4] Template REJECTED — reason={template.rejection_reason}")
        template.last_synced_at = timezone.now()
        template.save(update_fields=["status", "rejection_reason", "last_synced_at"])

        self._log("info", f"[STEP 4/4] get_template_status SUCCESS — status={canonical_status}")

        return AdapterResult(
            success=True,
            provider=self.PROVIDER_NAME,
            data={"status": canonical_status},
            raw_response=response,
        )

    # ──────────────────────────────────────────────────────────────────────

    @silk_profile(name="adapter.delete_template")
    def delete_template(self, template: "WATemplate") -> AdapterResult:
        """
        Delete a template from META.

        META Graph API: ``DELETE /{waba_id}/message_templates?name={element_name}``
        """
        self._log("info", f"[STEP 1/4] delete_template START — element_name={template.element_name}")

        try:
            api = self._get_template_api()
            self._log("info", f"[STEP 2/4] Credentials resolved — waba_id={api.waba_id}")
        except ValueError as exc:
            self._log("error", f"[STEP 2/4] Credential resolution FAILED — {exc}")
            return AdapterResult(
                success=False,
                provider=self.PROVIDER_NAME,
                error_message=str(exc),
            )

        url = f"{api.BASE_URL}{api.waba_id}/message_templates"
        try:
            import requests as http

            self._log("info", f"[STEP 3/4] Calling META — DELETE {url}?name={template.element_name}")
            resp = http.delete(
                url,
                headers=api.json_headers,
                params={"name": template.element_name},
                timeout=30,
            )
            response = resp.json()
            self._log("debug", f"[STEP 3/4] Response: {response}")
        except Exception as exc:
            self._log("error", f"[STEP 3/4] META API call FAILED — {exc}", exc_info=True)
            return AdapterResult(
                success=False,
                provider=self.PROVIDER_NAME,
                error_message=f"META API call failed: {exc}",
            )

        if response.get("error"):
            self._log("warning", f"[STEP 4/4] META error — {response['error'].get('message', response['error'])}")
            return AdapterResult(
                success=False,
                provider=self.PROVIDER_NAME,
                error_message=response["error"].get("message", str(response["error"])),
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

    # ── List templates ────────────────────────────────────────────────────

    @silk_profile(name="adapter.meta.list_templates")
    def list_templates(self) -> AdapterResult:
        """
        List all message templates from the META Graph API.

        Endpoint: ``GET /{waba_id}/message_templates``

        Returns ``data={"templates": [...]}`` with the raw META template
        objects on success.
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

        url = f"{api.BASE_URL}{api.waba_id}/message_templates"
        try:
            import requests as http

            resp = http.get(url, headers=api.json_headers, timeout=30)
            response = resp.json()
        except Exception as exc:
            self._log("error", f"list_templates API call FAILED — {exc}", exc_info=True)
            return AdapterResult(
                success=False,
                provider=self.PROVIDER_NAME,
                error_message=f"META API call failed: {exc}",
            )

        if response.get("error"):
            self._log("warning", f"list_templates META error — {response['error']}")
            return AdapterResult(
                success=False,
                provider=self.PROVIDER_NAME,
                error_message=response["error"].get("message", str(response["error"])),
                raw_response=response,
            )

        templates = response.get("data", [])
        self._log("info", f"list_templates SUCCESS — count={len(templates)}")

        return AdapterResult(
            success=True,
            provider=self.PROVIDER_NAME,
            data={"templates": templates},
            raw_response=response,
        )

    # ── Webhook Subscription operations ───────────────────────────────────

    @silk_profile(name="adapter.meta.register_webhook")
    def register_webhook(self, subscription: "WASubscription") -> AdapterResult:
        """
        Subscribe this WABA to the app so META actually delivers its events.

        Two separate things govern whether anything arrives, and only one of
        them is app-level:

        * The **callback URL** and verify token are configured once per app in
          the App Dashboard. There is no API call for those, which is what the
          previous implementation correctly observed.
        * Every **WABA must additionally be subscribed to that app** via
          ``POST /{waba_id}/subscribed_apps``, or META delivers nothing for it
          — no inbound messages, no delivery or read statuses, no
          ``message_template_status_update``, no ``account_update``.

        The second was missing entirely: this method flipped the local row to
        ACTIVE and reported success without making any call, so an onboarded
        customer silently received nothing while the platform showed a healthy
        subscription (#264).

        META derives which app to subscribe from the access token, so no app id
        is sent.
        """
        from wa.models import SubscriptionStatus

        self._log("info", f"[STEP 1/4] register_webhook START — url={subscription.webhook_url}")

        def _fail(message: str) -> AdapterResult:
            """Record a failure. A subscription that did not happen is not ACTIVE."""
            subscription.status = SubscriptionStatus.FAILED
            subscription.error_message = message
            subscription.save(update_fields=["status", "error_message"])
            self._log("error", f"register_webhook FAILED — {message}")
            return AdapterResult(
                success=False,
                provider=self.PROVIDER_NAME,
                error_message=message,
            )

        try:
            api = self._get_waba_api()
        except ValueError as exc:
            return _fail(str(exc))

        self._log("info", f"[STEP 2/4] Subscribing WABA {api.waba_id} — POST {api._subscribed_apps_url}")
        try:
            api.subscribe_app()
        except Exception as exc:  # noqa: BLE001 — surfaced to the caller below
            return _fail(f"META refused the WABA subscription: {exc}")

        # Verify rather than trust the write. A POST that returns success and a
        # WABA that is not actually subscribed is exactly the state this ticket
        # was about, and it is cheap to rule out.
        self._log("info", "[STEP 3/4] Verifying — GET subscribed_apps")
        try:
            listed = api.get_subscribed_apps() or {}
        except Exception as exc:  # noqa: BLE001
            return _fail(f"WABA subscription could not be verified: {exc}")

        subscribed_ids: list[str] = []
        for row in listed.get("data") or []:
            if not isinstance(row, dict):
                continue
            app_data = row.get("whatsapp_business_api_data") or {}
            app_id = str(app_data.get("id") or "")
            if app_id:
                subscribed_ids.append(app_id)

        if not subscribed_ids:
            return _fail(
                "META accepted the subscription but lists no subscribed app for this WABA. "
                "Nothing would be delivered."
            )

        # ``wa_app.app_id`` is overloaded — documented as the Gupshup app ID and
        # reused as the META App ID by ``upload_media`` — so a mismatch here is
        # not reliable enough to fail on. Worth a warning, not a refusal.
        own_app_id = str(getattr(self.wa_app, "app_id", "") or "")
        if own_app_id and own_app_id not in subscribed_ids:
            self._log(
                "warning",
                f"app_id {own_app_id!r} is not among the subscribed apps {subscribed_ids} — "
                "either app_id holds a non-META value or a different app is subscribed",
            )

        subscription.bsp_subscription_id = own_app_id if own_app_id in subscribed_ids else subscribed_ids[0]
        subscription.status = SubscriptionStatus.ACTIVE
        subscription.error_message = None
        subscription.save(update_fields=["bsp_subscription_id", "status", "error_message"])

        self._log(
            "info",
            f"[STEP 4/4] register_webhook SUCCESS — WABA {api.waba_id} subscribed, apps={subscribed_ids}",
        )

        return AdapterResult(
            success=True,
            provider=self.PROVIDER_NAME,
            data={
                "waba_id": api.waba_id,
                "subscribed_app_ids": subscribed_ids,
                "note": (
                    "WABA subscribed to the app. The callback URL and verify token remain "
                    "app-level configuration in the META App Dashboard."
                ),
            },
        )

    @silk_profile(name="adapter.meta.unregister_webhook")
    def unregister_webhook(self, subscription: "WASubscription") -> AdapterResult:
        """
        Unsubscribe this WABA from the app, so META stops delivering its events.

        The counterpart to :meth:`register_webhook`:
        ``DELETE /{waba_id}/subscribed_apps``. Previously this only flipped the
        local row to INACTIVE, which left META still delivering to a
        subscription the platform believed it had torn down.
        """
        from wa.models import SubscriptionStatus

        self._log("info", f"unregister_webhook START — sub_id={subscription.id}")

        try:
            api = self._get_waba_api()
        except ValueError as exc:
            # Nothing can be unsubscribed without credentials, but the local
            # row should still stop claiming to be live.
            subscription.status = SubscriptionStatus.INACTIVE
            subscription.error_message = str(exc)
            subscription.save(update_fields=["status", "error_message"])
            self._log("warning", f"unregister_webhook — marked INACTIVE without calling META: {exc}")
            return AdapterResult(
                success=False,
                provider=self.PROVIDER_NAME,
                error_message=str(exc),
            )

        try:
            api.unsubscribe_app()
        except Exception as exc:  # noqa: BLE001
            message = f"META refused the WABA unsubscribe: {exc}"
            subscription.status = SubscriptionStatus.FAILED
            subscription.error_message = message
            subscription.save(update_fields=["status", "error_message"])
            self._log("error", f"unregister_webhook FAILED — {message}")
            return AdapterResult(
                success=False,
                provider=self.PROVIDER_NAME,
                error_message=message,
            )

        subscription.status = SubscriptionStatus.INACTIVE
        subscription.error_message = None
        subscription.save(update_fields=["status", "error_message"])

        self._log("info", f"unregister_webhook SUCCESS — WABA {api.waba_id} unsubscribed")

        return AdapterResult(
            success=True,
            provider=self.PROVIDER_NAME,
            data={"waba_id": api.waba_id},
        )

    # ── Account information ──────────────────────────────────────────────

    #: META's ``messaging_limit_tier`` strings happen to match our own
    #: ``WABAInfo.MessagingLimit`` values exactly. Rather than rely on that,
    #: unknown tiers are refused and logged: storing a value the model does not
    #: know would read as a real tier while ``get_limit()`` silently scores it
    #: 50, which is the failure this ticket is about.
    _KNOWN_TIERS = frozenset(
        {"TIER_50", "TIER_250", "TIER_1K", "TIER_10K", "TIER_100K", "TIER_UNLIMITED", "TIER_NOT_SET"}
    )
    _KNOWN_QUALITY = frozenset({"GREEN", "YELLOW", "RED", "UNKNOWN"})
    _KNOWN_THROUGHPUT = frozenset({"HIGH", "STANDARD", "NOT_APPLICABLE"})

    @silk_profile(name="adapter.meta.fetch_waba_info")
    def fetch_waba_info(self) -> AdapterResult:
        """
        Read tier, quality and throughput from META.

        Two calls, because the fields live on different nodes:

        * ``GET /{waba_id}/phone_numbers`` — per-number ``quality_rating``,
          ``messaging_limit_tier``, ``throughput`` and ``verified_name``
        * ``GET /{waba_id}`` — account-level ``account_review_status``

        The phone-number edge returns every number on the WABA, so the row
        matching this app's ``phone_number_id`` is selected; a WABA with one
        number still works if the id is unset, but on a shared WABA picking the
        first row would attribute another customer's quality rating to this app,
        so that case is reported rather than guessed.
        """
        self._log("info", "[STEP 1/4] fetch_waba_info START")

        try:
            api = self._get_waba_api()
        except ValueError as exc:
            self._log("error", f"[STEP 1/4] Credential resolution FAILED — {exc}")
            return AdapterResult(success=False, provider=self.PROVIDER_NAME, error_message=str(exc))

        self._log("info", f"[STEP 2/4] GET phone_numbers for WABA {api.waba_id}")
        try:
            numbers_response = api.get_phone_numbers() or {}
        except Exception as exc:  # noqa: BLE001
            return AdapterResult(
                success=False,
                provider=self.PROVIDER_NAME,
                error_message=f"Could not read phone numbers from META: {exc}",
            )

        rows = [r for r in (numbers_response.get("data") or []) if isinstance(r, dict)]
        if not rows:
            return AdapterResult(
                success=False,
                provider=self.PROVIDER_NAME,
                error_message="META reports no phone numbers on this WABA.",
                raw_response=numbers_response,
            )

        wanted = str(getattr(self.wa_app, "phone_number_id", "") or "")
        row = next((r for r in rows if str(r.get("id") or "") == wanted), None)
        if row is None:
            if len(rows) > 1:
                return AdapterResult(
                    success=False,
                    provider=self.PROVIDER_NAME,
                    error_message=(
                        f"phone_number_id {wanted or '(unset)'} is not among the "
                        f"{len(rows)} numbers on this WABA, so quality and tier cannot be "
                        "attributed to this app."
                    ),
                    raw_response=numbers_response,
                )
            row = rows[0]
            self._log("warning", f"phone_number_id {wanted or '(unset)'} not matched; using the WABA's only number")

        data: dict = {"waba_id": api.waba_id}

        tier = str(row.get("messaging_limit_tier") or "")
        if tier:
            if tier in self._KNOWN_TIERS:
                data["messaging_limit"] = tier
            else:
                self._log("warning", f"META reported unknown messaging_limit_tier {tier!r} — not stored")

        quality = str(row.get("quality_rating") or "").upper()
        if quality:
            if quality in self._KNOWN_QUALITY:
                data["phone_quality"] = quality
            else:
                self._log("warning", f"META reported unknown quality_rating {quality!r} — not stored")

        # ``throughput`` is an object on this edge: {"level": "STANDARD"}.
        throughput = row.get("throughput")
        level = str((throughput or {}).get("level") or "").upper() if isinstance(throughput, dict) else ""
        if level:
            if level in self._KNOWN_THROUGHPUT:
                data["throughput"] = level
            else:
                self._log("warning", f"META reported unknown throughput level {level!r} — not stored")

        if row.get("verified_name") is not None:
            data["verified_name"] = row.get("verified_name")
        if row.get("display_phone_number") is not None:
            data["phone"] = row.get("display_phone_number")

        # Account-level review status is a separate node and is additive: a
        # failure here must not discard the tier we just read successfully.
        self._log("info", "[STEP 3/4] GET account review status")
        try:
            account = api.get_account_status() or {}
            review = str(account.get("account_review_status") or "").upper()
            if review:
                data["account_status"] = "APPROVED" if review == "APPROVED" else "PENDING"
        except Exception as exc:  # noqa: BLE001
            self._log("warning", f"account review status unavailable, continuing without it: {exc}")

        self._log("info", f"[STEP 4/4] fetch_waba_info SUCCESS — {sorted(data)}")
        return AdapterResult(
            success=True,
            provider=self.PROVIDER_NAME,
            data=data,
            raw_response=numbers_response,
        )

    # ── Media operations ─────────────────────────────────────────────────

    @silk_profile(name="adapter.meta.upload_media")
    def upload_media(
        self,
        file_obj,
        filename: str,
        file_type: str | None = None,
    ) -> AdapterResult:
        """
        Upload media to META via the Resumable Upload API.

        Uses ``POST /{app_id}/uploads`` to create an upload session, then
        uploads the file data to get a handle suitable for template
        ``header_handle`` fields.

        The regular media API (``/{phone_number_id}/media``) returns IDs
        that are only valid for *sending messages*, NOT for template
        creation.  Template headers require handles from the Resumable
        Upload API.

        Requires ``wa_app.app_id`` and ``wa_app.phone_number_id`` to be set.
        """
        self._log("info", f"upload_media START — filename={filename}, file_type={file_type}")

        # Resolve credentials
        token = self._resolve_access_token()
        if not token:
            return AdapterResult(
                success=False,
                provider=self.PROVIDER_NAME,
                error_message="META access token not configured.",
            )

        app_id = getattr(self.wa_app, "app_id", None)
        phone_number_id = getattr(self.wa_app, "phone_number_id", None)
        if not app_id:
            return AdapterResult(
                success=False,
                provider=self.PROVIDER_NAME,
                error_message="app_id not configured on the WAApp. Required for META Resumable Upload API.",
            )
        if not phone_number_id:
            return AdapterResult(
                success=False,
                provider=self.PROVIDER_NAME,
                error_message="phone_number_id not configured on the WAApp. Required for META media uploads.",
            )

        # Build the MediaAPI client
        from wa.utility.apis.meta.media_api import MetaMediaAPI

        media_api = MetaMediaAPI(token=token, phone_number_id=phone_number_id)

        try:
            handle_id = media_api.upload_media_for_template(
                app_id=app_id,
                file_obj=file_obj,
                filename=filename,
                mime_type=file_type,
            )
            self._log("info", f"upload_media (resumable) handle — {handle_id}")
        except ValueError as exc:
            # Validation errors (unsupported MIME, file too large)
            error_msg = f"Media validation failed: {exc}"
            self._log("warning", error_msg)
            return AdapterResult(
                success=False,
                provider=self.PROVIDER_NAME,
                error_message=error_msg,
            )
        except Exception as exc:
            error_msg = f"META media upload failed: {exc}"
            self._log("error", error_msg, exc_info=True)
            return AdapterResult(
                success=False,
                provider=self.PROVIDER_NAME,
                error_message=error_msg,
            )

        if not handle_id:
            return AdapterResult(
                success=False,
                provider=self.PROVIDER_NAME,
                error_message="META Resumable Upload returned no file handle.",
            )

        self._log("info", f"upload_media SUCCESS — handle_id={handle_id}")
        return AdapterResult(
            success=True,
            provider=self.PROVIDER_NAME,
            data={"handle_id": handle_id},
        )

    @silk_profile(name="adapter.meta.upload_session_media")
    def upload_session_media(
        self,
        file_obj,
        filename: str,
        file_type: str | None = None,
    ) -> AdapterResult:
        """
        Upload media via the **regular** Media API for session messages.

        Uses ``POST /{phone_number_id}/media`` which returns a media ID
        suitable for ``image.id`` / ``video.id`` etc. in session message
        payloads.  This is different from ``upload_media()`` which uses
        the Resumable Upload API for template header handles.
        """
        self._log("info", f"upload_session_media START — filename={filename}, file_type={file_type}")

        token = self._resolve_access_token()
        if not token:
            return AdapterResult(
                success=False,
                provider=self.PROVIDER_NAME,
                error_message="META access token not configured.",
            )

        phone_number_id = getattr(self.wa_app, "phone_number_id", None)
        if not phone_number_id:
            return AdapterResult(
                success=False,
                provider=self.PROVIDER_NAME,
                error_message="phone_number_id not configured on the WAApp.",
            )

        from wa.utility.apis.meta.media_api import MetaMediaAPI

        media_api = MetaMediaAPI(token=token, phone_number_id=phone_number_id)

        try:
            response = media_api.upload_media_from_file_object(
                file_obj=file_obj,
                filename=filename,
                mime_type=file_type,
            )
            media_id = response.get("id", "")
            self._log("info", f"upload_session_media response — {response}")
        except ValueError as exc:
            error_msg = f"Media validation failed: {exc}"
            self._log("warning", error_msg)
            return AdapterResult(
                success=False,
                provider=self.PROVIDER_NAME,
                error_message=error_msg,
            )
        except Exception as exc:
            error_msg = f"META session media upload failed: {exc}"
            self._log("error", error_msg, exc_info=True)
            return AdapterResult(
                success=False,
                provider=self.PROVIDER_NAME,
                error_message=error_msg,
            )

        if not media_id:
            return AdapterResult(
                success=False,
                provider=self.PROVIDER_NAME,
                error_message="META Media API returned no media ID.",
            )

        self._log("info", f"upload_session_media SUCCESS — media_id={media_id}")
        return AdapterResult(
            success=True,
            provider=self.PROVIDER_NAME,
            data={"handle_id": media_id},
        )

    @silk_profile(name="adapter.meta.list_webhooks")
    def list_webhooks(self) -> AdapterResult:
        """
        List webhook subscriptions for this META app.

        Returns locally-stored subscriptions since META doesn't offer a
        per-subscription listing API.
        """
        self._log("info", "list_webhooks START")

        from wa.models import WASubscription

        qs = WASubscription.objects.filter(wa_app=self.wa_app).values(
            "id",
            "webhook_url",
            "event_types",
            "status",
            "bsp_subscription_id",
        )
        subscriptions = list(qs)

        self._log("info", f"list_webhooks SUCCESS — count={len(subscriptions)}")

        return AdapterResult(
            success=True,
            provider=self.PROVIDER_NAME,
            data={"subscriptions": subscriptions},
        )

    @silk_profile(name="adapter.meta.purge_all_webhooks")
    def purge_all_webhooks(self) -> AdapterResult:
        """
        Purge all local webhook subscriptions for this META app.

        META webhooks are app-level (configured in the App Dashboard),
        so this only marks local WASubscription records as INACTIVE.
        """
        self._log("info", "purge_all_webhooks START")

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
                error_message="Purged during refresh",
            )
        )

        self._log("info", f"purge_all_webhooks SUCCESS — {deleted_count} local records marked INACTIVE")
        return AdapterResult(
            success=True,
            provider=self.PROVIDER_NAME,
            data={"deleted_count": deleted_count, "purged_on_bsp": False},
        )

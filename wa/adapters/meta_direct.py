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
    CAPABILITIES = frozenset({"templates", "subscriptions"})

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

            resp = http.get(url, headers=api.json_headers)
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
        Register a webhook with META Graph API.

        META manages webhooks at the **WABA level** via the App Dashboard
        or Graph API ``/{app-id}/subscriptions`` endpoint.  Per-subscription
        registration is not natively supported — META uses a single webhook
        URL per app configured in the App Dashboard.

        This adapter stores the subscription locally and marks it ACTIVE
        since the actual META webhook URL is configured once at the app
        level (not per-subscription).
        """
        self._log("info", f"register_webhook START — url={subscription.webhook_url}")

        from wa.models import SubscriptionStatus

        # META doesn't have per-subscription registration.
        # The webhook URL is configured once in App Dashboard.
        # We mark it ACTIVE so the app can route events.
        subscription.status = SubscriptionStatus.ACTIVE
        subscription.error_message = None
        subscription.save(update_fields=["status", "error_message"])

        self._log("info", "register_webhook SUCCESS — META uses app-level webhook, marked ACTIVE")

        return AdapterResult(
            success=True,
            provider=self.PROVIDER_NAME,
            data={
                "note": "META uses app-level webhooks configured in App Dashboard. "
                "Subscription marked ACTIVE for internal routing.",
            },
        )

    @silk_profile(name="adapter.meta.unregister_webhook")
    def unregister_webhook(self, subscription: "WASubscription") -> AdapterResult:
        """
        Unregister (deactivate) a webhook subscription.

        Since META doesn't support per-subscription unregistration, this
        simply marks the subscription INACTIVE locally.
        """
        self._log("info", f"unregister_webhook START — sub_id={subscription.id}")

        from wa.models import SubscriptionStatus

        subscription.status = SubscriptionStatus.INACTIVE
        subscription.save(update_fields=["status"])

        self._log("info", "unregister_webhook SUCCESS — marked INACTIVE")

        return AdapterResult(
            success=True,
            provider=self.PROVIDER_NAME,
            data={"note": "Subscription deactivated locally."},
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

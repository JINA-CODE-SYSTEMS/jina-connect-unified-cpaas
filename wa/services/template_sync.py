"""
Template Sync Service — Bulk-sync templates from BSP into WATemplate.

Called from the ``sync_from_bsp`` viewset action.  The service:

1. Calls ``adapter.list_templates()`` to get all templates from the BSP.
2. For each template, maps BSP fields → canonical ``WATemplate`` fields.
3. Upserts (create or update) by ``(wa_app, element_name, language_code)``.
4. Returns a summary dict.

Currently supports: Gupshup.
"""

from __future__ import annotations

import json
import logging
import mimetypes
import os
import re
from typing import Any, Dict, List, Optional, Tuple

import requests as http_requests
from django.core.files.base import ContentFile
from django.db import transaction
from django.utils import timezone
from tenants.models import TenantMedia
from wa.adapters import get_bsp_adapter
from wa.models import (TemplateCategory, TemplateStatus, TemplateType,
                       WATemplate)

logger = logging.getLogger(__name__)


# ═════════════════════════════════════════════════════════════════════════════
# Status / Category / Type mappings
# ═════════════════════════════════════════════════════════════════════════════

_STATUS_MAP = {
    "APPROVED": TemplateStatus.APPROVED,
    "PENDING": TemplateStatus.PENDING,
    "REJECTED": TemplateStatus.REJECTED,
    "FAILED": TemplateStatus.REJECTED,
    "PAUSED": TemplateStatus.PAUSED,
    "DISABLED": TemplateStatus.DISABLED,
    "DELETED": TemplateStatus.DISABLED,
}

_CATEGORY_MAP = {
    "MARKETING": TemplateCategory.MARKETING,
    "UTILITY": TemplateCategory.UTILITY,
    "AUTHENTICATION": TemplateCategory.AUTHENTICATION,
}

_TYPE_MAP = {
    "TEXT": TemplateType.TEXT,
    "IMAGE": TemplateType.IMAGE,
    "VIDEO": TemplateType.VIDEO,
    "DOCUMENT": TemplateType.DOCUMENT,
    "CAROUSEL": TemplateType.CAROUSEL,
    "LOCATION": TemplateType.TEXT,      # fallback
}


# ═════════════════════════════════════════════════════════════════════════════
# Gupshup field parser
# ═════════════════════════════════════════════════════════════════════════════

def _parse_container_meta(raw: str | None) -> Dict[str, Any]:
    """Parse the ``containerMeta`` JSON string from Gupshup."""
    if not raw:
        return {}
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return {}


def _parse_meta(raw: str | None) -> Dict[str, Any]:
    """Parse the ``meta`` JSON string from Gupshup."""
    if not raw:
        return {}
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return {}


def _extract_body_and_footer(container: Dict[str, Any], data_field: str | None) -> Tuple[str, Optional[str]]:
    """
    Extract body text and footer from Gupshup's containerMeta / data field.

    Gupshup ``data`` field format:
        ``"body text\\nfooter | [Button1] | [Button2,phone]"``

    ``containerMeta.data`` contains the clean body (with {{1}}, {{2}}).
    ``containerMeta.footer`` contains the footer if present.
    """
    body = container.get("data", "")
    footer = container.get("footer")

    if not body and data_field:
        # Fallback: split the flat ``data`` field at first ``|`` → body | buttons
        parts = data_field.split("|", 1)
        body = parts[0].strip()
        # Footer is embedded after \n in body
        if "\n" in body:
            lines = body.rsplit("\n", 1)
            body = lines[0].strip()
            footer = footer or lines[1].strip()

    return body, footer


def _extract_buttons(container: Dict[str, Any]) -> Optional[List[Dict[str, Any]]]:
    """
    Extract buttons from containerMeta.

    Gupshup stores buttons as a list of dicts in ``containerMeta.buttons``.
    """
    buttons = container.get("buttons")
    if not buttons:
        return None

    canonical = []
    for btn in buttons:
        btn_type = (btn.get("type") or "QUICK_REPLY").upper()
        entry: Dict[str, Any] = {
            "type": btn_type,
            "text": btn.get("text", ""),
        }
        if btn.get("phone_number"):
            entry["phone_number"] = btn["phone_number"]
        if btn.get("url"):
            entry["url"] = btn["url"]
        if btn.get("example"):
            entry["example"] = btn["example"]
        if btn_type == "OTP":
            entry["type"] = "COPY_CODE"
            entry["otp_type"] = btn.get("otp_type", "COPY_CODE")
        canonical.append(entry)

    return canonical or None


def _extract_cards(container: Dict[str, Any]) -> Optional[List[Dict[str, Any]]]:
    """Extract carousel cards from containerMeta."""
    cards = container.get("cards")
    if not cards:
        return None

    canonical = []
    for card in cards:
        # Detect headerType: prefer explicit value, then detect video from
        # exampleMedia / media_handle base64 ('dmlkZW8v' = 'video/' prefix).
        explicit_type = (card.get("headerType") or "").upper()
        if explicit_type:
            header_type = explicit_type
        else:
            handle = card.get("exampleMedia") or card.get("media_handle") or ""
            header_type = "VIDEO" if "dmlkZW8v" in handle else "IMAGE"

        entry: Dict[str, Any] = {
            "headerType": header_type,
            "body": card.get("body", ""),
        }
        if card.get("buttons"):
            entry["buttons"] = card["buttons"]
        if card.get("exampleMedia"):
            entry["exampleMedia"] = card["exampleMedia"]
        if card.get("media_handle"):
            entry["media_handle"] = card["media_handle"]
        canonical.append(entry)

    return canonical or None


def _extract_example_body(container: Dict[str, Any], meta: Dict[str, Any]) -> Optional[List[str]]:
    """
    Build example body values from sampleText / meta.example.

    Gupshup stores:
    - ``containerMeta.sampleText`` → full rendered example string
    - ``meta.example`` → same rendered example string

    We extract the positional placeholder values by comparing the template
    body (``containerMeta.data``) with the rendered sample.
    """
    sample = container.get("sampleText") or meta.get("example")
    body = container.get("data", "")
    if not sample or not body:
        return None

    # Build a regex from the body template, replacing {{N}} with capture groups
    pattern = re.sub(r"\{\{\d+\}\}", r"(.+?)", re.escape(body).replace(r"\{\{", "{{").replace(r"\}\}", "}}"))
    # Re-escape properly
    pattern = re.sub(r"\{\{(\d+)\}\}", r"(.+?)", re.escape(body))
    match = re.match(pattern, sample)
    if match:
        return list(match.groups())

    return [sample]


# ═════════════════════════════════════════════════════════════════════════════
# Mapper: single Gupshup template dict → WATemplate field dict
# ═════════════════════════════════════════════════════════════════════════════

def _map_gupshup_template(gs_tpl: Dict[str, Any]) -> Dict[str, Any]:
    """
    Convert a single Gupshup template response dict into a flat dict of
    ``WATemplate`` fields ready for ``create()`` or ``update()``.
    """
    container = _parse_container_meta(gs_tpl.get("containerMeta"))
    meta = _parse_meta(gs_tpl.get("meta"))

    body, footer = _extract_body_and_footer(container, gs_tpl.get("data"))
    buttons = _extract_buttons(container)
    cards = _extract_cards(container)
    example_body = _extract_example_body(container, meta)

    gs_status = (gs_tpl.get("status") or "").upper()
    category_raw = (gs_tpl.get("category") or "MARKETING").upper()
    tpl_type_raw = (gs_tpl.get("templateType") or "TEXT").upper()

    # If the template has cards, override type to CAROUSEL
    if cards:
        tpl_type_raw = "CAROUSEL"

    return {
        "element_name": gs_tpl.get("elementName", ""),
        "language_code": gs_tpl.get("languageCode", "en"),
        "name": gs_tpl.get("elementName", ""),
        "status": _STATUS_MAP.get(gs_status, TemplateStatus.PENDING),
        "category": _CATEGORY_MAP.get(category_raw, TemplateCategory.MARKETING),
        "template_type": _TYPE_MAP.get(tpl_type_raw, TemplateType.TEXT),
        "bsp_template_id": gs_tpl.get("id"),
        "meta_template_id": str(gs_tpl["externalId"]) if gs_tpl.get("externalId") else None,
        "content": body,
        "header": container.get("header") if tpl_type_raw == "TEXT" else None,
        "footer": footer,
        "buttons": buttons,
        "cards": cards,
        "example_body": example_body,
        "media_handle": container.get("sampleMedia") if tpl_type_raw in ("IMAGE", "VIDEO", "DOCUMENT") else None,
        "vertical": gs_tpl.get("vertical", "OTHER"),
        "error_message": gs_tpl.get("reason"),
        "rejection_reason": gs_tpl.get("reason") if gs_status in ("REJECTED", "FAILED") else None,
        "needs_sync": False,
        "last_synced_at": timezone.now(),
    }


# ═════════════════════════════════════════════════════════════════════════════
# Mapper: single META Graph API template dict → WATemplate field dict
# ═════════════════════════════════════════════════════════════════════════════

def _map_meta_template(meta_tpl: Dict[str, Any]) -> Dict[str, Any]:
    """
    Convert a single META Graph API template response dict into a flat dict
    of ``WATemplate`` fields ready for ``create()`` or ``update()``.

    META format::

        {
            "id": "123456",
            "name": "my_template",
            "language": "en",
            "status": "APPROVED",
            "category": "MARKETING",
            "components": [
                {"type": "HEADER", "format": "TEXT", "text": "..."},
                {"type": "BODY", "text": "Hello {{1}}"},
                {"type": "FOOTER", "text": "..."},
                {"type": "BUTTONS", "buttons": [...]},
                {"type": "CAROUSEL", "cards": [...]},
            ]
        }
    """
    components = meta_tpl.get("components", [])

    body_text = ""
    header_text = None
    footer_text = None
    buttons = None
    cards = None
    tpl_type = "TEXT"
    example_body = None

    for comp in components:
        comp_type = (comp.get("type") or "").upper()

        if comp_type == "BODY":
            body_text = comp.get("text", "")
            # Extract example body values
            example = comp.get("example", {})
            body_params = example.get("body_text", [])
            if body_params and isinstance(body_params[0], list):
                example_body = body_params[0]
            elif body_params:
                example_body = body_params

        elif comp_type == "HEADER":
            fmt = (comp.get("format") or "TEXT").upper()
            if fmt == "TEXT":
                header_text = comp.get("text", "")
            elif fmt in ("IMAGE", "VIDEO", "DOCUMENT"):
                tpl_type = fmt

        elif comp_type == "FOOTER":
            footer_text = comp.get("text", "")

        elif comp_type == "BUTTONS":
            raw_buttons = comp.get("buttons", [])
            if raw_buttons:
                canonical = []
                for btn in raw_buttons:
                    btn_type = (btn.get("type") or "QUICK_REPLY").upper()
                    entry: Dict[str, Any] = {
                        "type": btn_type,
                        "text": btn.get("text", ""),
                    }
                    if btn.get("phone_number"):
                        entry["phone_number"] = btn["phone_number"]
                    if btn.get("url"):
                        entry["url"] = btn["url"]
                    if btn.get("example"):
                        entry["example"] = btn["example"]
                    if btn_type == "OTP":
                        entry["type"] = "COPY_CODE"
                        entry["otp_type"] = btn.get("otp_type", "COPY_CODE")
                    canonical.append(entry)
                buttons = canonical or None

        elif comp_type == "CAROUSEL":
            tpl_type = "CAROUSEL"
            raw_cards = comp.get("cards", [])
            if raw_cards:
                canonical_cards = []
                for card in raw_cards:
                    card_body = ""
                    card_header_type = "IMAGE"
                    card_buttons = None
                    for card_comp in card.get("components", []):
                        cc_type = (card_comp.get("type") or "").upper()
                        if cc_type == "HEADER":
                            card_header_type = (card_comp.get("format") or "IMAGE").upper()
                        elif cc_type == "BODY":
                            card_body = card_comp.get("text", "")
                        elif cc_type == "BUTTONS":
                            card_buttons = card_comp.get("buttons", [])
                    entry = {
                        "headerType": card_header_type,
                        "body": card_body,
                    }
                    if card_buttons:
                        entry["buttons"] = card_buttons
                    canonical_cards.append(entry)
                cards = canonical_cards or None

    meta_status = (meta_tpl.get("status") or "").upper()
    category_raw = (meta_tpl.get("category") or "MARKETING").upper()

    return {
        "element_name": meta_tpl.get("name", ""),
        "language_code": meta_tpl.get("language", "en"),
        "name": meta_tpl.get("name", ""),
        "status": _STATUS_MAP.get(meta_status, TemplateStatus.PENDING),
        "category": _CATEGORY_MAP.get(category_raw, TemplateCategory.MARKETING),
        "template_type": _TYPE_MAP.get(tpl_type, TemplateType.TEXT),
        "bsp_template_id": None,
        "meta_template_id": str(meta_tpl["id"]) if meta_tpl.get("id") else None,
        "content": body_text,
        "header": header_text,
        "footer": footer_text,
        "buttons": buttons,
        "cards": cards,
        "example_body": example_body,
        "media_handle": None,
        "vertical": "OTHER",
        "error_message": None,
        "rejection_reason": meta_tpl.get("rejected_reason") if meta_status in ("REJECTED", "FAILED") else None,
        "needs_sync": False,
        "last_synced_at": timezone.now(),
    }


# ═════════════════════════════════════════════════════════════════════════════
# META Graph API — batch-fetch header media URLs
# ═════════════════════════════════════════════════════════════════════════════

def _fetch_meta_media_urls(wa_app) -> Dict[Tuple[str, str], str]:
    """
    Fetch header media CDN URLs for all templates from META's Graph API.

    For IMAGE / VIDEO / DOCUMENT templates, META stores the actual media
    on ``scontent.whatsapp.net`` and returns the URL via the
    ``components[].example.header_handle`` field.

    For CAROUSEL templates, META stores per-card media inside nested
    ``CAROUSEL → cards[] → components[] → type:HEADER`` structures.

    This function batch-fetches all templates from the WABA endpoint
    (paginated, up to 100 per page) and returns **two** lookup dicts:

    Returns:
        tuple: ``(media_urls, card_media_urls)``
        - ``media_urls``: ``{(element_name, language_code): media_url}``
          for standard IMAGE/VIDEO/DOCUMENT templates.
        - ``card_media_urls``: ``{(element_name, language_code): [url_card_0, url_card_1, ...]}``
          for CAROUSEL templates (one URL per card, positional).
    """
    from wa.services.meta_template_service import get_meta_access_token

    token = get_meta_access_token(wa_app)
    if not token:
        logger.info("[TemplateSync] No META access token — skipping media URL fetch")
        return {}, {}

    # Get WABA ID — try WABAInfo first, then wa_app, then bsp_credentials
    waba_id = None
    try:
        from tenants.models import WABAInfo
        waba_info = WABAInfo.objects.filter(wa_app=wa_app).first()
        if waba_info:
            waba_id = waba_info.waba_id
    except Exception:
        pass

    if not waba_id:
        # Try from wa_app.waba_id directly
        waba_id = getattr(wa_app, "waba_id", None)

    if not waba_id:
        # Try from bsp_credentials
        creds = wa_app.bsp_credentials or {}
        waba_id = creds.get("waba_id")

    if not waba_id:
        logger.info("[TemplateSync] No WABA ID — skipping media URL fetch")
        return {}, {}

    media_urls: Dict[Tuple[str, str], str] = {}
    card_media_urls: Dict[Tuple[str, str], List[str]] = {}
    url: Optional[str] = (
        f"https://graph.facebook.com/v24.0/{waba_id}/message_templates"
        f"?fields=name,language,components&limit=100"
    )

    page = 0
    while url:
        page += 1
        try:
            resp = http_requests.get(url, headers={"Authorization": f"Bearer {token}"}, timeout=30)
            if resp.status_code != 200:
                logger.warning(
                    f"[TemplateSync] META API returned {resp.status_code} on page {page}"
                )
                break
            data = resp.json()
        except Exception as exc:
            logger.warning(f"[TemplateSync] META API request failed: {exc}")
            break

        for tpl in data.get("data", []):
            name = tpl.get("name")
            lang = tpl.get("language")
            if not name or not lang:
                continue
            for comp in tpl.get("components", []):
                # ── Standard header media (IMAGE / VIDEO / DOCUMENT) ──
                if (
                    comp.get("type") == "HEADER"
                    and comp.get("format") in ("IMAGE", "VIDEO", "DOCUMENT")
                ):
                    example = comp.get("example", {})
                    handles = example.get("header_handle", [])
                    urls = example.get("header_url", [])
                    cdn_url = (
                        handles[0] if handles
                        else (urls[0] if urls else None)
                    )
                    if cdn_url:
                        media_urls[(name, lang)] = cdn_url

                # ── CAROUSEL card media ──────────────────────────────
                # META returns: components[] → type:CAROUSEL → cards[]
                # Each card has: components[] → type:HEADER → example.header_handle
                if comp.get("type") == "CAROUSEL":
                    card_urls = []
                    for card in comp.get("cards", []):
                        card_url = None
                        for card_comp in card.get("components", []):
                            if card_comp.get("type") == "HEADER":
                                example = card_comp.get("example", {})
                                handles = example.get("header_handle", [])
                                urls_list = example.get("header_url", [])
                                card_url = (
                                    handles[0] if handles
                                    else (urls_list[0] if urls_list else None)
                                )
                                break
                        card_urls.append(card_url)  # None if no media found for this card
                    if any(card_urls):
                        card_media_urls[(name, lang)] = card_urls

        # Follow pagination
        url = data.get("paging", {}).get("next")

    logger.info(
        f"[TemplateSync] Fetched {len(media_urls)} media URLs + "
        f"{len(card_media_urls)} carousel card media from META "
        f"(pages={page})"
    )
    return media_urls, card_media_urls


# ═════════════════════════════════════════════════════════════════════════════
# Phase 3 helper – download media → TenantMedia → Gupshup upload
# ═════════════════════════════════════════════════════════════════════════════

def _upload_media_to_gupshup(wa_app, tenant_media, content_type: str):
    """
    Upload a TenantMedia file to Gupshup and return the handle response dict
    (e.g. ``{"handleId": {"message": "4::..."}}``), or ``None`` on failure.
    """
    from tenants.models import BSPChoices

    bsp = getattr(wa_app, "bsp", None)
    if bsp and bsp != BSPChoices.GUPSHUP:
        logger.info(f"[TemplateSync] BSP is {bsp}, skipping Gupshup upload")
        return None

    app_id = wa_app.app_id
    app_secret = wa_app.app_secret
    if not app_id or not app_secret:
        logger.warning(
            "[TemplateSync] Missing Gupshup app_id/app_secret, skipping upload"
        )
        return None

    from wa.utility.apis.gupshup.template_api import \
        TemplateAPI as GupshupTemplateAPI

    api = GupshupTemplateAPI(appId=app_id, token=app_secret)

    tenant_media.media.open("rb")
    try:
        result = api.upload_media_from_file_object(
            file_obj=tenant_media.media,
            filename=os.path.basename(tenant_media.media.name),
            file_type=content_type,
        )
        return result  # typically {"handleId": "..."}
    except Exception as exc:
        logger.warning(f"[TemplateSync] Gupshup upload error: {exc}")
        return None
    finally:
        tenant_media.media.close()


def _patch_template_media(template: WATemplate):
    """
    For a single WATemplate with ``example_media_url`` but no ``tenant_media``:

    1. Download media from ``example_media_url``
    2. Create a ``TenantMedia`` record and save the file locally
    3. Upload the file to Gupshup to get ``wa_handle_id``
    4. Link the ``TenantMedia`` back to the template
    """
    wa_app = template.wa_app
    if not wa_app:
        raise ValueError("Template has no wa_app")

    tenant = wa_app.tenant
    if not tenant:
        raise ValueError("WA App has no tenant")

    # 1. Download
    resp = http_requests.get(template.example_media_url, timeout=30)
    resp.raise_for_status()

    content_type = resp.headers.get("Content-Type", "image/png")
    ext = mimetypes.guess_extension(content_type.split(";")[0].strip()) or ".png"
    filename = f"template_media_{template.element_name}{ext}"

    logger.debug(
        f"[TemplateSync] Downloaded {len(resp.content)} bytes for "
        f"'{template.element_name}' ({content_type})"
    )

    # 2. Create TenantMedia
    with transaction.atomic():
        tm = TenantMedia.objects.create(
            tenant=tenant,
            platform="whatsapp",
        )
        tm.media.save(filename, ContentFile(resp.content), save=True)

    # 3. Upload to Gupshup
    handle_id = _upload_media_to_gupshup(wa_app, tm, content_type)
    if handle_id:
        tm.wa_handle_id = handle_id
        tm.save(update_fields=["wa_handle_id"])

    # 4. Link to template
    template.tenant_media = tm
    template.save(update_fields=["tenant_media"])

    logger.info(
        f"[TemplateSync] Patched media for '{template.element_name}' → "
        f"TenantMedia id={tm.id}"
    )


# ═════════════════════════════════════════════════════════════════════════════
# Main entry point
# ═════════════════════════════════════════════════════════════════════════════

def sync_templates_from_bsp(wa_app, dry_run: bool = False) -> Dict[str, Any]:
    """
    Fetch all templates from the BSP and upsert into ``WATemplate``.

    Matching key: ``(wa_app, element_name, language_code)``
    (matches the ``unique_together`` constraint on the model).

    Args:
        wa_app: The TenantWAApp instance.
        dry_run: If True, fetch and map templates but don't write to DB.
                 Returns a ``preview`` list showing what would happen.

    Returns::

        {
            "created": 5,
            "updated": 3,
            "skipped": 12,
            "failed": 0,
            "errors": [],
            "total_from_bsp": 20,
        }
    """
    from tenants.models import BSPChoices

    adapter = get_bsp_adapter(wa_app)
    result = adapter.list_templates()

    if not result.success:
        return {
            "created": 0,
            "updated": 0,
            "skipped": 0,
            "failed": 1,
            "errors": [result.error_message or "Failed to fetch templates from BSP"],
            "total_from_bsp": 0,
        }

    bsp_templates = result.data.get("templates", [])

    # Detect BSP type to choose the right mapper
    bsp_type = getattr(wa_app, "bsp", None)
    is_meta_direct = bsp_type == BSPChoices.META

    created = 0
    updated = 0
    skipped = 0
    failed = 0
    errors: List[str] = []
    preview: List[Dict[str, Any]] = []  # only populated in dry_run mode

    for raw_tpl in bsp_templates:
        # Extract element_name / language_code based on BSP format
        if is_meta_direct:
            element_name = raw_tpl.get("name")
            language_code = raw_tpl.get("language", "en")
        else:
            element_name = raw_tpl.get("elementName")
            language_code = raw_tpl.get("languageCode", "en")

        if not element_name:
            failed += 1
            errors.append(f"Template missing name: id={raw_tpl.get('id')}")
            continue

        try:
            mapped = _map_meta_template(raw_tpl) if is_meta_direct else _map_gupshup_template(raw_tpl)

            existing = WATemplate.objects.filter(
                wa_app=wa_app,
                element_name=element_name,
                language_code=language_code,
            ).first()

            if existing:
                # Check if anything meaningful changed
                changed_fields = []
                for field_name in (
                    "status", "category", "template_type", "content",
                    "header", "footer", "buttons", "cards",
                    "bsp_template_id", "meta_template_id",
                    "media_handle",
                    "error_message", "rejection_reason",
                ):
                    new_val = mapped.get(field_name)
                    old_val = getattr(existing, field_name, None)
                    if new_val != old_val:
                        changed_fields.append(field_name)

                if not changed_fields:
                    if dry_run:
                        preview.append({
                            "element_name": element_name,
                            "language_code": language_code,
                            "action": "skip",
                            "reason": "no changes",
                            "status": mapped.get("status"),
                        })
                    else:
                        # Touch last_synced_at even if nothing changed
                        existing.last_synced_at = timezone.now()
                        existing.needs_sync = False
                        existing.save(update_fields=["last_synced_at", "needs_sync"])
                    skipped += 1
                    continue

                if dry_run:
                    preview.append({
                        "element_name": element_name,
                        "language_code": language_code,
                        "action": "update",
                        "changed_fields": changed_fields,
                        "status": mapped.get("status"),
                        "category": mapped.get("category"),
                        "template_type": mapped.get("template_type"),
                    })
                    updated += 1
                    continue

                # Apply changes
                for field_name in changed_fields:
                    setattr(existing, field_name, mapped[field_name])
                existing.last_synced_at = timezone.now()
                existing.needs_sync = False
                # Also update name to match element_name if empty
                if not existing.name:
                    existing.name = mapped["name"]
                    changed_fields.append("name")
                existing.save(
                    update_fields=changed_fields + ["last_synced_at", "needs_sync"]
                )
                updated += 1
                logger.info(
                    f"[TemplateSync] Updated '{element_name}' — "
                    f"fields: {changed_fields}"
                )
            else:
                if dry_run:
                    preview.append({
                        "element_name": element_name,
                        "language_code": language_code,
                        "action": "create",
                        "status": mapped.get("status"),
                        "category": mapped.get("category"),
                        "template_type": mapped.get("template_type"),
                        "content_preview": (mapped.get("content") or "")[:100],
                    })
                    created += 1
                    continue

                # Create new template
                WATemplate.objects.create(wa_app=wa_app, **mapped)
                created += 1
                logger.info(
                    f"[TemplateSync] Created '{element_name}' — "
                    f"status={mapped['status']}, category={mapped['category']}"
                )

        except Exception as exc:
            failed += 1
            errors.append(f"{element_name}: {exc}")
            logger.error(
                f"[TemplateSync] FAILED to sync '{element_name}': {exc}",
                exc_info=True,
            )

    # ── Dry-run: skip phases 2 & 3, return preview ──────────────────
    if dry_run:
        return {
            "dry_run": True,
            "created": created,
            "updated": updated,
            "skipped": skipped,
            "failed": failed,
            "errors": errors,
            "total_from_bsp": len(bsp_templates),
            "preview": preview,
        }

    # ── Phase 2: Fetch & apply header media URLs from META Graph API ──
    media_updated = 0
    carousel_media_updated = 0
    try:
        meta_media, card_media = _fetch_meta_media_urls(wa_app)

        # Phase 2a: Standard IMAGE/VIDEO/DOCUMENT templates
        if meta_media:
            media_templates = WATemplate.objects.filter(
                wa_app=wa_app,
                template_type__in=[
                    TemplateType.IMAGE,
                    TemplateType.VIDEO,
                    TemplateType.DOCUMENT,
                ],
            )
            for tpl in media_templates:
                cdn_url = meta_media.get((tpl.element_name, tpl.language_code))
                if cdn_url and cdn_url != tpl.example_media_url:
                    tpl.example_media_url = cdn_url
                    tpl.save(update_fields=["example_media_url"])
                    media_updated += 1

        # Phase 2b: CAROUSEL templates — write card-level example_media_url
        if card_media:
            carousel_templates = WATemplate.objects.filter(
                wa_app=wa_app,
                template_type=TemplateType.CAROUSEL,
            )
            for tpl in carousel_templates:
                card_urls = card_media.get((tpl.element_name, tpl.language_code))
                if not card_urls or not tpl.cards:
                    continue
                cards = list(tpl.cards)  # copy
                changed = False
                for idx, card in enumerate(cards):
                    if idx < len(card_urls) and card_urls[idx]:
                        existing = card.get("example_media_url")
                        if existing != card_urls[idx]:
                            card["example_media_url"] = card_urls[idx]
                            changed = True
                if changed:
                    tpl.cards = cards
                    tpl.save(update_fields=["cards"])
                    carousel_media_updated += 1
                    logger.info(
                        f"[TemplateSync] Carousel '{tpl.element_name}': "
                        f"updated {sum(1 for u in card_urls if u)} card media URLs"
                    )

        logger.info(
            f"[TemplateSync] Updated {media_updated} standard + "
            f"{carousel_media_updated} carousel templates with media URLs from META"
        )
    except Exception as exc:
        logger.warning(
            f"[TemplateSync] Failed to fetch/apply media URLs: {exc}",
            exc_info=True,
        )

    # ── Phase 3: Auto-create TenantMedia for media templates ─────────
    # For any IMAGE/VIDEO/DOCUMENT template that now has example_media_url
    # but no tenant_media, download the file, create a TenantMedia, upload
    # to Gupshup for wa_handle_id, and link back to the template.
    media_patched = 0
    media_patch_failed = 0
    try:
        orphan_media_templates = WATemplate.objects.filter(
            wa_app=wa_app,
            template_type__in=[
                TemplateType.IMAGE,
                TemplateType.VIDEO,
                TemplateType.DOCUMENT,
            ],
            tenant_media__isnull=True,
        ).exclude(
            example_media_url__isnull=True,
        ).exclude(
            example_media_url="",
        ).select_related("wa_app", "wa_app__tenant")

        for tpl in orphan_media_templates:
            try:
                _patch_template_media(tpl)
                media_patched += 1
            except Exception as exc:
                media_patch_failed += 1
                logger.warning(
                    f"[TemplateSync] Failed to patch media for "
                    f"'{tpl.element_name}': {exc}"
                )

        if media_patched or media_patch_failed:
            logger.info(
                f"[TemplateSync] Media patch — "
                f"patched={media_patched}, failed={media_patch_failed}"
            )
    except Exception as exc:
        logger.warning(
            f"[TemplateSync] Failed media patch phase: {exc}",
            exc_info=True,
        )

    logger.info(
        f"[TemplateSync] Done — "
        f"created={created}, updated={updated}, skipped={skipped}, "
        f"failed={failed}, media_urls={media_updated}, "
        f"carousel_media={carousel_media_updated}, "
        f"media_patched={media_patched}, media_patch_failed={media_patch_failed}, "
        f"total={len(bsp_templates)}"
    )

    return {
        "created": created,
        "updated": updated,
        "skipped": skipped,
        "failed": failed,
        "errors": errors,
        "total_from_bsp": len(bsp_templates),
        "media_urls_updated": media_updated,
        "carousel_media_updated": carousel_media_updated,
        "media_patched": media_patched,
        "media_patch_failed": media_patch_failed,
    }

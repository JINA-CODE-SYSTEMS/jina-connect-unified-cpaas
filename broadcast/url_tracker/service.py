"""
URL Tracking Service.

Provides the core logic to replace original URLs in WhatsApp template button
payloads with tracked redirect URLs.

─── How WhatsApp Template URL Buttons Work ───

WhatsApp templates are pre-approved with exact URLs. There are two kinds:

1. **Static URL** — e.g. ``https://shop.com/sale``
   The URL is baked in and CANNOT be changed at send time.
   We create a TrackedURL record for analytics, but cannot redirect-track.

2. **Dynamic URL** — e.g. ``https://shop.com/order?id={{1}}``
   The base URL is fixed but a suffix parameter is provided at send time.
   WhatsApp renders ``base_url + suffix``.

─── Tracking Strategy ───

For redirect-based click tracking to work, the template must be created with
our tracking domain as the base URL:

    Template URL:  https://localhost:8000/r/{{1}}

At send time we:
    1. Create a TrackedURL record mapping short_code → original destination
    2. Pass the short_code as the ``{{1}}`` parameter
    3. WhatsApp renders: https://localhost:8000/r/AbC12x
    4. User clicks → our server records click → 302 → original URL

For templates with third-party static URLs, we create TrackedURL records
purely for book-keeping (no redirect interception).

Usage (called from BroadcastMessage._build_button_components):

    from broadcast.url_tracker.service import create_tracked_urls_for_message
    url_map = create_tracked_urls_for_message(broadcast_message)
    # url_map = {0: "AbC12x", ...}  ← short codes to inject as {{1}} params
"""

import logging
import re

from django.conf import settings

from broadcast.url_tracker.models import TrackedURL

logger = logging.getLogger('broadcast.url_tracker')

# The base URL pattern we use in our tracking-enabled templates.
# Templates should be created with URL: {TRACKING_BASE_URL}/r/{{1}}
# so the dynamic suffix is our short code.
_TRACKING_BASE_URL = None  # Lazy-loaded from settings


def _get_tracking_base_url() -> str:
    """Return the base URL used for tracked redirect links."""
    global _TRACKING_BASE_URL
    if _TRACKING_BASE_URL is None:
        _TRACKING_BASE_URL = getattr(settings, 'BASE_URL', 'http://localhost:8000').rstrip('/')
    return _TRACKING_BASE_URL


def is_tracking_url(template_url: str) -> bool:
    """
    Check whether a template URL's base domain matches our tracking server.
    If yes, we can inject a short code as the dynamic suffix.

    Examples:
        Template URL: "https://localhost:8000/r/{{1}}"  → True
        Template URL: "https://shop.com/order?id={{1}}"               → False
    """
    base = _get_tracking_base_url()
    # Strip scheme for comparison
    def _domain(url):
        return re.sub(r'^https?://', '', url).split('/')[0].lower()
    return _domain(template_url).startswith(_domain(base))


def create_tracked_urls_for_message(broadcast_message) -> dict:
    """
    Create TrackedURL records for every URL button in the template.

    For buttons whose template URL base matches our tracking domain,
    returns a mapping of {button_index: short_code} so the caller can
    inject the short code as the dynamic URL parameter.

    For buttons with third-party URLs, creates a record for analytics
    but returns no mapping (the original URL is sent unchanged).

    Args:
        broadcast_message: BroadcastMessage instance (must have .broadcast,
                           .contact, .broadcast.template_number set)

    Returns:
        dict: {int(button_index): str(short_code)}
              Only includes buttons where we can inject a tracked redirect.
    """
    try:
        broadcast = broadcast_message.broadcast
        contact = broadcast_message.contact
        template_number = broadcast.template_number

        if not template_number:
            return {}

        from wa.models import WATemplate
        template: WATemplate = template_number.gupshup_template
        buttons = template.buttons or []

        if not buttons:
            return {}

        tenant = broadcast.tenant
        placeholder_data = broadcast.placeholder_data or {}
        reserved_vars = broadcast_message._get_contact_reserved_vars()
        all_data = {**reserved_vars, **placeholder_data}

        # Get the original destination URLs from placeholder_data
        # The user provides the actual destination URL in placeholder_data
        # (e.g. placeholder_data = {"link": "https://shop.com/sale"})
        tracked_map = {}  # {button_index: short_code}

        for idx, button in enumerate(buttons):
            if button.get('type') != 'URL':
                continue

            template_url = button.get('url', '')
            if not template_url:
                continue

            has_placeholder = bool(re.search(r'\{\{\d+\}\}', template_url))

            if has_placeholder and is_tracking_url(template_url):
                # ── Tracking-enabled template URL ─────────────────────────
                # The template URL is: https://our-server/r/{{1}}
                # We need the actual destination URL from placeholder_data.
                #
                # Find what placeholder name maps to this button's {{1}}.
                original_url = _resolve_original_url_for_button(
                    template, idx, all_data
                )
                if not original_url:
                    logger.warning(
                        f"No destination URL found for tracking button[{idx}] "
                        f"in message={broadcast_message.id}"
                    )
                    continue

                # Create tracked URL → get short code
                tracked_url_obj = TrackedURL.objects.create(
                    original_url=original_url,
                    tenant=tenant,
                    broadcast=broadcast,
                    broadcast_message=broadcast_message,
                    contact=contact,
                    button_index=idx,
                    button_text=button.get('text', ''),
                )
                # Return the short code — caller injects it as the {{1}} param
                tracked_map[idx] = tracked_url_obj.code

                logger.info(
                    f"Tracked URL [{tracked_url_obj.code}] created for "
                    f"message={broadcast_message.id}, button[{idx}], "
                    f"dest={original_url[:80]}"
                )

            else:
                # ── Third-party URL (static or dynamic) ───────────────────
                # Can't redirect-track, but create a record for analytics.
                if has_placeholder:
                    original_url = _resolve_full_url_for_button(
                        template_url, template, idx, all_data
                    )
                else:
                    original_url = template_url

                TrackedURL.objects.create(
                    original_url=original_url or template_url,
                    tenant=tenant,
                    broadcast=broadcast,
                    broadcast_message=broadcast_message,
                    contact=contact,
                    button_index=idx,
                    button_text=button.get('text', ''),
                )
                logger.debug(
                    f"Analytics-only tracked URL for message={broadcast_message.id}, "
                    f"button[{idx}], url={original_url[:80] if original_url else template_url[:80]}"
                )

        return tracked_map

    except Exception as e:
        logger.exception(f"Error creating tracked URLs for message {broadcast_message.id}: {e}")
        # Fail silently — message should still send with original URLs
        return {}


def create_tracked_url_for_resolved_button(
    broadcast_message,
    button_index: int,
    resolved_url: str,
    button_text: str = '',
) -> str:
    """
    Create a tracked URL for a dynamically-resolved URL button.

    For templates with URL buttons containing placeholders (e.g.
    https://shop.com/order?id={{1}}), the final URL is only known after
    placeholder resolution. This function creates the tracking record
    for that resolved URL.

    Args:
        broadcast_message: BroadcastMessage instance
        button_index: 0-based index of the button
        resolved_url: The fully resolved URL after placeholder substitution
        button_text: Button display text (for analytics)

    Returns:
        str: Tracked redirect URL, or the original resolved_url on error.
    """
    try:
        broadcast = broadcast_message.broadcast
        contact = broadcast_message.contact
        tenant = broadcast.tenant

        tracked_url_obj = TrackedURL.objects.create(
            original_url=resolved_url,
            tenant=tenant,
            broadcast=broadcast,
            broadcast_message=broadcast_message,
            contact=contact,
            button_index=button_index,
            button_text=button_text,
        )

        logger.info(
            f"Created tracked URL [{tracked_url_obj.code}] for resolved "
            f"button[{button_index}] of message={broadcast_message.id}, "
            f"url={resolved_url[:80]}"
        )
        return tracked_url_obj.tracked_url

    except Exception as e:
        logger.exception(
            f"Error creating tracked URL for resolved button "
            f"(message={broadcast_message.id}, idx={button_index}): {e}"
        )
        return resolved_url  # Fallback: use original URL


# ── Private Helpers ───────────────────────────────────────────────────────


def _resolve_original_url_for_button(template, button_index: int, all_data: dict) -> str:
    """
    Resolve the original destination URL for a tracking-enabled button.

    When the template URL is ``https://our-server/r/{{1}}``, the user's
    ``placeholder_data`` contains the actual destination URL that they want
    the contact to land on. This function finds that value.

    It checks the template's ``placeholder_mapping['buttons']`` to find which
    placeholder name maps to this button's parameter, then looks it up in
    ``all_data``.
    """
    placeholder_mapping = template.placeholder_mapping or {}
    button_mappings = placeholder_mapping.get('buttons', [])

    for bm in button_mappings:
        if bm.get('button_index') == button_index:
            url_mapping = bm.get('url_mapping', {})
            # Usually there's a single {{1}} parameter
            for number in sorted(url_mapping.keys(), key=int):
                placeholder_name = url_mapping[number]
                value = all_data.get(placeholder_name, '')
                if value:
                    return str(value)
    return ''


def _resolve_full_url_for_button(template_url: str, template, button_index: int, all_data: dict) -> str:
    """
    Build the fully resolved URL for a dynamic button with third-party base URL.

    E.g. template_url = "https://shop.com/order?id={{1}}"
    After substituting {{1}} with the value from placeholder_data → 
    "https://shop.com/order?id=12345"
    """
    placeholder_mapping = template.placeholder_mapping or {}
    button_mappings = placeholder_mapping.get('buttons', [])

    resolved = template_url
    for bm in button_mappings:
        if bm.get('button_index') == button_index:
            url_mapping = bm.get('url_mapping', {})
            for number in sorted(url_mapping.keys(), key=int):
                placeholder_name = url_mapping[number]
                value = all_data.get(placeholder_name, '')
                resolved = resolved.replace(f'{{{{{number}}}}}', str(value))
            break
    return resolved


# ── Analytics ─────────────────────────────────────────────────────────────


def get_click_analytics_for_broadcast(broadcast_id: int) -> dict:
    """
    Get aggregated click analytics for all tracked URLs in a broadcast.

    Returns:
        dict: {
            'total_tracked_urls': int,
            'total_clicks': int,
            'unique_contacts_clicked': int,
            'buttons': [
                {
                    'button_index': int,
                    'button_text': str,
                    'original_url': str,
                    'total_clicks': int,
                    'unique_clickers': int,
                    'first_click': datetime | None,
                    'last_click': datetime | None,
                }
            ]
        }
    """
    from django.db.models import Count, Max, Min, Q, Sum

    tracked_urls = TrackedURL.objects.filter(broadcast_id=broadcast_id)

    if not tracked_urls.exists():
        return {
            'total_tracked_urls': 0,
            'total_clicks': 0,
            'unique_contacts_clicked': 0,
            'buttons': [],
        }

    # Aggregate by button_index
    button_stats = (
        tracked_urls
        .values('button_index', 'button_text')
        .annotate(
            total_clicks=Sum('click_count'),
            unique_clickers=Count('contact', distinct=True, filter=Q(click_count__gt=0)),
            first_click=Min('first_clicked_at'),
            last_click=Max('last_clicked_at'),
        )
        .order_by('button_index')
    )

    # Get a sample original_url for each button_index
    sample_urls = {}
    for stat in button_stats:
        btn_idx = stat['button_index']
        sample = tracked_urls.filter(button_index=btn_idx).values_list('original_url', flat=True).first()
        sample_urls[btn_idx] = sample or ''

    total_clicks = tracked_urls.aggregate(t=Sum('click_count'))['t'] or 0
    unique_contacts = tracked_urls.filter(click_count__gt=0).values('contact').distinct().count()

    return {
        'total_tracked_urls': tracked_urls.count(),
        'total_clicks': total_clicks,
        'unique_contacts_clicked': unique_contacts,
        'buttons': [
            {
                'button_index': s['button_index'],
                'button_text': s['button_text'],
                'original_url': sample_urls.get(s['button_index'], ''),
                'total_clicks': s['total_clicks'] or 0,
                'unique_clickers': s['unique_clickers'],
                'first_click': s['first_click'],
                'last_click': s['last_click'],
            }
            for s in button_stats
        ],
    }


def get_click_analytics_for_message(broadcast_message_id: int) -> list:
    """
    Get click analytics for a specific broadcast message (single recipient).

    Returns:
        list: [
            {
                'button_index': int,
                'button_text': str,
                'original_url': str,
                'tracked_url': str,
                'click_count': int,
                'first_clicked_at': datetime | None,
                'last_clicked_at': datetime | None,
            }
        ]
    """
    tracked_urls = TrackedURL.objects.filter(
        broadcast_message_id=broadcast_message_id
    ).order_by('button_index')

    return [
        {
            'button_index': t.button_index,
            'button_text': t.button_text,
            'original_url': t.original_url,
            'tracked_url': t.tracked_url,
            'click_count': t.click_count,
            'first_clicked_at': t.first_clicked_at,
            'last_clicked_at': t.last_clicked_at,
        }
        for t in tracked_urls
    ]

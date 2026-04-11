"""
Deep Linking Views for Email Verification and Password Reset.

These views handle:
1. Apple App Site Association (iOS Universal Links)
2. Android Asset Links (Android App Links)
3. Deep link fallback pages (when app is not installed)
4. Smart redirection based on origin (mobile app vs web browser)

Flow:
- Email contains link to backend: http://localhost:8000/verify-email/{token}/
- If mobile app is installed: App intercepts via Universal/App Links (never reaches backend)
- If mobile browser or web: Backend redirects to web frontend (jinalocalhost:8000)
"""

import logging

from django.conf import settings
from django.http import JsonResponse
from django.shortcuts import redirect, render
from django.views.decorators.cache import cache_control
from django.views.decorators.http import require_GET

from users.models import EmailVerificationToken, PasswordResetToken

logger = logging.getLogger(__name__)


# =============================================================================
# ORIGIN DETECTION UTILITIES
# =============================================================================


def is_mobile_app_request(request):
    """
    Detect if request is from our mobile app.

    Mobile app requests will have:
    - Custom User-Agent containing 'JinaConnect' or our app identifier
    - Custom header 'X-JinaConnect-App: true'

    Returns:
        bool: True if request is from mobile app, False otherwise
    """
    user_agent = request.META.get("HTTP_USER_AGENT", "").lower()
    app_header = request.META.get("HTTP_X_JINACONNECT_APP", "")

    # Check for our app's custom identifier in User-Agent
    is_our_app = "jinaconnect" in user_agent or "jina-connect" in user_agent

    # Check for custom header
    has_app_header = app_header.lower() == "true"

    return is_our_app or has_app_header


def should_redirect_to_web(request):
    """
    Determine if request should be redirected to web frontend.

    Redirect to web frontend if:
    - Request is from a regular web browser (desktop or mobile browser)
    - Request is NOT from our mobile app

    Don't redirect if:
    - Request is from our mobile app
    - Request has specific query param to stay on backend (e.g., ?api=true)

    Returns:
        bool: True if should redirect to web frontend
    """
    # Check if explicitly requesting API response
    if request.GET.get("api") == "true":
        return False

    # Check if from our mobile app
    if is_mobile_app_request(request):
        return False

    # All other requests (web browsers, mobile browsers) redirect to frontend
    return True


def get_web_redirect_url(action, token):
    """
    Build the web frontend redirect URL.

    Args:
        action: 'verify-email' or 'reset-password'
        token: The token string

    Returns:
        str: Full URL to web frontend
    """
    frontend_url = getattr(settings, "FRONTEND_URL", "http://localhost:3000")
    # Remove trailing slash if present
    frontend_url = frontend_url.rstrip("/")

    return f"{frontend_url}/{action}?token={token}"


# =============================================================================
# DEEP LINK CONFIGURATION FILES
# =============================================================================


@require_GET
@cache_control(max_age=86400)  # Cache for 24 hours
def apple_app_site_association(request):
    """
    Serves the apple-app-site-association file for iOS Universal Links.
    Must be served at: /.well-known/apple-app-site-association

    Documentation: https://developer.apple.com/documentation/xcode/supporting-associated-domains
    """
    # Get configuration from settings
    apple_team_id = getattr(settings, "APPLE_TEAM_ID", "XXXXXXXXXX")
    ios_bundle_id = getattr(settings, "IOS_BUNDLE_ID", "com.jinacode.jinaconnect")

    data = {
        "applinks": {
            "apps": [],  # Must be empty array per Apple documentation
            "details": [
                {
                    "appID": f"{apple_team_id}.{ios_bundle_id}",
                    "paths": ["/verify-email/*", "/reset-password/*", "/deep/*"],
                }
            ],
        },
        "webcredentials": {"apps": [f"{apple_team_id}.{ios_bundle_id}"]},
    }

    response = JsonResponse(data)
    response["Content-Type"] = "application/json"
    # Must not have redirects - return 200 directly
    return response


@require_GET
@cache_control(max_age=86400)  # Cache for 24 hours
def android_asset_links(request):
    """
    Serves the assetlinks.json file for Android App Links.
    Must be served at: /.well-known/assetlinks.json

    Documentation: https://developer.android.com/training/app-links/verify-android-applinks
    """
    # Get configuration from settings
    android_package_name = getattr(settings, "ANDROID_PACKAGE_NAME", "com.jinacode.jinaconnect")
    android_sha256_fingerprints = getattr(settings, "ANDROID_SHA256_FINGERPRINTS", [])

    # If no fingerprints configured, provide a placeholder
    if not android_sha256_fingerprints:
        android_sha256_fingerprints = [
            "00:00:00:00:00:00:00:00:00:00:00:00:00:00:00:00:00:00:00:00:00:00:00:00:00:00:00:00:00:00:00:00"
        ]
        logger.warning("ANDROID_SHA256_FINGERPRINTS not configured in settings. Using placeholder.")

    data = [
        {
            "relation": ["delegate_permission/common.handle_all_urls"],
            "target": {
                "namespace": "android_app",
                "package_name": android_package_name,
                "sha256_cert_fingerprints": android_sha256_fingerprints,
            },
        }
    ]

    response = JsonResponse(data, safe=False)
    response["Content-Type"] = "application/json"
    return response


# =============================================================================
# DEEP LINK HANDLER VIEWS
# =============================================================================


def verify_email_deep_link(request, token):
    """
    Handler for email verification deep links.

    Flow:
    1. If mobile app installed: App intercepts via Universal/App Links (never reaches here)
    2. If web browser or mobile browser: Redirect to web frontend
    3. If explicitly requesting API (?api=true): Show fallback page

    URL Pattern: /verify-email/<token>/

    Redirection Logic:
    - Web browser → http://localhost:3000/verify-email?token={token}
    - Mobile browser (no app) → http://localhost:3000/verify-email?token={token}
    - Mobile app → Intercepted before reaching backend
    """
    # Check if should redirect to web frontend
    if should_redirect_to_web(request):
        redirect_url = get_web_redirect_url("verify-email", token)
        logger.info(f"Redirecting verify-email to web frontend: {redirect_url}")
        return redirect(redirect_url)

    # If we reach here, show the fallback page (for API requests or mobile app fallback)
    # Get app store URLs from settings
    ios_app_store_url = getattr(settings, "IOS_APP_STORE_URL", "https://apps.apple.com/app/jina-connect/id123456789")
    android_play_store_url = getattr(
        settings, "ANDROID_PLAY_STORE_URL", "https://play.google.com/store/apps/details?id=com.jinacode.jinaconnect"
    )

    # Validate token exists (optional - for better UX)
    token_valid = EmailVerificationToken.objects.filter(token=token, is_used=False).exists()

    frontend_url = getattr(settings, "FRONTEND_URL", "http://localhost:3000")

    context = {
        "token": token,
        "action": "verify-email",
        "action_title": "Verify Your Email",
        "action_description": "Click below to verify your email address and activate your Jina Connect account.",
        "action_button_text": "Verify Email",
        "ios_app_store_url": ios_app_store_url,
        "android_play_store_url": android_play_store_url,
        "web_action_url": f"{frontend_url}/verify-email?token={token}",
        "custom_scheme": f"jinaconnect://verify-email/{token}",
        "token_valid": token_valid,
    }

    return render(request, "users/deep_link_fallback.html", context)


def reset_password_deep_link(request, token):
    """
    Handler for password reset deep links.

    Flow:
    1. If mobile app installed: App intercepts via Universal/App Links (never reaches here)
    2. If web browser or mobile browser: Redirect to web frontend
    3. If explicitly requesting API (?api=true): Show fallback page

    URL Pattern: /reset-password/<token>/

    Redirection Logic:
    - Web browser → http://localhost:3000/reset-password?token={token}
    - Mobile browser (no app) → http://localhost:3000/reset-password?token={token}
    - Mobile app → Intercepted before reaching backend
    """
    # Check if should redirect to web frontend
    if should_redirect_to_web(request):
        redirect_url = get_web_redirect_url("reset-password", token)
        logger.info(f"Redirecting reset-password to web frontend: {redirect_url}")
        return redirect(redirect_url)

    # If we reach here, show the fallback page (for API requests or mobile app fallback)
    # Get app store URLs from settings
    ios_app_store_url = getattr(settings, "IOS_APP_STORE_URL", "https://apps.apple.com/app/jina-connect/id123456789")
    android_play_store_url = getattr(
        settings, "ANDROID_PLAY_STORE_URL", "https://play.google.com/store/apps/details?id=com.jinacode.jinaconnect"
    )

    # Validate token exists (optional - for better UX)
    token_valid = PasswordResetToken.objects.filter(token=token, is_used=False).exists()

    frontend_url = getattr(settings, "FRONTEND_URL", "http://localhost:3000")

    context = {
        "token": token,
        "action": "reset-password",
        "action_title": "Reset Your Password",
        "action_description": "Click below to reset your password and regain access to your Jina Connect account.",
        "action_button_text": "Reset Password",
        "ios_app_store_url": ios_app_store_url,
        "android_play_store_url": android_play_store_url,
        "web_action_url": f"{frontend_url}/reset-password?token={token}",
        "custom_scheme": f"jinaconnect://reset-password/{token}",
        "token_valid": token_valid,
    }

    return render(request, "users/deep_link_fallback.html", context)


# =============================================================================
# API ENDPOINTS FOR MOBILE APPS
# =============================================================================


@require_GET
def deep_link_config(request):
    """
    Returns deep link configuration for mobile apps.
    Useful for debugging and app configuration.

    URL: /deep-link-config/

    Response includes:
    - site_url: Backend URL (used in emails)
    - web_frontend_url: Web frontend URL (where browsers are redirected)
    - deep_link_paths: URL patterns for deep links
    - custom_scheme: Custom URL scheme for direct app opening
    """
    site_url = getattr(settings, "SITE_URL", "http://localhost:8000")
    frontend_url = getattr(settings, "FRONTEND_URL", "http://localhost:3000")

    config = {
        "site_url": site_url,
        "frontend_url": frontend_url,
        "redirect_info": {
            "description": "Emails link to site_url. Backend redirects browsers to frontend_url.",
            "mobile_app": "Intercepted by Universal/App Links before reaching backend",
            "web_browser": f"Redirected to {frontend_url}",
        },
        "deep_link_paths": {
            "verify_email": "/verify-email/{token}/",
            "reset_password": "/reset-password/{token}/",
        },
        "web_frontend_paths": {
            "verify_email": f"{frontend_url}/verify-email?token={{token}}",
            "reset_password": f"{frontend_url}/reset-password?token={{token}}",
        },
        "custom_scheme": "jinaconnect",
        "custom_scheme_paths": {
            "verify_email": "jinaconnect://verify-email/{token}",
            "reset_password": "jinaconnect://reset-password/{token}",
        },
        "ios": {
            "bundle_id": getattr(settings, "IOS_BUNDLE_ID", "com.jinacode.jinaconnect"),
            "app_store_url": getattr(settings, "IOS_APP_STORE_URL", ""),
        },
        "android": {
            "package_name": getattr(settings, "ANDROID_PACKAGE_NAME", "com.jinacode.jinaconnect"),
            "play_store_url": getattr(settings, "ANDROID_PLAY_STORE_URL", ""),
        },
    }

    return JsonResponse(config)

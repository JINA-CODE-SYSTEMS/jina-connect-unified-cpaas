"""
URL configuration for jina_connect project.

The `urlpatterns` list routes URLs to views. For more information please see:
    https://docs.djangoproject.com/en/5.1/topics/http/urls/
Examples:
Function views
    1. Add an import:  from my_app import views
    2. Add a URL to urlpatterns:  path('', views.home, name='home')
Class-based views
    1. Add an import:  from other_app.views import Home
    2. Add a URL to urlpatterns:  path('', Home.as_view(), name='home')
Including another URLconf
    1. Import the include() function: from django.urls import include, path
    2. Add a URL to urlpatterns:  path('blog/', include('blog.urls'))
"""

from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.http import JsonResponse
from django.urls import include, path
from drf_yasg import openapi
from drf_yasg.views import get_schema_view
from rest_framework import permissions
from rest_framework_simplejwt.views import TokenRefreshView

# URL tracking redirect
from broadcast.url_tracker.views import TrackedURLRedirectView

# Import version info for Swagger
from jina_connect.version import BUILD_DATE, BUILD_NUMBER, GIT_COMMIT, VERSION, get_full_version, get_version_string

# Deep linking views
from users.views import (
    android_asset_links,
    apple_app_site_association,
    deep_link_config,
    reset_password_deep_link,
    verify_email_deep_link,
)
from users.viewsets.token import JwtTokenObtainPairView


def version_info(request):
    """API endpoint to get version and build information."""
    return JsonResponse(get_full_version())


schema_view = get_schema_view(
    openapi.Info(
        title=f"JINA-CONNECT API - {get_version_string()}",
        default_version=f"v1 | Build #{BUILD_NUMBER}",
        description=f"""
        # JINA-CONNECT API Documentation

        **Version:** {VERSION} | **Build:** #{BUILD_NUMBER} | **Commit:** {GIT_COMMIT} | **Date:** {BUILD_DATE}

        ## 🌐 REST APIs
        Complete REST API documentation for JINA-CONNECT platform.

        ## 🚀 WebSocket APIs
        **Real-time Team Inbox WebSocket API**
        - **URL Pattern**: `ws://domain/ws/team-inbox/{{tenant_id}}/`
        - **Authentication**: JWT token (query param or header)
        - **Documentation**: [WebSocket API Info](/team-inbox/websocket/info/)
        - **Testing Interface**: [WebSocket Tester](/team-inbox/websocket/test/)

        ### WebSocket Endpoints:
        - `ws://domain/ws/team-inbox/{{tenant_id}}/` - Team inbox real-time messaging
        - `ws://domain/ws/notifications/` - General notifications

        ### Authentication:
        1. Get JWT token from `/token/` endpoint
        2. Include in WebSocket connection:
           - **Web**: Authorization header `Bearer {{token}}`
           - **Mobile**: Query parameter `?token={{token}}`

        ### Message Types:
        - **send_message** - Send new message to team
        - **contact_message** - Receive message from contact (webhook)
        - **get_messages** - Fetch message history
        - **mark_as_read** - Mark messages as read

        📚 **[Complete WebSocket Documentation →](/team-inbox/websocket/info/)**

        🧪 **[WebSocket API Tester →](/team-inbox/websocket/test/)**

        ## 📱 Deep Linking
        **Mobile App Deep Links (Universal Links / App Links)**
        - Email Verification: `https://domain/verify-email/{{token}}/`
        - Password Reset: `https://domain/reset-password/{{token}}/`
        - Config Endpoint: [Deep Link Config](/deep-link-config/)
        """,
        terms_of_service="https://www.google.com/policies/terms/",
        contact=openapi.Contact(email="hello@jinaconnect.com"),
        license=openapi.License(name="BSD License"),
    ),
    public=True,
    # permission_classes=(permissions.AllowAny,)
    permission_classes=(permissions.IsAuthenticated,),
)

urlpatterns = [
    # =========================================================================
    # DEEP LINKING - WELL-KNOWN FILES (Must be at root level)
    # These endpoints serve verification files for iOS Universal Links and
    # Android App Links. They MUST return 200 directly (no redirects).
    # =========================================================================
    path(".well-known/apple-app-site-association", apple_app_site_association, name="apple-app-site-association"),
    path(".well-known/assetlinks.json", android_asset_links, name="android-asset-links"),
    # =========================================================================
    # DEEP LINKING - ACTION HANDLERS
    # These URLs are intercepted by mobile apps (if installed).
    # If app is not installed, they render a fallback web page.
    # =========================================================================
    path("verify-email/<str:token>/", verify_email_deep_link, name="verify-email-deep"),
    path("reset-password/<str:token>/", reset_password_deep_link, name="reset-password-deep"),
    path("deep-link-config/", deep_link_config, name="deep-link-config"),
    # =========================================================================
    # URL TRACKING REDIRECT (public, no auth)
    # =========================================================================
    path("r/<str:code>/", TrackedURLRedirectView.as_view(), name="tracked-url-redirect"),
    # =========================================================================
    # ADMIN & AUTHENTICATION
    # =========================================================================
    path("admin/", admin.site.urls),
    path("token/", JwtTokenObtainPairView.as_view(), name="token_obtain_pair"),
    path("refresh/", TokenRefreshView.as_view(), name="token_refresh"),
    # =========================================================================
    # API ENDPOINTS
    # =========================================================================
    path("users/", include("users.urls")),
    path("tenants/", include(("tenants.urls", "tenants"), namespace="tenants")),
    path("wa/", include(("wa.urls", "wa"), namespace="wa")),
    path("contacts/", include(("contacts.urls", "contacts"), namespace="contacts")),
    path("razorpay/", include(("razorpay.urls", "razorpay"), namespace="razorpay")),
    path("transaction/", include(("transaction.urls", "transaction"), namespace="transaction")),
    path("broadcast/", include(("broadcast.urls", "broadcast"), namespace="broadcast")),
    path("team-inbox/", include(("team_inbox.urls", "team_inbox"), namespace="team_inbox")),
    path("chat-flow/", include(("chat_flow.urls", "chat_flow"), namespace="chat_flow")),
    path("notifications/", include(("notifications.urls", "notifications"), namespace="notifications")),
    path("telegram/", include(("telegram.urls", "telegram"), namespace="telegram")),
    path("sms/", include(("sms.urls", "sms"), namespace="sms")),
    # =========================================================================
    # MOBILE API ENDPOINTS
    # Same functionality as web APIs, separated for mobile client tracking.
    # =========================================================================
    path("mobile/broadcast/", include(("broadcast.mobile_urls", "mobile_broadcast"), namespace="mobile_broadcast")),
    path("mobile/wa/", include(("wa.mobile_urls", "mobile_wa"), namespace="mobile_wa")),
    # =========================================================================
    # API DOCUMENTATION
    # =========================================================================
    path("swagger/", schema_view.with_ui("swagger", cache_timeout=0), name="schema-swagger-ui"),
    path("redoc/", schema_view.with_ui("redoc", cache_timeout=0), name="schema-redoc"),
    path("version/", version_info, name="version-info"),
]

# Serve media and static files in development (needed for Daphne which doesn't auto-serve statics like runserver)
if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
    urlpatterns += static(settings.STATIC_URL, document_root=settings.STATIC_ROOT)

# ── Django Silk profiler UI ──
if settings.DEBUG:
    urlpatterns += [path("silk/", include("silk.urls", namespace="silk"))]

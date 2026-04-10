import logging

from abstract.viewsets.base import BaseTenantModelViewSet
from rest_framework import status
from rest_framework.decorators import action
from rest_framework.parsers import FormParser, MultiPartParser
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from tenants.converters import get_conversion_capabilities
from tenants.models import TenantMedia
from tenants.serializers import TenantMediaSerializer
from tenants.validators import (get_audio_rules, get_document_rules,
                                get_image_rules, get_video_rules)

logger = logging.getLogger(__name__)


class TenantMediaViewSet(BaseTenantModelViewSet):
    """
    A viewset for managing tenant media with WhatsApp-compatible validation and auto-conversion.
    
    Features:
    - Validates files against WhatsApp/Meta requirements
    - Auto-converts incompatible formats (HEIC→JPEG, MOV→MP4, etc.)
    - Compresses files to fit size limits
    - Detects file spoofing (renamed files)
    """
    queryset = TenantMedia.objects.all()
    serializer_class = TenantMediaSerializer
    parser_classes = [MultiPartParser, FormParser]  # Enable file uploads
    required_permissions = {
        "list": "template.view",
        "retrieve": "template.view",
        "create": "template.create",
        "partial_update": "template.edit",
        "supported_formats": "template.view",
        "conversion_status": "template.view",
        "upload_to_wa": "template.create",
        "refresh_url": "template.view",
        # serve is public — see get_permissions / get_authenticators below
        "default": "template.view",
    }

    # ── Make /serve/ publicly accessible ──────────────────────────────
    # The serve endpoint is used as <img src> / <video src> by the
    # browser, which sends plain GET requests without JWT headers.
    # The UUID-based URL is unguessable, and the endpoint only returns
    # a 302 redirect — no data is leaked.  (Fixes #377)

    def get_permissions(self):
        if self.action == 'serve':
            return [AllowAny()]
        return super().get_permissions()

    def get_authenticators(self):
        if getattr(self, 'action', None) == 'serve':
            return []  # skip JWT auth — browser can't send it on <img src>
        return super().get_authenticators()

    @action(detail=False, methods=['get'], url_path='supported-formats')
    def supported_formats(self, request):
        """
        Get information about supported media formats and conversion capabilities.
        
        Returns validation rules and available conversions for each media type.
        """
        is_template = request.query_params.get('is_template', 'true').lower() == 'true'
        
        return Response({
            'native_formats': {
                'document': get_document_rules(),
                'image': get_image_rules(is_template=is_template),
                'video': get_video_rules(is_template=is_template),
                'audio': get_audio_rules(),
            },
            'conversion_capabilities': get_conversion_capabilities(),
            'usage_hints': {
                'auto_convert': 'Set auto_convert=true (default) to automatically convert files',
                'is_template': f'Current mode: {"template (stricter limits)" if is_template else "message (relaxed limits)"}',
            }
        })
    
    @action(detail=False, methods=['get'], url_path='conversion-status')
    def conversion_status(self, request):
        """
        Check if required conversion tools are installed.
        
        Returns status of FFmpeg (for video/audio) and Ghostscript (for PDF compression).
        """
        capabilities = get_conversion_capabilities()
        
        all_available = all([
            capabilities['image']['available'],
            capabilities['video']['available'],
            capabilities['audio']['available'],
        ])
        
        missing_tools = []
        if not capabilities['video']['available']:
            missing_tools.append({
                'tool': 'FFmpeg',
                'required_for': 'Video and Audio conversion',
                'install': capabilities['video'].get('install_hint', '')
            })
        if not capabilities['pdf']['available']:
            missing_tools.append({
                'tool': 'Ghostscript',
                'required_for': 'PDF compression',
                'install': capabilities['pdf'].get('install_hint', '')
            })
        
        return Response({
            'all_conversions_available': all_available,
            'capabilities': {
                'image_conversion': True,  # Always available via Pillow
                'video_conversion': capabilities['video']['available'],
                'audio_conversion': capabilities['audio']['available'],
                'pdf_compression': capabilities['pdf']['available'],
            },
            'missing_tools': missing_tools if missing_tools else None,
            'message': 'All conversion tools are available!' if all_available else 'Some conversion tools are missing. See missing_tools for details.'
        })

    # ── WhatsApp upload (BSP-agnostic) ───────────────────────────────────

    @action(detail=True, methods=['post'], url_path='upload-to-wa')
    def upload_to_wa(self, request, pk=None):
        """
        Upload the saved media file to WhatsApp via the tenant's BSP.

        This is intentionally a **separate step** from saving the file
        (POST /tenant-media/).  The file must already exist on the server;
        this endpoint pushes it to the BSP and stores the returned
        ``handle_id`` (or ``media_id`` for carousel cards).

        Returns 200 with the handle on success, or 502 / 400 on failure.
        """
        instance = self.get_object()

        if not instance.media:
            return Response(
                {'detail': 'No media file on this record. Upload a file first.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        from tenants.signals import upload_media_to_whatsapp

        purpose = request.query_params.get('purpose', 'template')
        method = 'upload_session_media' if purpose == 'session' else 'upload_media'

        try:
            result = upload_media_to_whatsapp(instance, method=method)
        except (ValueError, NotImplementedError) as exc:
            return Response(
                {'detail': str(exc)},
                status=status.HTTP_400_BAD_REQUEST,
            )
        except Exception as exc:
            logger.exception(
                f"upload_to_wa unexpected error for TenantMedia {instance.id}: {exc}"
            )
            return Response(
                {'detail': f'Media upload to WhatsApp failed: {exc}'},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

        if not result.success:
            return Response(
                {
                    'detail': result.error_message or 'BSP upload failed.',
                    'provider': result.provider,
                },
                status=status.HTTP_502_BAD_GATEWAY,
            )

        # Persist the handle_id / media_id from the adapter result
        # Gupshup sometimes returns multiple handle IDs separated by
        # newlines — only the first one is needed.
        raw_handle = result.data.get('handle_id', '')
        handle_id = raw_handle.split('\n')[0].strip() if raw_handle else ''
        is_card = instance.card_index is not None

        if is_card:
            instance.media_id = handle_id
            instance.save(update_fields=['media_id'])
        else:
            instance.wa_handle_id = {'handleId': handle_id}
            instance.save(update_fields=['wa_handle_id'])

        return Response({
            'id': instance.id,
            'handle_id': handle_id,
            'provider': result.provider,
            'stored_on': f'card:{instance.card_index}' if is_card else 'template',
        })

    # ── Media URL helpers ────────────────────────────────────────────────

    @action(detail=True, methods=['get'], url_path='refresh-url')
    def refresh_url(self, request, pk=None):
        """
        Return a **fresh** signed URL for this media file.

        Use this when a previously-obtained signed URL has expired
        (GCS returns 400 for expired V4 signed URLs).  The response
        is lightweight — just the URL and its TTL — so the frontend
        can call it without re-fetching the full template payload.
        """
        instance = self.get_object()

        if not instance.media:
            return Response(
                {'detail': 'No media file on this record.'},
                status=status.HTTP_404_NOT_FOUND,
            )

        import mimetypes

        from django.conf import settings

        url = instance.media.url
        mime_type, _ = mimetypes.guess_type(instance.media.name or '')
        expiration = getattr(settings, 'GS_EXPIRATION', None)

        return Response({
            'id': str(instance.id),
            'url': request.build_absolute_uri(url),
            'content_type': mime_type,
            'expires_in': int(expiration.total_seconds()) if expiration else None,
        })

    @action(detail=True, methods=['get'], url_path='serve')
    def serve(self, request, pk=None):
        """
        Redirect (302) to a fresh signed URL for this media file.

        **Public endpoint** — no authentication required.  Used as the
        ``src`` attribute in ``<img>`` / ``<video>`` / ``<audio>`` tags,
        which cannot send JWT headers.  The UUID PK is unguessable so
        this is safe.  (Fixes #377)

        The redirect is **never cached** so subsequent requests always
        produce a fresh signed URL.
        """
        from django.http import HttpResponseRedirect

        # Direct DB lookup instead of self.get_object() which enforces
        # tenant-scope queryset (requires auth).  UUID is unguessable.
        try:
            instance = TenantMedia.objects.get(pk=pk)
        except TenantMedia.DoesNotExist:
            return Response(
                {'detail': 'Media not found.'},
                status=status.HTTP_404_NOT_FOUND,
            )

        if not instance.media:
            return Response(
                {'detail': 'No media file on this record.'},
                status=status.HTTP_404_NOT_FOUND,
            )

        url = instance.media.url  # generates fresh signed URL
        response = HttpResponseRedirect(url)
        response['Cache-Control'] = 'no-cache, no-store, must-revalidate'
        return response

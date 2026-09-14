"""Sending a voice note from the team inbox.

A live report: recording a voice note in the team inbox and uploading it to
``tenant-media`` came back ``400 Unsupported file extension '.m4a'``. Three
separate defects sat behind that one message, and each is pinned here by its
consequence — what a caller who posts a file actually gets back — rather than
by the shape of a config list.

1. **The gate contradicted the client.**
   ``wa.utility.apis.meta.media_api`` is what uploads to Graph and it accepts
   ``audio/mp4`` (``.m4a``), which is exactly what Safari/iOS ``MediaRecorder``
   produces. ``tenants.validators.MediaTypeConfig`` kept an older, narrower
   second copy of that table, and being the gate, the stale copy won. It now
   derives from the client's tables.

2. **A conversion failure was reported as an unsupported extension.**
   ``AutoMediaConverter.auto_convert`` caught ``ConversionError`` and returned
   the original file, so the serializer's "requires conversion but conversion
   failed" branch was unreachable and every FFmpeg-less host told users their
   file type was unsupported. There is no FFmpeg on the test host either,
   which is what makes these assertions real rather than mocked.

3. **Chrome's voice notes were classified as video.**
   Chrome/Android ``MediaRecorder`` produces ``audio/webm``; ``.webm`` lived
   only in the video conversion map, so a voice note was headed for MP4 and
   would have arrived as a video message. Routing now reads the browser's
   declared content type, and ``video/webm`` still goes to MP4.

HOW TO RUN:
    DJANGO_SETTINGS_MODULE=jina_connect.settings \
        python -m pytest tenants/tests/test_voice_note_media_upload.py -v
"""

from __future__ import annotations

import shutil
import tempfile
import uuid

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from tenants.converters import AutoMediaConverter, ConversionError, MediaConverter
from tenants.models import Tenant, TenantRole, TenantUser
from tenants.validators import MediaTypeConfig, WhatsAppMediaValidator
from users.models import User
from wa.utility.apis.meta.media_api import (
    EXTENSION_TO_MIME,
    SUPPORTED_AUDIO,
    SUPPORTED_DOCUMENT,
    SUPPORTED_IMAGE,
    SUPPORTED_VIDEO,
)

MEDIA_URL = "/tenants/tenant-media/"

# Byte samples chosen so libmagic identifies them the way a real recording
# would; content sniffing runs on every upload and a blob of zeroes named
# ``.m4a`` would be rejected for a different reason than the one under test.
M4A_BYTES = b"\x00\x00\x00\x20ftypM4A \x00\x00\x00\x00M4A mp42isom" + b"\x00" * 512
WAV_BYTES = b"RIFF\x24\x08\x00\x00WAVEfmt \x10\x00\x00\x00\x01\x00\x01\x00\x44\xac\x00\x00" + b"\x00" * 512
WEBM_BYTES = (
    b"\x1a\x45\xdf\xa3\x9fB\x86\x81\x01B\xf7\x81\x01B\xf2\x81\x04"
    b"B\xf3\x81\x08B\x82\x84webmB\x87\x81\x02B\x85\x81\x02" + b"\x00" * 512
)


def _upload(name, content, content_type):
    return SimpleUploadedFile(name, content, content_type=content_type)


@override_settings(STORAGE_BACKEND="local")
class VoiceNoteUploadTestCase(TestCase):
    """Posts real files at the real endpoint, with no FFmpeg available."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls._media_root = tempfile.mkdtemp(prefix="voicenote-")
        cls._media_override = override_settings(MEDIA_ROOT=cls._media_root)
        cls._media_override.enable()

    @classmethod
    def tearDownClass(cls):
        cls._media_override.disable()
        shutil.rmtree(cls._media_root, ignore_errors=True)
        super().tearDownClass()

    def setUp(self):
        self.tenant = Tenant.objects.create(name=f"Voice {uuid.uuid4().hex[:8]}")
        self.user = User.objects.create_user(
            username=f"agent_{uuid.uuid4().hex[:8]}",
            email=f"{uuid.uuid4().hex[:8]}@test.invalid",
            password="x",
            mobile=f"+1{uuid.uuid4().int % 10**10:010d}",
        )
        # #352: ``get_serializer`` refuses a ``tenant`` the caller does not
        # belong to, so the membership has to exist and the payload has to
        # name this tenant (or omit it and let the viewset derive it).
        TenantUser.objects.create(
            tenant=self.tenant,
            user=self.user,
            role=TenantRole.objects.get(tenant=self.tenant, slug="owner"),
        )
        self.client = APIClient()
        self.client.force_authenticate(user=self.user)

    def post(self, upload):
        return self.client.post(
            MEDIA_URL,
            {"media": upload, "tenant": self.tenant.pk},
            format="multipart",
        )

    @staticmethod
    def error_text(response):
        return str(response.data)

    # -- Defect 1: the reported bug -------------------------------------

    def test_safari_voice_note_uploads(self):
        """The reported case: an iOS/Safari voice note is accepted."""
        response = self.post(_upload("voice-note.m4a", M4A_BYTES, "audio/mp4"))

        self.assertEqual(response.status_code, 201, self.error_text(response))
        self.assertEqual(response.data["media_type"], "audio")

    def test_m4a_is_stored_as_recorded(self):
        """No transcode: Meta takes audio/mp4, so re-encoding is pure loss.

        This is also what makes the fix work on a host with no FFmpeg — the
        format that triggered the bug report no longer needs a converter at
        all.
        """
        response = self.post(_upload("voice-note.m4a", M4A_BYTES, "audio/mp4"))

        self.assertEqual(response.status_code, 201, self.error_text(response))
        self.assertFalse(response.data["was_converted"])
        self.assertTrue(response.data["media"].endswith(".m4a"), response.data["media"])

    def test_nothing_meta_accepts_is_queued_for_conversion(self):
        """The rule the bug broke, held for every format rather than just .m4a.

        ``.m4a``, ``.aac`` and ``.3gp`` were all listed for conversion while
        Meta accepted them as they were. An entry here costs quality, CPU and
        — with no FFmpeg — the whole upload.
        """
        convertible = set(MediaConverter.IMAGE_CONVERT_MAP) | set(MediaConverter.VIDEO_CONVERT_MAP)
        convertible |= set(MediaConverter.AUDIO_CONVERT_MAP)
        accepted = set(WhatsAppMediaValidator.ALL_SUPPORTED_EXTENSIONS)

        self.assertEqual(accepted & convertible, set())

    def test_accepted_formats_track_the_graph_client(self):
        """The gate's answer is the client's answer, not a second copy of it."""
        for supported, category in (
            (SUPPORTED_AUDIO, MediaTypeConfig.AUDIO),
            (SUPPORTED_VIDEO, MediaTypeConfig.VIDEO),
            (SUPPORTED_IMAGE, MediaTypeConfig.IMAGE),
        ):
            expected = {ext for ext, mime in EXTENSION_TO_MIME.items() if mime in supported}
            self.assertEqual(set(category["extensions"]), expected)

        # Documents are the one deliberate delta: the legacy Office binaries
        # are refused because the macro check cannot read them, and ``.txt``
        # is held back with them. Everything else the client takes is taken.
        document_exts = {ext for ext, mime in EXTENSION_TO_MIME.items() if mime in SUPPORTED_DOCUMENT}
        self.assertEqual(
            document_exts - set(MediaTypeConfig.DOCUMENT["extensions"]),
            {".doc", ".xls", ".ppt", ".txt"},
        )

    # -- Defect 2: the message when conversion is impossible -------------

    def test_unconvertible_file_names_conversion_not_the_extension(self):
        """A .wav on an FFmpeg-less host must say so.

        ``.wav`` genuinely needs converting — Meta accepts no WAV — so this
        request cannot succeed here. What it must not do is blame the file's
        type: the type is fine, the server just cannot re-encode it.
        """
        response = self.post(_upload("memo.wav", WAV_BYTES, "audio/wav"))

        self.assertEqual(response.status_code, 400)
        message = self.error_text(response)
        self.assertIn("conversion failed", message)
        self.assertIn("FFmpeg is not installed", message)
        self.assertNotIn("Unsupported file extension", message)

    def test_conversion_failure_reaches_the_caller(self):
        """The converter reports failure rather than returning the original.

        Swallowing it here is what made the serializer's message unreachable:
        a failed conversion looked exactly like a file that never needed one.
        """
        with self.assertRaises(ConversionError) as raised:
            AutoMediaConverter.auto_convert(_upload("memo.wav", WAV_BYTES, "audio/wav"))

        self.assertIn("FFmpeg", str(raised.exception))

    # -- Defect 3: webm routing -----------------------------------------

    def test_chrome_voice_note_and_video_diverge(self):
        """Same extension, same bytes, different declared type, different route.

        Neither can complete without FFmpeg, but the target each is headed
        for is visible in the answer — and a voice note headed for ``.ogg``
        is a voice note, while one headed for ``.mp4`` would have arrived in
        the conversation as a video.
        """
        voice = self.post(_upload("voice-note.webm", WEBM_BYTES, "audio/webm;codecs=opus"))
        clip = self.post(_upload("clip.webm", WEBM_BYTES, "video/webm;codecs=vp8"))

        self.assertEqual(voice.status_code, 400)
        self.assertEqual(clip.status_code, 400)
        self.assertIn("'.ogg'", self.error_text(voice))
        self.assertNotIn("'.mp4'", self.error_text(voice))
        self.assertIn("'.mp4'", self.error_text(clip))
        self.assertNotIn("'.ogg'", self.error_text(clip))

    def test_webm_without_a_usable_content_type_stays_video(self):
        """The default protects genuine video uploads.

        Every non-browser path — a file picked off disk, a server-side
        re-upload — arrives with no useful type. Defaulting those to audio
        would break real video uploads, which is the mirror of the bug being
        fixed here.
        """
        for declared in (None, "", "application/octet-stream"):
            with self.subTest(content_type=declared):
                self.assertEqual(
                    MediaConverter.needs_conversion("clip.webm", declared),
                    (True, "video", ".mp4"),
                )

    def test_audio_webm_routes_by_declared_type_not_by_sniffing(self):
        """Sniffing cannot answer this; only the uploader can.

        libmagic reads the EBML DocType and calls an audio-only WebM
        ``video/webm`` too, so the declared type is the only signal there is.
        """
        self.assertEqual(
            MediaConverter.needs_conversion("voice-note.webm", "audio/webm;codecs=opus"),
            (True, "audio", ".ogg"),
        )

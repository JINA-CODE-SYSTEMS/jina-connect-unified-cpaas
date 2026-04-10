"""
META Direct Media API — WhatsApp Cloud API Media Endpoints

Implements all 4 media endpoints from the WhatsApp Business Platform:
  - POST   /{PHONE_NUMBER_ID}/media  → Upload media
  - GET    /{MEDIA_ID}              → Get media URL
  - DELETE /{MEDIA_ID}              → Delete media
  - GET    {MEDIA_URL}              → Download binary media

Reference:
  https://developers.facebook.com/docs/whatsapp/cloud-api/reference/media

Supported Media Types & Size Limits:
  ┌─────────────┬────────────────────┬───────────────────────────────┬──────────┐
  │ Category    │ Extension          │ MIME Type                     │ Max Size │
  ├─────────────┼────────────────────┼───────────────────────────────┼──────────┤
  │ Audio       │ .aac               │ audio/aac                     │ 16 MB    │
  │             │ .amr               │ audio/amr                     │ 16 MB    │
  │             │ .mp3               │ audio/mpeg                    │ 16 MB    │
  │             │ .m4a               │ audio/mp4                     │ 16 MB    │
  │             │ .ogg               │ audio/ogg (OPUS only)         │ 16 MB    │
  ├─────────────┼────────────────────┼───────────────────────────────┼──────────┤
  │ Document    │ .txt               │ text/plain                    │ 100 MB   │
  │             │ .xls               │ application/vnd.ms-excel      │ 100 MB   │
  │             │ .xlsx              │ application/vnd.openxml...    │ 100 MB   │
  │             │ .doc               │ application/msword            │ 100 MB   │
  │             │ .docx              │ application/vnd.openxml...    │ 100 MB   │
  │             │ .ppt               │ application/vnd.ms-powerpoint │ 100 MB   │
  │             │ .pptx              │ application/vnd.openxml...    │ 100 MB   │
  │             │ .pdf               │ application/pdf               │ 100 MB   │
  ├─────────────┼────────────────────┼───────────────────────────────┼──────────┤
  │ Image       │ .jpeg / .jpg       │ image/jpeg                    │ 5 MB     │
  │             │ .png               │ image/png                     │ 5 MB     │
  ├─────────────┼────────────────────┼───────────────────────────────┼──────────┤
  │ Sticker     │ .webp (static)     │ image/webp                    │ 100 KB   │
  │             │ .webp (animated)   │ image/webp                    │ 500 KB   │
  ├─────────────┼────────────────────┼───────────────────────────────┼──────────┤
  │ Video       │ .3gp               │ video/3gpp                    │ 16 MB    │
  │             │ .mp4               │ video/mp4                     │ 16 MB    │
  └─────────────┴────────────────────┴───────────────────────────────┴──────────┘

  Media IDs from uploads expire after 30 days.
  Media IDs from webhooks expire after 7 days.
  Media URLs expire after 5 minutes.
  Max download size: 100 MB.
"""

from typing import Optional

from wa.utility.apis.meta.base_api import WAAPI

# =============================================================================
# SUPPORTED MEDIA TYPES — Constants
# =============================================================================

# { mime_type: max_size_bytes }
SUPPORTED_AUDIO = {
    "audio/aac": 16 * 1024 * 1024,       # .aac  — 16 MB
    "audio/amr": 16 * 1024 * 1024,       # .amr  — 16 MB
    "audio/mpeg": 16 * 1024 * 1024,      # .mp3  — 16 MB
    "audio/mp4": 16 * 1024 * 1024,       # .m4a  — 16 MB
    "audio/ogg": 16 * 1024 * 1024,       # .ogg  — 16 MB (OPUS codec only)
}

SUPPORTED_DOCUMENT = {
    "text/plain": 100 * 1024 * 1024,                                                              # .txt
    "application/vnd.ms-excel": 100 * 1024 * 1024,                                                # .xls
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": 100 * 1024 * 1024,        # .xlsx
    "application/msword": 100 * 1024 * 1024,                                                      # .doc
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": 100 * 1024 * 1024,  # .docx
    "application/vnd.ms-powerpoint": 100 * 1024 * 1024,                                           # .ppt
    "application/vnd.openxmlformats-officedocument.presentationml.presentation": 100 * 1024 * 1024,# .pptx
    "application/pdf": 100 * 1024 * 1024,                                                         # .pdf
}

SUPPORTED_IMAGE = {
    "image/jpeg": 5 * 1024 * 1024,       # .jpeg/.jpg — 5 MB (8-bit RGB or RGBA)
    "image/png": 5 * 1024 * 1024,        # .png       — 5 MB (8-bit RGB or RGBA)
}

SUPPORTED_STICKER = {
    "image/webp": 500 * 1024,            # .webp — 500 KB animated, 100 KB static
}
# For sticker validation we need separate limits
STICKER_STATIC_MAX = 100 * 1024          # 100 KB
STICKER_ANIMATED_MAX = 500 * 1024        # 500 KB

SUPPORTED_VIDEO = {
    "video/3gpp": 16 * 1024 * 1024,      # .3gp — 16 MB
    "video/mp4": 16 * 1024 * 1024,       # .mp4 — 16 MB (H.264 + AAC)
}

# Combined lookup
ALL_SUPPORTED_MEDIA = {
    **SUPPORTED_AUDIO,
    **SUPPORTED_DOCUMENT,
    **SUPPORTED_IMAGE,
    **SUPPORTED_STICKER,
    **SUPPORTED_VIDEO,
}

# Max download size for incoming media
MAX_DOWNLOAD_SIZE = 100 * 1024 * 1024    # 100 MB

# Expiry durations
UPLOAD_MEDIA_ID_EXPIRY_DAYS = 30
WEBHOOK_MEDIA_ID_EXPIRY_DAYS = 7
MEDIA_URL_EXPIRY_MINUTES = 5

# Extension → MIME type mapping (for auto-detection)
EXTENSION_TO_MIME = {
    ".aac": "audio/aac",
    ".amr": "audio/amr",
    ".mp3": "audio/mpeg",
    ".m4a": "audio/mp4",
    ".ogg": "audio/ogg",
    ".txt": "text/plain",
    ".xls": "application/vnd.ms-excel",
    ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    ".doc": "application/msword",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".ppt": "application/vnd.ms-powerpoint",
    ".pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    ".pdf": "application/pdf",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".webp": "image/webp",
    ".3gp": "video/3gpp",
    ".mp4": "video/mp4",
}

# MIME type → media category (for WhatsApp message type inference)
MIME_TO_CATEGORY = {}
for _mime in SUPPORTED_AUDIO:
    MIME_TO_CATEGORY[_mime] = "audio"
for _mime in SUPPORTED_DOCUMENT:
    MIME_TO_CATEGORY[_mime] = "document"
for _mime in SUPPORTED_IMAGE:
    MIME_TO_CATEGORY[_mime] = "image"
for _mime in SUPPORTED_STICKER:
    MIME_TO_CATEGORY[_mime] = "sticker"
for _mime in SUPPORTED_VIDEO:
    MIME_TO_CATEGORY[_mime] = "video"


# =============================================================================
# VALIDATION HELPERS
# =============================================================================

def validate_media_type(mime_type: str) -> bool:
    """Check if a MIME type is supported by WhatsApp Cloud API."""
    return mime_type.lower() in ALL_SUPPORTED_MEDIA


def validate_media_size(mime_type: str, file_size: int) -> bool:
    """
    Check if file size is within WhatsApp limits for the given MIME type.
    
    Args:
        mime_type: The MIME type of the media file
        file_size: Size of the file in bytes
        
    Returns:
        True if within limits, False if too large
    """
    max_size = ALL_SUPPORTED_MEDIA.get(mime_type.lower())
    if max_size is None:
        return False  # Unsupported type
    return file_size <= max_size


def get_media_category(mime_type: str) -> Optional[str]:
    """
    Get the WhatsApp media category for a MIME type.
    
    Returns: "audio", "document", "image", "sticker", "video", or None
    """
    return MIME_TO_CATEGORY.get(mime_type.lower())


def detect_mime_type(filename: str) -> Optional[str]:
    """
    Detect MIME type from filename extension.
    Falls back to mimetypes.guess_type() if extension not in our map.
    
    Args:
        filename: The filename or path
        
    Returns:
        MIME type string or None if undetectable
    """
    import mimetypes
    import os
    
    ext = os.path.splitext(filename)[1].lower()
    
    # Use our WhatsApp-specific mapping first
    if ext in EXTENSION_TO_MIME:
        return EXTENSION_TO_MIME[ext]
    
    # Fall back to system mimetypes
    mime, _ = mimetypes.guess_type(filename)
    return mime


# =============================================================================
# META MEDIA API CLIENT
# =============================================================================

class MetaMediaAPI(WAAPI):
    """
    META Direct API client for WhatsApp media operations.
    
    Provides methods for all 4 media endpoints:
      - upload_media()       → POST /{phone_number_id}/media
      - get_media_url()      → GET  /{media_id}
      - delete_media()       → DELETE /{media_id}
      - download_media()     → GET  {media_url} (binary)
    
    Usage:
        api = MetaMediaAPI(
            token="EAAJB...",
            phone_number_id="YOUR_PHONE_NUMBER_ID",
        )
        
        # Upload from file path
        result = api.upload_media(file_path="/path/to/image.jpg")
        media_id = result["id"]
        
        # Upload from Django InMemoryUploadedFile
        result = api.upload_media_from_file_object(
            file_obj=request.FILES['file'],
            filename="photo.jpg",
        )
        
        # Get download URL (valid 5 min)
        info = api.get_media_url(media_id="1037543291543636")
        url = info["url"]
        
        # Download binary data
        binary = api.download_media(media_url=url)
        
        # Delete
        api.delete_media(media_id="1037543291543636")
    """
    
    phone_number_id: str
    
    # -------------------------------------------------------------------------
    # URL builders
    # -------------------------------------------------------------------------
    
    @property
    def _upload_url(self) -> str:
        """POST /{phone_number_id}/media"""
        return f"{self.BASE_URL}{self.phone_number_id}/media"
    
    def _media_id_url(self, media_id: str) -> str:
        """GET/DELETE /{media_id}"""
        return f"{self.BASE_URL}{media_id}"
    
    @property
    def auth_header(self) -> dict:
        """Authorization-only header (no Content-Type — requests sets multipart boundary)."""
        return {
            "Authorization": f"Bearer {self.token}",
        }
    
    # -------------------------------------------------------------------------
    # 1. UPLOAD MEDIA
    # -------------------------------------------------------------------------
    
    def upload_media(self, file_path: str, mime_type: str = None) -> dict:
        """
        Upload a media file from local filesystem to WhatsApp.
        
        META API: POST https://graph.facebook.com/{version}/{phone_number_id}/media
        
        Curl equivalent:
            curl 'https://graph.facebook.com/v24.0/YOUR_PHONE_NUMBER_ID/media' \\
                -H 'Authorization: Bearer EAAJB...' \\
                -F 'messaging_product=whatsapp' \\
                -F 'file=@/path/to/file.jpg;type=image/jpeg'
        
        Args:
            file_path: Absolute path to the local file
            mime_type: MIME type (auto-detected from extension if not provided)
            
        Returns:
            {"id": "<MEDIA_ID>"}  — media ID valid for 30 days
            
        Raises:
            ValueError: If MIME type is unsupported or file exceeds size limit
            Exception: If META API returns a non-2xx response
        """
        import os

        import requests

        # Auto-detect MIME type if not provided
        if not mime_type:
            mime_type = detect_mime_type(file_path)
            if not mime_type:
                raise ValueError(
                    f"Cannot detect MIME type for '{file_path}'. "
                    f"Please provide mime_type explicitly."
                )
        
        mime_type = mime_type.lower()
        
        # Validate MIME type
        if not validate_media_type(mime_type):
            raise ValueError(
                f"Unsupported MIME type: '{mime_type}'. "
                f"Supported types: {list(ALL_SUPPORTED_MEDIA.keys())}"
            )
        
        # Validate file size
        file_size = os.path.getsize(file_path)
        if not validate_media_size(mime_type, file_size):
            max_size = ALL_SUPPORTED_MEDIA[mime_type]
            raise ValueError(
                f"File too large: {file_size:,} bytes. "
                f"Max for {mime_type}: {max_size:,} bytes ({max_size // (1024*1024)} MB)"
            )
        
        url = self._upload_url
        
        # Print curl equivalent for debugging
        curl_cmd = self._generate_upload_curl(url, file_path, mime_type)
        self._last_curl_command = curl_cmd
        print(curl_cmd)
        
        # Multipart form upload
        with open(file_path, 'rb') as f:
            files = {
                'file': (os.path.basename(file_path), f, mime_type),
            }
            data = {
                'messaging_product': 'whatsapp',
                'type': mime_type,
            }
            
            response = requests.post(
                url,
                headers=self.auth_header,
                files=files,
                data=data,
            )
        
        return self._handle_response(response, "Media upload")
    
    def upload_media_from_file_object(
        self,
        file_obj,
        filename: str,
        mime_type: str = None,
        file_size: int = None,
    ) -> dict:
        """
        Upload a media file-like object (e.g., Django InMemoryUploadedFile) to WhatsApp.
        
        Args:
            file_obj: File-like object with read() method
            filename: Original filename (used for MIME detection if mime_type not given)
            mime_type: MIME type (auto-detected from filename if not provided)
            file_size: File size in bytes (for validation; skipped if None)
            
        Returns:
            {"id": "<MEDIA_ID>"}
        """
        import requests

        # Auto-detect MIME type
        if not mime_type:
            mime_type = detect_mime_type(filename)
            if not mime_type:
                raise ValueError(
                    f"Cannot detect MIME type for '{filename}'. "
                    f"Please provide mime_type explicitly."
                )
        
        mime_type = mime_type.lower()
        
        # Validate
        if not validate_media_type(mime_type):
            raise ValueError(
                f"Unsupported MIME type: '{mime_type}'. "
                f"Supported types: {list(ALL_SUPPORTED_MEDIA.keys())}"
            )
        
        if file_size is not None and not validate_media_size(mime_type, file_size):
            max_size = ALL_SUPPORTED_MEDIA[mime_type]
            raise ValueError(
                f"File too large: {file_size:,} bytes. "
                f"Max for {mime_type}: {max_size:,} bytes ({max_size // (1024*1024)} MB)"
            )
        
        url = self._upload_url
        
        # Print curl equivalent
        curl_cmd = self._generate_upload_curl(url, filename, mime_type)
        self._last_curl_command = curl_cmd
        print(curl_cmd)
        
        files = {
            'file': (filename, file_obj, mime_type),
        }
        data = {
            'messaging_product': 'whatsapp',
            'type': mime_type,
        }
        
        response = requests.post(
            url,
            headers=self.auth_header,
            files=files,
            data=data,
        )
        
        return self._handle_response(response, "Media upload (file object)")
    
    # -------------------------------------------------------------------------
    # 2. GET MEDIA URL
    # -------------------------------------------------------------------------
    
    def get_media_url(self, media_id: str, phone_number_id: str = None) -> dict:
        """
        Get the download URL for a media asset by its ID.
        
        META API: GET https://graph.facebook.com/{version}/{media_id}
                      ?phone_number_id={phone_number_id}
        
        The returned URL is only valid for 5 minutes.
        
        Args:
            media_id: The META media ID
            phone_number_id: Optional — restricts to media uploaded by this phone.
                             Defaults to self.phone_number_id if not provided.
                             
        Returns:
            {
                "messaging_product": "whatsapp",
                "url": "<MEDIA_URL>",
                "mime_type": "<MIME_TYPE>",
                "sha256": "<HASH>",
                "file_size": "<SIZE>",
                "id": "<MEDIA_ID>"
            }
        """
        import requests
        
        url = self._media_id_url(media_id)
        params = {}
        if phone_number_id:
            params["phone_number_id"] = phone_number_id
        elif self.phone_number_id:
            params["phone_number_id"] = self.phone_number_id
        
        # Curl equivalent
        param_str = f"?phone_number_id={params.get('phone_number_id', '')}" if params else ""
        curl_cmd = (
            "=" * 80 + "\n"
            "EQUIVALENT CURL COMMAND (GET MEDIA URL):\n"
            "=" * 80 + "\n"
            f"curl '{url}{param_str}' \\\n"
            f"  -H 'Authorization: Bearer {self.token}'\n"
            "=" * 80
        )
        self._last_curl_command = curl_cmd
        print(curl_cmd)
        
        response = requests.get(
            url,
            headers=self.json_headers,
            params=params,
        )
        
        return self._handle_response(response, "Get media URL")
    
    # -------------------------------------------------------------------------
    # 3. DELETE MEDIA
    # -------------------------------------------------------------------------
    
    def delete_media(self, media_id: str, phone_number_id: str = None) -> dict:
        """
        Delete a media asset from WhatsApp.
        
        META API: DELETE https://graph.facebook.com/{version}/{media_id}
                         ?phone_number_id={phone_number_id}
        
        Args:
            media_id: The META media ID to delete
            phone_number_id: Optional — restricts to media uploaded by this phone
            
        Returns:
            {"success": true}
        """
        import requests
        
        url = self._media_id_url(media_id)
        params = {}
        if phone_number_id:
            params["phone_number_id"] = phone_number_id
        elif self.phone_number_id:
            params["phone_number_id"] = self.phone_number_id
        
        # Curl equivalent
        param_str = f"?phone_number_id={params.get('phone_number_id', '')}" if params else ""
        curl_cmd = (
            "=" * 80 + "\n"
            "EQUIVALENT CURL COMMAND (DELETE MEDIA):\n"
            "=" * 80 + "\n"
            f"curl -X DELETE '{url}{param_str}' \\\n"
            f"  -H 'Authorization: Bearer {self.token}'\n"
            "=" * 80
        )
        self._last_curl_command = curl_cmd
        print(curl_cmd)
        
        response = requests.delete(
            url,
            headers=self.json_headers,
            params=params,
        )
        
        return self._handle_response(response, "Delete media")
    
    # -------------------------------------------------------------------------
    # 4. DOWNLOAD MEDIA (binary)
    # -------------------------------------------------------------------------
    
    def download_media(self, media_url: str) -> bytes:
        """
        Download media binary data from a WhatsApp media URL.
        
        NOTE: This hits the media_url directly (NOT the Graph API base URL).
        The media_url comes from get_media_url() and is valid for 5 minutes.
        
        META API: GET {media_url}
                  Header: Authorization: Bearer {token}
        
        Args:
            media_url: The full media download URL from get_media_url()
            
        Returns:
            bytes — Raw binary data of the media file
            
        Raises:
            ValueError: If media exceeds 100 MB download limit
            Exception: If download fails (404 = URL expired, re-fetch via get_media_url)
        """
        import requests

        # Curl equivalent
        curl_cmd = (
            "=" * 80 + "\n"
            "EQUIVALENT CURL COMMAND (DOWNLOAD MEDIA):\n"
            "=" * 80 + "\n"
            f"curl '{media_url}' \\\n"
            f"  -H 'Authorization: Bearer {self.token}' \\\n"
            f"  -o 'downloaded_media_file'\n"
            "=" * 80
        )
        self._last_curl_command = curl_cmd
        print(curl_cmd)
        
        response = requests.get(
            media_url,
            headers=self.auth_header,
            stream=True,
        )
        
        if response.status_code == 404:
            raise Exception(
                "Media URL expired or not found (404). "
                "Media URLs are valid for only 5 minutes. "
                "Re-fetch via get_media_url() and try again."
            )
        
        if response.status_code not in [200, 201]:
            raise Exception(
                f"Media download failed with status {response.status_code}. "
                f"Response: {response.text[:500]}"
            )
        
        content = response.content
        
        if len(content) > MAX_DOWNLOAD_SIZE:
            raise ValueError(
                f"Downloaded media exceeds max size: {len(content):,} bytes "
                f"(max: {MAX_DOWNLOAD_SIZE:,} bytes / 100 MB)"
            )
        
        print(f"✅ Media downloaded: {len(content):,} bytes, "
              f"Content-Type: {response.headers.get('Content-Type', 'unknown')}")
        
        return content
    
    def download_media_to_file(self, media_url: str, output_path: str) -> dict:
        """
        Download media and save directly to a file.
        
        Streams the download to avoid loading large files into memory.
        
        Args:
            media_url: The full media download URL
            output_path: Local file path to save the media to
            
        Returns:
            {
                "path": "/saved/path/file.mp4",
                "size": 12345678,
                "content_type": "video/mp4"
            }
        """
        import requests
        
        curl_cmd = (
            "=" * 80 + "\n"
            "EQUIVALENT CURL COMMAND (DOWNLOAD MEDIA TO FILE):\n"
            "=" * 80 + "\n"
            f"curl '{media_url}' \\\n"
            f"  -H 'Authorization: Bearer {self.token}' \\\n"
            f"  -o '{output_path}'\n"
            "=" * 80
        )
        self._last_curl_command = curl_cmd
        print(curl_cmd)
        
        response = requests.get(
            media_url,
            headers=self.auth_header,
            stream=True,
        )
        
        if response.status_code == 404:
            raise Exception(
                "Media URL expired or not found (404). "
                "Media URLs are valid for only 5 minutes. "
                "Re-fetch via get_media_url() and try again."
            )
        
        if response.status_code not in [200, 201]:
            raise Exception(
                f"Media download failed with status {response.status_code}. "
                f"Response: {response.text[:500]}"
            )
        
        total_size = 0
        with open(output_path, 'wb') as f:
            for chunk in response.iter_content(chunk_size=8192):
                if chunk:
                    total_size += len(chunk)
                    if total_size > MAX_DOWNLOAD_SIZE:
                        raise ValueError(
                            f"Download exceeds max size ({MAX_DOWNLOAD_SIZE:,} bytes). Aborting."
                        )
                    f.write(chunk)
        
        content_type = response.headers.get('Content-Type', 'application/octet-stream')
        print(f"✅ Media saved to {output_path}: {total_size:,} bytes ({content_type})")
        
        return {
            "path": output_path,
            "size": total_size,
            "content_type": content_type,
        }
    
    # -------------------------------------------------------------------------
    # CONVENIENCE: Upload + get handle (for template headers)
    # -------------------------------------------------------------------------
    
    def upload_and_get_handle(self, file_path: str = None, file_obj=None, 
                              filename: str = None, mime_type: str = None) -> str:
        """
        Upload media and return just the media ID (handle) for use in templates.
        
        WATemplate.media_handle stores this value for template header media.
        
        Args:
            file_path: Local file path (mutually exclusive with file_obj)
            file_obj: File-like object (mutually exclusive with file_path)
            filename: Required if using file_obj
            mime_type: Optional, auto-detected if not provided
            
        Returns:
            str — The media ID (handle) e.g., "1037543291543636"
        """
        if file_path:
            result = self.upload_media(file_path=file_path, mime_type=mime_type)
        elif file_obj and filename:
            result = self.upload_media_from_file_object(
                file_obj=file_obj,
                filename=filename,
                mime_type=mime_type,
            )
        else:
            raise ValueError("Provide either file_path OR (file_obj + filename)")
        
        return result["id"]

    # -------------------------------------------------------------------------
    # RESUMABLE UPLOAD API — for template header media
    # -------------------------------------------------------------------------

    def create_resumable_upload_session(
        self, app_id: str, file_length: int, file_type: str
    ) -> str:
        """
        Create a resumable upload session via POST /{app-id}/uploads.

        This is REQUIRED for template header media (IMAGE/VIDEO/DOCUMENT).
        The regular media API (/{phone_number_id}/media) returns IDs that
        are only valid for message sending, NOT template creation.

        Args:
            app_id:      Facebook App ID (WAApp.app_id)
            file_length: File size in bytes
            file_type:   MIME type (e.g., "image/png")

        Returns:
            str — Upload session ID, e.g. "upload:ABcd1234..."
        """
        import requests

        url = f"{self.BASE_URL}{app_id}/uploads"
        params = {
            "file_length": file_length,
            "file_type": file_type,
            "access_token": self.token,
        }

        response = requests.post(url, params=params)
        result = self._handle_response(response, "Create resumable upload session")
        session_id = result.get("id")
        if not session_id:
            raise Exception(
                f"Resumable upload session creation returned no ID: {result}"
            )
        return session_id

    def upload_file_to_session(self, session_id: str, file_bytes: bytes) -> str:
        """
        Upload file data to a resumable upload session.

        POST /{session-id} with raw binary body.

        Args:
            session_id: Upload session ID from create_resumable_upload_session()
            file_bytes: Raw file content as bytes

        Returns:
            str — File handle for use in template header_handle, e.g. "4:aW1h..."
        """
        import requests

        url = f"{self.BASE_URL}{session_id}"
        headers = {
            "Authorization": f"OAuth {self.token}",
            "file_offset": "0",
        }

        response = requests.post(url, data=file_bytes, headers=headers)
        result = self._handle_response(response, "Upload file to resumable session")
        handle = result.get("h")
        if not handle:
            raise Exception(
                f"Resumable upload returned no file handle: {result}"
            )
        return handle

    def upload_media_for_template(
        self, app_id: str, file_obj, filename: str, mime_type: str = None
    ) -> str:
        """
        Upload media for use in template headers via the Resumable Upload API.

        This is a convenience method that:
        1. Creates an upload session
        2. Uploads the file data
        3. Returns the handle string

        Args:
            app_id:    Facebook App ID (WAApp.app_id)
            file_obj:  File-like object with read() method
            filename:  Original filename (for MIME detection fallback)
            mime_type: MIME type (auto-detected from filename if not provided)

        Returns:
            str — File handle for header_handle, e.g. "4:aW1hZ2Uv..."
        """
        # Auto-detect MIME type if not provided
        if not mime_type:
            import os
            ext = os.path.splitext(filename)[1].lower()
            mime_type = EXTENSION_TO_MIME.get(ext)
            if not mime_type:
                raise ValueError(
                    f"Cannot detect MIME type for '{filename}'. "
                    f"Provide mime_type explicitly."
                )

        # Read file content
        file_bytes = file_obj.read()
        file_length = len(file_bytes)

        # Step 1: Create upload session
        session_id = self.create_resumable_upload_session(
            app_id=app_id,
            file_length=file_length,
            file_type=mime_type,
        )

        # Step 2: Upload file data
        handle = self.upload_file_to_session(
            session_id=session_id,
            file_bytes=file_bytes,
        )

        return handle
    
    # -------------------------------------------------------------------------
    # INTERNAL: Response handler & curl generator
    # -------------------------------------------------------------------------
    
    def _handle_response(self, response, operation: str) -> dict:
        """Unified response handler with detailed error reporting."""
        import json
        
        if response.status_code in [200, 201]:
            result = response.json()
            print(f"✅ {operation} successful: {json.dumps(result, indent=2)}")
            return result
        
        error_msg = f"{operation} failed with status code {response.status_code}"
        try:
            error_details = response.json()
            error_msg += f"\nResponse: {json.dumps(error_details, indent=2)}"
        except Exception:
            error_msg += f"\nResponse text: {response.text[:1000]}"
        
        print("=" * 80)
        print(f"❌ {operation} FAILED")
        print("=" * 80)
        print(error_msg)
        print("=" * 80)
        
        raise Exception(error_msg)
    
    def _generate_upload_curl(self, url: str, file_path: str, mime_type: str) -> str:
        """Generate curl equivalent for upload requests."""
        return (
            "=" * 80 + "\n"
            "EQUIVALENT CURL COMMAND (UPLOAD MEDIA):\n"
            "=" * 80 + "\n"
            f"curl '{url}' \\\n"
            f"  -H 'Authorization: Bearer {self.token}' \\\n"
            f"  -F 'messaging_product=whatsapp' \\\n"
            f"  -F 'file=@{file_path};type={mime_type}'\n"
            "=" * 80
        )

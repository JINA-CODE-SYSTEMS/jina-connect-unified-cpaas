"""
Custom Google Cloud Storage backend that injects ``response_type``
(i.e. the ``Content-Type`` header GCS should return) into every
signed URL.

Why?
────
The default ``storages.backends.gcloud.GoogleCloudStorage.url()``
generates V4 signed URLs **without** telling GCS which
``Content-Type`` to use in the response.  GCS then falls back to
whatever metadata the blob was uploaded with — which may be
``application/octet-stream`` for files whose MIME type was not
correctly inferred at upload time.

When a browser loads such a URL cross-origin (e.g. ``<img>``,
``fetch()``, or ``canvas``), Chrome's **ORB** (Opaque Resource
Blocking) mechanism compares the response ``Content-Type`` against
the expected type for the request context.  A mismatch causes ORB to
block the response entirely.

By including ``response_type=<correct MIME>`` in the signed URL, GCS
is instructed to return the right ``Content-Type`` *regardless* of
blob metadata — fixing ORB and ``Content-Disposition`` issues in one
shot.

Additionally, this backend sets ``response_disposition=inline`` so
browsers render images/videos inline rather than triggering a
download.

Usage in settings.py
────────────────────
    STORAGES = {
        "default": {
            "BACKEND": "jina_connect.storage_backends.ORBSafeGoogleCloudStorage",
        },
        ...
    }
"""

import mimetypes

from storages.backends.gcloud import GoogleCloudStorage


class ORBSafeGoogleCloudStorage(GoogleCloudStorage):
    """
    GoogleCloudStorage subclass that adds ``response_type`` and
    ``response_disposition`` to every signed URL so that browsers
    receive the correct ``Content-Type`` header — preventing ORB
    (Opaque Resource Blocking) errors.
    """

    # MIME types that should be displayed inline in the browser
    _INLINE_MIME_PREFIXES = ("image/", "video/", "audio/", "application/pdf")

    def url(self, name, parameters=None):
        """
        Override to inject ``response_type`` (Content-Type) and
        ``response_disposition`` into signed-URL parameters when
        ``querystring_auth`` is enabled.

        For public (unsigned) URLs, falls through to the parent
        implementation unchanged.
        """
        if not self.querystring_auth:
            return super().url(name, parameters=parameters)

        params = dict(parameters or {})

        # Only add response_type if the caller didn't already set it
        if "response_type" not in params:
            mime_type, _ = mimetypes.guess_type(name)
            if mime_type:
                params["response_type"] = mime_type

        # Inline disposition for renderable media (images, video, pdf)
        if "response_disposition" not in params:
            mime = params.get("response_type", "")
            if any(mime.startswith(prefix) for prefix in self._INLINE_MIME_PREFIXES):
                params["response_disposition"] = "inline"

        return super().url(name, parameters=params)

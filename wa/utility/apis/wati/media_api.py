"""
WATI Media API Client

Provides media retrieval operations via the WATI API.

API Endpoints:
    - GET /api/v1/getMedia?fileName={fileName} — Get media by file name

API Reference: https://docs.wati.io/reference
"""

from wa.utility.apis.wati.base_api import WAAPI


class MediaAPI(WAAPI):
    """
    WATI API client for media operations.

    Handles:
    - Retrieving media files by filename

    The WATI media API works differently from Gupshup/META — media is
    retrieved by filename path (e.g., "data/images/uuid.jpg") rather
    than by media ID.

    Required credentials:
    - api_endpoint: WATI tenant API endpoint
    - token: Bearer token for API authentication
    """

    # =========================================================================
    # URL Properties
    # =========================================================================

    @property
    def _get_media_url(self) -> str:
        """URL for getting media by filename."""
        return self.v1_url("getMedia")

    # =========================================================================
    # Media Operations
    # =========================================================================

    def get_media(self, file_name: str) -> dict:
        """
        Get media by file name.

        The file name should be the full path as returned in webhook payloads
        (e.g., "data/images/c1d465a1-3cbf-4190-a936-1c2ddd63f057.jpg").

        Args:
            file_name: Full path of the media file to retrieve.

        Returns:
            dict: Media content or URL from WATI API.
        """
        request_data = {
            "method": "GET",
            "url": self._get_media_url,
            "headers": self.json_headers,
            "params": {"fileName": file_name},
        }
        return self.make_json_request(request_data)

    def download_media(self, file_name: str, output_path: str) -> str:
        """
        Download media by file name and save to local path.

        Args:
            file_name: Full path of the media file to download.
            output_path: Local file path to save the downloaded media.

        Returns:
            str: The output_path where the file was saved.
        """
        import requests

        url = self._get_media_url
        headers = {
            "Authorization": f"Bearer {self.token}",
            "Accept": "*/*",
        }

        print("=" * 80)
        print("EQUIVALENT CURL COMMAND FOR WATI MEDIA DOWNLOAD:")
        print("=" * 80)
        print(f"curl -X GET '{url}?fileName={file_name}' \\")
        print(f"  -H 'Authorization: Bearer {self.token}' \\")
        print(f"  -o '{output_path}'")
        print("=" * 80)

        response = requests.get(url, headers=headers, params={"fileName": file_name}, stream=True, timeout=30)

        if response.status_code not in [200, 201]:
            error_msg = f"Media download failed with status code {response.status_code}"
            try:
                error_details = response.json()
                error_msg += f"\nResponse: {error_details}"
            except Exception:
                error_msg += f"\nResponse text: {response.text}"
            raise Exception(error_msg)

        with open(output_path, "wb") as f:
            for chunk in response.iter_content(chunk_size=8192):
                f.write(chunk)

        print(f"Media downloaded to: {output_path}")
        return output_path

from .base_api import WAAPI


class TemplateAPI(WAAPI):
    """
    Gupshup Partner API for WhatsApp template operations.

    This API handles:
    - Template creation and submission (legacy - use META Direct API instead)
    - Template status checking
    - Media upload for templates
    - Template message sending
    - Template sync with META (for META Direct API integration)

    Base URL: https://partner.gupshup.io/partner/app/{appId}/

    Required credentials:
    - appId: Gupshup application ID
    - token: Partner app token (Authorization header)
    """

    # =========================================================================
    # URL Properties
    # =========================================================================

    @property
    def _apply_for_templates(self):
        """URL for template creation (legacy)"""
        return f"{self.BASE_URL}{self.appId}/templates"

    @property
    def _templates_base(self):
        """Base URL for template operations"""
        return f"{self.BASE_URL}{self.appId}/templates"

    @property
    def _template_sync(self):
        """URL for triggering template sync with META"""
        return f"{self.BASE_URL}{self.appId}/template/sync"

    @property
    def upload_media_to_whatsapp(self):
        return f"{self.BASE_URL}{self.appId}/upload/media"

    @property
    def upload_media_to_whatsapp_for_mediaId(self):
        return f"{self.BASE_URL}{self.appId}/media"

    @property
    def send_template_message(self):
        return f"{self.BASE_URL}{self.appId}/v3/message"

    @property
    def send_marketing_template_message(self):
        return f"{self.BASE_URL}{self.appId}/onboarding/marketing/msg"

    # =========================================================================
    # Template CRUD Operations
    # =========================================================================

    def apply_for_template(self, data: dict):
        url = self._apply_for_templates
        request_data = {"method": "POST", "url": url, "headers": self.headers, "data": data}
        return self.make_request(request_data)

    def upload_media(self, file_path: str, file_type: str = None, required_handle_id: bool = True):
        """
        Upload a media file to the configured WhatsApp upload endpoint.
        This method uploads a file as multipart/form-data to one of two endpoints
        determined by `required_handle_id` and returns the parsed JSON response.
        If `file_type` is not provided, the MIME type is auto-detected using
        mimetypes.guess_type() with a small extension-to-MIME fallback map.
        Behavior and side effects:
        - Chooses upload URL based on `required_handle_id`:
            - True  -> self.upload_media_to_whatsapp
            - False -> self.upload_media_to_whatsapp_for_mediaId
        - Sets an Authorization header using `self.token`.
        - Sends multipart/form-data with:
            - file field name: 'file' (opened in binary mode)
            - form field: 'file_type' (MIME type string)
        - Prints a curl-equivalent command for debugging and prints the parsed response.
        - Uses requests.post() to perform the upload.
        Parameters:
        - file_path (str): Path to the file to upload. The file is opened in binary mode.
        - file_type (str | None, optional): MIME type of the file (e.g. 'image/jpeg').
            If omitted or None, the method will attempt to auto-detect it from the file
            extension or use a sensible fallback. Defaults to None.
        - required_handle_id (bool, optional): Whether to use the endpoint that
            requires a handle id. If True, uses self.upload_media_to_whatsapp; if False,
            uses self.upload_media_to_whatsapp_for_mediaId. Defaults to True.
        Returns:
        - dict: Parsed JSON response from the upload endpoint on success.
        Raises:
        - Exception: If the HTTP response status code is not 200 or 201. The exception
            message will include the status code and any JSON/text response returned by
            the server.
        Notes:
        - The object (self) is expected to provide:
            - self.token: authorization token string to be placed in the Authorization header.
            - self.upload_media_to_whatsapp and self.upload_media_to_whatsapp_for_mediaId: URLs/strings.
        - Requests will automatically set the appropriate Content-Type boundary for multipart
            uploads; do not set Content-Type manually when using requests' files parameter.
        """

        import mimetypes
        import os

        import requests

        url = self.upload_media_to_whatsapp if required_handle_id else self.upload_media_to_whatsapp_for_mediaId

        # Auto-detect file type if not provided
        if not file_type:
            file_type, _ = mimetypes.guess_type(file_path)
            if not file_type:
                # Default fallback based on extension
                ext = os.path.splitext(file_path)[1].lower()
                mime_map = {
                    ".jpg": "image/jpeg",
                    ".jpeg": "image/jpeg",
                    ".png": "image/png",
                    ".mp4": "video/mp4",
                    ".pdf": "application/pdf",
                    ".ogg": "audio/ogg",
                    ".mp3": "audio/mpeg",
                }
                file_type = mime_map.get(ext, "application/octet-stream")

        # Prepare headers - only Authorization, no Content-Type (requests handles multipart)
        headers = {"Authorization": self.token}

        # Open file and prepare multipart form data matching curl --form format
        with open(file_path, "rb") as f:
            # --form 'file=@"{{FILE_PATH}}"' → files dict with file handle
            files = {"file": f}
            # --form 'file_type="{{FILE_TYPE}}"' → data dict
            data = {"file_type": file_type}

            # Print curl equivalent for debugging
            print("=" * 80)
            print("EQUIVALENT CURL COMMAND FOR MEDIA UPLOAD:")
            print("=" * 80)
            print(f"curl --location --request POST '{url}' \\")
            print(f"  --header 'Authorization: {self.token}' \\")
            print(f"  --form 'file_type=\"{file_type}\"' \\")
            print(f"  --form 'file=@\"{file_path}\"'")
            print("=" * 80)

            response = requests.post(url, headers=headers, files=files, data=data)

        if response.status_code not in [200, 201]:
            error_msg = f"Media upload failed with status code {response.status_code}"
            try:
                error_details = response.json()
                error_msg += f"\nResponse: {error_details}"
            except:
                error_msg += f"\nResponse text: {response.text}"
            raise Exception(error_msg)

        result = response.json()

        print(f"Media upload response: {result}")

        return result

    def upload_media_from_file_object(self, file_obj, filename: str, file_type: str = None):
        """
        Uploads a media file object to WhatsApp (Gupshup) using a multipart/form-data POST request.

        Matches curl format:
        curl --location --request POST 'https://partner.gupshup.io/partner/app/{{APP_ID}}/upload/media' \
            --header 'Authorization: {{PARTNER_APP_TOKEN}}' \
            --form 'file_type="{{FILE_TYPE}}"' \
            --form 'file=@"{{FILE_PATH}}"'

        Args:
            file_obj: File-like object (e.g., InMemoryUploadedFile from Django).
            filename (str): The name of the file.
            file_type (str, optional): The MIME type of the file. If not provided, it will be auto-detected.

        Returns:
            dict: Response containing 'handleId' on success.
        """
        import mimetypes
        import os

        import requests

        url = self.upload_media_to_whatsapp

        # Auto-detect file type if not provided
        if not file_type:
            file_type, _ = mimetypes.guess_type(filename)
            if not file_type:
                ext = os.path.splitext(filename)[1].lower()
                mime_map = {
                    ".jpg": "image/jpeg",
                    ".jpeg": "image/jpeg",
                    ".png": "image/png",
                    ".mp4": "video/mp4",
                    ".pdf": "application/pdf",
                    ".ogg": "audio/ogg",
                    ".mp3": "audio/mpeg",
                }
                file_type = mime_map.get(ext, "application/octet-stream")

        # Prepare headers - only Authorization
        headers = {"Authorization": self.token}

        # --form 'file=@"{{FILE_PATH}}"' → files dict with (filename, content)
        files = {"file": (filename, file_obj)}
        # --form 'file_type="{{FILE_TYPE}}"' → data dict
        data = {"file_type": file_type}

        print("=" * 80)
        print("EQUIVALENT CURL COMMAND FOR MEDIA UPLOAD:")
        print("=" * 80)
        print(f"curl --location --request POST '{url}' \\")
        print(f"  --header 'Authorization: {self.token}' \\")
        print(f"  --form 'file_type=\"{file_type}\"' \\")
        print(f"  --form 'file=@\"{filename}\"'")
        print("=" * 80)

        response = requests.post(url, headers=headers, files=files, data=data)

        if response.status_code not in [200, 201]:
            error_msg = f"Media upload failed with status code {response.status_code}"
            try:
                error_details = response.json()
                error_msg += f"\nResponse: {error_details}"
            except:
                error_msg += f"\nResponse text: {response.text}"
            raise Exception(error_msg)

        result = response.json()
        print(f"Media upload response: {result}")
        return result

    def send_template(self, data: dict, is_marketing: bool = False):
        url = self.send_template_message if not is_marketing else self.send_marketing_template_message
        request_data = {"method": "POST", "url": url, "headers": self.headers, "data": data}
        return self.make_json_request(request_data)

    def get_template_status(self, template_id: str):
        """
        Get template status from Gupshup API by template ID.

        Args:
            template_id (str): The Gupshup template ID to check status for

        Returns:
            dict: Template status response from Gupshup API
        """
        url = f"{self.BASE_URL}{self.appId}/templates/{template_id}"
        request_data = {"method": "GET", "url": url, "headers": self.headers}
        return self.make_request(request_data)

    # =========================================================================
    # Template Sync Operations (for META Direct API integration)
    # =========================================================================

    def sync_templates_with_meta(self) -> dict:
        """
        Trigger Gupshup to sync templates with META's database.

        Call this after creating templates via META Direct API to ensure
        Gupshup has the latest template data.

        Endpoint: GET /partner/app/{appId}/template/sync

        Returns:
            dict: Sync response from Gupshup

        Raises:
            Exception: If sync request fails
        """
        import requests

        url = self._template_sync
        headers = {**self.headers, "accept": "application/json"}

        # Generate curl for debugging
        curl_cmd = f"curl -X GET '{url}' -H 'Authorization: {self.token}' -H 'accept: application/json'"
        self._last_curl_command = curl_cmd
        print(f"[Gupshup Sync] {curl_cmd}")

        response = requests.get(url, headers=headers)

        if response.status_code not in [200, 201, 202]:
            error_msg = f"Template sync failed with status {response.status_code}"
            try:
                error_data = response.json()
                error_msg += f": {error_data}"
            except:
                error_msg += f": {response.text}"
            raise Exception(error_msg)

        return response.json() if response.text else {"status": "success"}

    def get_template_by_element_name(self, element_name: str) -> dict:
        """
        Get template from Gupshup by element name.

        Endpoint: GET /partner/app/{appId}/templates?elementName={element_name}

        Args:
            element_name: The template's unique element name

        Returns:
            dict: Template data or response containing templates list
        """
        import requests

        url = self._templates_base
        params = {"elementName": element_name}

        # Generate curl for debugging
        curl_cmd = f"curl -X GET '{url}?elementName={element_name}' -H 'Authorization: {self.token}'"
        self._last_curl_command = curl_cmd
        print(f"[Gupshup GetTemplate] {curl_cmd}")

        response = requests.get(url, headers=self.headers, params=params)

        if response.status_code != 200:
            print(f"[Gupshup] Failed to fetch template: {response.status_code} - {response.text}")
            return {}

        return response.json()

    def get_all_templates(self, status: str = None, page: int = None, limit: int = None) -> dict:
        """
        Get all templates from Gupshup.

        Endpoint: GET /partner/app/{appId}/templates

        Args:
            status: Optional filter - APPROVED, PENDING, REJECTED
            page: Optional page number for pagination
            limit: Optional limit per page

        Returns:
            dict: Response containing templates list
        """
        import requests

        url = self._templates_base
        params = {}

        if status:
            params["status"] = status
        if page is not None:
            params["page"] = page
        if limit is not None:
            params["limit"] = limit

        response = requests.get(url, headers=self.headers, params=params if params else None)

        if response.status_code != 200:
            print(f"[Gupshup] Failed to fetch templates: {response.status_code}")
            return {"templates": []}

        return response.json()

    def get_template_by_id(self, template_id: str) -> dict:
        """
        Get template from Gupshup by template ID.

        Endpoint: GET /partner/app/{appId}/templates/{templateId}

        Args:
            template_id: Gupshup's internal template ID

        Returns:
            dict: Template data
        """
        import requests

        url = f"{self._templates_base}/{template_id}"

        response = requests.get(url, headers=self.headers)

        if response.status_code != 200:
            print(f"[Gupshup] Failed to fetch template {template_id}: {response.status_code}")
            return {}

        return response.json()

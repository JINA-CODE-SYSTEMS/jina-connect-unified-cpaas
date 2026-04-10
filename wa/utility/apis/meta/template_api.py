from typing import Optional

from .waba import WABAAPI


class TemplateAPI(WABAAPI):

    # Required for message sending (POST /{phone_number_id}/messages).
    # Not needed for template CRUD which uses waba_id.
    phone_number_id: Optional[str] = None

    @property
    def _apply_for_templates(self):
        return f"{self.BASE_URL}{self.waba_id}/message_templates"
    
    # @property
    # def upload_media_to_whatsapp(self):
    #     return f"{self.BASE_URL}{self.appId}/upload/media"
    
    # @property
    # def upload_media_to_whatsapp_for_mediaId(self):
    #     return f"{self.BASE_URL}{self.appId}/media"
    
    @property
    def send_template_message(self):
        """Cloud API message-sending endpoint: POST /{phone_number_id}/messages"""
        if not self.phone_number_id:
            raise ValueError(
                "phone_number_id is required for sending messages. "
                "Pass it when constructing TemplateAPI."
            )
        return f"{self.BASE_URL}{self.phone_number_id}/messages"
    
    @property
    def send_marketing_template_message(self):
        """META Cloud API uses the same /{phone_number_id}/messages endpoint
        for all message types (marketing category is on the template itself)."""
        return self.send_template_message

    def apply_for_template(self, data: dict):
        """
        Create a new template via META Direct API.
        
        Uses JSON body (not form-urlencoded) as required by META's Graph API.
        
        Args:
            data: Template payload with name, language, category, components
            
        Returns:
            dict: Response from META API with template_id on success
        """
        url = self._apply_for_templates
        request_data = {
            "method": "POST",
            "url": url,
            "headers": self.json_headers,
            "data": data
        }
        # Use make_json_request for JSON body (META API requirement)
        return self.make_json_request(request_data)

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
                    '.jpg': 'image/jpeg',
                    '.jpeg': 'image/jpeg',
                    '.png': 'image/png',
                    '.mp4': 'video/mp4',
                    '.pdf': 'application/pdf',
                    '.ogg': 'audio/ogg',
                    '.mp3': 'audio/mpeg',
                }
                file_type = mime_map.get(ext, 'application/octet-stream')
        
        # Prepare headers - only Authorization, no Content-Type (requests handles multipart)
        headers = {
            "Authorization": self.token
        }
        
        # Open file and prepare multipart form data matching curl --form format
        with open(file_path, 'rb') as f:
            # --form 'file=@"{{FILE_PATH}}"' → files dict with file handle
            files = {
                'file': f
            }
            # --form 'file_type="{{FILE_TYPE}}"' → data dict
            data = {
                'file_type': file_type
            }
            
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
                    '.jpg': 'image/jpeg',
                    '.jpeg': 'image/jpeg',
                    '.png': 'image/png',
                    '.mp4': 'video/mp4',
                    '.pdf': 'application/pdf',
                    '.ogg': 'audio/ogg',
                    '.mp3': 'audio/mpeg',
                }
                file_type = mime_map.get(ext, 'application/octet-stream')
        
        # Prepare headers - only Authorization
        headers = {
            "Authorization": self.token
        }
        
        # --form 'file=@"{{FILE_PATH}}"' → files dict with (filename, content)
        files = {
            'file': (filename, file_obj)
        }
        # --form 'file_type="{{FILE_TYPE}}"' → data dict
        data = {
            'file_type': file_type
        }
        
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
        """Send a template message via META Cloud API.

        Both marketing and non-marketing messages use the same
        ``POST /{phone_number_id}/messages`` endpoint.  The template's
        category (MARKETING / UTILITY / AUTHENTICATION) is already encoded
        in the template itself.

        Requires ``phone_number_id`` to be set on this instance.
        """
        url = self.send_template_message  # same endpoint regardless of is_marketing
        request_data = {
            "method": "POST",
            "url": url,
            "headers": self.json_headers,
            "data": data
        }
        return self.make_json_request(request_data)

    def get_template_status(self, template_id: str):
        """
        Get template status from META Graph API by template ID.
        
        Args:
            template_id (str): The META template ID to check status for
            
        Returns:
            dict: Template status response from META API
        """
        # META Graph API endpoint for getting template details
        url = f"{self.BASE_URL}{template_id}"
        request_data = {
            "method": "GET",
            "url": url,
            "headers": self.json_headers
        }
        return self.make_json_request(request_data)

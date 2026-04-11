from pydantic import BaseModel


class WAAPI(BaseModel):
    """
    Base class for WhatsApp API interactions via Gupshup.
    Provides methods for making HTTP requests and handling responses.
    Also includes a utility to print equivalent curl commands for debugging.
    Attributes:
        BASE_URL (str): Base URL for Gupshup API. Default is "https://partner.gupshup.io/partner/app/".
        appId (str): Application ID for the Gupshup app.
        token (str): Authorization token for API access.
    """

    BASE_URL: str = "https://partner.gupshup.io/partner/app/"
    appId: str
    token: str

    # Store the last curl command generated
    _last_curl_command: str = ""

    class Config:
        # Allow arbitrary types for Pydantic
        arbitrary_types_allowed = True

    @property
    def last_curl_command(self) -> str:
        """Get the last curl command that was generated."""
        return self._last_curl_command

    @property
    def headers(self):
        return {"Content-Type": "application/x-www-form-urlencoded", "Authorization": f"{self.token}"}

    @property
    def json_headers(self):
        return {"Content-Type": "application/json", "Authorization": f"{self.token}"}

    def make_request(self, request_data: dict) -> dict:
        import json

        import requests

        method = request_data.get("method", "GET")
        url = request_data.get("url")
        headers = request_data.get("headers", {})
        data = request_data.get("data", {})

        # Process data to match curl format exactly
        processed_data = {}
        if data:
            for key, value in data.items():
                if value is None:
                    # Skip None values (curl would not include them)
                    continue
                elif isinstance(value, bool):
                    # Convert booleans to lowercase strings (exactly like curl)
                    processed_data[key] = str(value).lower()
                elif isinstance(value, (list, dict)):
                    # Convert arrays/objects to JSON strings with compact format (like curl)
                    print(f"Processing {key}: {value}")
                    processed_data[key] = json.dumps(value, separators=(",", ":"))
                elif isinstance(value, str) and (
                    value.startswith("[") and value.endswith("]") or value.startswith("{") and value.endswith("}")
                ):
                    # This looks like a JSON string - validate and reformat to ensure compact format
                    print(f"Processing {key}: {value} for []")
                    try:
                        # Parse and re-dump to ensure consistent formatting
                        parsed = json.loads(value)
                        processed_data[key] = json.dumps(parsed, separators=(",", ":"))
                    except json.JSONDecodeError:
                        # If it's not valid JSON, keep as-is
                        processed_data[key] = str(value)
                elif hasattr(value, "isoformat"):
                    # Handle datetime objects
                    processed_data[key] = value.isoformat()
                else:
                    # Keep everything else as-is but ensure it's a string
                    processed_data[key] = str(value)
        else:
            processed_data = data

        # Generate equivalent curl request for debug
        curl_cmd = self._generate_curl_equivalent(method, url, headers, processed_data)
        self._last_curl_command = curl_cmd
        print(curl_cmd)

        if method == "GET":
            response = requests.get(url, headers=headers, params=processed_data)
        elif method == "POST":
            response = requests.post(url, headers=headers, data=processed_data)
        elif method == "PUT":
            response = requests.put(url, headers=headers, data=processed_data)
        elif method == "DELETE":
            response = requests.delete(url, headers=headers, data=processed_data)
        else:
            raise ValueError(f"Unsupported HTTP method: {method}")

        # Enhanced error reporting
        if response.status_code not in [200, 201, 204]:
            error_msg = f"Request failed with status code {response.status_code}"
            try:
                error_details = response.json()
                error_msg += f"\nResponse: {json.dumps(error_details, indent=2)}"
            except:
                error_msg += f"\nResponse text: {response.text}"

            # Print the request details for debugging
            print("=" * 80)
            print("REQUEST DEBUG INFO:")
            print("=" * 80)
            print(f"URL: {url}")
            print(f"Method: {method}")
            print(f"Headers: {headers}")
            print(f"Data sent: {processed_data}")
            print("=" * 80)

            raise Exception(error_msg)

        # 204 No Content — return an empty success dict (common for DELETE)
        if response.status_code == 204 or not response.text:
            return {"status": "success"}

        return response.json()

    def make_json_request(self, request_data: dict):
        """
        Make HTTP request with JSON body (Content-Type: application/json).

        Args:
            request_data (dict): Dictionary containing:
                - method (str): HTTP method (GET, POST, PUT, DELETE)
                - url (str): Request URL
                - headers (dict, optional): Additional headers
                - data (dict, optional): JSON payload

        Returns:
            dict: JSON response from the API
        """
        import json

        import requests

        method = request_data.get("method", "GET")
        url = request_data.get("url")
        headers = request_data.get("json_headers", self.json_headers)
        data = request_data.get("data", {})

        # Generate equivalent curl request for debug
        curl_cmd = self._generate_curl_json_equivalent(method, url, headers, data)
        self._last_curl_command = curl_cmd
        print(curl_cmd)

        if method == "GET":
            response = requests.get(url, headers=headers, params=data)
        elif method == "POST":
            response = requests.post(url, headers=headers, json=data)
        elif method == "PUT":
            response = requests.put(url, headers=headers, json=data)
        elif method == "DELETE":
            response = requests.delete(url, headers=headers, json=data)
        else:
            raise ValueError(f"Unsupported HTTP method: {method}")

        # Enhanced error reporting
        if response.status_code not in [200, 201]:
            error_msg = f"Request failed with status code {response.status_code}"
            try:
                error_details = response.json()
                error_msg += f"\nResponse: {json.dumps(error_details, indent=2)}"
            except:
                error_msg += f"\nResponse text: {response.text}"

            # Print the request details for debugging
            print("=" * 80)
            print("REQUEST DEBUG INFO:")
            print("=" * 80)
            print(f"URL: {url}")
            print(f"Method: {method}")
            print(f"Headers: {headers}")
            print(f"JSON data sent: {json.dumps(data, indent=2)}")
            print("=" * 80)

            raise Exception(error_msg)

        return response.json()

    def _generate_curl_equivalent(self, method: str, url: str, headers: dict, data: dict) -> str:
        """
        Generate equivalent curl command for debugging purposes.
        Uses --data-urlencode for POST/PUT/PATCH as per Gupshup API requirements.

        Returns:
            str: The full curl command string with formatting
        """
        curl_command = f'curl -X {method} "{url}"'

        # Add headers
        for key, value in headers.items():
            curl_command += f' \\\n  -H "{key}: {value}"'

        # Add data based on method
        if method in ["POST", "PUT", "PATCH"] and data:
            if isinstance(data, dict):
                # Use --data-urlencode for form-encoded data (Gupshup requirement)
                for key, value in data.items():
                    # Handle None values and convert to string
                    if value is None:
                        continue
                    elif isinstance(value, str) and (
                        value.startswith("[") and value.endswith("]") or value.startswith("{") and value.endswith("}")
                    ):
                        # This is likely a JSON string - use it directly with proper escaping
                        # No need to double-escape for --data-urlencode as it handles URL encoding
                        curl_command += f' \\\n  --data-urlencode "{key}={value}"'
                    else:
                        curl_command += f' \\\n  --data-urlencode "{key}={value}"'
            else:
                # Raw data
                curl_command += f" \\\n  --data-urlencode '{data}'"
        elif method == "GET" and data:
            # Query parameters for GET requests
            params = "&".join([f"{key}={value}" for key, value in data.items() if value is not None])
            if params:
                separator = "?" if "?" not in url else "&"
                curl_command = f'curl -X {method} "{url}{separator}{params}"'
                # Re-add headers
                for key, value in headers.items():
                    curl_command += f' \\\n  -H "{key}: {value}"'

        output = "=" * 80 + "\n"
        output += "EQUIVALENT CURL COMMAND FOR DEBUG:\n"
        output += "=" * 80 + "\n"
        output += curl_command + "\n"
        output += "=" * 80

        return output

    def _generate_curl_json_equivalent(self, method: str, url: str, headers: dict, data: dict) -> str:
        """
        Generate equivalent curl command for JSON requests for debugging purposes.

        Returns:
            str: The full curl command string with formatting
        """
        import json

        curl_command = f'curl -X {method} "{url}"'

        # Add headers
        for key, value in headers.items():
            curl_command += f' \\\n  -H "{key}: {value}"'

        # Add JSON data based on method
        if method in ["POST", "PUT", "PATCH", "DELETE"] and data:
            json_str = json.dumps(data, indent=2)
            # Escape quotes for shell
            json_str_escaped = json_str.replace('"', '\\"')
            curl_command += f' \\\n  -d "{json_str_escaped}"'
        elif method == "GET" and data:
            # Query parameters for GET requests
            params = "&".join([f"{key}={value}" for key, value in data.items() if value is not None])
            if params:
                separator = "?" if "?" not in url else "&"
                curl_command = f'curl -X {method} "{url}{separator}{params}"'
                # Re-add headers
                for key, value in headers.items():
                    curl_command += f' \\\n  -H "{key}: {value}"'

        output = "=" * 80 + "\n"
        output += "EQUIVALENT CURL COMMAND (JSON) FOR DEBUG:\n"
        output += "=" * 80 + "\n"
        output += curl_command + "\n"
        output += "=" * 80

        return output

    # Keep old method names for backward compatibility
    def _print_curl_equivalent(self, method: str, url: str, headers: dict, data: dict):
        """Deprecated: Use _generate_curl_equivalent instead."""
        print(self._generate_curl_equivalent(method, url, headers, data))

    def _print_curl_json_equivalent(self, method: str, url: str, headers: dict, data: dict):
        """Deprecated: Use _generate_curl_json_equivalent instead."""
        print(self._generate_curl_json_equivalent(method, url, headers, data))

    def _submit_form(self, form_data: dict):
        import requests

        url = form_data.get("url")
        headers = form_data.get("headers", {})
        data = form_data.get("file_type", {})
        files = form_data.get("file_path", {})

        response = requests.post(url, headers=headers, data=data, files=files)

        if response.status_code != 200 and response.status_code != 201:
            raise Exception(f"Form submission failed with status code {response.status_code}: {response.text}")

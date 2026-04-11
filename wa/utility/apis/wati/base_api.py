"""
WATI Base API Client

Base class for all WATI API interactions.
WATI uses a per-tenant API endpoint and Bearer token authentication.

API Reference: https://docs.wati.io/reference

Authentication:
    All requests require an ``Authorization: Bearer <token>`` header.
    The token can be obtained from the WATI dashboard or via the rotate-token API.

Base URL pattern:
    https://{api_endpoint}/api/v1/   (v1 endpoints)
    https://{api_endpoint}/api/v2/   (v2 endpoints)

Usage:
    api = WAAPI(api_endpoint="your-tenant.wati.io", token="your-bearer-token")
    response = api.make_json_request({
        "method": "GET",
        "url": api.v1_url("getMessageTemplates"),
    })
"""

from pydantic import BaseModel, Field


class WAAPI(BaseModel):
    """
    Base class for WhatsApp API interactions via WATI.

    Provides methods for making HTTP requests and handling responses.
    Also includes a utility to print equivalent curl commands for debugging.

    Attributes:
        api_endpoint (str): WATI tenant API endpoint (e.g., "your-tenant.wati.io").
        token (str): Bearer token for API access.
    """

    api_endpoint: str = Field(..., description="WATI tenant API endpoint (e.g., 'your-tenant.wati.io')")
    token: str = Field(..., description="Bearer token for WATI API authentication")

    # Store the last curl command generated
    _last_curl_command: str = ""

    class Config:
        arbitrary_types_allowed = True

    # =========================================================================
    # URL Builders
    # =========================================================================

    @property
    def _base_url_v1(self) -> str:
        """Base URL for WATI v1 API."""
        return f"https://{self.api_endpoint}/api/v1"

    @property
    def _base_url_v2(self) -> str:
        """Base URL for WATI v2 API."""
        return f"https://{self.api_endpoint}/api/v2"

    def v1_url(self, path: str) -> str:
        """Construct a full v1 API URL."""
        return f"{self._base_url_v1}/{path.lstrip('/')}"

    def v2_url(self, path: str) -> str:
        """Construct a full v2 API URL."""
        return f"{self._base_url_v2}/{path.lstrip('/')}"

    # =========================================================================
    # Headers
    # =========================================================================

    @property
    def last_curl_command(self) -> str:
        """Get the last curl command that was generated."""
        return self._last_curl_command

    @property
    def headers(self):
        """Standard headers with Bearer token auth (form-urlencoded)."""
        return {
            "Content-Type": "application/x-www-form-urlencoded",
            "Authorization": f"Bearer {self.token}",
            "Accept": "application/json",
        }

    @property
    def json_headers(self):
        """JSON headers with Bearer token auth."""
        return {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.token}",
            "Accept": "application/json",
        }

    # =========================================================================
    # HTTP Methods
    # =========================================================================

    def make_request(self, request_data: dict) -> dict:
        """
        Make an HTTP request with form-urlencoded body.

        Args:
            request_data (dict): Dictionary containing:
                - method (str): HTTP method (GET, POST, PUT, DELETE)
                - url (str): Request URL
                - headers (dict, optional): Additional headers
                - data (dict, optional): Form data payload

        Returns:
            dict: JSON response from the API
        """
        import json

        import requests

        method = request_data.get("method", "GET")
        url = request_data.get("url")
        headers = request_data.get("headers", self.headers)
        data = request_data.get("data", {})
        params = request_data.get("params", {})

        # Process data to match curl format exactly
        processed_data = {}
        if data:
            for key, value in data.items():
                if value is None:
                    continue
                elif isinstance(value, bool):
                    processed_data[key] = str(value).lower()
                elif isinstance(value, (list, dict)):
                    processed_data[key] = json.dumps(value, separators=(",", ":"))
                elif isinstance(value, str) and (
                    (value.startswith("[") and value.endswith("]")) or (value.startswith("{") and value.endswith("}"))
                ):
                    try:
                        parsed = json.loads(value)
                        processed_data[key] = json.dumps(parsed, separators=(",", ":"))
                    except json.JSONDecodeError:
                        processed_data[key] = str(value)
                elif hasattr(value, "isoformat"):
                    processed_data[key] = value.isoformat()
                else:
                    processed_data[key] = str(value)
        else:
            processed_data = data

        # Generate equivalent curl request for debug
        curl_cmd = self._generate_curl_equivalent(method, url, headers, processed_data)
        self._last_curl_command = curl_cmd
        print(curl_cmd)

        if method == "GET":
            response = requests.get(url, headers=headers, params=params or processed_data, timeout=30)
        elif method == "POST":
            response = requests.post(url, headers=headers, data=processed_data, params=params, timeout=30)
        elif method == "PUT":
            response = requests.put(url, headers=headers, data=processed_data, params=params, timeout=30)
        elif method == "DELETE":
            response = requests.delete(url, headers=headers, data=processed_data, params=params, timeout=30)
        else:
            raise ValueError(f"Unsupported HTTP method: {method}")

        # Enhanced error reporting
        if response.status_code not in [200, 201]:
            error_msg = f"Request failed with status code {response.status_code}"
            try:
                error_details = response.json()
                error_msg += f"\nResponse: {json.dumps(error_details, indent=2)}"
            except Exception:
                error_msg += f"\nResponse text: {response.text}"

            print("=" * 80)
            print("REQUEST DEBUG INFO:")
            print("=" * 80)
            print(f"URL: {url}")
            print(f"Method: {method}")
            print(f"Headers: {headers}")
            print(f"Data sent: {processed_data}")
            print("=" * 80)

            raise Exception(error_msg)

        return response.json()

    def make_json_request(self, request_data: dict) -> dict:
        """
        Make HTTP request with JSON body (Content-Type: application/json).

        This is the primary method for WATI API calls since WATI predominantly
        uses JSON request/response format.

        Args:
            request_data (dict): Dictionary containing:
                - method (str): HTTP method (GET, POST, PUT, DELETE)
                - url (str): Request URL
                - headers (dict, optional): Additional headers
                - data (dict, optional): JSON payload
                - params (dict, optional): Query parameters

        Returns:
            dict: JSON response from the API
        """
        import json

        import requests

        method = request_data.get("method", "GET")
        url = request_data.get("url")
        headers = request_data.get("headers", self.json_headers)
        data = request_data.get("data", {})
        params = request_data.get("params", {})

        # Generate equivalent curl request for debug
        curl_cmd = self._generate_curl_json_equivalent(method, url, headers, data)
        self._last_curl_command = curl_cmd
        print(curl_cmd)

        if method == "GET":
            response = requests.get(url, headers=headers, params=params or data, timeout=30)
        elif method == "POST":
            response = requests.post(url, headers=headers, json=data, params=params, timeout=30)
        elif method == "PUT":
            response = requests.put(url, headers=headers, json=data, params=params, timeout=30)
        elif method == "DELETE":
            response = requests.delete(url, headers=headers, json=data, params=params, timeout=30)
        else:
            raise ValueError(f"Unsupported HTTP method: {method}")

        # Enhanced error reporting
        if response.status_code not in [200, 201]:
            error_msg = f"Request failed with status code {response.status_code}"
            try:
                error_details = response.json()
                error_msg += f"\nResponse: {json.dumps(error_details, indent=2)}"
            except Exception:
                error_msg += f"\nResponse text: {response.text}"

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

    # =========================================================================
    # Curl Debug Helpers
    # =========================================================================

    def _generate_curl_equivalent(self, method: str, url: str, headers: dict, data: dict) -> str:
        """Generate equivalent curl command for form-encoded requests."""
        curl_command = f'curl -X {method} "{url}"'

        for key, value in headers.items():
            curl_command += f' \\\n  -H "{key}: {value}"'

        if method in ["POST", "PUT", "PATCH"] and data:
            if isinstance(data, dict):
                for key, value in data.items():
                    if value is None:
                        continue
                    curl_command += f' \\\n  --data-urlencode "{key}={value}"'
            else:
                curl_command += f" \\\n  --data-urlencode '{data}'"
        elif method == "GET" and data:
            params = "&".join([f"{key}={value}" for key, value in data.items() if value is not None])
            if params:
                separator = "?" if "?" not in url else "&"
                curl_command = f'curl -X {method} "{url}{separator}{params}"'
                for key, value in headers.items():
                    curl_command += f' \\\n  -H "{key}: {value}"'

        output = "=" * 80 + "\n"
        output += "EQUIVALENT CURL COMMAND (WATI) FOR DEBUG:\n"
        output += "=" * 80 + "\n"
        output += curl_command + "\n"
        output += "=" * 80

        return output

    def _generate_curl_json_equivalent(self, method: str, url: str, headers: dict, data: dict) -> str:
        """Generate equivalent curl command for JSON requests."""
        import json

        curl_command = f'curl -X {method} "{url}"'

        for key, value in headers.items():
            curl_command += f' \\\n  -H "{key}: {value}"'

        if method in ["POST", "PUT", "PATCH", "DELETE"] and data:
            json_str = json.dumps(data, indent=2)
            json_str_escaped = json_str.replace('"', '\\"')
            curl_command += f' \\\n  -d "{json_str_escaped}"'
        elif method == "GET" and data:
            params = "&".join([f"{key}={value}" for key, value in data.items() if value is not None])
            if params:
                separator = "?" if "?" not in url else "&"
                curl_command = f'curl -X {method} "{url}{separator}{params}"'
                for key, value in headers.items():
                    curl_command += f' \\\n  -H "{key}: {value}"'

        output = "=" * 80 + "\n"
        output += "EQUIVALENT CURL COMMAND (WATI JSON) FOR DEBUG:\n"
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

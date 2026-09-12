import logging

from pydantic import BaseModel

from wa.utility.apis.curl_debug import build_form_curl, build_json_curl, log_curl, log_request_failure

logger = logging.getLogger(__name__)


class WAAPI(BaseModel):
    """
    Base class for WhatsApp API interactions via META DIRECT.
    Provides methods for making HTTP requests and handling responses.
    Also includes a utility to print equivalent curl commands for debugging.
    Attributes:
        token (str): Authorization token for API access.
        version (str): API version to use in the base URL.
    """

    _BASE_URL: str = "https://graph.facebook.com/{version}/"
    token: str
    version: str = "v24.0"

    # Store the last curl command generated
    _last_curl_command: str = ""

    class Config:
        # Allow arbitrary types for Pydantic
        arbitrary_types_allowed = True

    @property
    def BASE_URL(self):
        """Construct the full base URL with version."""
        return self._BASE_URL.format(version=self.version)

    @property
    def last_curl_command(self) -> str:
        """Get the last curl command that was generated."""
        return self._last_curl_command

    @property
    def headers(self):
        return {"Content-Type": "application/x-www-form-urlencoded", "Authorization": f"Bearer {self.token}"}

    @property
    def json_headers(self):
        return {"Content-Type": "application/json", "Authorization": f"Bearer {self.token}"}

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
                    logger.debug("Processing %s: %s", key, value)
                    processed_data[key] = json.dumps(value, separators=(",", ":"))
                elif isinstance(value, str) and (
                    value.startswith("[") and value.endswith("]") or value.startswith("{") and value.endswith("}")
                ):
                    # This looks like a JSON string - validate and reformat to ensure compact format
                    logger.debug("Processing %s: %s for []", key, value)
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
        log_curl(logger, curl_cmd)

        if method == "GET":
            response = requests.get(url, headers=headers, params=processed_data, timeout=30)
        elif method == "POST":
            response = requests.post(url, headers=headers, data=processed_data, timeout=30)
        elif method == "PUT":
            response = requests.put(url, headers=headers, data=processed_data, timeout=30)
        elif method == "DELETE":
            response = requests.delete(url, headers=headers, data=processed_data, timeout=30)
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

            # The masked curl command logged above already carries the headers;
            # this adds nothing a second, separately-masked copy of them would.
            log_request_failure(
                logger,
                method=method,
                url=url,
                body=processed_data,
                secrets=(self.token,),
            )

            # The response rides out on the exception, ``requests.HTTPError``
            # style: this raise is the last place the headers exist, and a 429's
            # Retry-After is what lets the send path delay by the interval the
            # provider asked for instead of re-queueing blind (#271).
            failure = Exception(error_msg)
            failure.response = response
            raise failure

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
        log_curl(logger, curl_cmd)

        if method == "GET":
            response = requests.get(url, headers=headers, params=data, timeout=30)
        elif method == "POST":
            response = requests.post(url, headers=headers, json=data, timeout=30)
        elif method == "PUT":
            response = requests.put(url, headers=headers, json=data, timeout=30)
        elif method == "DELETE":
            response = requests.delete(url, headers=headers, json=data, timeout=30)
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

            # The masked curl command logged above already carries the headers;
            # this adds nothing a second, separately-masked copy of them would.
            log_request_failure(
                logger,
                method=method,
                url=url,
                body=json.dumps(data, indent=2),
                body_label="JSON data sent",
                secrets=(self.token,),
            )

            # The response rides out on the exception, ``requests.HTTPError``
            # style: this raise is the last place the headers exist, and a 429's
            # Retry-After is what lets the send path delay by the interval the
            # provider asked for instead of re-queueing blind (#271).
            failure = Exception(error_msg)
            failure.response = response
            raise failure

        return response.json()

    def _generate_curl_equivalent(self, method: str, url: str, headers: dict, data: dict) -> str:
        """Reconstruct a form-encoded request as a *masked* curl command (#336).

        Credential headers come out as ``Bearer [redacted]``: this string is kept
        on ``last_curl_command``, which callers copy into a task result and a
        template's debug blob, so masking anywhere later than here would leave
        those durable paths holding a live token.
        """
        return build_form_curl(
            method,
            url,
            headers,
            data,
            title="EQUIVALENT CURL COMMAND FOR DEBUG:",
            secrets=(self.token,),
        )

    def _generate_curl_json_equivalent(self, method: str, url: str, headers: dict, data: dict) -> str:
        """Reconstruct a JSON request as a *masked* curl command (#336)."""
        return build_json_curl(
            method,
            url,
            headers,
            data,
            title="EQUIVALENT CURL COMMAND (JSON) FOR DEBUG:",
            secrets=(self.token,),
        )

    # Keep old method names for backward compatibility
    def _print_curl_equivalent(self, method: str, url: str, headers: dict, data: dict):
        """Deprecated: Use _generate_curl_equivalent instead."""
        log_curl(logger, self._generate_curl_equivalent(method, url, headers, data))

    def _print_curl_json_equivalent(self, method: str, url: str, headers: dict, data: dict):
        """Deprecated: Use _generate_curl_json_equivalent instead."""
        log_curl(logger, self._generate_curl_json_equivalent(method, url, headers, data))

    def _submit_form(self, form_data: dict):
        import requests

        url = form_data.get("url")
        headers = form_data.get("headers", {})
        data = form_data.get("file_type", {})
        files = form_data.get("file_path", {})

        response = requests.post(url, headers=headers, data=data, files=files, timeout=30)

        if response.status_code != 200 and response.status_code != 201:
            raise Exception(f"Form submission failed with status code {response.status_code}: {response.text}")

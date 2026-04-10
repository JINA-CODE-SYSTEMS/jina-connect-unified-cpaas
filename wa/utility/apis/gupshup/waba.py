from typing import Optional

from wa.utility.apis.gupshup.base_api import WAAPI


class WABAAPI(WAAPI):
    """
    API client for WhatsApp Business Account (WABA) operations.
    This class provides methods to interact with WABA-specific endpoints,
    extending the base WAAPI class functionality.
    Attributes:
        Inherits all attributes from WAAPI parent class.
    Methods:
        get_waba_details(): Retrieves WhatsApp Business Account details.
    Properties:
        _get_waba_details: Constructs the URL endpoint for fetching WABA information.
    """

    regenerate:Optional[bool] = None
    user:Optional[str] = None
    lang:Optional[str] = None


    @property
    def _get_waba_details(self):
        return f"{self.BASE_URL}{self.appId}/waba/info/"
    
    @property
    def _create_new_app(self):
        return f"{self.BASE_URL}"
    
    @property
    def _get_partner_token(self):
        return f"{self.BASE_URL}".replace("app","account/login")
    
    @property
    def _generate_access_token_for_app(self):
        return f"{self.BASE_URL}{self.appId}/token"
    
    @property
    def _generate_esf_link(self):
        return f"{self.BASE_URL}{self.appId}/onboarding/embed/link?regenerate={str(self.regenerate).lower() if self.regenerate is not None else 'false'}&user={self.user if self.user else ''}&lang={self.lang if self.lang else ''}"


    def get_waba_details(self):
        url = self._get_waba_details
        request_data = {
            "method": "GET",
            "url": url,
            "headers": self.headers,
        }
        return self.make_request(request_data)
    
    def create_new_app(self, data: dict):
        url = self._create_new_app
        # Use make_request (form-urlencoded) instead of make_json_request
        # The Gupshup partner app creation API expects application/x-www-form-urlencoded
        request_data = {
            "method": "POST",
            "url": url,
            "headers": {
                "Content-Type": "application/x-www-form-urlencoded",
                "Authorization": self.token
            },
            "data": data
        }
        return self.make_request(request_data)
    
    def get_partner_token(self, data: dict):
        url = self._get_partner_token
        # Use make_request (form-urlencoded) instead of make_json_request
        # The Gupshup partner login API expects application/x-www-form-urlencoded
        request_data = {
            "method": "POST",
            "url": url,
            "headers": {
                "Content-Type": "application/x-www-form-urlencoded",
                "Accept": "application/json"
            },
            "data": data
        }
        return self.make_request(request_data)

    def generate_access_token_for_app(self, data: dict):
        url = self._generate_access_token_for_app
        request_data = {
            "method": "POST",
            "url": url,
            "headers": self.headers,
            "data": data
        }
        return self.make_json_request(request_data)
    
    def generate_esf_link(self, data: dict):
        url = self._generate_esf_link
        # ESF link API is a GET request with 'token' header (not Authorization)
        request_data = {
            "method": "GET",
            "url": url,
            "headers": {
                "token": self.token
            },
            "data": data
        }
        return self.make_request(request_data)
from typing import Optional

from wa.utility.apis.meta.base_api import WAAPI


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

    waba_id: Optional[str] = None


    @property
    def _get_waba_details(self):
        return f"{self.BASE_URL}{self.waba_id}?fields=name,status,currency,country,business_verification_status,onboarding_status,marketing_messages_onboarding_status"
        

    def get_waba_details(self):
        url = self._get_waba_details
        request_data = {
            "method": "GET",
            "url": url,
            "headers": self.headers,
        }
        return self.make_request(request_data)
    
    
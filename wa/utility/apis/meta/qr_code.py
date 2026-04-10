from typing import Optional

from wa.utility.apis.meta.base_api import WAAPI


class QRCodeAPI(WAAPI):
    """
    API client for WhatsApp QR Code operations.
    This class provides methods to interact with QR code-specific endpoints,
    extending the base WAAPI class functionality.
    Attributes:
        Inherits all attributes from WAAPI parent class.
    Methods:
        get_qr_code_details(): Retrieves WhatsApp QR Code details.
    Properties:
        _get_qr_code_details: Constructs the URL endpoint for fetching QR code information.
    """

    waba_id: Optional[str] = None
    generate_qr_image: str = "SVG"


    @property
    def _get_qr_code_details(self):
        return f"{self.BASE_URL}{self.waba_id}/message_qrdls"
    
    def get_qr_code_details(self, message_text: str):
        url = self._get_qr_code_details
        request_data = {
            "method": "POST",
            "url": url,
            "headers": self.json_headers,
            "data": {"generate_qr_image": self.generate_qr_image, "message_text": message_text},
        }
        return self.make_request(request_data)
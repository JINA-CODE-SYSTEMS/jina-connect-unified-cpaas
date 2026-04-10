from typing import Any, Union

from wa.utility.apis.gupshup.base_api import WAAPI
from wa.utility.data_model.gupshup.session_message_base import SessionMessageBase


class SessionMessageAPI(WAAPI):

    @property
    def send_session_message(self):
        return f"{self.BASE_URL}{self.appId}/v3/message"
    
    


    def send_message(self, data: Union[SessionMessageBase, Any]):
        url = self.send_session_message
        request_data = {
            "method": "POST",
            "url": url,
            "headers": self.json_headers,
            "data": data.model_dump(by_alias=True, exclude_none=True) if isinstance(data, SessionMessageBase) else data
        }
        return self.make_request(request_data)
    
    
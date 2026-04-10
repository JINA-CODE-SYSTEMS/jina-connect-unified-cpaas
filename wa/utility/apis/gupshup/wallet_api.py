from wa.utility.apis.gupshup.base_api import WAAPI

class WalletAPI(WAAPI):

    @property
    def _get_wallet_balance_url(self):
        return f"{self.BASE_URL}{self.appId}/wallet/balance"

    def get_wallet_balance(self):
        url = self._get_wallet_balance_url
        request_data = {
            "method": "GET",
            "url": url,
            "headers": self.headers
        }
        return self.make_request(request_data)

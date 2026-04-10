from .base_api import WAAPI


class SubscriptionAPI(WAAPI):

    @property
    def _all_subscription_url(self):
        return f"{self.BASE_URL}{self.appId}/subscription"
    
    @property
    def _single_subscription_url(self):
        return f"{self.BASE_URL}{self.appId}/subscription/{{subscriptionId}}"

    @property
    def _post_subscription_url(self):
        return f"{self.BASE_URL}{self.appId}/subscription"
    
    @property
    def _update_subscription_url(self):
        return f"{self.BASE_URL}{self.appId}/subscription/{{subscriptionId}}"
    
    @property
    def _delete_subscription_url(self):
        return f"{self.BASE_URL}{self.appId}/subscription/{{subscriptionId}}"
    
    @property
    def _delete_all_subscription_url(self):
        return f"{self.BASE_URL}{self.appId}/subscription"
    
    def get_all_subscriptions(self):
        url = self._all_subscription_url
        request_data = {
            "method": "GET",
            "url": url,
            "headers": self.headers
        }
        return self.make_request(request_data)
    
    def get_subscription(self, subscriptionId: str):
        url = self._single_subscription_url.format(subscriptionId=subscriptionId)
        request_data = {
            "method": "GET",
            "url": url,
            "headers": self.headers
        }
        return self.make_request(request_data)
    
    def create_subscription(self, data: dict):
        url = self._post_subscription_url
        request_data = {
            "method": "POST",
            "url": url,
            "headers": self.headers,
            "data": data
        }
        return self.make_request(request_data)
    
    def update_subscription(self, subscriptionId: str, data: dict):
        url = self._update_subscription_url.format(subscriptionId=subscriptionId)
        request_data = {
            "method": "PUT",
            "url": url,
            "headers": self.headers,
            "data": data
        }
        return self.make_request(request_data)
    
    def delete_subscription(self, subscriptionId: str):
        url = self._delete_subscription_url.format(subscriptionId=subscriptionId)
        request_data = {
            "method": "DELETE",
            "url": url,
            "headers": self.headers
        }
        return self.make_request(request_data)
    
    def delete_all_subscriptions(self):
        url = self._delete_all_subscription_url
        request_data = {
            "method": "DELETE",
            "url": url,
            "headers": self.headers
        }
        return self.make_request(request_data)
    

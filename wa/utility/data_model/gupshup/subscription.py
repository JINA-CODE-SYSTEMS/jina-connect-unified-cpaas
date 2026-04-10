from typing import List, Literal

from pydantic import BaseModel


class SubscriptionFormData(BaseModel):
    """
    Represents the form data for creating or updating a Gupshup subscription.
    TEMPLATE: The template events get forwarded to subscriptions if TEMPLATE mode is subscribed.
    ACCOUNT: The account events get forwarded to subscriptions if ACCOUNT mode is subscribed.
    PAYMENTS: Incoming Whatsapp pay events get forwarded to subscriptions if PAYMENTS mode is subscribed. [Only for v3].
    FLOWS_MESSAGE: Incoming flow messages get forwarded to subscriptions if FLOWS_MESSAGE mode is subscribed. [Only for v3].
    MESSAGE: All incoming messages (not events) except flow messages get forwarded to subscriptions if MESSAGE mode is subscribed.
    OTHERS: All incoming new events whose exclusive mode is not present (eg read, delivered, sent, payments) get forwarded to subscriptions if OTHERS mode is subscribed [Only for v3].
    ALL: All incoming messages (not events) get forwarded to subscriptions if ALL mode is subscribed [Only for v3].
    BILLING: Billing events get forwarded to subscriptions if billing mode is subscribed.
    FAILED: The failed events get forwarded to subscriptions if failed mode is subscribed.
    SENT: Sent events get forwarded to subscriptions if sent mode is subscribed.
    DELIVERED: Delivered events get forwarded to subscriptions if delivered mode is subscribed.
    READ: Read events get forwarded to subscriptions if read mode is subscribed.
    ENQUEUED Enqueued events get forwarded to subscriptions if enqueued mode is subscribed.
    """
    modes: List[Literal[
        "WHATSAPP", "SMS", "VOICE", "TEMPLATE", "ACCOUNT", "PAYMENTS",
        "FLOWS_MESSAGE", "MESSAGE", "OTHERS", "ALL", "BILLING", "FAILED",
        "SENT", "DELIVERED", "READ", "ENQUEUED"
    ]]
    tag: str
    url: str
    version:int = 3
    showOnUI:bool = True

    def to_form_data(self) -> dict:
        """
        Convert to form data dict with modes as comma-separated string.
        Use this method instead of __dict__ for API requests.
        """
        data = self.model_dump()
        data['modes'] = ','.join(data['modes'])
        return data
    
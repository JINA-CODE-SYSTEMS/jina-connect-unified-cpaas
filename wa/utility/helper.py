from wa.utility.data_model.gupshup.subscription import SubscriptionFormData


def get_subscription_data(instance):
    from wa.models import SubscriptionModel

    instance: SubscriptionModel = instance
    form_data = SubscriptionFormData(
        modes=instance.mode, tag=f"sub_{instance.pk}_tenant_{instance.gupshup_model.pk}", url=instance.webhook_urls
    )
    return form_data.to_form_data()


def extract_subscription_id(response):
    """Extracts subscription id from Gupshup API response."""
    try:
        return response.get("subscription", {}).get("id")
    except Exception:
        raise Exception("Failed to extract subscription ID from response")


def extract_message_input(data: dict):
    from wa.utility.data_model.gupshup.message_input import MessageInput

    try:
        message_input = MessageInput.from_webhook_payload(data)
        return message_input
    except Exception as e:
        raise Exception(f"Failed to parse message input: {str(e)}")

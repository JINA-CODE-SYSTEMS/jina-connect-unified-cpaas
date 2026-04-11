import requests
from django.conf import settings
from requests.auth import HTTPBasicAuth


def create_razorpay_order(amount, currency, receipt="receipt#1", notes=None):
    """
    Creates an order in Razorpay and returns the response JSON.
    """
    payload = {
        "amount": int(amount * 100),  # Razorpay expects amount in paisa (INR) / cents
        "currency": currency,
        "receipt": receipt,
        "notes": notes or {},
    }

    response = requests.post(
        settings.RAZORPAY_URL + "orders",
        auth=HTTPBasicAuth(settings.RAZORPAY_KEY_ID, settings.RAZORPAY_KEY_SECRET),
        json=payload,
        headers={"content-type": "application/json"},
    )

    if response.status_code != 200:
        print(response.status_code)
        raise Exception(f"Razorpay Error: {response.text}")

    return response.json()

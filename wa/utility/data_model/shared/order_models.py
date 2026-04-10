"""
Shared Order Pydantic Models

Single source of truth for order-related data structures used by:
- Checkout template validators (create & send)
- Session message models (order_details & order_status)
- Session validators
- Payment lifecycle services

All monetary values follow META's offset-based integer convention:
  ₹12.34 → OrderAmount(value=1234, offset=100)

Reference:
  https://developers.facebook.com/docs/whatsapp/cloud-api/messages/interactive-order-details-messages
"""

from typing import List, Literal, Optional

from pydantic import BaseModel, Field


# ============================================================================
# Core Monetary Model
# ============================================================================


class OrderAmount(BaseModel):
    """
    META offset-based integer amount.

    All monetary values in the Cloud API use integer arithmetic to
    avoid floating-point precision issues.

    Examples:
        ₹12.34 → OrderAmount(value=1234, offset=100)
        ₹100.00 → OrderAmount(value=10000, offset=100)
    """

    offset: int = Field(
        default=100,
        description="Divisor to convert value to the display currency. "
        "For INR/USD (2 decimal places) this is always 100.",
    )
    value: int = Field(
        ...,
        ge=0,
        description="Amount in the smallest currency unit (e.g., paise for INR).",
    )


# ============================================================================
# Order Line Items
# ============================================================================


class OrderItem(BaseModel):
    """
    A single line item in an order.

    Used in both checkout templates and order_details session messages.
    The META API mandates name, amount, and quantity; the rest are optional
    enrichment fields.
    """

    retailer_id: str = Field(
        default="",
        max_length=100,
        description="Product retailer ID from catalog (optional identifier).",
    )
    name: str = Field(
        ...,
        min_length=1,
        max_length=60,
        description="Product / item name. Max 60 characters.",
    )
    amount: OrderAmount = Field(
        ...,
        description="Unit price of the item.",
    )
    quantity: int = Field(
        ...,
        ge=1,
        description="Quantity ordered. Must be ≥ 1.",
    )
    sale_amount: Optional[OrderAmount] = Field(
        default=None,
        description="Discounted / sale price per unit, if applicable.",
    )
    country_of_origin: Optional[str] = Field(
        default=None,
        max_length=60,
        description="Country of origin (required for India cross-border).",
    )
    importer_name: Optional[str] = Field(
        default=None,
        max_length=120,
        description="Importer name (India compliance).",
    )
    importer_address: Optional[str] = Field(
        default=None,
        max_length=256,
        description="Importer address (India compliance).",
    )
    image: Optional[dict] = Field(
        default=None,
        description='Product image. Example: {"link": "https://..."}',
    )


# ============================================================================
# Order-Level Financial Adjustments
# ============================================================================


class OrderTax(BaseModel):
    """Tax line for the order (e.g., GST, VAT)."""

    value: int = Field(
        ...,
        ge=0,
        description="Tax amount in smallest currency unit.",
    )
    offset: int = Field(
        default=100,
        description="Offset for the tax amount.",
    )
    description: Optional[str] = Field(
        default=None,
        max_length=60,
        description="Tax label, e.g., 'GST 18%'.",
    )


class OrderDiscount(BaseModel):
    """Discount applied to the order."""

    value: int = Field(
        ...,
        ge=0,
        description="Discount amount in smallest currency unit.",
    )
    offset: int = Field(
        default=100,
        description="Offset for the discount amount.",
    )
    description: Optional[str] = Field(
        default=None,
        max_length=60,
        description="Discount label, e.g., 'WELCOME10'.",
    )
    discount_program_name: Optional[str] = Field(
        default=None,
        max_length=60,
        description="Name of the discount programme.",
    )


class OrderShipping(BaseModel):
    """Shipping / delivery charge for the order."""

    value: int = Field(
        ...,
        ge=0,
        description="Shipping cost in smallest currency unit.",
    )
    offset: int = Field(
        default=100,
        description="Offset for the shipping amount.",
    )
    description: Optional[str] = Field(
        default=None,
        max_length=60,
        description="Shipping method label, e.g., 'Express Delivery'.",
    )


# ============================================================================
# Payment Gateway Configuration
# ============================================================================


class PaymentGatewayConfig(BaseModel):
    """
    Payment gateway configuration for WhatsApp India Payments.

    Exactly one of the gateway-specific fields (razorpay, payu, billdesk,
    zaakpay) should be populated, matching the ``type`` value.
    """

    type: Literal["razorpay", "payu", "billdesk", "zaakpay"] = Field(
        ...,
        description="Payment gateway identifier.",
    )
    configuration_name: str = Field(
        ...,
        max_length=60,
        description="Configuration name registered with META.",
    )
    razorpay: Optional[dict] = Field(
        default=None,
        description='Razorpay-specific params, e.g., {"notes": {}, "receipt": ""}.',
    )
    payu: Optional[dict] = Field(
        default=None,
        description="PayU-specific params.",
    )
    billdesk: Optional[dict] = Field(
        default=None,
        description="BillDesk-specific params.",
    )
    zaakpay: Optional[dict] = Field(
        default=None,
        description="Zaakpay-specific params.",
    )


class PaymentSettings(BaseModel):
    """
    Top-level payment settings block for order_details session messages.

    Currently only ``payment_gateway`` type is supported by META.
    """

    type: Literal["payment_gateway"] = Field(
        default="payment_gateway",
        description="Payment type. Only 'payment_gateway' is supported.",
    )
    payment_gateway: PaymentGatewayConfig = Field(
        ...,
        description="Gateway configuration details.",
    )

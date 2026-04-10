from typing import List, Optional

from pydantic import BaseModel, Field
from typing_extensions import Literal

from wa.utility.data_model.shared.order_models import (
    OrderAmount,
    OrderItem,
    PaymentSettings,
)


class SessionMessageBase(BaseModel):
    """
    Base data model for session messages.
    """

    messaging_product: Literal['whatsapp'] = Field(
        'whatsapp', description="Messaging product, fixed to 'whatsapp'"
    )
    recipient_type: Literal['individual'] = Field(
        'individual', description="Recipient type, fixed to 'individual'"
    )
    to: str = Field(..., description="Recipient's WhatsApp ID in international format")

# ============ Image Message Data Models ============

class ImageMessageInput(BaseModel):
    """Input data for image message content."""
    id: str = Field(..., description="Media ID of the image message")
    caption: str | None = Field(None, description="Caption for the image message")


class ImageMessage(SessionMessageBase):
    """
    Data model for image messages.
    """
    type: Literal['image'] = Field('image', description="Type of the message content")
    image: ImageMessageInput = Field(..., description="Image message content")

# ============ Contact Message Data Models ============

class ContactAddress(BaseModel):
    """Address information for a contact."""
    street: Optional[str] = Field(None, description="Street number and name")
    city: Optional[str] = Field(None, description="City name")
    state: Optional[str] = Field(None, description="State or province code")
    zip: Optional[str] = Field(None, description="ZIP or postal code")
    country: Optional[str] = Field(None, description="Country name")
    country_code: Optional[str] = Field(None, description="Two-letter country code")
    type: Optional[Literal['HOME', 'WORK']] = Field(None, description="Address type")


class ContactEmail(BaseModel):
    """Email information for a contact."""
    email: Optional[str] = Field(None, description="Email address")
    type: Optional[Literal['HOME', 'WORK']] = Field(None, description="Email type")


class ContactName(BaseModel):
    """Name information for a contact (required)."""
    formatted_name: str = Field(..., description="Full formatted name (required)")
    first_name: Optional[str] = Field(None, description="First name")
    last_name: Optional[str] = Field(None, description="Last name")
    middle_name: Optional[str] = Field(None, description="Middle name")
    suffix: Optional[str] = Field(None, description="Name suffix (e.g., Jr., Sr.)")
    prefix: Optional[str] = Field(None, description="Name prefix (e.g., Mr., Mrs., Dr.)")


class ContactOrg(BaseModel):
    """Organization information for a contact."""
    company: Optional[str] = Field(None, description="Company or organization name")
    department: Optional[str] = Field(None, description="Department name")
    title: Optional[str] = Field(None, description="Job title")


class ContactPhone(BaseModel):
    """Phone information for a contact."""
    phone: Optional[str] = Field(None, description="Phone number")
    type: Optional[Literal['CELL', 'MAIN', 'IPHONE', 'HOME', 'WORK']] = Field(None, description="Phone number type")
    wa_id: Optional[str] = Field(None, description="WhatsApp user ID (if registered on WhatsApp)")


class ContactUrl(BaseModel):
    """URL information for a contact."""
    url: Optional[str] = Field(None, description="Website URL")
    type: Optional[Literal['HOME', 'WORK']] = Field(None, description="Website type")


class Contact(BaseModel):
    """
    A single contact with all its information.
    Only 'name' with 'formatted_name' is required.
    """
    addresses: Optional[List[ContactAddress]] = Field(None, description="List of addresses")
    birthday: Optional[str] = Field(None, description="Birthday in YYYY-MM-DD format")
    emails: Optional[List[ContactEmail]] = Field(None, description="List of email addresses")
    name: ContactName = Field(..., description="Contact name (required)")
    org: Optional[ContactOrg] = Field(None, description="Organization information")
    phones: Optional[List[ContactPhone]] = Field(None, description="List of phone numbers")
    urls: Optional[List[ContactUrl]] = Field(None, description="List of URLs/websites")


class ContactMessage(SessionMessageBase):
    """
    Data model for contact messages.
    """
    type: Literal['contacts'] = Field('contacts', description="Type of the message content")
    contacts: List[Contact] = Field(..., description="List of contacts to send", min_length=1)

# ============ Other Message Data Models ============    

class AudioMessageInput(BaseModel):
    """Input data for audio message content."""
    id: str = Field(..., description="Media ID of the audio message")


class AudioMessage(SessionMessageBase):
    """
    Data model for audio messages.
    """
    type: Literal['audio'] = Field('audio', description="Type of the message content")
    audio: AudioMessageInput = Field(..., description="Audio message content")


class ReactionMessageInput(BaseModel):
    """Input data for reaction message content."""
    message_id: str = Field(..., description="ID of the message being reacted to")
    emoji: str = Field(..., description="Emoji used for the reaction")


class ReactionMessage(SessionMessageBase):
    """
    Data model for reaction messages.
    """
    type: Literal['reaction'] = Field('reaction', description="Type of the message content")
    reaction: ReactionMessageInput = Field(..., description="Reaction message content")


class VideoMessageInput(BaseModel):
    """Input data for video message content."""
    id: str = Field(..., description="Media ID of the video message")
    caption: str | None = Field(None, description="Caption for the video message")


class VideoMessage(SessionMessageBase):
    """
    Data model for video messages.
    """
    type: Literal['video'] = Field('video', description="Type of the message content")
    video: VideoMessageInput = Field(..., description="Video message content")


class StickerMessageInput(BaseModel):
    """Input data for sticker message content."""
    id: str = Field(..., description="Media ID of the sticker message")


class StickerMessage(SessionMessageBase):
    """
    Data model for sticker messages.
    """
    type: Literal['sticker'] = Field('sticker', description="Type of the message content")
    sticker: StickerMessageInput = Field(..., description="Sticker message content")


class TextMessageInput(BaseModel):
    """Input data for text message content."""
    body: str = Field(..., description="Text content of the message")
    preview_url: bool = Field(False, description="Whether to show URL preview")


class TextMessage(SessionMessageBase):
    """
    Data model for text messages.
    """
    type: Literal['text'] = Field('text', description="Type of the message content")
    text: TextMessageInput = Field(..., description="Text message content")


# ============ Interactive Message Data Models ============
# Supports: button (quick reply buttons), list (list menu), cta_url (call-to-action URL)
# Reference: https://developers.facebook.com/docs/whatsapp/cloud-api/messages/interactive-reply-buttons-messages
# Reference: https://developers.facebook.com/docs/whatsapp/cloud-api/messages/interactive-list-messages

# --- Common Interactive Components ---

class InteractiveHeader(BaseModel):
    """
    Header for interactive messages.
    Supports text, image, video, or document.
    """
    type: Literal['text', 'image', 'video', 'document'] = Field(
        ..., description="Header type: text, image, video, or document"
    )
    text: Optional[str] = Field(None, description="Header text (max 60 chars, required if type=text)")
    image: Optional[dict] = Field(None, description="Image object with 'id' or 'link' (required if type=image)")
    video: Optional[dict] = Field(None, description="Video object with 'id' or 'link' (required if type=video)")
    document: Optional[dict] = Field(None, description="Document object with 'id' or 'link' (required if type=document)")


class InteractiveBody(BaseModel):
    """Body text for interactive messages (required)."""
    text: str = Field(..., description="Body text (max 1024 chars for buttons, max 4096 chars for lists)")


class InteractiveFooter(BaseModel):
    """Footer text for interactive messages (optional)."""
    text: str = Field(..., description="Footer text (max 60 chars)")


# --- Interactive Button Message (Quick Reply Buttons) ---
# Max 3 buttons, each with unique ID (max 256 chars) and title (max 20 chars)

class ButtonReply(BaseModel):
    """Reply button content."""
    id: str = Field(..., description="Unique button ID (max 256 chars)")
    title: str = Field(..., description="Button label text (max 20 chars)")


class InteractiveButton(BaseModel):
    """A single interactive reply button."""
    type: Literal['reply'] = Field('reply', description="Button type, always 'reply' for quick reply buttons")
    reply: ButtonReply = Field(..., description="Button reply content")


class InteractiveButtonAction(BaseModel):
    """Action object for button messages."""
    buttons: List[InteractiveButton] = Field(
        ..., 
        description="List of buttons (max 3)",
        min_length=1,
        max_length=3
    )


class InteractiveButtonContent(BaseModel):
    """
    Content for interactive button messages.
    
    Example payload:
    {
        "type": "button",
        "header": {"type": "text", "text": "Header"},
        "body": {"text": "Please choose an option:"},
        "footer": {"text": "Footer text"},
        "action": {
            "buttons": [
                {"type": "reply", "reply": {"id": "opt1", "title": "Option 1"}},
                {"type": "reply", "reply": {"id": "opt2", "title": "Option 2"}}
            ]
        }
    }
    """
    type: Literal['button'] = Field('button', description="Interactive type: button")
    body: InteractiveBody = Field(..., description="Message body (required)")
    action: InteractiveButtonAction = Field(..., description="Button action (required)")
    header: Optional[InteractiveHeader] = Field(None, description="Optional header (text/image/video/document)")
    footer: Optional[InteractiveFooter] = Field(None, description="Optional footer")


class InteractiveButtonMessage(SessionMessageBase):
    """
    Data model for interactive button messages (quick reply buttons).
    
    Allows sending up to 3 predefined reply buttons that users can tap.
    When tapped, triggers a webhook with the button ID.
    
    Usage:
        message = InteractiveButtonMessage(
            to="919876543210",
            interactive=InteractiveButtonContent(
                body=InteractiveBody(text="Choose an option:"),
                action=InteractiveButtonAction(
                    buttons=[
                        InteractiveButton(reply=ButtonReply(id="yes", title="Yes")),
                        InteractiveButton(reply=ButtonReply(id="no", title="No"))
                    ]
                )
            )
        )
    """
    type: Literal['interactive'] = Field('interactive', description="Message type: interactive")
    interactive: InteractiveButtonContent = Field(..., description="Interactive button content")


# --- Interactive List Message ---
# Max 10 sections, max 10 rows per section (10 rows total across all sections)
# Button text max 20 chars, row title max 24 chars, row description max 72 chars

class ListRow(BaseModel):
    """A single row/option in a list section."""
    id: str = Field(..., description="Unique row ID (max 200 chars)")
    title: str = Field(..., description="Row title (max 24 chars)")
    description: Optional[str] = Field(None, description="Row description (max 72 chars)")


class ListSection(BaseModel):
    """A section in the list containing rows."""
    title: Optional[str] = Field(None, description="Section title (max 24 chars, required if >1 section)")
    rows: List[ListRow] = Field(
        ..., 
        description="List of rows in this section (max 10 rows total across all sections)",
        min_length=1
    )


class InteractiveListAction(BaseModel):
    """Action object for list messages."""
    button: str = Field(..., description="Button text to open the list menu (max 20 chars)")
    sections: List[ListSection] = Field(
        ..., 
        description="List of sections (max 10 sections)",
        min_length=1,
        max_length=10
    )


class InteractiveListContent(BaseModel):
    """
    Content for interactive list messages.
    
    Example payload:
    {
        "type": "list",
        "header": {"type": "text", "text": "Choose Option"},
        "body": {"text": "Please select from the menu:"},
        "footer": {"text": "Powered by Jina Connect"},
        "action": {
            "button": "View Options",
            "sections": [
                {
                    "title": "Category A",
                    "rows": [
                        {"id": "a1", "title": "Item 1", "description": "Description 1"},
                        {"id": "a2", "title": "Item 2", "description": "Description 2"}
                    ]
                }
            ]
        }
    }
    """
    type: Literal['list'] = Field('list', description="Interactive type: list")
    body: InteractiveBody = Field(..., description="Message body (required, max 4096 chars)")
    action: InteractiveListAction = Field(..., description="List action with button and sections (required)")
    header: Optional[InteractiveHeader] = Field(None, description="Optional header (text only for lists)")
    footer: Optional[InteractiveFooter] = Field(None, description="Optional footer")


class InteractiveListMessage(SessionMessageBase):
    """
    Data model for interactive list messages.
    
    Allows sending a list menu with sections and rows. Users tap the button
    to reveal the list, then select an option which triggers a webhook.
    
    Usage:
        message = InteractiveListMessage(
            to="919876543210",
            interactive=InteractiveListContent(
                body=InteractiveBody(text="Select a shipping option:"),
                action=InteractiveListAction(
                    button="View Options",
                    sections=[
                        ListSection(
                            title="Express",
                            rows=[
                                ListRow(id="express", title="Express Delivery", description="1-2 days")
                            ]
                        )
                    ]
                )
            )
        )
    """
    type: Literal['interactive'] = Field('interactive', description="Message type: interactive")
    interactive: InteractiveListContent = Field(..., description="Interactive list content")


# --- Interactive CTA URL Message ---
# Call-to-action URL button that opens a URL when tapped

class CtaUrlButton(BaseModel):
    """CTA URL button parameters."""
    display_text: str = Field(..., description="Button display text (max 20 chars)")
    url: str = Field(..., description="URL to open when button is tapped")


class CtaUrlAction(BaseModel):
    """Action object for CTA URL messages."""
    name: Literal['cta_url'] = Field('cta_url', description="Action name, always 'cta_url'")
    parameters: CtaUrlButton = Field(..., description="CTA button parameters")


class InteractiveCtaUrlContent(BaseModel):
    """
    Content for interactive CTA URL messages.
    
    Example payload:
    {
        "type": "cta_url",
        "header": {"type": "text", "text": "Visit Us"},
        "body": {"text": "Click below to visit our website"},
        "footer": {"text": "Jina Connect"},
        "action": {
            "name": "cta_url",
            "parameters": {
                "display_text": "Visit Website",
                "url": "https://example.com"
            }
        }
    }
    """
    type: Literal['cta_url'] = Field('cta_url', description="Interactive type: cta_url")
    body: InteractiveBody = Field(..., description="Message body (required)")
    action: CtaUrlAction = Field(..., description="CTA URL action (required)")
    header: Optional[InteractiveHeader] = Field(None, description="Optional header")
    footer: Optional[InteractiveFooter] = Field(None, description="Optional footer")


class InteractiveCtaUrlMessage(SessionMessageBase):
    """
    Data model for interactive CTA URL messages.
    
    Sends a message with a call-to-action button that opens a URL when tapped.
    
    Usage:
        message = InteractiveCtaUrlMessage(
            to="919876543210",
            interactive=InteractiveCtaUrlContent(
                body=InteractiveBody(text="Visit our store!"),
                action=CtaUrlAction(
                    parameters=CtaUrlButton(
                        display_text="Shop Now",
                        url="https://shop.example.com"
                    )
                )
            )
        )
    """
    type: Literal['interactive'] = Field('interactive', description="Message type: interactive")
    interactive: InteractiveCtaUrlContent = Field(..., description="Interactive CTA URL content")


# ============ Interactive Order Details Message ============
# Sends an order with a "Review and Pay" button for WhatsApp India Payments.
# Reference: https://developers.facebook.com/docs/whatsapp/cloud-api/messages/interactive-order-details-messages


class OrderSession(BaseModel):
    """
    Order contents within an order_details session message.

    ``status`` is always ``"pending"`` for new orders sent via
    ``review_and_pay``.  The items list, subtotal, and tax are
    required; shipping, discount, and expiration are optional.
    """
    status: Literal["pending"] = Field(
        "pending", description="Order status, always 'pending' for new orders"
    )
    catalog_id: Optional[str] = Field(
        None, description="Facebook catalog ID (optional)"
    )
    items: List[OrderItem] = Field(
        ...,
        min_length=1,
        max_length=999,
        description="List of order items (1–999)",
    )
    subtotal: OrderAmount = Field(
        ..., description="Subtotal = sum of (item.amount.value × item.quantity)"
    )
    tax: OrderAmount = Field(
        ..., description="Tax amount"
    )
    shipping: Optional[OrderAmount] = Field(
        None, description="Shipping cost (optional)"
    )
    discount: Optional[OrderAmount] = Field(
        None, description="Discount amount (optional)"
    )
    expiration: Optional[dict] = Field(
        None,
        description='Payment expiration: {"timestamp": "EPOCH", "description": "text"}',
    )


class OrderDetailsParameters(BaseModel):
    """
    Parameters for the ``review_and_pay`` action.

    ``total_amount`` must equal ``subtotal + tax + shipping − discount``.
    ``currency`` is currently limited to ``"INR"`` (WhatsApp India Payments).
    """
    reference_id: str = Field(
        ...,
        min_length=1,
        max_length=35,
        description="Unique order reference ID (max 35 chars)",
    )
    type: Literal["digital-goods", "physical-goods"] = Field(
        ..., description="Goods type"
    )
    currency: Literal["INR"] = Field(
        "INR", description="Currency code (only INR supported)"
    )
    total_amount: OrderAmount = Field(
        ..., description="Order total = subtotal + tax + shipping − discount"
    )
    payment_settings: List[PaymentSettings] = Field(
        ...,
        min_length=1,
        description="Payment gateway configurations",
    )
    order: OrderSession = Field(
        ..., description="Order contents (items, subtotal, tax, etc.)"
    )


class OrderDetailsAction(BaseModel):
    """Action wrapper for order_details messages."""
    name: Literal["review_and_pay"] = Field(
        "review_and_pay", description="Action name for order details"
    )
    parameters: OrderDetailsParameters = Field(
        ..., description="Order details parameters"
    )


class InteractiveOrderDetailsContent(BaseModel):
    """
    Content for interactive order_details messages.

    Example payload shape::

        {
            "type": "order_details",
            "header": {"type": "image", "image": {"link": "https://..."}},
            "body": {"text": "Your order is ready for payment"},
            "footer": {"text": "Thank you for shopping"},
            "action": {
                "name": "review_and_pay",
                "parameters": { ... OrderDetailsParameters ... }
            }
        }
    """
    type: Literal["order_details"] = Field(
        "order_details", description="Interactive type: order_details"
    )
    body: InteractiveBody = Field(
        ..., description="Message body (required, max 1024 chars)"
    )
    action: OrderDetailsAction = Field(
        ..., description="Order details action with review_and_pay parameters"
    )
    header: Optional[InteractiveHeader] = Field(
        None, description="Optional header (text or image only)"
    )
    footer: Optional[InteractiveFooter] = Field(
        None, description="Optional footer"
    )


class InteractiveOrderDetailsMessage(SessionMessageBase):
    """
    Data model for interactive order_details messages.

    Sends an order with item details, pricing, and a "Review and Pay" button
    that triggers WhatsApp India Payments (Razorpay / PayU / BillDesk / Zaakpay).

    Usage::

        message = InteractiveOrderDetailsMessage(
            to="919876543210",
            interactive=InteractiveOrderDetailsContent(
                body=InteractiveBody(text="Your order is ready for payment"),
                action=OrderDetailsAction(
                    parameters=OrderDetailsParameters(
                        reference_id="order-12345",
                        type="digital-goods",
                        currency="INR",
                        total_amount=OrderAmount(value=60000),
                        payment_settings=[PaymentSettings(
                            payment_gateway=PaymentGatewayConfig(
                                type="razorpay",
                                configuration_name="my-razorpay-config",
                            )
                        )],
                        order=OrderSession(
                            items=[OrderItem(name="Earbuds", amount=OrderAmount(value=25000), quantity=2)],
                            subtotal=OrderAmount(value=55000),
                            tax=OrderAmount(value=5000),
                        ),
                    )
                ),
            )
        )
    """
    type: Literal['interactive'] = Field(
        'interactive', description="Message type: interactive"
    )
    interactive: InteractiveOrderDetailsContent = Field(
        ..., description="Interactive order details content"
    )


# ============ Interactive Order Status Message ============
# Updates the status of a previously sent order.
# Reference: https://developers.facebook.com/docs/whatsapp/cloud-api/messages/interactive-order-status-messages


class OrderStatusOrder(BaseModel):
    """
    Order status payload for order_status messages.

    ``status`` must be one of the META-defined lifecycle states.
    ``description`` is optional free-text (e.g. tracking number).
    """
    status: Literal[
        "processing", "shipped", "completed",
        "canceled", "partially_shipped",
    ] = Field(..., description="New order status")
    description: Optional[str] = Field(
        None,
        max_length=120,
        description="Optional description, e.g., 'Tracking #: TRK12345'",
    )


class OrderStatusParameters(BaseModel):
    """Parameters for the ``review_order`` action."""
    reference_id: str = Field(
        ...,
        min_length=1,
        max_length=35,
        description="Same reference_id from the original order_details message",
    )
    order: OrderStatusOrder = Field(
        ..., description="New order status"
    )


class OrderStatusAction(BaseModel):
    """Action wrapper for order_status messages."""
    name: Literal["review_order"] = Field(
        "review_order", description="Action name for order status updates"
    )
    parameters: OrderStatusParameters = Field(
        ..., description="Order status parameters"
    )


class InteractiveOrderStatusContent(BaseModel):
    """
    Content for interactive order_status messages.

    Example payload shape::

        {
            "type": "order_status",
            "body": {"text": "Your order has been shipped!"},
            "action": {
                "name": "review_order",
                "parameters": {
                    "reference_id": "order-12345",
                    "order": {"status": "shipped", "description": "Tracking #: TRK12345"}
                }
            }
        }
    """
    type: Literal["order_status"] = Field(
        "order_status", description="Interactive type: order_status"
    )
    body: InteractiveBody = Field(
        ..., description="Message body (required)"
    )
    action: OrderStatusAction = Field(
        ..., description="Order status action with review_order parameters"
    )


class InteractiveOrderStatusMessage(SessionMessageBase):
    """
    Data model for interactive order_status messages.

    Updates the status of a previously sent order (identified by ``reference_id``).

    Usage::

        message = InteractiveOrderStatusMessage(
            to="919876543210",
            interactive=InteractiveOrderStatusContent(
                body=InteractiveBody(text="Your order has been shipped!"),
                action=OrderStatusAction(
                    parameters=OrderStatusParameters(
                        reference_id="order-12345",
                        order=OrderStatusOrder(
                            status="shipped",
                            description="Tracking #: TRK12345",
                        ),
                    )
                ),
            )
        )
    """
    type: Literal['interactive'] = Field(
        'interactive', description="Message type: interactive"
    )
    interactive: InteractiveOrderStatusContent = Field(
        ..., description="Interactive order status content"
    )
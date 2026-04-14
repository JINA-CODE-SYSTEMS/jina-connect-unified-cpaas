"""Send media messages to all connected Telegram users."""
import os, sys
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "jina_connect.settings")
import django; django.setup()

import requests
from telegram.services.bot_client import TelegramBotClient
from telegram.services.message_sender import TelegramMessageSender
from tenants.models import Tenant
from telegram.models import TelegramBotApp
from contacts.models import TenantContact

TOKEN = "8711890430:AAHDwwOw4oEOrRA00KxCr2eJkLzx6x3Ci_g"

# ── Setup ─────────────────────────────────────────────────────────────
tenant = Tenant.objects.get(name="E2E Test Tenant")
bot_app = TelegramBotApp.objects.get(tenant=tenant, bot_user_id=8711890430)
sender = TelegramMessageSender(bot_app)
contacts = TenantContact.objects.filter(tenant=tenant, telegram_chat_id__isnull=False)

print(f"Bot: @{bot_app.bot_username}")
print(f"Contacts: {contacts.count()}\n")

for contact in contacts:
    chat_id = str(contact.telegram_chat_id)
    name = contact.first_name or "there"
    print(f"→ @{contact.telegram_username} (chat_id={chat_id})")

    # 1. Photo with caption — product showcase
    r1 = sender.send_media(
        chat_id=chat_id,
        media_type="photo",
        media_url="https://images.unsplash.com/photo-1553877522-43269d4ea984?w=800",
        caption=f"🖼 Hi {name}! Here's a look at Jina Connect — unified multi-channel messaging",
        contact=contact,
    )
    print(f"  Photo: {'✅' if r1.get('success') else '❌ ' + r1.get('error', '')}")

    # 2. Document (send a sample PDF)
    r2 = sender.send_media(
        chat_id=chat_id,
        media_type="document",
        media_url="https://www.w3.org/WAI/ER/tests/xhtml/testfiles/resources/pdf/dummy.pdf",
        caption="📄 Jina Connect — Sample Integration Guide",
        contact=contact,
    )
    print(f"  Document: {'✅' if r2.get('success') else '❌ ' + r2.get('error', '')}")

    # 3. Photo — multi-channel feature
    r3 = sender.send_media(
        chat_id=chat_id,
        media_type="photo",
        media_url="https://images.unsplash.com/photo-1611746872915-64382b5c76da?w=800",
        caption="🚀 Multi-channel messaging — WhatsApp + Telegram unified in one platform",
        contact=contact,
    )
    print(f"  Feature photo: {'✅' if r3.get('success') else '❌ ' + r3.get('error', '')}")

    print()

print("=" * 50)
print("Media template send complete!")

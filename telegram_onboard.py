"""Find all users who messaged the bot and send onboarding messages."""

import os
import sys

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "jina_connect.settings")
import django

django.setup()

import requests

TOKEN = "8711890430:AAHDwwOw4oEOrRA00KxCr2eJkLzx6x3Ci_g"
BASE = f"https://api.telegram.org/bot{TOKEN}"

# ── Step 1: Clear webhook & discover users ────────────────────────────
print("[1] Clearing webhook and fetching updates...")
requests.post(f"{BASE}/deleteWebhook")
resp = requests.post(f"{BASE}/getUpdates", json={"limit": 100, "timeout": 5}, timeout=15)
body = resp.json()
print(f"    ok={body.get('ok')}, updates={len(body.get('result', []))}")

users = {}
for upd in body.get("result", []):
    msg = upd.get("message") or upd.get("edited_message") or {}
    user = msg.get("from", {})
    chat = msg.get("chat", {})
    if user.get("id") and not user.get("is_bot"):
        uid = user["id"]
        if uid not in users:
            users[uid] = {
                "chat_id": chat.get("id"),
                "username": user.get("username", ""),
                "first_name": user.get("first_name", ""),
                "last_name": user.get("last_name", ""),
            }

if not users:
    print("\n❌ No users found. Ask people to send /start to @Jinaconnectbot")
    sys.exit(1)

print(f"\n[2] Found {len(users)} user(s):")
for uid, info in users.items():
    print(f"    chat_id={info['chat_id']}  @{info['username']}  ({info['first_name']} {info['last_name']})")

# ── Step 2: Set up DB records ─────────────────────────────────────────
print("\n[3] Setting up DB records...")
from contacts.models import TenantContact
from telegram.models import TelegramBotApp
from tenants.models import Tenant

tenant, _ = Tenant.objects.get_or_create(name="E2E Test Tenant")
bot_app, _ = TelegramBotApp.objects.update_or_create(
    tenant=tenant,
    bot_user_id=8711890430,
    defaults={"bot_token": TOKEN, "bot_username": "Jinaconnectbot", "is_active": True},
)
print(f"    ✅ TelegramBotApp pk={bot_app.pk}")

for uid, info in users.items():
    contact, created = TenantContact.objects.update_or_create(
        tenant=tenant,
        telegram_chat_id=info["chat_id"],
        defaults={
            "first_name": info["first_name"] or "User",
            "last_name": info.get("last_name", ""),
            "telegram_username": info["username"],
            "source": "TELEGRAM",
        },
    )
    info["contact"] = contact
    info["db_action"] = "Created" if created else "Updated"
    print(f"    ✅ {info['db_action']} contact pk={contact.pk} @{info['username']}")

# ── Step 3: Send messages via full TelegramMessageSender stack ────────
print("\n[4] Sending messages...")
from telegram.services.keyboard_builder import build_callback_data
from telegram.services.message_sender import TelegramMessageSender

sender = TelegramMessageSender(bot_app)

WELCOME_MSG = """👋 *Hi {name}\\!*

Welcome to *Jina Connect* — your unified communication platform\\!

🚀 Here\\'s what Jina Connect can do for you:"""

ONBOARDING_MSG = """📋 *Getting Started with Jina Connect*

✅ *Multi\\-Channel Messaging* — WhatsApp \\+ Telegram from one dashboard
✅ *Smart Broadcasts* — Send campaigns to thousands instantly
✅ *Team Inbox* — Your whole team collaborates on customer conversations
✅ *Chat Flows* — Automate responses with visual flow builder
✅ *MCP Server* — Let AI assistants manage your communications

_Powered by Jina Connect Unified CPaaS_
🔗 [Learn more](https://github.com/JINA\\-CODE\\-SYSTEMS/jina\\-connect\\-unified\\-cpaas)"""

results = []
for uid, info in users.items():
    chat_id = str(info["chat_id"])
    name = info["first_name"] or "there"
    contact = info["contact"]

    print(f"\n  → @{info['username']} (chat_id={chat_id})...")

    # Message 1: Welcome
    r1 = sender.send_text(
        chat_id=chat_id,
        text=WELCOME_MSG.format(name=name),
        contact=contact,
        parse_mode="MarkdownV2",
    )
    status1 = "✅" if r1.get("success") else f"❌ {r1.get('error', '')}"
    print(f"    Welcome: {status1}")

    # Message 2: Onboarding with inline keyboard
    keyboard_rows = [
        [
            {
                "text": "📖 View Features",
                "url": "https://github.com/JINA-CODE-SYSTEMS/jina-connect-unified-cpaas#-features",
            },
        ],
        [
            {"text": "💬 WhatsApp", "callback_data": build_callback_data("onboard", "whatsapp", "demo")},
            {"text": "🤖 Telegram", "callback_data": build_callback_data("onboard", "telegram", "demo")},
        ],
        [
            {"text": "🚀 Get Started", "callback_data": build_callback_data("onboard", "start", "demo")},
        ],
    ]

    r2 = sender.send_keyboard(
        chat_id=chat_id,
        text=ONBOARDING_MSG,
        keyboard=keyboard_rows,
        contact=contact,
        parse_mode="MarkdownV2",
    )
    status2 = "✅" if r2.get("success") else f"❌ {r2.get('error', '')}"
    print(f"    Onboarding: {status2}")

    results.append({"user": f"@{info['username']}", "welcome": r1.get("success"), "onboarding": r2.get("success")})

# ── Summary ───────────────────────────────────────────────────────────
print("\n" + "=" * 55)
print("  ONBOARDING RESULTS")
print("=" * 55)
for r in results:
    w = "✅" if r["welcome"] else "❌"
    o = "✅" if r["onboarding"] else "❌"
    print(f"  {r['user']:20s}  Welcome: {w}  Onboarding: {o}")
print("=" * 55)
print(f"  Total: {len(results)} user(s) messaged")

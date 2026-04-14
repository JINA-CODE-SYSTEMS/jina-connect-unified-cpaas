#!/usr/bin/env python
"""
Real end-to-end Telegram test.

Usage:
    python telegram_e2e_test.py <BOT_TOKEN>

Steps performed:
  1. Validate the bot token via getMe
  2. Fetch recent updates (getUpdates) to find @kjha521's chat_id
  3. Create Tenant + TelegramBotApp in DB
  4. Send a test message via TelegramMessageSender (the full stack)
  5. Clean up DB records
"""
import os
import sys

# ── Bootstrap Django ──────────────────────────────────────────────────────
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "jina_connect.settings")

import django  # noqa: E402
django.setup()

import requests  # noqa: E402
from telegram.services.bot_client import TelegramBotClient, TelegramAPIError  # noqa: E402

TARGET_USERNAME = "kjha521"  # without @
BASE = "https://api.telegram.org/bot{token}/{method}"


def main():
    if len(sys.argv) < 2:
        print("Usage: python telegram_e2e_test.py <BOT_TOKEN>")
        sys.exit(1)

    token = sys.argv[1].strip()
    print(f"Token: ***{token[-4:]}")

    # ── Step 1: Validate token ────────────────────────────────────────────
    print("\n[1/5] Validating bot token via getMe...")
    client = TelegramBotClient(token=token)
    try:
        me = client.get_me()
        print(f"  ✅ Bot: @{me.get('username')} (id={me.get('id')})")
        bot_user_id = me["id"]
        bot_username = me.get("username", "")
    except TelegramAPIError as e:
        print(f"  ❌ Token invalid: {e}")
        sys.exit(1)

    # ── Step 2: Find @kjha521's chat_id from recent updates ──────────────
    print(f"\n[2/5] Looking for @{TARGET_USERNAME} in recent updates (getUpdates)...")

    # Must delete webhook first — getUpdates doesn't work while webhook is active
    try:
        client.delete_webhook(drop_pending_updates=False)
        print("  (webhook cleared for getUpdates)")
    except Exception:
        pass

    url = BASE.format(token=token, method="getUpdates")
    resp = requests.post(url, json={"limit": 100, "timeout": 5}, timeout=15)
    body = resp.json()

    chat_id = None
    if body.get("ok"):
        updates = body.get("result", [])
        print(f"  Found {len(updates)} update(s)")
        for upd in updates:
            msg = upd.get("message") or upd.get("edited_message") or {}
            user = msg.get("from", {})
            uname = (user.get("username") or "").lower()
            chat = msg.get("chat", {})
            if uname == TARGET_USERNAME.lower():
                chat_id = chat.get("id")
                print(f"  ✅ Found @{TARGET_USERNAME} → chat_id={chat_id}")
                print(f"     Name: {user.get('first_name', '')} {user.get('last_name', '')}")
                break
    else:
        print(f"  ⚠️  getUpdates failed: {body}")

    if not chat_id:
        print(f"\n  ❌ @{TARGET_USERNAME} not found in recent updates.")
        print(f"     Ask them to send /start to @{bot_username} and re-run this script.")
        # Try manual input as fallback
        manual = input("  Or enter chat_id manually (leave blank to abort): ").strip()
        if manual and manual.isdigit():
            chat_id = int(manual)
        else:
            sys.exit(1)

    # ── Step 3: Create DB records ─────────────────────────────────────────
    print("\n[3/5] Setting up TelegramBotApp in database...")
    from tenants.models import Tenant
    from telegram.models import TelegramBotApp
    from contacts.models import TenantContact

    tenant, _ = Tenant.objects.get_or_create(name="E2E Test Tenant")
    bot_app, created = TelegramBotApp.objects.update_or_create(
        tenant=tenant,
        bot_user_id=bot_user_id,
        defaults={
            "bot_token": token,
            "bot_username": bot_username,
            "is_active": True,
        },
    )
    action = "Created" if created else "Updated"
    print(f"  ✅ {action} TelegramBotApp pk={bot_app.pk}")

    contact, created = TenantContact.objects.update_or_create(
        tenant=tenant,
        telegram_chat_id=chat_id,
        defaults={
            "first_name": TARGET_USERNAME,
            "source": "TELEGRAM",
            "telegram_username": TARGET_USERNAME,
        },
    )
    action = "Created" if created else "Updated"
    print(f"  ✅ {action} TenantContact pk={contact.pk} (chat_id={chat_id})")

    # ── Step 4: Send via full TelegramMessageSender stack ─────────────────
    print("\n[4/5] Sending test message via TelegramMessageSender...")
    from telegram.services.message_sender import TelegramMessageSender

    sender = TelegramMessageSender(bot_app)
    result = sender.send_text(
        chat_id=str(chat_id),
        text="🚀 *Jina Connect — Telegram E2E Test*\n\nIf you see this, the Telegram adapter is fully operational!\n\n_Sent via TelegramMessageSender → TelegramBotClient → Bot API_",
        contact=contact,
        parse_mode="Markdown",
    )

    if result.get("success"):
        print(f"  ✅ Message sent! message_id={result.get('message_id')}")
    else:
        print(f"  ❌ Send failed: {result}")

    # ── Step 5: Send keyboard test ────────────────────────────────────────
    print("\n[5/5] Sending inline keyboard test...")
    from telegram.services.keyboard_builder import build_callback_data

    keyboard_rows = [
        [
            {"text": "✅ Working!", "callback_data": build_callback_data("test", "confirm", "e2e")},
            {"text": "📖 Docs", "url": "https://github.com/JINA-CODE-SYSTEMS/jina-connect-unified-cpaas"},
        ],
    ]

    result2 = sender.send_keyboard(
        chat_id=str(chat_id),
        text="👆 Tap a button to verify inline keyboards work:",
        keyboard=keyboard_rows,
        contact=contact,
    )

    if result2.get("success"):
        print(f"  ✅ Keyboard message sent! message_id={result2.get('message_id')}")
    else:
        print(f"  ❌ Keyboard send failed: {result2}")

    # ── Summary ───────────────────────────────────────────────────────────
    print("\n" + "=" * 50)
    print("E2E TEST COMPLETE")
    print(f"  Bot: @{bot_username}")
    print(f"  Target: @{TARGET_USERNAME} (chat_id={chat_id})")
    print(f"  Text msg: {'✅' if result.get('success') else '❌'}")
    print(f"  Keyboard: {'✅' if result2.get('success') else '❌'}")
    print("=" * 50)


if __name__ == "__main__":
    main()

"""
Register (or delete) a Telegram webhook for a TelegramBotApp.

Usage:
    python manage.py register_telegram_webhook --bot-app-id <uuid>
    python manage.py register_telegram_webhook --bot-app-id <uuid> --delete
"""

from django.core.management.base import BaseCommand, CommandError

from telegram.constants import WEBHOOK_ALLOWED_UPDATES
from telegram.models import TelegramBotApp
from telegram.services.bot_client import TelegramAPIError, TelegramBotClient


class Command(BaseCommand):
    help = "Register or delete a Telegram Bot API webhook"

    def add_arguments(self, parser):
        parser.add_argument(
            "--bot-app-id",
            required=True,
            help="UUID of the TelegramBotApp to configure",
        )
        parser.add_argument(
            "--delete",
            action="store_true",
            help="Delete the webhook instead of registering it",
        )

    def handle(self, *args, **options):
        bot_app_id = options["bot_app_id"]
        delete = options["delete"]

        try:
            bot_app = TelegramBotApp.objects.get(pk=bot_app_id)
        except TelegramBotApp.DoesNotExist:
            raise CommandError(f"TelegramBotApp {bot_app_id} not found")

        client = TelegramBotClient(token=bot_app.bot_token)

        if delete:
            try:
                client.delete_webhook()
                self.stdout.write(self.style.SUCCESS(f"Webhook deleted for bot @{bot_app.bot_username}"))
            except TelegramAPIError as exc:
                raise CommandError(f"Failed to delete webhook: {exc.description}")
            return

        # Register webhook
        try:
            client.set_webhook(
                url=bot_app.webhook_url,
                secret_token=bot_app.webhook_secret,
                allowed_updates=WEBHOOK_ALLOWED_UPDATES,
            )
            self.stdout.write(self.style.SUCCESS(f"Webhook registered: {bot_app.webhook_url}"))
        except TelegramAPIError as exc:
            raise CommandError(f"Failed to register webhook: {exc.description}")

        # Populate bot info if missing
        if not bot_app.bot_username or not bot_app.bot_user_id:
            try:
                me = client.get_me()
                bot_app.bot_username = me.get("username", bot_app.bot_username)
                bot_app.bot_user_id = me.get("id", bot_app.bot_user_id)
                bot_app.save(update_fields=["bot_username", "bot_user_id"])
                self.stdout.write(
                    self.style.SUCCESS(f"Bot info updated: @{bot_app.bot_username} (ID: {bot_app.bot_user_id})")
                )
            except TelegramAPIError as exc:
                self.stdout.write(self.style.WARNING(f"Could not fetch bot info: {exc.description}"))

        # Set default bot commands
        try:
            client.set_my_commands(
                [
                    {"command": "start", "description": "Start a conversation"},
                    {"command": "help", "description": "Show help"},
                    {"command": "stop", "description": "Stop notifications"},
                ]
            )
            self.stdout.write(self.style.SUCCESS("Bot commands set: /start, /help, /stop"))
        except TelegramAPIError as exc:
            self.stdout.write(self.style.WARNING(f"Could not set bot commands: {exc.description}"))

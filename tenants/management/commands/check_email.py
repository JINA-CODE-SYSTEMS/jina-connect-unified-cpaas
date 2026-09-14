"""Prove the configured SMTP credentials work, before a user's password reset does.

Platform email is Django's plain SMTP backend, so any provider that speaks
authenticated SMTP works by setting environment variables and nothing else.
What a .env cannot tell you is whether the values in it are *right* — and every
way they can be wrong surfaces at the same place today: somebody clicking
"forgot password" and getting a 500, hours or weeks after the deploy that
broke it.

This opens the connection the application would open, with the settings the
application would use, and says what happened. With ``--to`` it delivers a real
message, because a successful login proves the credentials and proves nothing
about whether the provider will accept mail *from* the configured sender.

HOW TO RUN:
    python manage.py check_email
    python manage.py check_email --to you@example.com
"""

import smtplib
import socket
from email.utils import parseaddr

from django.conf import settings
from django.core.mail import EmailMessage, get_connection
from django.core.management.base import BaseCommand

SMTP_BACKEND = "django.core.mail.backends.smtp.EmailBackend"


class Command(BaseCommand):
    help = "Open the configured SMTP connection and report what happened. --to also sends a test message."

    def add_arguments(self, parser):
        parser.add_argument(
            "--to",
            help="Send a test message to this address. Without it, the connection is opened and closed.",
        )

    def handle(self, *args, **options):
        self._report_configuration()

        if settings.EMAIL_BACKEND != SMTP_BACKEND:
            # A console or locmem backend is a legitimate local choice, but it
            # answers "yes" to every question this command asks, so saying so is
            # more useful than a green tick that means nothing.
            self.stdout.write(
                self.style.WARNING(
                    f"\nEMAIL_BACKEND is {settings.EMAIL_BACKEND}, not SMTP. "
                    "Nothing here reaches a mail server, so this check cannot tell you "
                    "whether your credentials work."
                )
            )
            return

        if not self._connects():
            return

        recipient = options["to"]
        if not recipient:
            self.stdout.write(
                "\nNo --to given, so nothing was sent. A login that succeeds still says nothing "
                "about whether this provider will accept mail from your DEFAULT_FROM_EMAIL — "
                "run again with --to to find out."
            )
            return

        self._send(recipient)

    # ── reporting ────────────────────────────────────────────────────────────

    def _report_configuration(self):
        transport = (
            "implicit SSL" if settings.EMAIL_USE_SSL else ("STARTTLS" if settings.EMAIL_USE_TLS else "plaintext")
        )

        self.stdout.write("Configured platform email:")
        self.stdout.write(f"  host      {settings.EMAIL_HOST}:{settings.EMAIL_PORT}")
        self.stdout.write(f"  transport {transport}")
        self.stdout.write(f"  username  {settings.EMAIL_HOST_USER or '(none)'}")
        self.stdout.write(f"  password  {'set' if settings.EMAIL_HOST_PASSWORD else self.style.ERROR('NOT SET')}")
        self.stdout.write(f"  from      {settings.DEFAULT_FROM_EMAIL}")
        self.stdout.write(f"  timeout   {settings.EMAIL_TIMEOUT}s")

        if not settings.EMAIL_USE_SSL and not settings.EMAIL_USE_TLS:
            self.stdout.write(
                self.style.WARNING(
                    "  ! Neither TLS nor SSL is on, so the password above crosses the network in the clear."
                )
            )

        self._warn_about_sender_mismatch()

    def _warn_about_sender_mismatch(self):
        """The rejection that looks like a code bug and is not.

        Most providers only let an authenticated mailbox send as itself or as
        one of its configured aliases, and answer anything else with a 5xx at
        ``MAIL FROM`` — after the login has already succeeded. So the symptom is
        "SMTP works, mail does not", which sends people to read the sending code.
        """
        sender = parseaddr(settings.DEFAULT_FROM_EMAIL)[1]
        username = settings.EMAIL_HOST_USER

        if not sender or not username or "@" not in username:
            return
        if sender.lower() == username.lower():
            return

        self.stdout.write(
            self.style.WARNING(
                f"  ! DEFAULT_FROM_EMAIL sends as {sender} while authenticating as {username}.\n"
                "    That is fine where the provider treats it as an alias of the mailbox, and is "
                "rejected at MAIL FROM where it does not — after a successful login. Use --to to "
                "find out which."
            )
        )

    # ── the two things that can be checked ───────────────────────────────────

    def _connects(self) -> bool:
        self.stdout.write("\nOpening the connection...")
        connection = get_connection()
        try:
            connection.open()
        except smtplib.SMTPAuthenticationError as exc:
            self.stdout.write(
                self.style.ERROR(
                    f"  Rejected the credentials: {exc}\n"
                    "  The host answered, so EMAIL_HOST and EMAIL_PORT are right and "
                    "EMAIL_HOST_USER or EMAIL_HOST_PASSWORD is not."
                )
            )
            return False
        except (socket.timeout, TimeoutError):
            self.stdout.write(
                self.style.ERROR(
                    f"  No answer within {settings.EMAIL_TIMEOUT}s.\n"
                    "  Either nothing is listening on this host and port, or outbound SMTP is "
                    "filtered — cloud providers commonly block 25, 465 and 587 by default. A "
                    "timeout on 465 with STARTTLS configured is the same symptom: set "
                    "EMAIL_USE_SSL=True for that port."
                )
            )
            return False
        except (smtplib.SMTPException, OSError) as exc:
            self.stdout.write(self.style.ERROR(f"  Could not connect: {type(exc).__name__}: {exc}"))
            return False

        self.stdout.write(self.style.SUCCESS("  Connected and authenticated."))
        connection.close()
        return True

    def _send(self, recipient: str):
        self.stdout.write(f"\nSending a test message to {recipient}...")
        message = EmailMessage(
            subject="Platform email check",
            body=(
                "This message was sent by `manage.py check_email`.\n\n"
                f"Host: {settings.EMAIL_HOST}:{settings.EMAIL_PORT}\n"
                f"From: {settings.DEFAULT_FROM_EMAIL}\n\n"
                "If you are reading it, platform notifications can reach this address."
            ),
            from_email=settings.DEFAULT_FROM_EMAIL,
            to=[recipient],
        )
        try:
            sent = message.send(fail_silently=False)
        except smtplib.SMTPSenderRefused as exc:
            self.stdout.write(
                self.style.ERROR(
                    f"  The sender was refused: {exc}\n"
                    "  The login worked; this provider will not send as "
                    f"{parseaddr(settings.DEFAULT_FROM_EMAIL)[1]}. Set DEFAULT_FROM_EMAIL to the "
                    "authenticated mailbox, or add it as an alias with the provider."
                )
            )
            return
        except smtplib.SMTPRecipientsRefused as exc:
            self.stdout.write(self.style.ERROR(f"  The recipient was refused: {exc}"))
            return
        except (smtplib.SMTPException, OSError) as exc:
            self.stdout.write(self.style.ERROR(f"  Could not send: {type(exc).__name__}: {exc}"))
            return

        if sent:
            self.stdout.write(self.style.SUCCESS("  Accepted for delivery."))
            self.stdout.write(
                "  Accepted is not delivered — check the inbox, and the spam folder. Mail from a "
                "domain with no SPF or DKIM record is commonly accepted and then filtered."
            )
        else:
            self.stdout.write(self.style.ERROR("  The backend reported that nothing was sent."))

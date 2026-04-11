"""
Management command to seed a demo tenant with sample data.

Usage:
    python manage.py seed_demo
    python manage.py seed_demo --reset   # wipe existing demo data first
"""

import uuid

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand

User = get_user_model()

DEMO_USERNAME = "demo"
DEMO_EMAIL = "demo@jinaconnect.com"
DEMO_MOBILE = "+919999900000"
DEMO_PASSWORD = "demo1234"
DEMO_TENANT = "Demo Workspace"
DEMO_API_KEY = "jc_demo_key_do_not_use_in_production"


class Command(BaseCommand):
    help = "Seed a demo tenant with sample contacts, templates, and a broadcast."

    def add_arguments(self, parser):
        parser.add_argument(
            "--reset",
            action="store_true",
            help="Delete existing demo tenant and user before seeding.",
        )

    def handle(self, *args, **options):
        from contacts.models import TenantContact
        from message_templates.models import TemplateNumber
        from tenants.models import Tenant, TenantAccessKey, TenantUser, TenantWAApp
        from wa.models import WATemplate

        if options["reset"]:
            self.stdout.write("Resetting demo data...")
            Tenant.objects.filter(name=DEMO_TENANT).delete()
            User.objects.filter(username=DEMO_USERNAME).delete()
            self.stdout.write(self.style.SUCCESS("  Deleted."))

        # ── 1. User ──────────────────────────────────────────────────────
        user, created = User.objects.get_or_create(
            username=DEMO_USERNAME,
            defaults={
                "email": DEMO_EMAIL,
                "mobile": DEMO_MOBILE,
                "first_name": "Demo",
                "last_name": "User",
                "is_staff": True,
            },
        )
        if created:
            user.set_password(DEMO_PASSWORD)
            user.save()
            self.stdout.write(self.style.SUCCESS(f"  Created user: {DEMO_USERNAME}"))
        else:
            self.stdout.write(f"  User '{DEMO_USERNAME}' already exists, skipping.")

        # ── 2. Tenant (auto-seeds 5 RBAC roles via signal) ──────────────
        tenant, created = Tenant.objects.get_or_create(
            name=DEMO_TENANT,
            defaults={
                "created_by": user,
                "updated_by": user,
                "country": "IN",
                "industry": "ecommerce",
            },
        )
        if created:
            self.stdout.write(self.style.SUCCESS(f"  Created tenant: {DEMO_TENANT}"))
        else:
            self.stdout.write(f"  Tenant '{DEMO_TENANT}' already exists, skipping.")

        # ── 3. Link user → tenant as OWNER ───────────────────────────────
        owner_role = tenant.roles.filter(slug="owner").first()
        if owner_role:
            TenantUser.objects.get_or_create(
                tenant=tenant,
                user=user,
                defaults={"role": owner_role},
            )

        # ── 4. API key (used by MCP tools) ───────────────────────────────
        TenantAccessKey.objects.get_or_create(
            tenant=tenant,
            key=DEMO_API_KEY,
        )
        self.stdout.write(self.style.SUCCESS(f"  API key: {DEMO_API_KEY}"))

        # ── 5. WA App (Meta Direct BSP) ──────────────────────────────────
        wa_app, _ = TenantWAApp.objects.get_or_create(
            tenant=tenant,
            app_name="Demo WhatsApp",
            defaults={
                "app_id": "demo-app-id",
                "app_secret": "demo-app-secret",
                "wa_number": "+14155551234",
                "bsp": "META",
                "waba_id": "demo-waba-id",
                "phone_number_id": "demo-phone-number-id",
                "is_verified": True,
                "daily_limit": 1000,
            },
        )
        self.stdout.write(self.style.SUCCESS(f"  WA App: {wa_app.app_name} (BSP={wa_app.bsp})"))

        # ── 6. Sample contacts ───────────────────────────────────────────
        contacts_data = [
            {"phone": "+919876543210", "first_name": "Rahul", "last_name": "Sharma"},
            {"phone": "+919876543211", "first_name": "Priya", "last_name": "Patel"},
            {"phone": "+919876543212", "first_name": "Amit", "last_name": "Singh"},
            {"phone": "+919876543213", "first_name": "Sneha", "last_name": "Gupta"},
            {"phone": "+919876543214", "first_name": "Vikram", "last_name": "Reddy"},
        ]
        contacts = []
        for cd in contacts_data:
            c, _ = TenantContact.objects.get_or_create(
                tenant=tenant,
                phone=cd["phone"],
                defaults={
                    "first_name": cd["first_name"],
                    "last_name": cd["last_name"],
                    "source": "WHATSAPP",
                },
            )
            contacts.append(c)
        self.stdout.write(self.style.SUCCESS(f"  Contacts: {len(contacts)} seeded"))

        # ── 7. Sample templates ──────────────────────────────────────────
        templates_data = [
            {
                "element_name": "welcome_message",
                "category": "MARKETING",
                "template_type": "TEXT",
                "content": "Hi {{1}}, welcome to {{2}}! We're glad to have you.",
                "header": "",
                "footer": "Reply STOP to opt out",
                "status": "APPROVED",
                "buttons": [{"type": "QUICK_REPLY", "text": "Get Started"}],
            },
            {
                "element_name": "order_update",
                "category": "UTILITY",
                "template_type": "TEXT",
                "content": "Hi {{1}}, your order #{{2}} has been {{3}}.",
                "header": "Order Update",
                "footer": "",
                "status": "APPROVED",
                "buttons": [{"type": "URL", "text": "Track Order", "url": "https://example.com/track/{{1}}"}],
            },
            {
                "element_name": "otp_verification",
                "category": "AUTHENTICATION",
                "template_type": "TEXT",
                "content": "Your verification code is {{1}}. Valid for 10 minutes.",
                "header": "",
                "footer": "",
                "status": "APPROVED",
                "buttons": [{"type": "COPY_CODE", "text": "Copy Code"}],
            },
        ]
        for td in templates_data:
            tn, _ = TemplateNumber.objects.get_or_create(
                name=td["element_name"],
                defaults={"number": uuid.uuid4()},
            )
            WATemplate.objects.get_or_create(
                wa_app=wa_app,
                element_name=td["element_name"],
                language_code="en",
                defaults={
                    "name": td["element_name"].replace("_", " ").title(),
                    "content": td["content"],
                    "category": td["category"],
                    "template_type": td["template_type"],
                    "header": td["header"],
                    "footer": td["footer"],
                    "status": td["status"],
                    "buttons": td["buttons"],
                    "needs_sync": False,
                    "number": tn,
                    "created_by": user,
                    "updated_by": user,
                },
            )
        self.stdout.write(self.style.SUCCESS(f"  Templates: {len(templates_data)} seeded"))

        # ── 8. Sample broadcast (DRAFT) ──────────────────────────────────
        from broadcast.models import Broadcast

        tn = TemplateNumber.objects.filter(name="welcome_message").first()
        broadcast, created = Broadcast.objects.get_or_create(
            tenant=tenant,
            name="Welcome Campaign (Demo)",
            defaults={
                "platform": "WHATSAPP",
                "status": "DRAFT",
                "template_number": tn,
                "created_by": user,
                "updated_by": user,
            },
        )
        if created:
            broadcast.recipients.set(contacts)
        self.stdout.write(self.style.SUCCESS(f"  Broadcast: '{broadcast.name}' with {len(contacts)} recipients"))

        # ── Done ─────────────────────────────────────────────────────────
        self.stdout.write("")
        self.stdout.write(self.style.SUCCESS("Demo data seeded successfully!"))
        self.stdout.write("")
        self.stdout.write(f"  Login:    {DEMO_USERNAME} / {DEMO_PASSWORD}")
        self.stdout.write(f"  API key:  {DEMO_API_KEY}")
        self.stdout.write(f"  Tenant:   {DEMO_TENANT}")
        self.stdout.write("")
        self.stdout.write("  MCP usage: python -m mcp_server")
        self.stdout.write(f'  Then call send_template(api_key="{DEMO_API_KEY}", ...)')

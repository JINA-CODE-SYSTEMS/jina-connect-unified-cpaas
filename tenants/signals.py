import logging
import os

from django.conf import settings
from django.db.models.signals import post_save, pre_save
from django.dispatch import receiver
from djmoney.contrib.exchange.models import convert_money
from djmoney.money import Money

from abstract.models import TransactionTypeChoices
from transaction.models import TenantTransaction

logger = logging.getLogger(__name__)


@receiver(post_save, sender="tenants.TenantWAApp")
def create_waba_info_on_wa_app_creation(sender, instance, created, **kwargs):
    """
    Signal to automatically create a WABAInfo entry when a TenantWAApp is created.
    This ensures every WA app has a corresponding WABA info record.

    For Gupshup apps, also queues auto-registration of our webhook receiver
    so template approval/rejection callbacks arrive automatically.
    """
    if created:
        from tenants.models import BSPChoices, WABAInfo

        # Create WABAInfo entry for the new WA app
        WABAInfo.objects.get_or_create(wa_app=instance)
        logger.info(f"Created WABAInfo entry for TenantWAApp {instance.id} ({instance.app_name})")

        # Auto-register our webhook receiver for Gupshup apps
        if instance.bsp == BSPChoices.GUPSHUP:
            if settings.CELERY_BROKER_URL:
                from wa.tasks import auto_register_gupshup_webhook

                auto_register_gupshup_webhook.delay(instance.pk)
                logger.info(f"Queued auto webhook registration for Gupshup app {instance.pk}")
            else:
                logger.info(f"Skipping auto webhook registration for app {instance.pk} (no Celery broker configured)")


def upload_media_to_whatsapp(tenant_media_instance, method="upload_media"):
    """
    Upload a TenantMedia file to WhatsApp via the tenant's BSP adapter.

    Uses the ``get_bsp_adapter()`` factory so Gupshup, META Direct (and
    any future BSP) are handled transparently.

    Args:
        tenant_media_instance: TenantMedia instance with a saved file.
        method: Adapter method to call — ``'upload_media'`` (default,
                for templates) or ``'upload_session_media'`` (for session
                messages).

    Returns:
        AdapterResult on success / failure — never ``None``.

    Raises:
        ValueError: if no WA app exists for the tenant.
    """
    from wa.adapters import get_bsp_adapter

    tenant = tenant_media_instance.tenant
    wa_app = tenant.wa_apps.first()

    if not wa_app:
        raise ValueError(f"No WA app found for tenant {tenant.id}. Cannot upload media to WhatsApp.")

    adapter = get_bsp_adapter(wa_app)

    # The adapter expects a file-like object
    media_file = tenant_media_instance.media
    if not media_file:
        raise ValueError(f"No media file on TenantMedia {tenant_media_instance.id}.")

    filename = os.path.basename(media_file.name) if media_file.name else "upload"

    logger.info(f"Uploading TenantMedia {tenant_media_instance.id} via {adapter.__class__.__name__} (BSP={wa_app.bsp})")

    # Read the file content into memory so the adapter gets a real
    # file-like object that supports seek/read — cloud storage backends
    # (GCS, S3) return streaming wrappers that some HTTP libraries
    # (requests) cannot handle properly for multipart uploads.
    import io

    media_file.open("rb")
    try:
        file_bytes = media_file.read()
    finally:
        media_file.close()

    file_obj = io.BytesIO(file_bytes)
    logger.info(f"Read {len(file_bytes)} bytes from storage for TenantMedia {tenant_media_instance.id}")

    upload_fn = getattr(adapter, method, None) or adapter.upload_media
    result = upload_fn(
        file_obj=file_obj,
        filename=filename,
    )

    if result.success:
        handle_id = result.data.get("handle_id")
        logger.info(f"Upload succeeded for TenantMedia {tenant_media_instance.id}: handle_id={handle_id}")
    else:
        logger.warning(f"Upload failed for TenantMedia {tenant_media_instance.id}: {result.error_message}")

    return result


@receiver(post_save, sender=TenantTransaction)
def update_tenant_balance(sender, instance, created, **kwargs):
    if created:
        logger.warning(f"TenantTransaction created: {instance.id}")
        # New transaction created
        if instance.transaction_type == TransactionTypeChoices.SUCCESS_RECHARGE:
            pass
            instance.tenant.balance = money_currency_converter_and_adder(instance.tenant.balance, instance.amount)
        elif instance.transaction_type == TransactionTypeChoices.FAILED_RECHARGE:
            instance.tenant.balance = money_currency_converter_and_adder(
                instance.tenant.balance, instance.amount, add=False
            )
        instance.tenant.save()
    else:
        # Transaction updated
        logger.debug(f"TenantTransaction updated: {instance.id}")
        previous_type = getattr(instance, "_old_transaction_type", None)
        if previous_type != instance.transaction_type:
            if instance.transaction_type == TransactionTypeChoices.SUCCESS_RECHARGE:
                logger.warning(f"Updating tenant balance for successful recharge: {instance.id}")
                print("Updating tenant balance for successful recharge")
                instance.tenant.balance = money_currency_converter_and_adder(instance.tenant.balance, instance.amount)
            # elif instance.transaction_type == TransactionTypeChoices.FAILURE_RECHARGE:
            #     instance.tenant.balance -= instance.amount
            else:
                pass
            instance.tenant.save()


def money_currency_converter_and_adder(balance: Money, recharge: Money, add: bool = True) -> Money:
    if balance.currency != recharge.currency:
        recharge_converted = convert_money(recharge, balance.currency)
    else:
        recharge_converted = recharge
    if add:
        return balance + recharge_converted
    return balance - recharge_converted


@receiver(pre_save, sender=TenantTransaction)
def store_previous_transaction_type(sender, instance, **kwargs):
    if instance.pk:
        old = sender.objects.filter(pk=instance.pk).first()
        instance._old_transaction_type = old.transaction_type if old else None
    else:
        instance._old_transaction_type = None


@receiver(post_save, sender="tenants.Tenant")
def seed_default_roles_on_tenant_creation(sender, instance, created, **kwargs):
    """
    When a new Tenant is created, automatically seed the 5 default RBAC
    roles and their permission rows.  Idempotent — safe if called on an
    existing tenant (uses get_or_create internally).
    """
    if created:
        from tenants.permissions import seed_default_roles

        seed_default_roles(instance)
        logger.info(f"Seeded default RBAC roles for tenant {instance.id} ({instance.name})")

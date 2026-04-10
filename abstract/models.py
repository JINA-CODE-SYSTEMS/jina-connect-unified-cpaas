import secrets
import string
import uuid

from abstract.managers import BaseTenantModelForFilterUserManager
from django.conf import settings
from django.contrib.auth import get_user_model
from django.db import models
from djmoney.models.fields import MoneyField
from djmoney.money import Money
from phonenumber_field.modelfields import PhoneNumberField
from simple_history.models import HistoricalRecords

User = settings.AUTH_USER_MODEL


def generate_transaction_id(prefix="jc", length=16):
    """
    Generate a clean transaction ID with format: prefix_RANDOMSTRING
    
    Args:
        prefix: Prefix for the transaction ID (default: "jc")
        length: Length of random string (default: 16)
    
    Returns:
        str: Transaction ID like "jc_A8K9X2M5P7Q1N4R6"
    """
    chars = string.ascii_uppercase + string.digits
    random_string = ''.join(secrets.choice(chars) for _ in range(length))
    return f"{prefix}_{random_string}"

class BaseModel(models.Model):
    """
    BaseModel is an abstract base model that provides common fields and functionality 
    for other models to inherit.
    Attributes:
        description (TextField): An optional text field to store a description.
        name (CharField): A required field to store the name, with a maximum length of 100 characters.
        created_at (DateTimeField): A timestamp indicating when the object was created. Automatically set on creation.
        updated_at (DateTimeField): A timestamp indicating when the object was last updated. Automatically updated on save.
    Methods:
        __str__(): Returns the string representation of the model, which is the value of the `name` field.
    Meta:
        abstract (bool): Indicates that this model is abstract and will not be used to create database tables.
    """

    description = models.TextField(blank=True, null=True)
    name = models.CharField(max_length=100)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        abstract = True

    def __str__(self):
        return self.name


class BaseModelWithOwner(BaseModel):
    """
    BaseModelWithOwner is an abstract base model that extends BaseModel and adds fields to track
    the user who created and last updated the model instance.
    Attributes:
        created_by (ForeignKey): A foreign key to the User model, representing the user who created
            the instance. If the user is deleted, the field is set to NULL.
        updated_by (ForeignKey): A foreign key to the User model, representing the user who last updated
            the instance. This field is optional and can be NULL. If the user is deleted, the field is set to NULL.
    Meta:
        abstract (bool): Indicates that this is an abstract model and will not be used to create any database table.
    Methods:
        __str__(): Returns the string representation of the model instance, which is the value of the `name` attribute.
    """
    
    created_by = models.ForeignKey(User, on_delete=models.SET_NULL, related_name="%(class)s_created_by", blank=True, null=True)
    updated_by = models.ForeignKey(User, on_delete=models.SET_NULL, related_name="%(class)s_updated_by", blank=True, null=True)
   

    class Meta:
        abstract = True

    def __str__(self):
        return self.name    



class BaseFileModel(BaseModel):

    class Meta:
        abstract = True




class BaseEntity(BaseModelWithOwner):
    """
    Entity model represents an organization entity with attributes such as address, phone, email, website.
    It includes the following fields:

    Attributes:
        address (TextField): The address of the organization. Optional.
        phone (CharField): The phone number of the organization. Optional.
        email (EmailField): The email address of the organization. Optional.
        website (URLField): The website URL of the organization. Optional.
    """
    address = models.TextField(blank=True, null=True)
    phone = PhoneNumberField(blank=True)
    email = models.EmailField(blank=True, null=True)
    website = models.URLField(blank=True, null=True)
    
    class Meta:
        verbose_name = "Entity"
        verbose_name_plural = "Entities"
        abstract = True






class TransactionTypeChoices(models.TextChoices):
    PENDING_RECHARGE = "PENDING RECHARGE", "PENDING RECHARGE"
    SUCCESS_RECHARGE = "SUCCESS RECHARGE", "SUCCESS RECHARGE"
    FAILED_RECHARGE = "FAILED RECHARGE", "FAILED RECHARGE"
    CONSUMPTION = "CONSUMPTION", "CONSUMPTION"
    REFUND = "REFUND", "REFUND"
    VOICE_OUTBOUND = "VOICE_OUTBOUND", "Voice Outbound Call"
    VOICE_INBOUND = "VOICE_INBOUND", "Voice Inbound Call"
    VOICE_NUMBER_RENT = "VOICE_NUMBER_RENT", "Voice Number Rental"
    VOICE_AI_AGENT = "VOICE_AI_AGENT", "Voice AI Agent"
    VOICE_RECORDING = "VOICE_RECORDING", "Voice Recording"



class BaseTransaction(BaseModelWithOwner):
    """
    TransactionBaseModel is an abstract base model that extends from BaseModelWithOwner.
    It includes a Boolean field 'is_active' to indicate whether the model instance is active.
    Attributes:
        is_active (BooleanField): A flag indicating if the model instance is active. Defaults to True.
    Meta:
        abstract (bool): Indicates that this is an abstract base class and should not be used to create any database table.
    """
    system_transaction_id = models.CharField(max_length=100, editable=False, null=True, blank=True)
    transaction_id = models.CharField(max_length=100, editable=True, null=True, blank=True)
    amount = MoneyField(max_digits=14, decimal_places=4, default_currency='INR')
    transaction_type = models.CharField(max_length=20, choices=TransactionTypeChoices.choices, default=TransactionTypeChoices.CONSUMPTION)
        
    def save(self, *args, **kwargs):
        if not self.system_transaction_id:
            # Get prefix from settings
            from django.conf import settings
            prefix = getattr(settings, 'TRANSACTION_ID_PREFIX', 'jc')
            
            # Generate unique transaction ID with retry logic
            max_attempts = 10
            for attempt in range(max_attempts):
                transaction_id = generate_transaction_id(prefix=prefix, length=16)
                # Check uniqueness across all Transaction models
                if not self.__class__.objects.filter(system_transaction_id=transaction_id).exists():
                    self.system_transaction_id = transaction_id
                    break
            else:
                # Fallback to UUID if unable to generate unique ID after max attempts
                self.system_transaction_id = f"{prefix}_{str(uuid.uuid4())}"
        super().save(*args, **kwargs)

    class Meta:
        abstract = True


class BaseWallet(BaseModelWithOwner):
    """
    Abstract base model representing a wallet with balance, credit line, and threshold alert functionality.
    Attributes:
        balance (MoneyField): Current wallet balance. Can be negative if credit line is used.
        credit_line (MoneyField): Credit line available to the wallet.
        threshold_alert (MoneyField): Threshold value for balance alerts. Default is 10 USD.
        history (HistoricalRecords): Tracks historical changes to wallet records.
    Properties:
        is_overdrawn (bool): Returns True if the wallet balance is negative.
        total_balance (Money): Returns the sum of balance and credit_line - the total available funds.
        is_below_threshold (bool): Returns True if total_balance is below the threshold alert.
        is_prepaid (bool): Returns True if the wallet has no credit line.
    Methods:
        save(*args, **kwargs): Saves the wallet instance. Raises ValueError if total_balance is negative.
    Meta:
        abstract (bool): Indicates that this is an abstract base class.
    """
    
    balance = MoneyField(max_digits=14, decimal_places=6, default_currency='USD', help_text="Current wallet balance - can be negative if credit line is used", default=Money(0, 'USD'))
    credit_line = MoneyField(max_digits=15, decimal_places=6, default_currency='USD', default=Money(0, 'USD'), help_text="Credit line available to the wallet")
    threshold_alert = MoneyField(max_digits=15, decimal_places=6, default_currency='USD', default=Money(10, 'USD'))
    history = HistoricalRecords(inherit=True)

    @property
    def is_overdrawn(self):
        return self.balance < 0
    
    @property
    def total_balance(self):
        """
        Calculate the total available balance including credit line.
        
        Returns:
            Money: The sum of current balance and available credit line.
            
        Examples:
            - balance: $100, credit_line: $50 → total_balance: $150
            - balance: -$30, credit_line: $50 → total_balance: $20
            - balance: $75, credit_line: $0 → total_balance: $75
        """
        return self.balance + self.credit_line
    
    @property
    def is_below_threshold(self):
        return self.total_balance < self.threshold_alert
    
    @property
    def is_prepaid(self):
        return self.credit_line.amount == 0

    class Meta:
        abstract = True

    def save(self, *args, **kwargs):
        if self.total_balance < Money(0, self.balance.currency):
            raise ValueError("Total balance (balance + credit_line) cannot be negative")
        super().save(*args, **kwargs)

    
class BaseWebhookDumps(BaseModel):
    """
    Model to store webhook dumps for Gupshup apps.
    Attributes:
        gupshup_app (ForeignKey): Reference to the TenantGupshupApps model.
        payload (JSONField): The JSON payload received from the webhook.
        is_processed (BooleanField): Flag indicating if the webhook has been processed. Defaults to False.
        processed_at (DateTimeField): Timestamp when the webhook was processed. Optional.
        error_message (TextField): Any error message encountered during processing. Optional.
    """    
    payload = models.JSONField()
    received_at = models.DateTimeField(auto_now_add=True)
    is_processed = models.BooleanField(default=False)
    processed_at = models.DateTimeField(blank=True, null=True, editable=False)
    error_message = models.TextField(blank=True, null=True)
    name = None

    def __str__(self):
        return f"WebhookDump {self.pk}"
    
    class Meta:
        abstract = True


    def save(self, *args, **kwargs):
        if self.is_processed and not self.processed_at:
            from django.utils import timezone
            self.processed_at = timezone.now()
        super().save(*args, **kwargs)
        
class BaseTenantModelForFilterUser(BaseModelWithOwner):
    """
    Abstract base model that includes a manager to filter by user tenant foreign key.
    Inherits from BaseModelWithOwner to include common fields and functionality.
    Attributes:
        filter_by_user_tenant_fk (str): A string attribute to specify the foreign key for filtering by user tenant.
        objects (BaseTenantModelForFilterUserManager): Custom manager to handle filtering by user tenant.
    Meta:
        abstract (bool): Indicates that this is an abstract base class and should not be used to
    """
    filter_by_user_tenant_fk:str = None
    objects = BaseTenantModelForFilterUserManager()

    class Meta:
        abstract = True
    



from django.apps import apps
from django.contrib import admin
from django.contrib.auth.admin import UserAdmin
from django.urls import reverse
from django.utils.html import format_html
from django.utils.translation import gettext_lazy as _
from users.models import EmailVerificationToken, User


def duplicate_users(modeladmin, request, queryset):
    """Duplicate selected users with a '_copy' suffix. Password must be set manually after duplication."""
    for user in queryset:
        original_username = user.username
        # Find a unique username
        copy_num = 1
        new_username = f"{original_username}_copy"
        while User.objects.filter(username=new_username).exists():
            copy_num += 1
            new_username = f"{original_username}_copy{copy_num}"

        # Find a unique mobile (append 0s)
        new_mobile = str(user.mobile) + "0" if user.mobile else None
        while new_mobile and User.objects.filter(mobile=new_mobile).exists():
            new_mobile += "0"

        User.objects.create(
            username=new_username,
            email=f"copy_{user.email}" if user.email else "",
            first_name=user.first_name,
            last_name=user.last_name,
            mobile=new_mobile,
            is_active=False,  # Inactive until password is set
            is_staff=user.is_staff,
            password="!",  # Unusable password — must be set in admin
        )
    modeladmin.message_user(
        request,
        f"{queryset.count()} user(s) duplicated. They are INACTIVE with no password — edit each to set a password and activate.",
    )

duplicate_users.short_description = "Duplicate selected users (password must be set after)"


@admin.register(User)
class EximUserAdmin(UserAdmin):
    search_fields = ["username", "mobile", "email", "first_name", "last_name"]
    actions = [duplicate_users]

    fieldsets = (
        (None, {"fields": ("username", "password")}),
        (_("Personal info"), {"fields": ("first_name", "last_name", "email", "mobile", "image")}),
        (
            _("Permissions"),
            {
                "fields": (
                    "is_active",
                    "is_staff",
                    "is_superuser",
                    "groups",
                    "user_permissions",
                ),
            },
        ),
        (_("Important dates"), {"fields": ("last_login", "date_joined")}),
    )
    
    # Add fieldsets for creating new users - includes mobile number
    add_fieldsets = (
        (None, {
            'classes': ('wide',),
            'fields': ('username', 'mobile', 'password1', 'password2'),
        }),
        (_("Personal info"), {
            'classes': ('wide',),
            'fields': ('first_name', 'last_name', 'email'),
        }),
        (_("Permissions"), {
            'classes': ('wide',),
            'fields': ('is_active', 'is_staff', 'is_superuser'),
        }),
    )

    list_display = ("username", "first_name", "last_name", "email", "mobile", "is_staff")
    list_filter = ("is_staff", "is_superuser", "is_active", "date_joined")
    
    # Add mobile to the ordering options
    ordering = ("username",)


@admin.register(EmailVerificationToken)
class EmailVerificationTokenAdmin(admin.ModelAdmin):
    list_display = ("user", "token", "created_at", "expires_at", "is_used", "is_valid")
    list_filter = ("is_used", "created_at")
    search_fields = ("user__email", "user__username", "token")
    readonly_fields = ("token", "created_at", "is_valid", "is_expired")
    raw_id_fields = ("user",)
    
    def is_valid(self, obj):
        return obj.is_valid
    is_valid.boolean = True
    is_valid.short_description = "Valid"
    
    def is_expired(self, obj):
        return obj.is_expired
    is_expired.boolean = True
    is_expired.short_description = "Expired"
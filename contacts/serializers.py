from django.contrib.auth import get_user_model
from rest_framework import serializers

from abstract.serializers import BaseSerializer
from contacts.models import AssigneeTypeChoices, TenantContact

User = get_user_model()


class TenantContactSerializer(BaseSerializer):
    # Read-only computed fields
    assigned_to_name = serializers.CharField(read_only=True)
    assigned_by_name = serializers.CharField(read_only=True)

    class Meta:
        model = TenantContact
        fields = "__all__"
        read_only_fields = ["tenant", "assigned_at", "assigned_by_type", "assigned_by_id", "assigned_by_user"]


class ContactAssignmentSerializer(serializers.Serializer):
    """
    Serializer for assigning a contact to a user, bot, or chatflow.
    """

    assigned_to_type = serializers.ChoiceField(
        choices=[
            (AssigneeTypeChoices.USER, "User"),
            (AssigneeTypeChoices.BOT, "Bot"),
            (AssigneeTypeChoices.CHATFLOW, "ChatFlow"),
            (AssigneeTypeChoices.UNASSIGNED, "Unassigned"),
        ],
        help_text="Type of assignee: USER, BOT, CHATFLOW, or UNASSIGNED",
    )
    assigned_to_id = serializers.IntegerField(
        required=False,
        allow_null=True,
        help_text="ID of the user, bot, or chatflow to assign to. Required for USER, BOT, CHATFLOW types.",
    )
    note = serializers.CharField(
        required=False,
        allow_blank=True,
        allow_null=True,
        max_length=2000,
        help_text="Note or instructions for the assignee",
    )

    def validate(self, attrs):
        assigned_to_type = attrs.get("assigned_to_type")
        assigned_to_id = attrs.get("assigned_to_id")

        # Require ID for USER, BOT, CHATFLOW types
        if assigned_to_type in [AssigneeTypeChoices.USER, AssigneeTypeChoices.BOT, AssigneeTypeChoices.CHATFLOW]:
            if not assigned_to_id:
                raise serializers.ValidationError(
                    {"assigned_to_id": f"assigned_to_id is required when assigned_to_type is {assigned_to_type}"}
                )

            # Validate user exists if type is USER
            if assigned_to_type == AssigneeTypeChoices.USER:
                if not User.objects.filter(id=assigned_to_id).exists():
                    raise serializers.ValidationError(
                        {"assigned_to_id": f"User with ID {assigned_to_id} does not exist"}
                    )

        # Clear ID for UNASSIGNED
        if assigned_to_type == AssigneeTypeChoices.UNASSIGNED:
            attrs["assigned_to_id"] = None

        return attrs


class ContactCSVUploadSerializer(serializers.Serializer):
    """
    Serializer for CSV file upload for bulk contact import.
    """

    file = serializers.FileField(
        required=True, help_text="CSV file with contacts. Required column: phone. Optional: first_name, last_name, tag"
    )
    skip_duplicates = serializers.BooleanField(
        required=False, default=True, help_text="Skip contacts with phone numbers that already exist (default: true)"
    )
    default_tag = serializers.CharField(
        required=False,
        default="",
        max_length=255,
        allow_blank=True,
        help_text="Default tag to apply to contacts without a tag in the CSV",
    )

    def validate_file(self, value):
        """Validate the uploaded file."""
        # Check file extension
        filename = value.name.lower()
        if not filename.endswith(".csv"):
            raise serializers.ValidationError("Invalid file format. Please upload a CSV file (.csv extension).")

        # Check file size (max 10MB)
        max_size = 10 * 1024 * 1024  # 10 MB
        if value.size > max_size:
            raise serializers.ValidationError(
                f"File too large. Maximum size is 10 MB. Your file is {value.size / (1024 * 1024):.2f} MB."
            )

        # Check MIME type
        content_type = value.content_type
        allowed_types = ["text/csv", "application/csv", "text/plain", "application/vnd.ms-excel"]
        if content_type not in allowed_types:
            # Allow if extension is correct even if MIME type is different
            pass

        return value


class ContactBulkCreateSerializer(serializers.Serializer):
    """
    Serializer for bulk contact creation via JSON.
    """

    contacts = serializers.ListField(
        child=serializers.DictField(),
        min_length=1,
        max_length=1000,
        help_text="List of contact objects. Each must have 'phone'. Optional: first_name, last_name, tag",
    )
    skip_duplicates = serializers.BooleanField(
        required=False, default=True, help_text="Skip contacts with phone numbers that already exist"
    )
    default_tag = serializers.CharField(
        required=False,
        default="",
        max_length=255,
        allow_blank=True,
        help_text="Default tag to apply to contacts without a tag",
    )

from django.db import models


class BaseTenantModelForFilterUserManager(models.Manager):
    """
    Custom Django manager that provides a method to filter queryset results based on a user-tenant relationship.
    Methods
    -------
    filter_by_user_tenant(user):
        Filters the queryset to include only objects related to the given user, using the model's
        'filter_by_user_tenant_fk' attribute as the foreign key. Raises a ValueError if the attribute
        is not defined on the model.
    """

    def filter_by_user_tenant(self, user):
        fk = self.model.filter_by_user_tenant_fk
        if not fk:
            raise ValueError("Model must define 'filter_by_user_tenant_fk' attribute.")
        return self.filter(**{fk: user})

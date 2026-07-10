from django.conf import settings
from django.db import models, transaction
from django.db.models import Q
from django.utils import timezone


class Event(models.Model):
    """
    Represents an event in time (transaction boundary).
    Implements the 5Ws of audit trails:
      - Who: user
      - What: represented by the modified objects
      - When: timestamp
      - Where: http_request_id
      - Why: comment
    """

    timestamp = models.DateTimeField(default=timezone.now, db_index=True)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+")
    comment = models.TextField(null=True, blank=True)
    http_request_id = models.CharField(max_length=255, null=True, blank=True)

    class Meta:
        app_label = "django_audit_trail"

        indexes = [
            models.Index(
                fields=["http_request_id"],
                name="dat_event_http_request_id_idx",
                # index only rows with value != ("", null)
                condition=~Q(http_request_id="") & Q(http_request_id__isnull=False),
            )
        ]

    def __str__(self):
        username = self.user.get_username()
        return f"Event {self.id} at {self.timestamp} by {username}"


class AuditTrailManager(models.Manager):
    """
    Hides soft-deleted (revoked) entities.
    """
    def get_queryset(self):
        return super().get_queryset().filter(revoked_event__isnull=True)


class AuditTrailModel(models.Model):
    """
    Base class audited models.
    """
    created_event = models.ForeignKey(Event, on_delete=models.PROTECT, related_name="+")
    revoked_event = models.ForeignKey(Event, on_delete=models.PROTECT, null=True, blank=True, related_name="+")

    # active objects only (without revoked)
    objects = AuditTrailManager()

    # all objects (with revoked)
    all_objects = models.Manager()

    class Meta:
        abstract = True

    def save(self, *args, **kwargs):
        from .context import get_audit_trail_event

        if not self.pk and self.created_event_id is None:
            self.created_event = get_audit_trail_event()
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        from .context import get_audit_trail_event

        event = get_audit_trail_event()
        using = kwargs.get("using")

        with transaction.atomic(using=using):
            if self.revoked_event_id is not None:
                # TODO: check how Django handles deleting already deleted objects
                raise RuntimeError(f"{type(self).__name__} is already deleted.")

            self.revoked_event = event
            super().save(update_fields=["revoked_event"], using=using)

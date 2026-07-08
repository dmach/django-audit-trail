from django.conf import settings
from django.db import models
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

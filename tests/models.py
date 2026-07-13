import uuid
from django.db import models
from django_audit_trail.models import AuditTrailModel


class PullRequest(AuditTrailModel):
    owner = models.CharField(max_length=255)
    repo = models.CharField(max_length=255)
    number = models.IntegerField()

    # Anchor-only modification timestamp for demonstrating and testing
    # non-audited field changes on save.
    updated_at = models.DateTimeField(auto_now=True)

    class State:
        title = models.CharField(max_length=255)
        description = models.TextField(null=True, blank=True, default="Default Description")
        user = models.ForeignKey("auth.User", on_delete=models.PROTECT, null=True, blank=True)

    class Meta:
        app_label = "tests"
        unique_together = ("owner", "repo", "number")


class UUIDModel(AuditTrailModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4)

    class State:
        value = models.CharField(max_length=255)

    class Meta:
        app_label = "tests"

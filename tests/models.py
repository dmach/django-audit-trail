from django.db import models
from django_audit_trail.models import AuditTrailModel


class PullRequest(AuditTrailModel):
    owner = models.CharField(max_length=255)
    repo = models.CharField(max_length=255)
    number = models.IntegerField()
    title = models.CharField(max_length=255)

    class Meta:
        app_label = "tests"
        unique_together = ("owner", "repo", "number")

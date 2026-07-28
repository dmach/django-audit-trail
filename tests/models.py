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


class Tag(AuditTrailModel):
    class State:
        name = models.CharField(max_length=50)

    class Meta:
        app_label = "tests"


class Article(AuditTrailModel):
    tags = models.ManyToManyField(Tag, through="ArticleTag")

    class State:
        title = models.CharField(max_length=255)

    class Meta:
        app_label = "tests"


class ArticleTag(AuditTrailModel):
    article = models.ForeignKey(Article, on_delete=models.CASCADE)
    tag = models.ForeignKey(Tag, on_delete=models.CASCADE)

    class Meta:
        app_label = "tests"


# --- Models for Testing Chained Audited/Unaudited M2M Relations ---

class Label(AuditTrailModel):
    """An audited model at the end of the chain."""
    class State:
        text = models.CharField(max_length=50)

    class Meta:
        app_label = "tests"


class UnauditedCategory(models.Model):
    """A standard, non-audited Django model."""
    name = models.CharField(max_length=100)
    labels = models.ManyToManyField(Label, through="CategoryLabel")

    class Meta:
        app_label = "tests"


class Document(AuditTrailModel):
    """An audited model at the start of the chain."""
    categories = models.ManyToManyField(UnauditedCategory, through="DocumentCategory")

    class State:
        title = models.CharField(max_length=255)

    class Meta:
        app_label = "tests"


class CategoryLabel(AuditTrailModel):
    """Audited through-table for UnauditedCategory <-> Label."""
    category = models.ForeignKey(UnauditedCategory, on_delete=models.CASCADE)
    label = models.ForeignKey(Label, on_delete=models.CASCADE)

    class Meta:
        app_label = "tests"


class DocumentCategory(AuditTrailModel):
    """Audited through-table for Document <-> UnauditedCategory."""
    document = models.ForeignKey(Document, on_delete=models.CASCADE)
    category = models.ForeignKey(UnauditedCategory, on_delete=models.CASCADE)

    class Meta:
        app_label = "tests"


# --- Models for Testing Inherited Audited State ---

class AbstractDocument(AuditTrailModel):
    title = models.CharField(max_length=255)
    author = models.CharField(max_length=100)

    class State:
        rating = models.IntegerField()

    class Meta:
        abstract = True
        app_label = "tests"


class BlogArticle(AbstractDocument):
    url = models.URLField()

    class Meta:
        app_label = "tests"


class Book(AbstractDocument):
    isbn = models.CharField(max_length=20, unique=True)

    class State:
        price = models.DecimalField(max_digits=10, decimal_places=2)

    class Meta:
        app_label = "tests"

# django-audit-trail

Transparent, race-condition resilient audit trail for Django models with queryable history and database-enforced integrity.

## Key Features

- **Transparent Anchor/State Split:** Audited fields are declared in a nested `State` block. The library automatically splits this into a public anchor table and a dynamically generated history (`<ModelName>State`) companion model. State fields remain exposed on the main model instance transparently.
- **Strict Data Integrity:** Chronological consistency is enforced at both the database and application level. Out-of-order/late-arriving events are rejected.
- **Race-Condition Resilient:** Automatically handles row-level database locking (`SELECT FOR UPDATE`) under the hood to ensure strict concurrency safety.
- **Thread & Async Safety:** Isolates the active audit event context using `contextvars`, making it safe for multi-threaded and asynchronous environments (like ASGI).
- **Soft Deletion:** Implemented out of the box via `.delete()`, marking records as revoked instead of permanently purging them.

---

## Installation

Ensure your project uses Django 6.0+ and Python 3.13+.

Add `django_audit_trail` to your `INSTALLED_APPS` in `settings.py`:

```python
INSTALLED_APPS = [
    ...,
    "django_audit_trail",
]
```

---

## Tutorial

### 1. Define Audited Models

Inherit from `AuditTrailModel` and define any fields you want to audit inside a nested `State` class.
Fields outside `State` reside on the main table (anchor) and are not versioned.

```python
from django.db import models
from django_audit_trail.models import AuditTrailModel

class Article(AuditTrailModel):
    # Anchor fields (unversioned, stored on the main table)
    slug = models.SlugField(unique=True)

    class State:
        # Audited fields (every change creates a new row in ArticleState table)
        title = models.CharField(max_length=200)
        content = models.TextField()
```

Run `makemigrations` and `migrate` normally. Django will generate the `Article` table
and the companion `ArticleState` table under the hood.

### 2. Creating and Updating within an Event Context

All modifications (creates, updates, soft-deletes) to audited models must occur within an active `audit_trail_event` context.
This models the "Who, When, Where, Why" of your changes (the 5Ws).

```python
from django_audit_trail.context import audit_trail_event
from django_audit_trail.models import Event

# 1. Create an audit event (representing a transaction boundary)
event = Event.objects.create(
    user=request.user,
    comment="Publishing first version",
    http_request_id="unique-request-uuid",
)

# 2. Perform operations inside the context
with audit_trail_event(event):
    # Create
    article = Article.objects.create(
        slug="hello-world",
        title="Hello World!",
        content="Welcome to my new blog.",
    )

    # Update
    article.title = "Hello World! (Updated)"
    article.save()
```

### 3. Querying History

Because history is saved in a standard companion model (`ArticleState`),
querying audit history is completely native and extremely fast.

```python
# Get the currently active (current) state of the article
print(article.title)  # "Hello World! (Updated)"

# Retrieve all historical versions of the state, including revoked/superseded ones
history = article.states.all_objects.order_by("created_event__timestamp")
for state in history:
    print(f"Version by {state.created_event.user} at {state.created_event.timestamp}: {state.title}")
```

### 4. Soft Deletion

Calling `.delete()` soft-deletes the entity by assigning a `revoked_event` under the hood.

```python
delete_event = Event.objects.create(user=request.user, comment="Removing spam article")

with audit_trail_event(delete_event):
    article.delete()

# Standard queries automatically exclude soft-deleted entities
assert Article.objects.filter(slug="hello-world").exists() is False

# Use all_objects to access soft-deleted records
assert Article.all_objects.filter(slug="hello-world").exists() is True
```

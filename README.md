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

Ensure your project uses Django 5.2+ and Python 3.12+.

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

### 5. Auditing Relationships (Foreign Keys & Many-to-Many)

To maintain absolute data integrity and represent the 5Ws ("Who, When, Where, Why") of every change,
relations are handled with the following architectural rules:

#### A) Foreign Keys (1:N)
If a relationship can change over time, define the `ForeignKey` inside the nested `State` class.
If the relationship defines an immutable identity, place it directly on the anchor model.

```python
class Article(AuditTrailModel):
    # Immutable identity relation (Anchor)
    creator = models.ForeignKey(User, on_delete=models.PROTECT)

    class State:
        # Mutable relation (State)
        editor = models.ForeignKey(User, on_delete=models.PROTECT)
```

#### B) Many-to-Many Relationships (N:M)
Standard, implicit `ManyToManyField` declarations (which automatically generate a hidden intermediate join table)
are **disallowed** in `django_audit_trail`. Since auto-generated join tables lack the metadata to track creation
and revocation events, they break the audit trail. This constraint is verified at startup via **Django System Checks**.

Instead, you must define an **explicit intermediate model** that inherits from `AuditTrailModel`:

```python
class Tag(AuditTrailModel):
    class State:
        name = models.CharField(max_length=50)

class Article(AuditTrailModel):
    tags = models.ManyToManyField(Tag, through="ArticleTag")

    class State:
        title = models.CharField(max_length=100)

class ArticleTag(AuditTrailModel):
    article = models.ForeignKey(Article, on_delete=models.CASCADE)
    tag = models.ForeignKey(Tag, on_delete=models.CASCADE)
```

With this pattern:
1. Every addition/removal of a relationship is an audited transaction bound to a dedicated `Event` block.
2. Lazy queries (`article.tags.all()`) and eagerly loaded queries (`prefetch_related('tags')`) automatically filter out soft-deleted connections.
3. Time travel (`with audit_trail_time_travel(...)`) automatically reconstructs which tags were linked to the article as of any historic point in time!

### 6. Instance Binding Propagation (Advanced Time-Travel)

`django_audit_trail` implements an advanced feature called **Instance Binding Propagation**.
This ensures that historical data integrity is maintained even when you execute code *outside*
of an active time-travel context, or when your object graphs pass through unaudited Django models.

When you load an instance inside a time-travel context, that specific instance permanently "remembers"
its historical bound state. Furthermore, when you navigate through relationships (like `ManyToManyField` or `ForeignKey`),
this historical context propagates like a virus through the entire object graph.

```python
with audit_trail_time_travel(historic_time):
    # Load the article in the past. It becomes permanently bound to `historic_time`.
    historic_article = Article.objects.get(pk=1)

# We have EXITED the time-travel block! We are now in present day.
# However, navigating relationships on `historic_article` continues to query history:

# 1. This returns the tags as they were at `historic_time` (even if unlinked today)
historic_tags = list(historic_article.tags.all())

# 2. The resolved target instances inherit the binding!
# This returns the historic name of the tag at `historic_time`
print(historic_tags[0].name)
```

**Unaudited Models:** This propagation is so robust that it successfully navigates through standard, non-audited Django models.
For example, in a chain like `Document (Audited)` -> `UnauditedCategory (Unaudited)` -> `Label (Audited)`,
resolving labels from a historically bound document will yield historically correct labels, seamlessly bypassing the "blind spot" of the unaudited category model!

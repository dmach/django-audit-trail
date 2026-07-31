from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models, transaction
from django.db.models import Q
from django.db.models.fields.related_descriptors import ManyToManyDescriptor
from django.utils.functional import cached_property
from django.utils import timezone
import copy
import typing

from .context import get_time_travel_event, get_audit_trail_event, BEFORE_FIRST_EVENT


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
                # Index only rows with value != ("", null).
                condition=~Q(http_request_id="") & Q(http_request_id__isnull=False),
            )
        ]

    def __str__(self):
        username = self.user.get_username()
        return f"Event {self.id} at {self.timestamp} by {username}"


class AuditTrailModelStateManager(models.Manager):
    """
    Manager for companion State tables that hides revoked entries.
    Supports time travel when a time-travel context is active.
    """
    def get_queryset(self):
        # If accessed via a related manager, self.instance points to the parent anchor model.
        instance = getattr(self, "instance", None)
        event = getattr(instance, "_django_audit_trail_time_travel_event", None) if instance is not None else None
        if event is None:
            event = get_time_travel_event()

        if event is None:
            # Return the latest state.
            return super().get_queryset().filter(revoked_event__isnull=True)

        if event is BEFORE_FIRST_EVENT:
            # Return an empty queryset when querying before the first Event.
            return super().get_queryset().none()

        # Return the state at the given event.
        return (
            super()
            .get_queryset()
            .filter(created_event_id__lte=event.id)
            .filter(Q(revoked_event__isnull=True) | Q(revoked_event_id__gt=event.id))
        )


class AuditTrailModelState(models.Model):
    """
    Abstract base for the companion State tables that hold the audited fields.
    Each edit is a new row; the latest non-revoked row is the current state.
    Here `revoked_event` marks a superseded version.
    Deletion of the entity is recorded on the anchor instead.
    A partial unique constraint ensures only one active state per anchor.
    """
    created_event = models.ForeignKey(Event, on_delete=models.PROTECT, related_name="+")
    revoked_event = models.ForeignKey(Event, on_delete=models.PROTECT, null=True, blank=True, related_name="+")

    # Active objects only (without revoked).
    objects = AuditTrailModelStateManager()

    # All objects (including revoked).
    all_objects = models.Manager()

    class Meta:
        abstract = True


def _clone_field(field):
    """
    Safely clones a Django field (including ForeignKeys) without calling .deconstruct()
    to avoid AppRegistryNotReady errors at class import time.
    """
    cloned = copy.deepcopy(field)
    # Assign a new creation_counter to preserve order.
    cloned.creation_counter = models.Field.creation_counter
    models.Field.creation_counter += 1
    return cloned


class AuditTrailMeta(models.base.ModelBase):
    """
    Metaclass that splits an audited model declaration into two parts:
      - The public anchor model (the class itself).
      - A dynamically generated `<ModelName>State` companion model and its matching table.

    The split is transparent. Fields defined in the nested `State` class are
    proxied directly to the anchor instance via descriptors. Models without
    a nested `State` class are left unmodified.
    """
    def __new__(mcs, name, bases, namespace, **kwargs):
        # Determine if the model is abstract before processing the namespace.
        meta_class = namespace.get("Meta")
        is_abstract = getattr(meta_class, "abstract", False) if meta_class is not None else False

        if is_abstract:
            # Keep the nested State class in abstract models.
            nested_state_class = namespace.get("State", None)
        else:
            # Remove the nested State class from the model, as it has no use after the <Name>State model is generated.
            nested_state_class = namespace.pop("State", None)

        if nested_state_class is not None and not isinstance(nested_state_class, type):
            raise RuntimeError(f"The 'State' attribute on {name} must be a class, got {type(nested_state_class).__name__}.")

        cls = super().__new__(mcs, name, bases, namespace, **kwargs)

        audit_bases = [b for b in bases if isinstance(b, mcs) and (hasattr(b, "State") or hasattr(b, "_state_model"))]

        if len(audit_bases) > 1:
            raise RuntimeError(f"Multiple inheritance with audited states is not supported on {name}.")

        if getattr(cls._meta, "abstract", False):
            # Don't create 'ModelName>State' companion model for abstract models.
            if audit_bases:
                raise RuntimeError(f"Abstract model {name} cannot inherit from another audited model. Multi-level inheritance is not supported.")
            return cls

        state_fields = {}

        if audit_bases:
            # Inherit fields from nested State class of the abstract base model.
            audit_base = audit_bases[0]
            if not getattr(audit_base._meta, "abstract", False):
                raise RuntimeError(f"Model {name} must inherit state from an abstract model, not {audit_base.__name__}.")

            if hasattr(audit_base, "State"):
                for key, value in vars(audit_base.State).items():
                    if isinstance(value, models.Field):
                        state_fields[key] = _clone_field(value)

        # Copy fields from the nested State class.
        if nested_state_class is not None:
            for key, value in vars(nested_state_class).items():
                if isinstance(value, models.Field):
                    # We could assign `value` directly since these are the model's own fields.
                    # However, cloning them through `_clone_field` assigns fresh, sequential
                    # creation_counters, guaranteeing they order after any inherited fields.
                    state_fields[key] = _clone_field(value)

        if not state_fields:
            return cls

        # Dynamically create the <Name>State companion model.
        State = type(
            f"{name}State",
            (AuditTrailModelState,),
            {
                "__module__": cls.__module__,
                # Link back to the anchor.
                "anchor": models.ForeignKey(cls, on_delete=models.PROTECT, related_name="states"),
                # Copy of state fields.
                **state_fields,
                # Class Meta.
                "Meta": type("Meta", (), {
                    "app_label": cls._meta.app_label,
                    "constraints": [
                        # Constraint that guarantees only one active state.
                        models.UniqueConstraint(
                            fields=["anchor"],
                            condition=Q(revoked_event__isnull=True),
                            name=f"{cls._meta.app_label}_{cls._meta.model_name}_one_active_state",
                        )
                    ],
                }),
            },
        )

        cls._state_model = State

        # Pre-compute and cache field metadata for optimal performance during save/draft operations
        internal_fields = {"id", "anchor", "created_event", "revoked_event"}
        cls._state_data_fields_list = [f for f in State._meta.fields if f.name not in internal_fields]
        cls._anchor_fields_set = {f.name for f in cls._meta.fields} | {f.attname for f in cls._meta.fields}
        cls._state_fields_set = {f.name for f in State._meta.fields} | {f.attname for f in State._meta.fields}
        cls._auto_now_fields = [f.attname for f in cls._meta.concrete_fields if getattr(f, "auto_now", False)]

        for field_name, field in state_fields.items():
            setattr(cls, field_name, _AuditTrailStateField(field_name, field.attname))
            if field.attname != field_name:
                setattr(cls, field.attname, _AuditTrailStateField(field.attname, field.attname))
        return cls


class _AuditTrailStateField(property):
    """
    Property descriptor that connects the anchor model to its State fields.
    It automatically routes all reads and writes to a temporary "draft" state.
    This allows Django to handle defaults and relationships (like ForeignKeys)
    normally, while letting us track changes. It subclasses `property` so
    fields can still be passed to the model's `__init__`.
    """
    def __init__(self, attr_name, field_attname):
        super().__init__()
        # The attribute this descriptor exposes: "title", "user" or "user_id".
        self.attr_name = attr_name
        # The normalized state column recorded as changed, e.g. "user_id".
        self.field_attname = field_attname

    def __get__(self, obj, owner=None):
        # Class-level access (e.g. MyModel.title) returns the descriptor itself.
        if obj is None:
            return self

        if obj.pk is not None and obj._loaded_state is None:
            raise AttributeError(
                f"Cannot access audited field '{self.attr_name}'. No state exists for "
                f"this {obj._meta.object_name} at the requested point in time."
            )

        return getattr(obj._draft_state, self.attr_name)

    def __set__(self, obj, value):
        setattr(obj._draft_state, self.attr_name, value)
        obj._draft_state_dirty_fields.add(self.field_attname)


class AuditTrailManager(models.Manager):
    """
    Hides soft-deleted (revoked) entities.
    Supports time travel when a time-travel context is active.
    """
    def get_queryset(self):
        event = get_time_travel_event()

        if event is None:
            # Return the latest state.
            return super().get_queryset().filter(revoked_event__isnull=True)

        if event is BEFORE_FIRST_EVENT:
            # Return an empty queryset when querying before the first Event.
            return super().get_queryset().none()

        # Return the state at the given event.
        return (
            super()
            .get_queryset()
            .filter(created_event_id__lte=event.id)
            .filter(Q(revoked_event__isnull=True) | Q(revoked_event_id__gt=event.id))
        )


class AuditTrailAllObjectsManager(models.Manager):
    """
    Manager that allows retrieving all anchor objects, but raises an exception
    if accessed during an active time-travel context to prevent out-of-bounds
    queries and preserve strict chronological isolation.
    """
    def get_queryset(self):
        if get_time_travel_event() is not None:
            raise ValidationError("Cannot query all_objects inside a time-travel context.")
        return super().get_queryset()


class AuditTrailManyToManyDescriptor(ManyToManyDescriptor):
    """
    Custom descriptor that explicitly replaces Django's default ManyToManyDescriptor
    on models participating in audited relationships to filter out soft-deleted
    relationships and respect the active time-travel context.
    """
    def __get__(self, instance, owner=None):
        manager = super().__get__(instance, owner)
        # Class-level access (e.g. MyModel.categories) returns the default manager.
        if instance is None:
            return manager

        through = manager.through
        if not issubclass(through, AuditTrailModel):
            return manager

        original_get_queryset = manager.get_queryset

        def get_queryset():
            qs = original_get_queryset()

            # Find the relation query path from target model back to the through model.
            target_field = through._meta.get_field(manager.target_field_name)
            query_path = target_field.related_query_name()

            # Retrieve active time-travel event
            event = getattr(instance, "_django_audit_trail_time_travel_event", None)
            if event is None:
                event = get_time_travel_event()

            if event is None:
                # Standard view (exclude soft-deleted relationships).
                revoked_null_filter = f"{query_path}__revoked_event__isnull"
                return qs.filter(**{revoked_null_filter: True})

            if event is BEFORE_FIRST_EVENT:
                return qs.none()

            # Apply time-travel filtering.
            created_filter = f"{query_path}__created_event_id__lte"
            revoked_null_filter = f"{query_path}__revoked_event__isnull"
            revoked_gt_filter = f"{query_path}__revoked_event_id__gt"

            qs = qs.filter(**{created_filter: event.id}).filter(
                Q(**{revoked_null_filter: True}) | Q(**{revoked_gt_filter: event.id})
            )

            # Dynamically subclass the QuerySet to propagate the time-travel event
            # to all instantiated target models upon execution/evaluation.
            class AuditTrailContextPropagatingQuerySet(qs.__class__):
                def _fetch_all(self):
                    super()._fetch_all()
                    if self._result_cache:
                        for obj in self._result_cache:
                            if isinstance(obj, models.Model) and getattr(obj, "_django_audit_trail_time_travel_event", None) is None:
                                obj._django_audit_trail_time_travel_event = event

                def iterator(self, *args, **kwargs):
                    for obj in super().iterator(*args, **kwargs):
                        if isinstance(obj, models.Model) and getattr(obj, "_django_audit_trail_time_travel_event", None) is None:
                            obj._django_audit_trail_time_travel_event = event
                        yield obj

            qs.__class__ = AuditTrailContextPropagatingQuerySet
            return qs

        manager.get_queryset = get_queryset
        return manager


def _assert_event_not_stale(loaded_state, event):
    """
    Prevents saving changes out of order (late arrivals).
    We cannot apply an event that is older than the current state, because
    it would break the audit history timeline.

    Raises a `ValidationError` if the event is older than the latest event in the history timeline.
    """
    if loaded_state is not None and event.timestamp < loaded_state.created_event.timestamp:
        raise ValidationError(
            f"Chronological consistency error: Cannot apply event {event.id} "
            f"({event.timestamp}) because a newer state from event "
            f"{loaded_state.created_event.id} "
            f"({loaded_state.created_event.timestamp}) already exists."
        )


def _assert_not_in_time_travel_mode(instance):
    """
    Prevents modifying or deleting models when a time-travel context is active,
    or if the specific instance was loaded during time-travel.
    """
    if getattr(instance, "_django_audit_trail_time_travel_event", None) is not None or get_time_travel_event() is not None:
        raise ValidationError("Cannot modify or delete models in time-travel mode.")


class AuditTrailModel(models.Model, metaclass=AuditTrailMeta):
    """
    Base class for models that require an audit trail.

    When you inherit from this class and define a nested `State` class,
    the history of changes for all fields in `State` is preserved. Every change
    creates a new version in the history timeline, while keeping the model's
    API identical to a standard Django model.

    Soft-deletion is supported via the `.delete()` method, which marks
    the instance as revoked instead of permanently removing it from the database.
    """
    created_event = models.ForeignKey(Event, on_delete=models.PROTECT, related_name="+")
    revoked_event = models.ForeignKey(Event, on_delete=models.PROTECT, null=True, blank=True, related_name="+")

    # Active objects only (without revoked).
    objects = AuditTrailManager()

    # All objects (including revoked).
    all_objects = AuditTrailAllObjectsManager()

    class Meta:
        abstract = True

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        event = get_time_travel_event()
        if event is not None:
            self._django_audit_trail_time_travel_event = event

    @classmethod
    def from_db(cls, db, field_names, values):
        instance = super().from_db(db, field_names, values)
        # Snapshot the original anchor values upon load.
        # This allows us to detect which fields were modified in memory
        # and avoid overwriting concurrent database changes during save().
        instance.__dict__["_loaded_anchor"] = dict(zip(field_names, values))

        # Capture the active time-travel point at load time.
        event = get_time_travel_event()
        if event is not None:
            instance._django_audit_trail_time_travel_event = event

        return instance

    def lock(self, using=None) -> typing.Self:
        """
        Acquires a `SELECT FOR UPDATE` row-level database lock on this anchor instance
        and returns the locked instance from the database.

        Raises a `ValueError` if the instance is not yet saved to the database.
        """
        if self.pk is None:
            raise ValueError(f"Cannot lock an unsaved {self._meta.object_name} object.")
        return self.__class__.all_objects.using(using).select_for_update().get(pk=self.pk)

    @property
    def _draft_state(self):
        """
        A temporary working copy of the state fields. All state reads and writes
        are routed through here so Django can naturally handle defaults and relationships.
        This draft is discarded after a successful save.
        """
        if self.__dict__.get("_draft_state_cache") is None:
            draft = self._state_model()
            current = self._loaded_state
            if current is not None:
                for f in self._state_data_fields_list:
                    setattr(draft, f.attname, getattr(current, f.attname))
            self.__dict__["_draft_state_cache"] = draft
        return self.__dict__["_draft_state_cache"]

    @property
    def _draft_state_dirty_fields(self):
        """
        A set of state field names that have been modified in memory.
        This tracks which specific fields need to be saved to the database.
        """
        return self.__dict__.setdefault("_draft_state_dirty_fields_set", set())

    @cached_property
    def _loaded_state(self):
        """
        Returns the currently active state row from the database.
        """
        if not hasattr(self, "_state_model") or self.pk is None:
            return None

        # Slicing the queryset (states[0]) instead of .first() prevents N+1 queries
        # by allowing Django's prefetch_related to cache the result.
        states = self.states.all()
        return states[0] if states else None

    def save(self, *args, **kwargs):
        """
        Saves the current instance, handling transparent state splitting, history tracking,
        and database row locking for concurrency protection.

        Raises a `RuntimeError` if attempting to save a soft-deleted model.
        Raises a `ValidationError` if the event breaks the chronological audit timeline.
        """
        _assert_not_in_time_travel_mode(self)

        if self.revoked_event_id is not None:
            raise RuntimeError(f"{self._meta.object_name} is already deleted.")

        event = get_audit_trail_event()
        using = kwargs.get("using")

        if kwargs.get("update_fields") is not None:
            raise ValueError("update_fields is not supported by django_audit_trail")

        # Use _state.adding to reliably detect an insert. Relying on self.pk
        # is unsafe for models with explicit or UUID primary keys.
        is_new = self._state.adding

        # For models without a State table, write the anchor row directly 
        # and skip all the transaction and history machinery.
        if not hasattr(self, "_state_model"):
            if is_new and self.created_event_id is None:
                self.created_event = event
            super().save(*args, **kwargs)
            return

        should_save_state = is_new or bool(self._draft_state_dirty_fields)

        # If no state fields were modified, just save the anchor and exit.
        if not should_save_state:
            super().save(*args, **kwargs)
            return

        # Note: If the transaction fails, the database rolls back, but the in-memory 
        # object remains dirty and potentially inconsistent. Do not reuse it.
        with transaction.atomic(using=using):
            if is_new and self.created_event_id is None:
                self.created_event = event

            # For existing anchors, acquire a row-level lock (SELECT FOR UPDATE).
            # This serializes concurrent writers, ensures the anchor isn't soft-deleted,
            # and allows us to safely read the freshest active state.
            # (Locking is skipped for new anchors, which are safe due to MVCC visibility
            # and unique database constraints.)
            current = None
            if not is_new and self.pk is not None:
                locked_anchor = self.lock(using=using)
                if locked_anchor.revoked_event_id is not None:
                    raise RuntimeError(f"{self._meta.object_name} is already deleted.")
                # Drop any cached _loaded_state to ensure we read the fresh row.
                self.__dict__.pop("_loaded_state", None)
                # Use select_related on created_event to make the late-arrival check query-free.
                current = (
                    self._state_model.objects.using(using)
                    .select_related("created_event")
                    .filter(anchor=self)
                    .first()
                )
                # Block late arrivals under the lock, before performing any writes.
                _assert_event_not_stale(current, event)

            # Persist the anchor. We strictly write only the fields changed in this process
            # (based on the loaded snapshot) to avoid overwriting concurrent updates.
            if is_new:
                super().save(*args, **kwargs)
            else:
                auto_now = self._auto_now_fields
                loaded = self.__dict__.get("_loaded_anchor")
                if loaded is None:
                    # Object was built in memory, not loaded. Safely update auto_now fields only.
                    changed = auto_now
                else:
                    changed = [f.attname for f in self._meta.concrete_fields if f.attname in loaded and getattr(self, f.attname) != loaded[f.attname]]
                changed = list(set(changed).union(auto_now))
                if changed:
                    super().save(update_fields=changed, using=using)

            if current is None:
                current = self._loaded_state

            # Build the new state row: modified fields come from the draft, while untouched
            # fields are copied from the freshly-locked database row to prevent data loss.
            new_values = {}
            for f in self._state_data_fields_list:
                if f.attname in self._draft_state_dirty_fields:
                    new_values[f.attname] = getattr(self._draft_state, f.attname)
                elif current is not None:
                    new_values[f.attname] = getattr(current, f.attname)
                else:
                    new_values[f.attname] = getattr(self._draft_state, f.attname)

            # Supersede the active state and insert the new one.
            # We revoke before inserting to satisfy the "one active state" DB constraint.
            if current is not None:
                self._state_model.objects.using(using).filter(anchor=self).update(revoked_event=event)
            new_state = self._state_model.objects.using(using).create(
                anchor=self, created_event=event, **new_values
            )

            # Sync in-memory caches after a successful write: cache the new current
            # state, and selectively clean up the draft and dirty trackers.
            self.__dict__["_loaded_state"] = new_state

            # Discard the draft and the dirty fields set.
            self.__dict__.pop("_draft_state_cache", None)
            self.__dict__.pop("_draft_state_dirty_fields_set", None)
            self.__dict__["_loaded_anchor"] = {
                f.attname: getattr(self, f.attname) for f in self._meta.concrete_fields
            }

    def refresh_from_db(self, using=None, fields=None):
        """
        Reloads the instance from the database and clears all internal state caches.
        """
        super().refresh_from_db(using=using, fields=fields)

        # Clear internal state caches so they are re-fetched on next access
        self.__dict__.pop("_loaded_state", None)
        self.__dict__.pop("_draft_state_cache", None)
        self.__dict__.pop("_draft_state_dirty_fields_set", None)
        self.__dict__.pop("_loaded_anchor", None)

    def delete(self, *args, **kwargs):
        """
        Soft-deletes the instance by marking it as revoked.
        Acquires a `SELECT FOR UPDATE` lock on the anchor row to prevent concurrent race conditions.

        Raises a `ValueError` if the instance is unsaved or has already been deleted in memory.
        """
        _assert_not_in_time_travel_mode(self)

        if self.pk is None or getattr(self, "_deleted_in_memory", False):
            raise ValueError(f"{self._meta.object_name} object can't be deleted because its id attribute is set to None.")

        event = get_audit_trail_event()
        using = kwargs.get("using")

        with transaction.atomic(using=using):
            current_anchor = self.lock(using=using)

            if current_anchor.revoked_event_id is not None:
                return (0, {})

            # Prevent chronological errors: a deletion is just another state mutation.
            # We compare against the locked database state to ensure the event isn't too old.
            if hasattr(self, "_state_model"):
                loaded_state = (
                    self._state_model.objects.using(using)
                    .select_related("created_event")
                    .filter(anchor=self)
                    .first()
                )
                _assert_event_not_stale(loaded_state, event)

            self.revoked_event = event
            super().save(update_fields=["revoked_event"], using=using)

            # Prevent double-deletion errors on the same in-memory instance.
            self._deleted_in_memory = True

            return (1, {self._meta.label: 1})

import pytest
from django_audit_trail.context import audit_trail_event
from django_audit_trail.models import Event
from tests.models import PullRequest, UUIDModel


@pytest.mark.django_db
def test_create_and_delete_audited_model_with_context(alice, bob):
    """
    Ensure creating and deleting an AuditTrailModel within an active
    event context assigns the proper events correctly.
    """
    event_create = Event.objects.create(user=alice, comment="Creating pull request")
    event_delete = Event.objects.create(user=bob, comment="Deleting pull request")

    # Test creation within context.
    with audit_trail_event(event_create):
        obj = PullRequest.objects.create(
            owner="octocat",
            repo="hello-world",
            number=1,
            title="Audited PullRequest test",
        )

    # Check that created_event is properly assigned.
    assert obj.created_event == event_create
    assert obj.revoked_event is None

    # Retrieve from DB to verify persistence.
    db_obj = PullRequest.objects.get(pk=obj.pk)
    assert db_obj.created_event == event_create
    assert db_obj.revoked_event is None
    assert db_obj.owner == "octocat"
    assert db_obj.repo == "hello-world"
    assert db_obj.number == 1
    assert db_obj.title == "Audited PullRequest test"

    # Test deletion within context.
    with audit_trail_event(event_delete):
        db_obj.delete()

    # Verify that revoked_event is assigned on the soft-deleted instance.
    assert db_obj.revoked_event == event_delete

    # Retrieve from DB using all_objects to verify persistence of revocation.
    db_obj_after = PullRequest.all_objects.get(pk=obj.pk)
    assert db_obj_after.revoked_event == event_delete

    # Verify that standard objects manager does NOT return the revoked object.
    with pytest.raises(PullRequest.DoesNotExist):
        PullRequest.objects.get(pk=obj.pk)


@pytest.mark.django_db
def test_create_outside_context_raises_error():
    """
    Ensure that trying to create an AuditTrailModel outside an active
    event context raises a RuntimeError.
    """
    with pytest.raises(RuntimeError, match="No active audit trail event context"):
        PullRequest.objects.create(
            owner="octocat",
            repo="hello-world",
            number=1,
        )


@pytest.mark.django_db
def test_delete_outside_context_raises_error(alice):
    """
    Ensure that trying to delete an AuditTrailModel outside an active
    event context raises a RuntimeError.
    """
    event_create = Event.objects.create(user=alice, comment="Creation")

    # Create within context.
    with audit_trail_event(event_create):
        obj = PullRequest.objects.create(
            owner="octocat",
            repo="hello-world",
            number=1,
        )

    # Attempt delete outside context.
    with pytest.raises(RuntimeError, match="No active audit trail event context"):
        obj.delete()


@pytest.mark.django_db
def test_double_delete_behavior(alice, bob):
    """
    Ensure that calling delete() twice on the same in-memory instance raises ValueError,
    while calling delete() on a different in-memory instance of an already soft-deleted
    row is idempotent and returns (0, {}).
    """
    event_create = Event.objects.create(user=alice, comment="Creation")
    event_delete1 = Event.objects.create(user=bob, comment="First deletion")
    event_delete2 = Event.objects.create(user=bob, comment="Second deletion")

    # Create within context.
    with audit_trail_event(event_create):
        obj = PullRequest.objects.create(
            owner="octocat",
            repo="hello-world",
            number=1,
            title="Double Delete Test PR",
        )

    # First delete within context.
    with audit_trail_event(event_delete1):
        res1 = obj.delete()
        assert res1 == (1, {"tests.PullRequest": 1})

    # Scenario A: second delete on the same in-memory instance raises ValueError because pk is None
    with audit_trail_event(event_delete2):
        with pytest.raises(ValueError, match="PullRequest object can't be deleted because its id attribute is set to None"):
            obj.delete()

    # Scenario B: second delete on a different in-memory instance of the already deleted row is idempotent
    obj_different = PullRequest.all_objects.get(owner="octocat", repo="hello-world", number=1)
    with audit_trail_event(event_delete2):
        res2 = obj_different.delete()
        assert res2 == (0, {})


@pytest.mark.django_db
def test_audit_trail_managers(alice, bob):
    """
    Verify that the default 'objects' manager automatically filters out
    revoked (soft-deleted) entities, while 'all_objects' retrieves everything.
    """
    event_create = Event.objects.create(user=alice, comment="Batch creation")
    event_delete = Event.objects.create(user=bob, comment="Delete half of batch")

    # Create active objects.
    with audit_trail_event(event_create):
        pr1 = PullRequest.objects.create(
            owner="octocat",
            repo="hello-world",
            number=1,
            title="PR 1",
        )
        pr2 = PullRequest.objects.create(
            owner="octocat",
            repo="hello-world",
            number=2,
            title="PR 2",
        )
        pr3 = PullRequest.objects.create(
            owner="octocat",
            repo="hello-world",
            number=3,
            title="PR 3",
        )

    # All three should be active initially.
    assert PullRequest.objects.count() == 3
    assert PullRequest.all_objects.count() == 3

    # Delete (revoke) pr2.
    with audit_trail_event(event_delete):
        pr2.delete()

    # Verify filtering under 'objects' manager.
    active_prs = list(PullRequest.objects.all())
    assert len(active_prs) == 2
    assert pr1 in active_prs
    assert pr3 in active_prs
    assert pr2 not in active_prs

    # Verify 'all_objects' manager still returns everything.
    all_prs = list(PullRequest.all_objects.all())
    assert len(all_prs) == 3
    assert pr1 in all_prs
    assert pr2 in all_prs
    assert pr3 in all_prs

    # Verify queryset filtering on 'objects'.
    assert PullRequest.objects.filter(number=1).exists()
    assert not PullRequest.objects.filter(number=2).exists()

    # Verify queryset filtering on 'all_objects'.
    assert PullRequest.all_objects.filter(number=2).exists()


@pytest.mark.django_db
def test_update_state_field_creates_new_state(alice, bob):
    """
    Ensure updating an audited state field creates a new state record,
    correctly links it to the new event, and revokes the older state row.
    Also verify updated_at changes.
    """
    event_create = Event.objects.create(user=alice, comment="PR creation")
    event_update = Event.objects.create(user=bob, comment="PR title update")

    # Create within initial context.
    with audit_trail_event(event_create):
        pr = PullRequest.objects.create(
            owner="octocat",
            repo="hello-world",
            number=1,
            title="Initial Title",
        )

    initial_updated_at = pr.updated_at
    assert initial_updated_at is not None

    # Update state field within a new context
    with audit_trail_event(event_update):
        pr.title = "Updated Title"
        pr.save()

    # Verify resolved property has updated value
    assert pr.title == "Updated Title"

    # Verify that updated_at was refreshed/updated on the anchor
    assert pr.updated_at > initial_updated_at

    # Total companion states should be 2 (1 active, 1 revoked)
    StateClass = pr._state_model
    assert StateClass.all_objects.count() == 2
    assert StateClass.objects.count() == 1

    # Check that older state row is revoked by event_update
    old_state = StateClass.all_objects.get(revoked_event__isnull=False)
    assert old_state.title == "Initial Title"
    assert old_state.created_event == event_create
    assert old_state.revoked_event == event_update

    # Check that newer state row is active
    active_state = StateClass.objects.get()
    assert active_state.title == "Updated Title"
    assert active_state.created_event == event_update
    assert active_state.revoked_event is None


@pytest.mark.django_db
def test_update_anchor_only_field_does_not_create_new_state(alice, bob):
    """
    Ensure updating only anchor fields (like updated_at or other non-state fields)
    does NOT generate a new companion State row or revoke the active state.
    """
    event_create = Event.objects.create(user=alice, comment="PR creation")
    event_update = Event.objects.create(user=bob, comment="No-op state save")

    with audit_trail_event(event_create):
        pr = PullRequest.objects.create(
            owner="octocat",
            repo="hello-world",
            number=1,
            title="Consistent Title",
        )

    initial_updated_at = pr.updated_at
    StateClass = pr._state_model
    assert StateClass.all_objects.count() == 1

    # Save again under a new context without modifying state fields
    # Here, save() will update updated_at automatically, but self._draft_state_dirty_fields is empty
    with audit_trail_event(event_update):
        pr.save()

    # Verify updated_at changed
    assert pr.updated_at > initial_updated_at

    # Verify NO new companion State row was created
    assert StateClass.all_objects.count() == 1
    state = StateClass.objects.get()
    assert state.title == "Consistent Title"
    assert state.created_event == event_create
    assert state.revoked_event is None


@pytest.mark.django_db
def test_chronological_consistency_validation(alice, bob):
    """
    Ensure chronological checks prevent applying/saving a state row with an
    event that has an older timestamp than the currently active state row.
    """
    from django.core.exceptions import ValidationError
    from django.utils import timezone
    from datetime import timedelta

    now = timezone.now()
    event_new = Event.objects.create(user=alice, comment="New Event", timestamp=now)
    event_old = Event.objects.create(user=bob, comment="Old Event", timestamp=now - timedelta(days=1))

    # Create model in the newer event
    with audit_trail_event(event_new):
        pr = PullRequest.objects.create(
            owner="octocat",
            repo="hello-world",
            number=1,
            title="New State Title",
        )

    # Attempt to update the state using the older event
    with audit_trail_event(event_old):
        pr.title = "Older State Title Attempt"
        with pytest.raises(ValidationError, match="Chronological consistency error"):
            pr.save()

    # Verify that nothing was changed in the DB
    pr_refresh = PullRequest.objects.get(pk=pr.pk)
    assert pr_refresh.title == "New State Title"
    assert pr_refresh._state_model.all_objects.count() == 1


@pytest.mark.django_db
def test_delete_chronological_consistency_validation(alice, bob):
    """
    Ensure late-arriving deletions are blocked.

    Calling delete() with an Event whose timestamp is older than the currently
    active state's created_event ("late arrival") MUST raise a ValidationError
    and MUST NOT revoke the anchor.
    """
    from django.core.exceptions import ValidationError
    from django.utils import timezone
    from datetime import timedelta

    now = timezone.now()
    event_new = Event.objects.create(user=alice, comment="New Event", timestamp=now)
    event_old = Event.objects.create(
        user=bob, comment="Old Event", timestamp=now - timedelta(days=1)
    )

    # Create the model within the newer event.
    with audit_trail_event(event_new):
        pr = PullRequest.objects.create(
            owner="octocat",
            repo="hello-world",
            number=1,
            title="New State Title",
        )

    # Attempt to delete using the older event (out-of-order / late arrival).
    with audit_trail_event(event_old):
        with pytest.raises(ValidationError, match="Chronological consistency error"):
            pr.delete()

    # Verify the object was NOT revoked in the database.
    pr_refresh = PullRequest.all_objects.get(pk=pr.pk)
    assert pr_refresh.revoked_event is None
    # It must still be visible through the default (active-only) manager.
    assert PullRequest.objects.filter(pk=pr.pk).exists()


@pytest.mark.django_db
def test_no_op_state_reassignment_creates_state(alice, bob):
    """
    Ensure explicitly re-assigning the same value to a state field (e.g. pr.title = pr.title)
    still triggers a state change (it writes to _draft_state_dirty_fields) and writes a new State row.
    """
    event_create = Event.objects.create(user=alice, comment="PR creation")
    event_reassign = Event.objects.create(user=bob, comment="Title reassignment")

    with audit_trail_event(event_create):
        pr = PullRequest.objects.create(
            owner="octocat",
            repo="hello-world",
            number=1,
            title="Same Title",
        )

    # Re-assign the same title under a new event context
    with audit_trail_event(event_reassign):
        pr.title = "Same Title"
        pr.save()

    # Verify that a new state record was indeed created
    StateClass = pr._state_model
    assert StateClass.all_objects.count() == 2
    assert StateClass.objects.count() == 1

    # Check revoked state
    old_state = StateClass.all_objects.get(revoked_event__isnull=False)
    assert old_state.title == "Same Title"
    assert old_state.created_event == event_create
    assert old_state.revoked_event == event_reassign

    # Check active state
    active_state = StateClass.objects.get()
    assert active_state.title == "Same Title"
    assert active_state.created_event == event_reassign
    assert active_state.revoked_event is None


@pytest.mark.django_db
def test_save_failure_rolls_back_database(alice):
    """
    On a failed save the transaction MUST be rolled back so the database is left
    consistent (no partial writes). The in-memory instance is left as-is; callers
    are expected to discard and re-fetch it (see requirements-audit-trail.md).
    """
    from django.db import IntegrityError

    event_create = Event.objects.create(user=alice, comment="PR creation")

    with audit_trail_event(event_create):
        PullRequest.objects.create(
            owner="octocat",
            repo="hello-world",
            number=1,
            title="PR 1",
        )

    # A duplicate (owner, repo, number) makes the INSERT fail at the DB level.
    pr2 = PullRequest(
        owner="octocat",
        repo="hello-world",
        number=1,
        title="Duplicate PR",
    )

    with audit_trail_event(event_create):
        with pytest.raises(IntegrityError):
            pr2.save()

    # The database is unchanged: still exactly one anchor and one state row.
    assert PullRequest.all_objects.count() == 1
    assert PullRequest.all_objects.get().title == "PR 1"


@pytest.mark.django_db
def test_update_fields_behavior(alice, bob):
    """
    Ensure that save(update_fields=...) correctly filters anchor and state fields.
    1. Saving only anchor fields (like owner) does not write a state row, and leaves pending state edits intact.
    2. Saving only state fields (like title) writes a state row but does not persist unsaved anchor changes.
    """
    event_create = Event.objects.create(user=alice, comment="PR creation")
    event_update1 = Event.objects.create(user=bob, comment="Update anchor only")
    event_update2 = Event.objects.create(user=bob, comment="Update state only")

    # 1. Create original PR
    with audit_trail_event(event_create):
        pr = PullRequest.objects.create(
            owner="octocat",
            repo="hello-world",
            number=1,
            title="Original Title",
        )

    # 2. Modify both in memory: an anchor field (owner) and a state field (title)
    pr.owner = "new-owner"
    pr.title = "Staged New Title"

    # Save only the anchor field "owner"
    with audit_trail_event(event_update1):
        pr.save(update_fields=["owner"])

    # DB Assertions:
    # - Anchor field 'owner' should be updated
    # - Companion state should NOT have been updated (still 1 state row in total)
    # - Pending state edit 'title' is still intact in _draft_state_dirty_fields
    pr_db = PullRequest.objects.get(pk=pr.pk)
    assert pr_db.owner == "new-owner"
    assert pr._state_model.all_objects.count() == 1
    assert pr.title == "Staged New Title"

    # 3. Now modify another anchor field in memory but save only "title"
    pr.repo = "new-repo"

    with audit_trail_event(event_update2):
        pr.save(update_fields=["title"])

    # DB Assertions:
    # - Companion state should now be updated (total 2 states)
    # - Pending state edit 'title' is cleared from _draft_state_dirty_fields
    # - Anchor field 'repo' was NOT saved (should still be 'hello-world')
    pr_db2 = PullRequest.objects.get(pk=pr.pk)
    assert pr_db2.title == "Staged New Title"
    assert pr_db2.repo == "hello-world"  # remains unchanged in DB
    assert "title" not in pr._draft_state_dirty_fields
    assert pr._state_model.all_objects.count() == 2


@pytest.mark.django_db
def test_loaded_state_caching(django_assert_num_queries, alice):
    """
    Ensure that accessing state fields caches `_loaded_state` and
    does not trigger N+1 query overhead.
    """
    event_create = Event.objects.create(user=alice, comment="PR creation")
    with audit_trail_event(event_create):
        pr = PullRequest.objects.create(
            owner="octocat", repo="hello-world", number=1, title="Test PR"
        )

    # Fetch a fresh instance from the database
    fetched_pr = PullRequest.objects.get(pk=pr.pk)

    # First access will hit the database to retrieve _loaded_state
    # Subsequent accesses should use the cache
    with django_assert_num_queries(1):
        # Access title multiple times
        t1 = fetched_pr.title
        t2 = fetched_pr.title
        assert t1 == t2 == "Test PR"

    # Save a new state and ensure cache is updated/cleared
    event_update = Event.objects.create(user=alice, comment="PR update")
    with audit_trail_event(event_update):
        fetched_pr.title = "Updated PR"
        fetched_pr.save()

    # The save method should have updated the cache
    with django_assert_num_queries(0):
        assert fetched_pr.title == "Updated PR"


@pytest.mark.django_db
def test_delete_pk_none_raises_value_error():
    """
    Ensure that calling delete() on an unsaved model instance (pk is None)
    raises a ValueError.
    """
    pr = PullRequest(owner="octocat", repo="hello-world", number=1)
    with pytest.raises(ValueError, match="PullRequest object can't be deleted because its id attribute is set to None"):
        pr.delete()


@pytest.mark.filterwarnings("ignore:Error when trying to teardown test databases:pytest.PytestWarning")
def test_state_unique_constraint_name_is_namespaced_by_app_label():
    """
    B6: The generated "one active state" unique constraint must be namespaced by
    the app label. Otherwise two apps defining a model with the same name would
    produce colliding constraint names in the database.
    """
    state_model = PullRequest._state_model
    constraint_names = {c.name for c in state_model._meta.constraints}

    expected = (
        f"{PullRequest._meta.app_label}_{PullRequest._meta.model_name}"
        "_one_active_state"
    )
    assert expected in constraint_names
    # The old, un-namespaced name must no longer be used.
    assert f"{PullRequest._meta.model_name}_one_active_state" not in constraint_names


@pytest.mark.django_db
def test_concurrent_soft_deletion_safeguard(alice, bob):
    """
    Verify that delete() locks the anchor row and reads the up-to-date
    revoked_event_id from the database, preventing duplicate deletion mutations
    by being idempotent and returning (0, {}) when already deleted in the database.
    """
    event_create = Event.objects.create(user=alice, comment="Creation")
    event_delete_concurrent = Event.objects.create(user=bob, comment="Concurrent delete")
    event_delete_main = Event.objects.create(user=bob, comment="Main delete")

    with audit_trail_event(event_create):
        pr = PullRequest.objects.create(
            owner="octocat", repo="hello-world", number=1, title="PR"
        )

    # Get two in-memory instances representing concurrent requests.
    pr_process1 = PullRequest.objects.get(pk=pr.pk)
    pr_process2 = PullRequest.objects.get(pk=pr.pk)

    # Process 2 soft-deletes the object in the database out-of-band.
    with audit_trail_event(event_delete_concurrent):
        res2 = pr_process2.delete()
        assert res2 == (1, {"tests.PullRequest": 1})

    # Process 1 still has revoked_event_id=None in memory.
    # When it tries to delete, it should fetch the locked row, detect that it
    # is already soft-deleted in the database, and return (0, {}) idempotently.
    with audit_trail_event(event_delete_main):
        res1 = pr_process1.delete()
        assert res1 == (0, {})


@pytest.mark.django_db
def test_race_condition_stale_cache(alice, bob):
    """
    BUG REPRODUCTION: Lost Updates Concurrency Race Condition (Critical)

    Description:
    When an audited state field is accessed (e.g. `pr.title`), the companion state
    row is cached in the `_loaded_state` property cache (via Django's @cached_property).
    When `save()` is subsequently called, it correctly acquires a database SELECT FOR UPDATE
    lock on the anchor row. However, it then calls `self._loaded_state` to build the new state values,
    which returns the cached, stale state row from BEFORE the lock was acquired!

    If another process/host concurrently modified a different state field in the database
    after this process read the object, the first process's update will completely
    overwrite the concurrent changes with the stale cached values.
    """
    event1 = Event.objects.create(user=alice, comment="Initial Event")
    event2 = Event.objects.create(user=bob, comment="Concurrent Event 2")
    event3 = Event.objects.create(user=alice, comment="Concurrent Event 3")

    with audit_trail_event(event1):
        pr = PullRequest.objects.create(
            owner="octocat",
            repo="hello-world",
            number=1,
            title="Initial",
            description="Initial Description",
        )

    # 1. Process A retrieves the object and reads a state field, triggering in-memory state caching.
    pr_a = PullRequest.objects.get(pk=pr.pk)
    _ = pr_a.title  # State is cached in pr_a's _loaded_state cached_property

    # 2. Process B retrieves the same object, modifies a DIFFERENT field, and saves it.
    pr_b = PullRequest.objects.get(pk=pr.pk)
    with audit_trail_event(event2):
        pr_b.description = "Updated by Process B"
        pr_b.save()

    # 3. Process A modifies its original field and saves it.
    with audit_trail_event(event3):
        pr_a.title = "Updated by Process A"
        pr_a.save()

    # 4. Fetch the final state of the object in the database.
    pr_final = PullRequest.objects.get(pk=pr.pk)

    # EXPECTATION (Under correctness):
    # Process A should NOT have overwritten Process B's concurrent modification.
    # Process A changed 'title', and Process B changed 'description'. Both changes must persist.
    assert pr_final.title == "Updated by Process A"
    assert pr_final.description == "Updated by Process B"


@pytest.mark.django_db
def test_attribute_error_on_optional_state_fields(alice):
    """
    BUG REPRODUCTION: AttributeError on Optional/Default State Fields during Creation (Logical Bug)

    Description:
    During a new instance creation, `current = self._loaded_state` evaluates to `None`.
    When building state values to write, `save()` attempts to fallback to default values
    using: `getattr(current, field.name, field.get_default())`
    But because `current` is `None`, Python throws:
    `AttributeError: 'NoneType' object has no attribute 'description'`
    instead of gracefully falling back to the field's default value.
    """
    event = Event.objects.create(user=alice, comment="Creation Event")

    # If we do not specify the optional/default field 'description' during creation,
    # it should still succeed and set it to its default value 'Default Description'.
    # In the current implementation, this crashes with AttributeError.
    with audit_trail_event(event):
        pr = PullRequest.objects.create(
            owner="octocat",
            repo="hello-world",
            number=2,
            title="Succeeds or Crashes?",
        )

    assert pr.description == "Default Description"


@pytest.mark.django_db
def test_preset_primary_keys_is_new_check(alice):
    """
    BUG REPRODUCTION: Pre-set Primary Keys (UUIDs / Custom IDs) Break is_new check (Logical Bug)

    Description:
    In `save()`, `is_new` is determined via `is_new = self.pk is None`.
    When saving a model instance with a pre-set primary key (e.g. UUIDs), `self.pk` is NOT None
    even before the row is created in the database. Therefore, `is_new` is incorrectly evaluated
    as `False`. This skips assigning `created_event` to the model and bypasses initialization checks,
    which causes IntegrityError in the database.
    """
    event = Event.objects.create(user=alice, comment="UUID Creation")

    # Creating a new instance of a model with a UUID/explicit PK should succeed and assign created_event.
    with audit_trail_event(event):
        obj = UUIDModel.objects.create(
            value="UUID Test",
        )

    # In the buggy implementation, obj.created_event is never set because is_new was evaluated as False,
    # causing an IntegrityError in the database (since created_event is not nullable) or a validation error.
    assert obj.created_event == event


@pytest.mark.django_db
def test_preset_pk_create_skips_lock_then_update_locks(alice, bob):
    """
    A model with a pre-set primary key (e.g. UUID) must save without attempting
    to lock a not-yet-existing row: creation is a new anchor, so the anchor lock
    (SELECT FOR UPDATE ... .get()) is skipped entirely rather than being attempted
    and swallowing the resulting DoesNotExist. A subsequent update of the now
    existing row locks it and supersedes the state normally.
    """
    event_create = Event.objects.create(user=alice, comment="create")
    event_update = Event.objects.create(user=bob, comment="update")

    # Creation: new anchor with a pre-set UUID pk. Must not query a lock on a
    # row that does not exist yet, and must succeed.
    with audit_trail_event(event_create):
        obj = UUIDModel.objects.create(value="v1")

    assert obj.created_event == event_create
    assert obj.value == "v1"
    assert obj._state_model.all_objects.count() == 1

    # Update: existing anchor with a pre-set pk -> the lock targets a real row
    # (this is the path that must NOT rely on catching DoesNotExist).
    with audit_trail_event(event_update):
        obj.value = "v2"
        obj.save()

    assert obj.value == "v2"
    assert obj._state_model.objects.count() == 1
    assert obj._state_model.all_objects.count() == 2

    reloaded = UUIDModel.objects.get(pk=obj.pk)
    assert reloaded.value == "v2"


@pytest.mark.django_db
def test_foreign_key_direct_id_assignment(alice, bob):
    """
    BUG REPRODUCTION: ForeignKey direct ID assignment support (Logical Bug / API Gap)

    Description:
    1. Only the declared relation name (e.g. "user") is registered as a descriptor.
       Assigning the direct DB attribute (e.g. `obj.user_id = bob.id`) bypasses the descriptor,
       writes directly to `__dict__`, is not recorded in `_draft_state_dirty_fields`, and is completely ignored.
    2. Furthermore, `save(update_fields=["user_id"])` does not map the attribute name to the relation,
       causing the update to be ignored or fail.
    """
    event_initial = Event.objects.create(user=alice, comment="Initial")
    event_new = Event.objects.create(user=alice, comment="New Event")

    with audit_trail_event(event_initial):
        pr = PullRequest.objects.create(
            owner="octocat", repo="hello-world", number=3, title="PR", user=alice
        )

    # Attempt to directly set the ID of the ForeignKey rather than the object instance.
    with audit_trail_event(event_new):
        pr.user_id = bob.id
        pr.save()

    pr_refresh = PullRequest.objects.get(pk=pr.pk)
    # The direct ID assignment should have been saved. In the buggy implementation, this is completely ignored.
    assert pr_refresh.user_id == bob.id


@pytest.mark.django_db
def test_dirty_fk_read_is_cached(django_assert_num_queries, alice, bob):
    """
    C4: Reading a ForeignKey relation whose id was set via the direct *_id
    attribute must not re-query the related object on every access.
    """
    event_create = Event.objects.create(user=alice, comment="Creation")
    with audit_trail_event(event_create):
        pr = PullRequest.objects.create(
            owner="octocat", repo="hello-world", number=1, title="PR", user=alice
        )

    fetched = PullRequest.objects.get(pk=pr.pk)
    # Warm the _loaded_state cache so it does not count towards the query budget.
    _ = fetched.title

    # Set the FK via its direct id attribute -> becomes a dirty change whose
    # related object must be fetched lazily (and then cached).
    fetched.user_id = bob.id

    with django_assert_num_queries(1):
        u1 = fetched.user
        u2 = fetched.user

    assert u1 == u2 == bob


@pytest.mark.django_db
def test_race_condition_prefetch_related(alice, bob):
    """
    BUG REPRODUCTION: Lost Updates Concurrency Race Condition with prefetch_related (Critical)
    Ensures that when PullRequest is prefetched with states, concurrent saves in separate transactions
    (which are correctly serialized via SELECT FOR UPDATE on the anchor) do not get overwritten by
    the stale, cached state from the prefetch cache.
    """
    event1 = Event.objects.create(user=alice, comment="Initial Event")
    event2 = Event.objects.create(user=bob, comment="Concurrent Event 2")
    event3 = Event.objects.create(user=alice, comment="Concurrent Event 3")

    with audit_trail_event(event1):
        pr = PullRequest.objects.create(
            owner="octocat",
            repo="hello-world",
            number=1,
            title="Initial",
            description="Initial Description",
        )

    # Load with prefetch_related.
    pr_a = PullRequest.objects.prefetch_related("states").get(pk=pr.pk)
    _ = pr_a.title  # Trigger cache populate.

    # Process B retrieves the same object, modifies a DIFFERENT field, and saves it.
    pr_b = PullRequest.objects.get(pk=pr.pk)
    with audit_trail_event(event2):
        pr_b.description = "Updated by Process B"
        pr_b.save()

    # Process A modifies its original field and saves it.
    with audit_trail_event(event3):
        pr_a.title = "Updated by Process A"
        pr_a.save()

    # Fetch the final state of the object in the database.
    pr_final = PullRequest.objects.get(pk=pr.pk)

    # Process A should NOT have overwritten Process B's concurrent modification.
    assert pr_final.title == "Updated by Process A"
    assert pr_final.description == "Updated by Process B"


@pytest.mark.django_db
def test_cannot_save_soft_deleted_model(alice, bob):
    """
    Ensures that once an audited model instance is soft-deleted (revoked),
    any subsequent attempts to save() it raise a RuntimeError.
    """
    event_create = Event.objects.create(user=alice, comment="Creation")
    event_delete = Event.objects.create(user=bob, comment="Deletion")
    event_update = Event.objects.create(user=alice, comment="Post-delete update")

    with audit_trail_event(event_create):
        pr = PullRequest.objects.create(
            owner="octocat", repo="hello-world", number=1, title="PR"
        )

    with audit_trail_event(event_delete):
        pr.delete()

    with audit_trail_event(event_update):
        pr.title = "New Title on Deleted PR"
        with pytest.raises(RuntimeError, match="PullRequest is already deleted"):
            pr.save()


@pytest.mark.django_db
def test_state_update_does_not_clobber_concurrent_anchor_field(alice, bob):
    """
    C1: A pure state update must not rewrite unrelated anchor fields with stale
    in-memory values.

    Otherwise a concurrent in-place anchor update (e.g. an ephemeral lock flag
    or, here, `owner`) performed by another process is silently clobbered when
    this process saves an unrelated *state* field.
    """
    event1 = Event.objects.create(user=alice, comment="Initial")
    event2 = Event.objects.create(user=bob, comment="Concurrent anchor update")
    event3 = Event.objects.create(user=alice, comment="State update")

    with audit_trail_event(event1):
        pr = PullRequest.objects.create(
            owner="octocat",
            repo="hello-world",
            number=1,
            title="Initial",
        )

    # Process A loads the object; `owner` is cached as "octocat" in memory.
    pr_a = PullRequest.objects.get(pk=pr.pk)
    _ = pr_a.title  # Populate state cache.

    # Process B updates an anchor field in place and saves it.
    pr_b = PullRequest.objects.get(pk=pr.pk)
    with audit_trail_event(event2):
        pr_b.owner = "new-owner"
        pr_b.save()

    # Process A updates only a STATE field.
    with audit_trail_event(event3):
        pr_a.title = "Updated by A"
        pr_a.save()

    pr_final = PullRequest.objects.get(pk=pr.pk)
    assert pr_final.title == "Updated by A"
    # B's concurrent anchor change must survive A's state-only update.
    assert pr_final.owner == "new-owner"


@pytest.mark.django_db
def test_combined_anchor_and_state_change_persists_both(alice, bob):
    """
    Transparency: saving with no explicit update_fields persists BOTH a changed
    anchor field and a changed state field, exactly like a normal Django model
    save. Anchor dirty-tracking detects the changed anchor column and writes it
    (alongside auto_now columns) without clobbering concurrent anchor updates.
    """
    event1 = Event.objects.create(user=alice, comment="Initial")
    event2 = Event.objects.create(user=bob, comment="Combined update")

    with audit_trail_event(event1):
        pr = PullRequest.objects.create(
            owner="octocat",
            repo="hello-world",
            number=1,
            title="Initial",
        )

    pr2 = PullRequest.objects.get(pk=pr.pk)
    with audit_trail_event(event2):
        pr2.owner = "changed-owner"  # Anchor field.
        pr2.title = "Changed Title"  # State field.
        pr2.save()

    pr_final = PullRequest.objects.get(pk=pr.pk)
    assert pr_final.owner == "changed-owner"
    assert pr_final.title == "Changed Title"


@pytest.mark.django_db
def test_partial_save_retains_unsaved_draft_changes(alice):
    """
    Ensure that saving with update_fields only clears the targeted state fields
    from the draft dirty set and retains any other unsaved changes in the draft state.
    """
    event1 = Event.objects.create(user=alice, comment="Initial")
    event2 = Event.objects.create(user=alice, comment="Partial Update")

    with audit_trail_event(event1):
        pr = PullRequest.objects.create(
            owner="octocat",
            repo="hello-world",
            number=1,
            title="Initial Title",
            description="Initial Description",
        )

    # Modify both state fields in memory.
    pr.title = "Staged New Title"
    pr.description = "Staged New Description"

    assert pr._draft_state_dirty_fields == {"title", "description"}

    # Save only 'title'.
    with audit_trail_event(event2):
        pr.save(update_fields=["title"])

    # DB verification: title should be updated, description should be unchanged in DB.
    db_pr = PullRequest.objects.get(pk=pr.pk)
    assert db_pr.title == "Staged New Title"
    assert db_pr.description == "Initial Description"

    # In-memory verification: description change must still be present and dirty.
    assert pr.title == "Staged New Title"
    assert pr.description == "Staged New Description"
    assert pr._draft_state_dirty_fields == {"description"}


@pytest.mark.django_db
def test_abstract_model_state_inheritance(alice):
    """
    Ensure that an abstract AuditTrailModel with State does not get a state model
    on its own, but a concrete subclass correctly inherits the State fields.
    Also check that the inherited fields appear first, followed by the model's own fields (if any).
    """
    from tests.models import AbstractDocument, BlogArticle, Book

    # AbstractDocument should not have been processed as usual (no state model).
    assert not hasattr(AbstractDocument, "_state_model")

    # BlogArticle should have a state model inheriting 'rating', but defining no state of its own.
    assert hasattr(BlogArticle, "_state_model")
    ArticleState = BlogArticle._state_model
    internal_fields = {"id", "anchor", "created_event", "revoked_event"}
    article_state_fields = [f.name for f in ArticleState._meta.fields if f.name not in internal_fields]
    assert article_state_fields == ["rating"]

    # Book should have a state model with 'rating' (inherited) first, and 'price' (own) second.
    assert hasattr(Book, "_state_model")
    BookState = Book._state_model
    book_state_fields = [f.name for f in BookState._meta.fields if f.name not in internal_fields]
    assert book_state_fields == ["rating", "price"]

    # Verify functionality: creation, updating and history tracking works for BlogArticle.
    event_create_article = Event.objects.create(user=alice, comment="Publish blog article")
    with audit_trail_event(event_create_article):
        article = BlogArticle.objects.create(
            title="Django Audit Trail",
            author="John Doe",
            url="https://example.com/django-audit-trail",
            rating=5,
        )

    assert article.title == "Django Audit Trail"
    assert article.author == "John Doe"
    assert article.url == "https://example.com/django-audit-trail"
    assert article.rating == 5
    assert article.created_event == event_create_article

    # Verify functionality: creation, updating and history tracking works for Book.
    event_create_book = Event.objects.create(user=alice, comment="Stock book")
    with audit_trail_event(event_create_book):
        book = Book.objects.create(
            title="Two Years in the Wild",
            author="Jane Doe",
            isbn="978-3-16-148410-0",
            rating=4,
            price=19.99,
        )

    assert book.title == "Two Years in the Wild"
    assert book.author == "Jane Doe"
    assert book.isbn == "978-3-16-148410-0"
    assert book.rating == 4
    assert book.price == 19.99
    assert book.created_event == event_create_book


@pytest.mark.django_db
def test_multiple_inheritance_raises_error():
    """
    Ensure that attempting to inherit from multiple audited models with states
    raises a RuntimeError.
    """
    from django_audit_trail.models import AuditTrailModel
    from django.db import models

    class AbstractA(AuditTrailModel):
        class State:
            field_a = models.IntegerField()
        class Meta:
            abstract = True
            app_label = "tests"

    class AbstractB(AuditTrailModel):
        class State:
            field_b = models.IntegerField()
        class Meta:
            abstract = True
            app_label = "tests"

    with pytest.raises(RuntimeError, match="Multiple inheritance with audited states is not supported"):
        type(
            "MultipleStateDocument",
            (AbstractA, AbstractB),
            {
                "__module__": "tests.models",
                "Meta": type("Meta", (), {"app_label": "tests"}),
            }
        )


@pytest.mark.django_db
def test_inheritance_from_concrete_raises_error():
    """
    Ensure that attempting to inherit state from a concrete audited model
    raises a RuntimeError.
    """
    from tests.models import Book
    from django.db import models

    with pytest.raises(RuntimeError, match="must inherit state from an abstract model, not Book"):
        type(
            "ConcreteSubclass",
            (Book,),
            {
                "__module__": "tests.models",
                "State": type("State", (), {"another_field": models.CharField(max_length=50)}),
                "Meta": type("Meta", (), {"app_label": "tests"}),
            }
        )


@pytest.mark.django_db
def test_multilevel_inheritance_abstract_raises_error():
    """
    Ensure that attempting to inherit an abstract audited model from another
    audited model raises a RuntimeError.
    """
    from tests.models import AbstractDocument
    from django.db import models

    with pytest.raises(RuntimeError, match="cannot inherit from another audited model. Multi-level inheritance is not supported"):
        type(
            "AbstractSubDocument",
            (AbstractDocument,),
            {
                "__module__": "tests.models",
                "State": type("State", (), {"stock_count": models.IntegerField()}),
                "Meta": type("Meta", (), {"abstract": True, "app_label": "tests"}),
            }
        )


@pytest.mark.django_db
def test_state_attribute_must_be_class():
    """
    Ensure that defining a 'State' attribute that is not a class (e.g., a property or field)
    raises a RuntimeError.
    """
    from django_audit_trail.models import AuditTrailModel
    from django.db import models

    with pytest.raises(RuntimeError, match="The 'State' attribute on BadStateModel must be a class"):
        type(
            "BadStateModel",
            (AuditTrailModel,),
            {
                "__module__": "tests.models",
                "State": models.CharField(max_length=20),
                "Meta": type("Meta", (), {"app_label": "tests"}),
            }
        )

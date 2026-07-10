import pytest
from django_audit_trail.context import audit_trail_event
from django_audit_trail.models import Event
from tests.models import PullRequest


@pytest.mark.django_db
def test_create_and_delete_audited_model_with_context(alice, bob):
    """
    Ensure creating and deleting an AuditTrailModel within an active
    event context assigns the proper events correctly.
    """
    event_create = Event.objects.create(user=alice, comment="Creating pull request")
    event_delete = Event.objects.create(user=bob, comment="Deleting pull request")

    # test creation within context
    with audit_trail_event(event_create):
        obj = PullRequest.objects.create(
            owner="octocat",
            repo="hello-world",
            number=1,
            title="Audited PullRequest test",
        )

    # check that created_event is properly assigned
    assert obj.created_event == event_create
    assert obj.revoked_event is None

    # retrieve from DB to verify persistence
    db_obj = PullRequest.objects.get(pk=obj.pk)
    assert db_obj.created_event == event_create
    assert db_obj.revoked_event is None
    assert db_obj.owner == "octocat"
    assert db_obj.repo == "hello-world"
    assert db_obj.number == 1
    assert db_obj.title == "Audited PullRequest test"

    # test deletion within context
    with audit_trail_event(event_delete):
        db_obj.delete()

    # verify that revoked_event is assigned on the soft-deleted instance
    assert db_obj.revoked_event == event_delete

    # retrieve from DB to verify persistence of revocation
    db_obj_after = PullRequest.objects.get(pk=obj.pk)
    assert db_obj_after.revoked_event == event_delete


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

    # create within context
    with audit_trail_event(event_create):
        obj = PullRequest.objects.create(
            owner="octocat",
            repo="hello-world",
            number=1,
        )

    # attempt delete outside context
    with pytest.raises(RuntimeError, match="No active audit trail event context"):
        obj.delete()


@pytest.mark.django_db
def test_double_delete_raises_error(alice, bob):
    """
    Ensure that deleting an already deleted AuditTrailModel raises a RuntimeError.
    """
    event_create = Event.objects.create(user=alice, comment="Creation")
    event_delete1 = Event.objects.create(user=bob, comment="First deletion")
    event_delete2 = Event.objects.create(user=bob, comment="Second deletion")

    # create within context
    with audit_trail_event(event_create):
        obj = PullRequest.objects.create(
            owner="octocat",
            repo="hello-world",
            number=1,
            title="Double Delete Test PR",
        )

    # delete within context
    with audit_trail_event(event_delete1):
        obj.delete()

    # attempt to delete again within another context
    with audit_trail_event(event_delete2):
        with pytest.raises(RuntimeError, match="PullRequest is already deleted"):
            obj.delete()

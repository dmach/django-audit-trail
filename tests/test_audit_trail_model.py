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

    # retrieve from DB using all_objects to verify persistence of revocation
    db_obj_after = PullRequest.all_objects.get(pk=obj.pk)
    assert db_obj_after.revoked_event == event_delete

    # verify that standard objects manager does NOT return the revoked object
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


@pytest.mark.django_db
def test_audit_trail_managers(alice, bob):
    """
    Verify that the default 'objects' manager automatically filters out
    revoked (soft-deleted) entities, while 'all_objects' retrieves everything.
    """
    event_create = Event.objects.create(user=alice, comment="Batch creation")
    event_delete = Event.objects.create(user=bob, comment="Delete half of batch")

    # create active objects
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

    # all three should be active initially
    assert PullRequest.objects.count() == 3
    assert PullRequest.all_objects.count() == 3

    # delete (revoke) pr2
    with audit_trail_event(event_delete):
        pr2.delete()

    # verify filtering under 'objects' manager
    active_prs = list(PullRequest.objects.all())
    assert len(active_prs) == 2
    assert pr1 in active_prs
    assert pr3 in active_prs
    assert pr2 not in active_prs

    # verify 'all_objects' manager still returns everything
    all_prs = list(PullRequest.all_objects.all())
    assert len(all_prs) == 3
    assert pr1 in all_prs
    assert pr2 in all_prs
    assert pr3 in all_prs

    # verify queryset filtering on 'objects'
    assert PullRequest.objects.filter(number=1).exists()
    assert not PullRequest.objects.filter(number=2).exists()

    # verify queryset filtering on 'all_objects'
    assert PullRequest.all_objects.filter(number=2).exists()

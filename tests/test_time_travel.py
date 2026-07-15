import asyncio
from datetime import timedelta
import threading
import time
import pytest
from django.core.exceptions import ValidationError
from django.utils import timezone
from django_audit_trail.context import audit_trail_event, audit_trail_time_travel
from django_audit_trail.models import Event
from tests.models import PullRequest


@pytest.mark.django_db
def test_time_travel_basic_flow(alice):
    """
    Verify basic time travel querying with Event instances.
    We retrieve the correct state fields as of different event points.
    """
    base_time = timezone.now()
    event_1 = Event.objects.create(user=alice, comment="E1", timestamp=base_time - timedelta(hours=2))
    event_2 = Event.objects.create(user=alice, comment="E2", timestamp=base_time - timedelta(hours=1))

    # 1. Create a PullRequest at event_1
    with audit_trail_event(event_1):
        pr = PullRequest.objects.create(
            owner="octocat",
            repo="hello-world",
            number=1,
            title="Version 1 Title",
        )

    # 2. Update the PullRequest at event_2
    with audit_trail_event(event_2):
        pr.title = "Version 2 Title"
        pr.save()

    # Querying in present-time (outside time travel context)
    reloaded = PullRequest.objects.get(pk=pr.pk)
    assert reloaded.title == "Version 2 Title"

    # Querying inside time-travel context as of event_1
    with audit_trail_time_travel(event_1):
        pr_past = PullRequest.objects.get(pk=pr.pk)
        assert pr_past.title == "Version 1 Title"

    # Querying inside time-travel context as of event_2
    with audit_trail_time_travel(event_2):
        pr_past2 = PullRequest.objects.get(pk=pr.pk)
        assert pr_past2.title == "Version 2 Title"


@pytest.mark.django_db
def test_time_travel_by_datetime(alice):
    """
    Verify that passing standard timezone-aware datetime objects
    to audit_trail_time_travel correctly resolves past states.
    """
    base_time = timezone.now()
    t1 = base_time - timedelta(hours=3)
    t2 = base_time - timedelta(hours=2)
    t3 = base_time - timedelta(hours=1)

    event_1 = Event.objects.create(user=alice, comment="E1", timestamp=t1)
    event_2 = Event.objects.create(user=alice, comment="E2", timestamp=t3)

    with audit_trail_event(event_1):
        pr = PullRequest.objects.create(
            owner="octocat",
            repo="hello",
            number=1,
            title="Title T1",
        )

    with audit_trail_event(event_2):
        pr.title = "Title T3"
        pr.save()

    # Query as of t2 (which is between t1 and t3)
    # The title should be the initial one since t2 is before the update
    with audit_trail_time_travel(t2):
        pr_at_t2 = PullRequest.objects.get(pk=pr.pk)
        assert pr_at_t2.title == "Title T1"

    # Query as of t3
    with audit_trail_time_travel(t3):
        pr_at_t3 = PullRequest.objects.get(pk=pr.pk)
        assert pr_at_t3.title == "Title T3"


@pytest.mark.django_db
def test_time_travel_manager_filtering(alice, bob):
    """
    Verify that managers correctly filter the list of anchor instances
    representing only the objects that existed and were not soft-deleted
    as of that target point.
    """
    base_time = timezone.now()
    event_1 = Event.objects.create(user=alice, comment="E1", timestamp=base_time - timedelta(hours=3))
    event_2 = Event.objects.create(user=alice, comment="E2", timestamp=base_time - timedelta(hours=2))
    event_3 = Event.objects.create(user=bob, comment="E3", timestamp=base_time - timedelta(hours=1))

    # Create first PR at event_1
    with audit_trail_event(event_1):
        pr1 = PullRequest.objects.create(owner="octocat", repo="hello", number=1, title="PR 1")

    # Create second PR and soft-delete first PR at event_2
    with audit_trail_event(event_2):
        pr2 = PullRequest.objects.create(owner="octocat", repo="world", number=2, title="PR 2")

    with audit_trail_event(event_3):
        pr1.delete()

    # Outside time-travel (present): pr1 is soft-deleted, only pr2 is visible
    assert list(PullRequest.objects.all()) == [pr2]

    # As of event_1: only pr1 existed
    with audit_trail_time_travel(event_1):
        assert list(PullRequest.objects.all()) == [pr1]

    # As of event_2: both existed and neither was soft-deleted yet
    with audit_trail_time_travel(event_2):
        results = list(PullRequest.objects.order_by("number"))
        assert results == [pr1, pr2]

    # As of event_3: pr1 is soft-deleted, so only pr2 is visible
    with audit_trail_time_travel(event_3):
        assert list(PullRequest.objects.all()) == [pr2]


@pytest.mark.django_db
def test_time_travel_mutation_guards(alice):
    """
    Verify that attempting to save or delete objects when a time-travel context
    is active raises a ValidationError to enforce strict data integrity.
    """
    event = Event.objects.create(user=alice)

    with audit_trail_event(event):
        pr = PullRequest.objects.create(
            owner="octocat",
            repo="hello-world",
            number=1,
            title="Initial Title",
        )

    # Protect on instance-level when the instance is loaded inside time-travel mode
    with audit_trail_time_travel(event):
        pr_past = PullRequest.objects.get(pk=pr.pk)
        with pytest.raises(ValidationError, match="Cannot modify or delete models in time-travel mode."):
            pr_past.save()
        with pytest.raises(ValidationError, match="Cannot modify or delete models in time-travel mode."):
            pr_past.delete()

    # Protect on standard instances when a global time-travel context is active
    with audit_trail_time_travel(event):
        with pytest.raises(ValidationError, match="Cannot modify or delete models in time-travel mode."):
            pr.save()
        with pytest.raises(ValidationError, match="Cannot modify or delete models in time-travel mode."):
            pr.delete()


@pytest.mark.django_db
def test_time_travel_prefetch_related(django_assert_num_queries, alice):
    """
    Verify that historical states are correctly prefetchable, avoiding N+1 queries,
    and returning the correct states as of the time-travel point.
    """
    base_time = timezone.now()
    event_1 = Event.objects.create(user=alice, comment="E1", timestamp=base_time - timedelta(hours=2))
    event_2 = Event.objects.create(user=alice, comment="E2", timestamp=base_time - timedelta(hours=1))

    with audit_trail_event(event_1):
        pr1 = PullRequest.objects.create(owner="octocat", repo="hello", number=1, title="PR1 V1")
        pr2 = PullRequest.objects.create(owner="octocat", repo="world", number=2, title="PR2 V1")

    with audit_trail_event(event_2):
        pr1.title = "PR1 V2"
        pr1.save()
        pr2.title = "PR2 V2"
        pr2.save()

    # In time travel context, with prefetch_related, loading both should only take 2 queries:
    # 1. Fetch PullRequests
    # 2. Fetch the correct historical state records
    with audit_trail_time_travel(event_1):
        with django_assert_num_queries(2):
            prs = list(PullRequest.objects.prefetch_related("states").order_by("number"))
            assert len(prs) == 2
            assert prs[0].title == "PR1 V1"
            assert prs[1].title == "PR2 V1"


@pytest.mark.django_db(transaction=True)
def test_time_travel_thread_and_async_isolation(alice, bob):
    """
    Verify that the time-travel point is properly isolated between
    different threads and concurrent asyncio tasks.
    """
    base_time = timezone.now()
    event_alice = Event.objects.create(user=alice, comment="Alice event", timestamp=base_time - timedelta(hours=2))
    event_bob = Event.objects.create(user=bob, comment="Bob event", timestamp=base_time - timedelta(hours=1))

    with audit_trail_event(event_alice):
        pr = PullRequest.objects.create(owner="octocat", repo="hello", number=1, title="Alice Value")

    with audit_trail_event(event_bob):
        pr.title = "Bob Value"
        pr.save()

    # 1. Thread Isolation Check
    thread_errors = []

    def run_alice_thread():
        from django.db import connections
        try:
            with audit_trail_time_travel(event_alice):
                time.sleep(0.05)
                # Should see the historical state
                assert PullRequest.objects.get(pk=pr.pk).title == "Alice Value"
        except Exception as e:
            thread_errors.append(f"Alice thread error: {e}")
        finally:
            connections.close_all()

    def run_bob_thread():
        from django.db import connections
        try:
            with audit_trail_time_travel(event_bob):
                time.sleep(0.05)
                # Should see the updated state
                assert PullRequest.objects.get(pk=pr.pk).title == "Bob Value"
        except Exception as e:
            thread_errors.append(f"Bob thread error: {e}")
        finally:
            connections.close_all()

    thread1 = threading.Thread(target=run_alice_thread)
    thread2 = threading.Thread(target=run_bob_thread)
    thread1.start()
    thread2.start()
    thread1.join()
    thread2.join()

    assert not thread_errors, f"Thread isolation failed: {thread_errors}"

    # 2. Async Isolation Check
    results = {}

    async def worker(name, event_point):
        with audit_trail_time_travel(event_point):
            # Interleave tasks
            await asyncio.sleep(0.01)
            results[name] = (await PullRequest.objects.aget(pk=pr.pk)).title

    async def main():
        await asyncio.gather(
            worker("alice", event_alice),
            worker("bob", event_bob),
        )

    import os
    os.environ["DJANGO_ALLOW_ASYNC_UNSAFE"] = "true"
    try:
        asyncio.run(main())
    finally:
        del os.environ["DJANGO_ALLOW_ASYNC_UNSAFE"]

    assert results["alice"] == "Alice Value"
    assert results["bob"] == "Bob Value"


@pytest.mark.django_db
def test_time_travel_before_first_event(alice):
    """
    Verify that querying as of a time before any event exists in the database
    returns empty results (no anchors found, and _loaded_state is None).
    """
    base_time = timezone.now()
    t0 = base_time - timedelta(hours=2)
    t1 = base_time - timedelta(hours=1)

    event_1 = Event.objects.create(user=alice, comment="E1", timestamp=t1)

    with audit_trail_event(event_1):
        pr = PullRequest.objects.create(
            owner="octocat",
            repo="hello",
            number=1,
            title="Version 1 Title",
        )

    # 1. Under present context, the object is queryable.
    assert PullRequest.objects.filter(pk=pr.pk).exists() is True

    # 2. Inside time-travel to t0 (before the first event), the standard objects manager returns empty.
    with audit_trail_time_travel(t0):
        assert PullRequest.objects.filter(pk=pr.pk).exists() is False
        assert PullRequest.objects.count() == 0

    # 3. Retrieving manually via all_objects inside time-travel context will raise ValidationError
    #    to prevent bypassing chronological boundaries.
    with audit_trail_time_travel(t0):
        with pytest.raises(ValidationError, match="Cannot query all_objects inside a time-travel context."):
            PullRequest.all_objects.get(pk=pr.pk)

    # 4. Retrieving outside time-travel context returns the latest state.
    pr_current = PullRequest.all_objects.get(pk=pr.pk)
    assert pr_current.title == "Version 1 Title"


@pytest.mark.django_db
def test_time_travel_no_events_in_db():
    """
    Verify that querying inside a time-travel context when no Event has ever been
    created in the database returns an empty result set and doesn't crash.
    """
    # Verify we have no events in the database
    assert Event.objects.count() == 0

    past_time = timezone.now() - timedelta(days=1)
    with audit_trail_time_travel(past_time):
        assert PullRequest.objects.count() == 0
        assert list(PullRequest.objects.all()) == []



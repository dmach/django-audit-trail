import asyncio
import pytest
import threading
import time
from django_audit_trail.context import audit_trail_event, get_audit_trail_event
from django_audit_trail.models import Event


@pytest.mark.django_db
def test_get_event_outside_context():
    """
    Ensure get_audit_trail_event() raises RuntimeError when no context is active.
    """
    with pytest.raises(RuntimeError, match="No active audit trail event context"):
        get_audit_trail_event()


@pytest.mark.django_db
def test_single_context(alice):
    """
    Ensure the context manager correctly sets the event inside the context block
    and restores it to raising RuntimeError after the block exits.
    """
    event = Event.objects.create(user=alice)

    with pytest.raises(RuntimeError, match="No active audit trail event context"):
        get_audit_trail_event()

    with audit_trail_event(event):
        assert get_audit_trail_event() == event

    with pytest.raises(RuntimeError, match="No active audit trail event context"):
        get_audit_trail_event()


@pytest.mark.django_db
def test_nested_contexts(alice):
    """
    Ensure that nested contexts correctly override the active event and
    restore the outer event upon exit.
    """
    event_outer = Event.objects.create(user=alice, comment="Outer context event")
    event_inner = Event.objects.create(user=alice, comment="Inner context event")

    with pytest.raises(RuntimeError, match="No active audit trail event context"):
        get_audit_trail_event()

    with audit_trail_event(event_outer):
        assert get_audit_trail_event() == event_outer

        with audit_trail_event(event_inner):
            assert get_audit_trail_event() == event_inner

        # restored to the outer event after nested block exits
        assert get_audit_trail_event() == event_outer

    # restored to raising RuntimeError after outer block exits
    with pytest.raises(RuntimeError, match="No active audit trail event context"):
        get_audit_trail_event()


@pytest.mark.django_db
def test_context_error_handling(alice):
    """
    Ensure that even when an exception is raised inside the nested or outer contexts,
    the previous events are safely and correctly restored.
    """
    event_outer = Event.objects.create(user=alice, comment="Outer context event")
    event_inner = Event.objects.create(user=alice, comment="Inner context event")

    class ContextTestException(Exception):
        pass

    with pytest.raises(RuntimeError, match="No active audit trail event context"):
        get_audit_trail_event()

    with audit_trail_event(event_outer):
        assert get_audit_trail_event() == event_outer

        try:
            with audit_trail_event(event_inner):
                assert get_audit_trail_event() == event_inner
                raise ContextTestException("Error inside the inner context")
        except ContextTestException:
            pass

        # verify that the inner context was cleaned up and outer context was restored
        assert get_audit_trail_event() == event_outer

    # verify that the outer context is also cleaned up
    with pytest.raises(RuntimeError, match="No active audit trail event context"):
        get_audit_trail_event()


@pytest.mark.django_db
def test_context_thread_safety(alice, bob):
    """
    Ensure that thread-local storage isolates event context between different threads.
    Each thread should only see its own active event context.
    """
    event_alice = Event.objects.create(user=alice, comment="Alice event")
    event_bob = Event.objects.create(user=bob, comment="Bob event")

    thread_errors = []

    def run_alice_thread():
        """
        Target function for Alice's thread context checks.
        """
        from django.db import connections
        try:
            with pytest.raises(RuntimeError, match="No active audit trail event context"):
                get_audit_trail_event()
            with audit_trail_event(event_alice):
                assert get_audit_trail_event() == event_alice
                # sleep briefly to allow thread interleaving and context collision check
                time.sleep(0.102)
                assert get_audit_trail_event() == event_alice
            with pytest.raises(RuntimeError, match="No active audit trail event context"):
                get_audit_trail_event()
        except (AssertionError, Exception) as e:
            thread_errors.append(f"Alice thread error: {e}")
        finally:
            connections.close_all()

    def run_bob_thread():
        """
        Target function for Bob's thread context checks.
        """
        from django.db import connections
        try:
            with pytest.raises(RuntimeError, match="No active audit trail event context"):
                get_audit_trail_event()
            with audit_trail_event(event_bob):
                assert get_audit_trail_event() == event_bob
                # sleep briefly to allow thread interleaving and context collision check
                time.sleep(0.101)
                assert get_audit_trail_event() == event_bob
            with pytest.raises(RuntimeError, match="No active audit trail event context"):
                get_audit_trail_event()
        except (AssertionError, Exception) as e:
            thread_errors.append(f"Bob thread error: {e}")
        finally:
            connections.close_all()

    thread1 = threading.Thread(target=run_alice_thread)
    thread2 = threading.Thread(target=run_bob_thread)

    thread1.start()
    thread2.start()

    thread1.join()
    thread2.join()

    # if any assertions failed in either thread, thread_errors will contain details
    assert not thread_errors, f"Thread safety assertions failed: {thread_errors}"


@pytest.mark.django_db
def test_context_async_task_isolation(alice, bob):
    """
    B5: The event context must be isolated between concurrent asyncio tasks
    running on the same thread.

    threading.local is shared by all coroutines on a thread, so interleaved
    tasks clobber each other's context (a leak). contextvars gives each task its
    own copied context, keeping them isolated.
    """
    event_alice = Event.objects.create(user=alice, comment="Alice event")
    event_bob = Event.objects.create(user=bob, comment="Bob event")

    results = {}

    async def worker(name, event):
        with audit_trail_event(event):
            # Yield control so the sibling task runs while our context is active;
            # this is exactly where thread-local storage would leak.
            await asyncio.sleep(0.01)
            results[name] = get_audit_trail_event()

    async def main():
        await asyncio.gather(
            worker("alice", event_alice),
            worker("bob", event_bob),
        )

    asyncio.run(main())

    # Each task must observe only its own event.
    assert results["alice"] == event_alice
    assert results["bob"] == event_bob

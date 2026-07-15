import contextvars
from contextlib import contextmanager
from datetime import datetime


# A ContextVar (rather than threading.local) so the active event is correctly
# isolated across both threads and concurrent asyncio tasks / coroutines.
_current_event = contextvars.ContextVar(
    "django_audit_trail_current_event", default=None
)


class _BeforeFirstEvent:
    def __str__(self):
        return "<Before First Event>"

    def __repr__(self):
        return "<Before First Event>"


# Sentinel to indicate we are traveling to a time before any Event exists.
BEFORE_FIRST_EVENT = _BeforeFirstEvent()


# A ContextVar to track the active time travel point.
# Can be an Event instance, BEFORE_FIRST_EVENT, or None (representing the present / latest state).
_time_travel_event = contextvars.ContextVar(
    "django_audit_trail_time_travel_event", default=None
)


@contextmanager
def audit_trail_event(event):
    """
    Context manager to implicitly pass the current Event to save/delete operations.
    """
    token = _current_event.set(event)
    try:
        yield
    finally:
        _current_event.reset(token)


def get_audit_trail_event():
    event = _current_event.get()
    if event is None:
        raise RuntimeError(
            "No active audit trail event context. "
            "Wrap your code in the 'audit_trail_event(event)' context manager."
        )
    return event


@contextmanager
def audit_trail_time_travel(point):
    """
    Context manager to query and view historical states at a given point in time.
    The point can be either an Event model instance or a timezone-aware datetime.
    It is resolved internally to a strictly Event-based model boundary.
    """
    from .models import Event

    if isinstance(point, datetime):
        event = Event.objects.filter(timestamp__lte=point).order_by("-timestamp", "-pk").first()
        if event is None:
            event = BEFORE_FIRST_EVENT
    else:
        event = point

    token = _time_travel_event.set(event)
    try:
        yield
    finally:
        _time_travel_event.reset(token)


def get_time_travel_event():
    """
    Returns the current active time-travel Event, BEFORE_FIRST_EVENT, or None.
    """
    return _time_travel_event.get()


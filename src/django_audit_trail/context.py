import contextvars
from contextlib import contextmanager


# A ContextVar (rather than threading.local) so the active event is correctly
# isolated across both threads and concurrent asyncio tasks / coroutines.
_current_event = contextvars.ContextVar(
    "django_audit_trail_current_event", default=None
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

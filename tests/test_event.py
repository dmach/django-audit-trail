import pytest


from django_audit_trail.models import Event


@pytest.mark.django_db
def test_create_event(alice):
    Event.objects.create(
        user=alice,
        comment="comment",
        http_request_id="d41d8cd98f00b204e9800998ecf8427e",
    )

    event = Event.objects.all().first()
    assert event.timestamp is not None
    assert event.user == alice
    assert event.comment == "comment"
    assert event.http_request_id == "d41d8cd98f00b204e9800998ecf8427e"

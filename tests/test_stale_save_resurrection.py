import pytest
from django_audit_trail.context import audit_trail_event
from django_audit_trail.models import Event
from tests.models import PullRequest, Article, Tag, ArticleTag


@pytest.mark.django_db(transaction=True)
def test_stale_save_resurrection(alice, bob):
    """
    Test that saving a stale instance of an audited model with State
    does not resurrect a concurrently deleted object.
    """
    event_create = Event.objects.create(user=alice, comment="Create PR")
    event_delete = Event.objects.create(user=bob, comment="Delete PR")
    event_update = Event.objects.create(user=alice, comment="Update PR")

    with audit_trail_event(event_create):
        pr = PullRequest.objects.create(
            owner="octocat",
            repo="hello-world",
            number=1,
            title="Initial Title",
        )

    # Load two separate instances representing the same database row
    pr_stale = PullRequest.objects.get(pk=pr.pk)
    pr_fresh = PullRequest.objects.get(pk=pr.pk)

    # Delete the fresh instance
    with audit_trail_event(event_delete):
        pr_fresh.delete()

    # Verify it's deleted
    assert PullRequest.objects.filter(pk=pr.pk).count() == 0
    assert PullRequest.all_objects.get(pk=pr.pk).revoked_event_id == event_delete.id

    # Now try to save the stale instance
    # It should raise a RuntimeError because the object is already deleted in the DB
    with pytest.raises(RuntimeError, match="is already deleted"):
        with audit_trail_event(event_update):
            pr_stale.title = "Updated Title"
            pr_stale.save()

    # Verify it's still deleted in the database
    assert PullRequest.objects.filter(pk=pr.pk).count() == 0
    assert PullRequest.all_objects.get(pk=pr.pk).revoked_event_id == event_delete.id


@pytest.mark.django_db(transaction=True)
def test_stale_save_resurrection_no_state(alice, bob):
    """
    Test that saving a stale instance of an audited model without State (e.g. M2M through)
    does not resurrect a concurrently deleted object.
    """
    event_create = Event.objects.create(user=alice, comment="Create Article/Tag")
    event_delete = Event.objects.create(user=bob, comment="Delete Link")
    event_update = Event.objects.create(user=alice, comment="Update Link")

    with audit_trail_event(event_create):
        article = Article.objects.create(title="My Article")
        tag = Tag.objects.create(name="My Tag")
        link = ArticleTag.objects.create(article=article, tag=tag)

    # Load two separate instances representing the same database row
    link_stale = ArticleTag.objects.get(pk=link.pk)
    link_fresh = ArticleTag.objects.get(pk=link.pk)

    # Delete the fresh instance
    with audit_trail_event(event_delete):
        link_fresh.delete()

    # Verify it's deleted
    assert ArticleTag.objects.filter(pk=link.pk).count() == 0
    assert ArticleTag.all_objects.get(pk=link.pk).revoked_event_id == event_delete.id

    # Now try to save the stale instance
    # It should raise a RuntimeError because the object is already deleted in the DB
    with pytest.raises(RuntimeError, match="is already deleted"):
        with audit_trail_event(event_update):
            link_stale.save()

    # Verify it's still deleted in the database
    assert ArticleTag.objects.filter(pk=link.pk).count() == 0
    assert ArticleTag.all_objects.get(pk=link.pk).revoked_event_id == event_delete.id

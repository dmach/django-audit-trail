import pytest
from django.db import models
from django.apps import apps
from django_audit_trail.models import AuditTrailModel
from django_audit_trail.checks import check_audit_trail_models


@pytest.mark.django_db
def test_system_check_implicit_m2m_on_anchor():
    """
    Verify that define an implicit ManyToManyField (without 'through')
    on an audited model generates a system check error (E002).
    """
    class BadAnchorM2M(AuditTrailModel):
        users = models.ManyToManyField("auth.User")

        class Meta:
            app_label = "tests"

    try:
        errors = check_audit_trail_models(None)
        bad_errors = [e for e in errors if "BadAnchorM2M" in e.msg]
        assert len(bad_errors) == 1
        assert bad_errors[0].id == "django_audit_trail.E002"
        assert "must use an explicit 'through' model" in bad_errors[0].msg
    finally:
        # Clean up Django's model registry to avoid leaking state.
        apps.all_models["tests"].pop("badanchorm2m", None)


@pytest.mark.django_db
def test_system_check_m2m_in_state():
    """
    Verify that defining any ManyToManyField inside the nested State class
    of an audited model generates a system check error (E001).
    """
    class BadStateM2M(AuditTrailModel):
        class State:
            users = models.ManyToManyField("auth.User")

        class Meta:
            app_label = "tests"

    try:
        errors = check_audit_trail_models(None)
        bad_errors = [e for e in errors if "BadStateM2M" in e.msg]
        assert len(bad_errors) == 1
        assert bad_errors[0].id == "django_audit_trail.E001"
        assert "disallowed inside the nested 'State' class" in bad_errors[0].hint
    finally:
        # Clean up Django's model registry to avoid leaking state.
        apps.all_models["tests"].pop("badstatem2m", None)
        apps.all_models["tests"].pop("badstatem2mstate", None)


@pytest.mark.django_db
def test_system_check_valid_explicit_m2m():
    """
    Verify that defining a ManyToManyField on the anchor model
    with an explicit through model inheriting from AuditTrailModel
    passes the system check without errors.
    """
    errors = check_audit_trail_models(None)
    relevant_errors = [
        e for e in errors
        if "Article" in e.msg or "Tag" in e.msg or "ArticleTag" in e.msg
    ]
    assert len(relevant_errors) == 0

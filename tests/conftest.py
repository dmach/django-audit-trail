"""
Shared pytest fixtures for the test suite.

Pytest automatically discovers fixtures defined in conftest.py, so they
do NOT need to be explicitly imported into individual test files.

The 'db' parameter used in these fixtures is provided by pytest-django.
It explicitly grants database access, which is required to actually save
the created user instances to the test database.
"""

import pytest
from django.contrib.auth import get_user_model


@pytest.fixture(scope="session")
def django_db_modify_db_settings(postgresql_proc):
    """
    Overriding django_db_modify_db_settings dynamically configures the database settings
    just before Django initializes the connections, routing them to the ephemeral PostgreSQL.

    PostgreSQL doesn't run under root for security reasons.
    Dropping perms has issues, stick to running tests under an unprivileged user.
    """
    from django.conf import settings

    settings.DATABASES["default"].update(
        {
            "ENGINE": "django.db.backends.postgresql",
            "NAME": postgresql_proc.dbname,
            "USER": postgresql_proc.user,
            "PASSWORD": postgresql_proc.password,
            "HOST": postgresql_proc.host,
            "PORT": postgresql_proc.port,
        }
    )


@pytest.fixture(scope="session", autouse=True)
def close_db_connections_at_teardown():
    """
    Cleanly close all active Django database connections at the end of the test session.
    This prevents 'database is being accessed by other users' warnings when pytest-postgresql
    attempts to drop the test database during teardown.
    """
    yield
    from django.db import connections
    connections.close_all()


@pytest.fixture
def alice(db):
    User = get_user_model()
    return User.objects.create_user(
        username="alice",
        email="alice@example.com",
    )


@pytest.fixture
def bob(db):
    User = get_user_model()
    return User.objects.create_user(
        username="bob",
        email="bob@example.com",
    )

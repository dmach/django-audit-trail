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

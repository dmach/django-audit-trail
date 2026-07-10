import os

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

SECRET_KEY = "dummy-secret-key-for-testing"
DEBUG = True

INSTALLED_APPS = [
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django_audit_trail",
    "tests",
]

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": "testdb",
    }
}

USE_TZ = True

# HACK: Disable migrations during early development.
#       We need to collect all apps in the correct order, otherwise Event table would fail on ``user`` foreign key:
#       django.db.utils.ProgrammingError: relation "auth_user" does not exist
MIGRATION_MODULES = {i.split(".")[-1]: None for i in INSTALLED_APPS}

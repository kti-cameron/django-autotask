#!/usr/bin/env python
import subprocess
import sys

from django.conf import settings
from django.core.management import call_command
from django.test.utils import get_runner
import django
import tempfile

DEBUG = True
tmp_media = tempfile.TemporaryDirectory()

settings.configure(
    DEBUG=True,
    ALLOWED_HOSTS=('testserver',),
    INSTALLED_APPS=(  # Including django.contrib apps prevents warnings during
        # tests.
        'djautotask',
        'django.contrib.contenttypes',
        'django.contrib.auth',
        'django.contrib.sessions',
    ),
    AUTOTASK_SERVER_URL='https://localhost',
    AUTOTASK_CREDENTIALS={
        'username': '',
        'password': '',
        'integration_code': '',
    },
    DATABASES={
        'default': {
            'ENGINE': 'django.db.backends.sqlite3',
            'NAME': 'djautotask-test.sqlite',
        },
    },
    # Member avatar tests like to save files to disk,
    # so here's a temporary place for them.
    MEDIA_ROOT=tmp_media.name,
    USE_TZ=True,  # Prevent 'ValueError: SQLite backend does not support
    # timezone-aware datetimes when USE_TZ is False.'
    # ROOT_URLCONF='djautotask.tests.urls',
    CACHES={
        'default': {
            'BACKEND': 'django.core.cache.backends.locmem.LocMemCache',
            'LOCATION': 'unique-snowflake',
        }
    },
)


def _setup():
    """Configure Django stuff for tests."""
    django.setup()
    # Set up the test DB, if necessary.
    # Note that the test DB is not deleted before or after a test,
    # which speeds up subsequent tests because migrations
    # don't need to be run. But if you run into any funny errors,
    # you may want to remove the DB file and start fresh.
    # The DB file is stored in settings.DATABASES['default']['NAME'].
    call_command('migrate')
    # Clear out the test DB
    call_command('flush', '--noinput')


def exit_on_failure(command, message=None):
    if command:
        sys.exit(command)


def flake8_main():
    print('Running: flake8')
    _call = ['flake8'] + ['.']
    command = subprocess.call(_call)

    print("Failed: flake8 failed." if command else "Success. flake8 passed.")
    return command


def suite():
    """
    Set up and return a test suite. This is used in `python setup.py test`.
    """
    _setup()
    runner_cls = get_runner(settings)
    return runner_cls().build_suite(test_labels=None)


if __name__ == '__main__':
    _setup()
    call_command('test')
    # To run specific tests, try something such as:
    # call_command('test', 'djautotask.tests.test_api.TestAPISettings.test_retry_attempts_cloud_domain_warm_cache')  # noqa: E501
    exit_on_failure(flake8_main())

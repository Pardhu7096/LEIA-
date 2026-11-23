"""Test isolation: never load backend/.env and never reach a shared database.

pytest imports this file before any test module, so these guards are active
before database.py or main.py run their module-level load_dotenv() calls.
"""

import os
from urllib.parse import urlparse

import dotenv
import psycopg

# 1. backend/.env holds the Render staging DATABASE_URL and real API keys.
#    Replace load_dotenv so `from dotenv import load_dotenv` gets a no-op.
dotenv.load_dotenv = lambda *args, **kwargs: False

# 2. Remove any real credentials inherited from the developer's shell.
for name in (
    "DATABASE_URL", "DB_HOST", "DB_PORT", "DB_NAME", "DB_USER", "DB_PASSWORD",
    "GOOGLE_PLACES_API_KEY", "GOOGLE_ROUTES_API_KEY",
):
    os.environ.pop(name, None)

os.environ["JWT_SECRET"] = "isolated-test-secret-never-use-in-production-12345"
os.environ["GROQ_API_KEY"] = "test-groq-key-not-real"

# 3. Only an explicitly configured, local, disposable database may be opened.
_TEST_DATABASE_URL = os.getenv("TEST_DATABASE_URL", "")
_ALLOWED_TEST_HOSTS = {"localhost", "127.0.0.1", "::1"}

if _TEST_DATABASE_URL:
    host = urlparse(_TEST_DATABASE_URL).hostname or ""
    if host not in _ALLOWED_TEST_HOSTS:
        raise RuntimeError(
            "TEST_DATABASE_URL must point to a local disposable database "
            f"(localhost/127.0.0.1), not {host or 'an unknown host'}."
        )

_real_connect = psycopg.connect


def _guarded_connect(conninfo="", *args, **kwargs):
    if not _TEST_DATABASE_URL or conninfo != _TEST_DATABASE_URL:
        raise RuntimeError(
            "Blocked a database connection during tests. Only TEST_DATABASE_URL "
            "pointing at a local disposable database is allowed."
        )
    return _real_connect(conninfo, *args, **kwargs)


psycopg.connect = _guarded_connect

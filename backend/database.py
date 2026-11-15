import os
from pathlib import Path

import psycopg
from dotenv import load_dotenv
from psycopg.rows import dict_row

load_dotenv(Path(__file__).resolve().parent / ".env")


def get_connection():
    # DB_SSLMODE (for example "require" or "verify-full") is needed when the
    # database is not on the same host; unset keeps the driver default.
    sslmode = os.getenv("DB_SSLMODE", "").strip()
    options = {"sslmode": sslmode} if sslmode else {}
    return psycopg.connect(
        host=os.environ["DB_HOST"],
        port=int(os.environ["DB_PORT"]),
        dbname=os.environ["DB_NAME"],
        user=os.environ["DB_USER"],
        password=os.environ["DB_PASSWORD"],
        row_factory=dict_row,
        connect_timeout=5,
        **options,
    )
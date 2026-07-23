import argparse
import json
import os
from pathlib import Path

from dotenv import load_dotenv
from psycopg2.extras import Json
import psycopg2


ROOT = Path(__file__).resolve().parents[1]
COLLECTIONS = {
    "users": ROOT / "data" / "exam_system_users.json",
    "classes": ROOT / "data" / "exam_system_classes.json",
    "lessons": ROOT / "data" / "exam_system_lessons.json",
    "exams": ROOT / "data" / "exam_system_exams.json",
    "submissions": ROOT / "data" / "exam_system_submissions.json",
    "materials": ROOT / "data" / "exam_system_materials.json",
}


def normalize_database_url(url):
    if url and url.startswith("postgres://"):
        return "postgresql://" + url[len("postgres://"):]
    return url


def load_json(path, fallback):
    if not path.exists():
        return fallback
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def connect(database_url):
    dsn = normalize_database_url(database_url)
    kwargs = {"connect_timeout": 10}
    if "sslmode=" not in dsn:
        kwargs["sslmode"] = os.environ.get("DATABASE_SSLMODE", "require")
    return psycopg2.connect(dsn, **kwargs)


def ensure_table(conn):
    with conn.cursor() as cur:
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS exam_system_store (
                collection TEXT PRIMARY KEY,
                payload JSONB NOT NULL,
                updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )
            """
        )


def collection_exists(conn, collection):
    with conn.cursor() as cur:
        cur.execute(
            "SELECT 1 FROM exam_system_store WHERE collection = %s",
            (collection,),
        )
        return cur.fetchone() is not None


def upsert_collection(conn, collection, payload):
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO exam_system_store (collection, payload, updated_at)
            VALUES (%s, %s, NOW())
            ON CONFLICT (collection)
            DO UPDATE SET payload = EXCLUDED.payload, updated_at = NOW()
            """,
            (collection, Json(payload)),
        )


def main():
    load_dotenv(ROOT / ".env")
    parser = argparse.ArgumentParser(
        description="Import exam system JSON data into PostgreSQL/Supabase."
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Overwrite collections that already exist in the database.",
    )
    args = parser.parse_args()

    database_url = os.environ.get("DATABASE_URL") or os.environ.get("SUPABASE_DATABASE_URL")
    if not database_url:
        raise SystemExit("Missing DATABASE_URL or SUPABASE_DATABASE_URL.")

    fallbacks = {
        "users": {"students": [], "teachers": [], "parents": []},
        "classes": [],
        "lessons": [],
        "exams": [],
        "submissions": [],
        "materials": [],
    }

    with connect(database_url) as conn:
        ensure_table(conn)
        for collection, path in COLLECTIONS.items():
            if collection_exists(conn, collection) and not args.force:
                print(f"skip {collection}: already exists")
                continue
            payload = load_json(path, fallbacks[collection])
            upsert_collection(conn, collection, payload)
            size = len(payload) if hasattr(payload, "__len__") else 1
            print(f"imported {collection}: {size}")


if __name__ == "__main__":
    main()

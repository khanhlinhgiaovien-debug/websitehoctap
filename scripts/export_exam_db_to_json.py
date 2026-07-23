import argparse
import json
import os
from pathlib import Path

from dotenv import load_dotenv
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


def connect(database_url):
    dsn = normalize_database_url(database_url)
    kwargs = {"connect_timeout": 10}
    if "sslmode=" not in dsn:
        kwargs["sslmode"] = os.environ.get("DATABASE_SSLMODE", "require")
    return psycopg2.connect(dsn, **kwargs)


def main():
    load_dotenv(ROOT / ".env")
    parser = argparse.ArgumentParser(
        description="Export exam system PostgreSQL/Supabase collections to JSON files."
    )
    parser.add_argument(
        "--output-dir",
        default=str(ROOT / "data"),
        help="Directory to write JSON files into. Defaults to ./data.",
    )
    args = parser.parse_args()

    database_url = os.environ.get("DATABASE_URL") or os.environ.get("SUPABASE_DATABASE_URL")
    if not database_url:
        raise SystemExit("Missing DATABASE_URL or SUPABASE_DATABASE_URL.")

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    with connect(database_url) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT collection, payload
                FROM exam_system_store
                WHERE collection = ANY(%s)
                ORDER BY collection
                """,
                (list(COLLECTIONS.keys()),),
            )
            rows = cur.fetchall()

    for collection, payload in rows:
        filename = COLLECTIONS[collection].name
        path = output_dir / filename
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        size = len(payload) if hasattr(payload, "__len__") else 1
        print(f"exported {collection}: {size} -> {path}")


if __name__ == "__main__":
    main()

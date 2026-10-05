"""Run the additive admissions workflow upgrade against DATABASE_URL.

Take a database backup before upgrading a deployed installation. This script
adds the workflow table, relaxes the historical global student-ID index and
separates candidate identities that older intake grouped across universities.
It is idempotent and does not invent criteria for historical applications.
"""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from app.config import get_settings
from app.db.init_db import init_db
from app.db.session import create_db_engine, get_database_url


if __name__ == "__main__":
    settings = get_settings()
    engine = create_db_engine(get_database_url(settings), settings.database_schema)
    init_db(engine)
    engine.dispose()
    print("Admissions workflow schema and legacy identity upgrade completed.")

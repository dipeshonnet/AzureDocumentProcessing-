from __future__ import annotations

import argparse
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from sqlalchemy import select

from app.config import AppSettings
from app.db.session import create_db_engine, create_session_factory
from app.models import ApplicationDocument, ExtractedDocumentContent
from app.services.document_extraction import count_source_document_pages, resolved_document_page_count
from app.services.storage import StorageError, file_extension, get_storage_service


def main() -> int:
    parser = argparse.ArgumentParser(description="Backfill document page counts from stored source files.")
    parser.add_argument("--apply", action="store_true", help="Persist the corrected page counts.")
    args = parser.parse_args()

    settings = AppSettings()
    if not settings.database_url:
        raise SystemExit("DATABASE_URL is not configured.")

    storage = get_storage_service(settings)
    engine = create_db_engine(settings.database_url)
    session_factory = create_session_factory(engine)

    updated = 0
    unchanged = 0
    skipped = 0
    with session_factory() as session:
        documents = list(
            session.scalars(
                select(ApplicationDocument).order_by(ApplicationDocument.created_at)
            )
        )
        for document in documents:
            extracted = session.get(ExtractedDocumentContent, document.document_id)
            try:
                content = storage.read_bytes(storage_path=document.blob_url_or_path)
            except StorageError as exc:
                skipped += 1
                print(f"SKIP {document.original_filename}: {exc.__class__.__name__}")
                continue

            source_count = count_source_document_pages(content, file_extension(document.original_filename))
            if source_count is None and extracted is not None:
                source_count = resolved_document_page_count(extracted)
            if source_count is None or source_count <= 0:
                skipped += 1
                print(f"SKIP {document.original_filename}: no page count detected")
                continue

            old_count = document.page_count
            if old_count == source_count:
                unchanged += 1
                print(f"OK   {document.original_filename}: {old_count}")
                continue

            updated += 1
            print(f"FIX  {document.original_filename}: {old_count} -> {source_count}")
            if args.apply:
                document.page_count = source_count
                if extracted is not None:
                    extracted.extraction_metadata = {
                        **(extracted.extraction_metadata or {}),
                        "source_page_count": source_count,
                    }

        if args.apply:
            session.commit()

    mode = "applied" if args.apply else "dry-run"
    print(f"{mode}: updated={updated} unchanged={unchanged} skipped={skipped}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

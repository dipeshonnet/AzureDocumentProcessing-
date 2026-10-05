"""Fail closed when the Render Free deployment profile is misconfigured."""
from urllib.parse import urlsplit

from sqlalchemy.engine import make_url

from app.config import AppSettings


def validate_free_hosting(settings: AppSettings) -> None:
    if settings.app_env != "render-free":
        return
    if settings.dev_auth_enabled or settings.expose_legacy_api:
        raise RuntimeError("Render Free requires production authentication and a disabled legacy API.")
    if settings.storage_backend != "supabase" or settings.document_extraction_backend != "llamaparse":
        raise RuntimeError("Render Free requires Supabase storage and LlamaParse OCR.")
    if any(value != "mock" for value in (
        settings.document_classification_backend, settings.structured_extraction_backend,
        settings.document_summarization_backend, settings.rubric_scoring_backend,
    )):
        raise RuntimeError("Render Free does not enable paid AI inference backends.")
    if settings.database_schema != "admissions_app":
        raise RuntimeError("Render Free requires the private admissions_app database schema.")
    try:
        url = make_url(settings.database_url or "")
        valid_database = (url.drivername == "postgresql+psycopg"
                          and url.query.get("sslmode") in {"require", "verify-full"}
                          and bool(url.host and url.username and url.password))
    except Exception:
        valid_database = False
    if not valid_database:
        raise RuntimeError("Render Free requires a PostgreSQL psycopg URL with credentials and TLS.")
    if settings.max_upload_bytes > 4 * 1024 * 1024:
        raise RuntimeError("Render Free uploads must be limited to 4 MB.")
    if settings.parser_mode != "azure" or not settings.intake_worker_enabled:
        raise RuntimeError("Render Free requires live parsing and the single-process intake worker.")
    if not all((settings.supabase_url, settings.supabase_service_key, settings.llama_cloud_api_key,
                settings.bootstrap_admin_email, settings.bootstrap_admin_password)):
        raise RuntimeError("Render Free backend secrets and bootstrap administrator are required.")
    if len(settings.bootstrap_admin_password or "") < 16:
        raise RuntimeError("The bootstrap administrator password must have at least 16 characters.")
    public_url = urlsplit(settings.public_app_url or "")
    if public_url.scheme != "https" or not public_url.hostname or settings.public_app_url not in settings.cors_origins:
        raise RuntimeError("Render Free requires an HTTPS public app URL included in CORS origins.")


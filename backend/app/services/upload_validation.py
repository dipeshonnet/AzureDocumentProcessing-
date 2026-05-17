from __future__ import annotations

from fastapi import HTTPException, UploadFile, status

from app.services.storage import ALLOWED_UPLOAD_EXTENSIONS, file_extension


CHUNK_SIZE_BYTES = 1024 * 1024


def validate_upload_filename(filename: str | None) -> str:
    if not filename:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Uploaded file must include a filename.",
        )

    extension = file_extension(filename)
    if extension not in ALLOWED_UPLOAD_EXTENSIONS:
        allowed = ", ".join(sorted(ALLOWED_UPLOAD_EXTENSIONS))
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Unsupported file type. Allowed types: {allowed}.",
        )
    return filename


async def read_upload_with_limit(upload: UploadFile, *, max_bytes: int) -> bytes:
    content = bytearray()
    while True:
        chunk = await upload.read(CHUNK_SIZE_BYTES)
        if not chunk:
            break
        content.extend(chunk)
        if len(content) > max_bytes:
            raise HTTPException(
                status_code=status.HTTP_413_CONTENT_TOO_LARGE,
                detail="Uploaded file exceeds the configured size limit.",
            )
    return bytes(content)

"""
RAG pipeline router — Client-Led Decentralized Sync.

Implements the O(1) memory footprint pattern: the frontend admin UI drives
the sync loop file-by-file, preventing OOM on 512MB free-tier hosts.

Flow:
1. GET /rag/delta → Backend scans Google Drive vs. DB, returns file delta
2. POST /rag/sync-file → Backend processes ONE file (extract, chunk, embed, store)
"""

from __future__ import annotations

import logging
import re
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from postgrest.exceptions import APIError

from core.auth import require_permission
from core.config import get_settings
from core.database import get_supabase_client
from services.llm.hybrid import get_hybrid_client

logger = logging.getLogger(__name__)
router = APIRouter(
    prefix="/rag", tags=["rag"],
    dependencies=[Depends(require_permission("knowledge.manage"))],
)

# Maximum chunk size in characters for text splitting
CHUNK_SIZE = 1000
CHUNK_OVERLAP = 200


# ── Models ──────────────────────────────────────────────────────────────────


class FileDelta(BaseModel):
    gdrive_id: str
    name: str
    mime_type: str
    md5_checksum: str
    operation: str  # 'new', 'modified', 'deleted'


class DeltaResponse(BaseModel):
    new: list[FileDelta]
    modified: list[FileDelta]
    deleted: list[FileDelta]
    total_changes: int


class SyncResult(BaseModel):
    file_id: str
    gdrive_id: str
    name: str
    chunks_created: int
    status: str


class RagStatsResponse(BaseModel):
    total_files: int
    total_chunks: int
    files: list[dict[str, Any]]


# ── Google Drive Helpers ────────────────────────────────────────────────────


def _get_drive_service() -> Any:
    """Build an authorized Google Drive API service."""
    from google.oauth2 import service_account
    from googleapiclient.discovery import build

    settings = get_settings()
    info = settings.google_service_account_info
    if not info:
        raise HTTPException(
            status_code=503, detail="Google Drive credentials not configured."
        )

    credentials = service_account.Credentials.from_service_account_info(
        info, scopes=["https://www.googleapis.com/auth/drive.readonly"]
    )
    return build("drive", "v3", credentials=credentials, cache_discovery=False)


def _list_drive_files() -> list[dict[str, str]]:
    """List all files in the configured Google Drive folder."""
    settings = get_settings()
    if not settings.google_drive_folder_id:
        raise HTTPException(status_code=503, detail="Google Drive folder is not configured.")

    service = _get_drive_service()
    folder = service.files().get(
        fileId=settings.google_drive_folder_id, fields="mimeType,trashed", supportsAllDrives=True,
    ).execute()
    if folder.get("trashed") or folder.get("mimeType") != "application/vnd.google-apps.folder":
        raise HTTPException(status_code=503, detail="Configured Google Drive folder is unavailable.")
    files: list[dict[str, str]] = []
    page_token = None

    while True:
        response = (
            service.files()
            .list(
                q=f"'{settings.google_drive_folder_id}' in parents and trashed = false",
                fields="nextPageToken,incompleteSearch,files(id,name,mimeType,md5Checksum,version)",
                supportsAllDrives=True, includeItemsFromAllDrives=True,
                pageToken=page_token,
                pageSize=100,
            )
            .execute()
        )

        if response.get("incompleteSearch"):
            raise HTTPException(status_code=503, detail="Google Drive returned an incomplete file list.")
        for f in response.get("files", []):
            if f["mimeType"] == "application/vnd.google-apps.folder":
                continue
            revision = f.get("md5Checksum") or (f"version:{f['version']}" if f.get("version") else "")
            if not revision:
                raise HTTPException(status_code=503, detail="Google Drive returned incomplete revision metadata.")
            files.append({
                "gdrive_id": f["id"],
                "name": f["name"],
                "mime_type": f["mimeType"],
                "md5_checksum": revision,
            })

        page_token = response.get("nextPageToken")
        if not page_token:
            break

    return files


def _download_file_content(gdrive_id: str, mime_type: str) -> str:
    """Download and extract text content from a Google Drive file.

    Supports Google Docs (export as plain text) and binary files
    (PDF text extraction via basic parsing).
    """
    service = _get_drive_service()

    # Google Docs/Sheets/Slides → export as plain text
    google_mime_exports = {
        "application/vnd.google-apps.document": "text/plain",
        "application/vnd.google-apps.spreadsheet": "text/csv",
        "application/vnd.google-apps.presentation": "text/plain",
    }

    if mime_type in google_mime_exports:
        request = service.files().export_media(
            fileId=gdrive_id, mimeType=google_mime_exports[mime_type]
        )
    else:
        request = service.files().get_media(fileId=gdrive_id)

    content_bytes = request.execute()

    if isinstance(content_bytes, bytes):
        if mime_type == "application/pdf":
            try:
                import PyPDF2
                from io import BytesIO
                reader = PyPDF2.PdfReader(BytesIO(content_bytes))
                text = ""
                for page in reader.pages:
                    extracted = page.extract_text()
                    if extracted:
                        text += extracted + "\\n\\n"
                return text
            except Exception as e:
                raise HTTPException(status_code=422, detail="Could not extract text from PDF.") from e

        # Attempt UTF-8 decode for other raw formats
        try:
            return content_bytes.decode("utf-8", errors="ignore")
        except Exception:
            return content_bytes.decode("latin-1", errors="ignore")

    return str(content_bytes)


# ── Text Chunking ───────────────────────────────────────────────────────────


def _chunk_text(text: str, chunk_size: int = CHUNK_SIZE, overlap: int = CHUNK_OVERLAP) -> list[str]:
    """Split text into overlapping chunks for embedding.

    Uses paragraph-aware splitting to maintain context boundaries.
    """
    if not text.strip():
        return []

    # Split on double newlines (paragraphs) first
    paragraphs = re.split(r"\n{2,}", text.strip())
    chunks: list[str] = []
    current_chunk: list[str] = []
    current_length = 0

    for paragraph in paragraphs:
        paragraph = paragraph.strip()
        if not paragraph:
            continue

        if current_length + len(paragraph) > chunk_size and current_chunk:
            chunk_text = "\n\n".join(current_chunk)
            chunks.append(chunk_text)

            # Keep overlap: retain the last paragraph(s) up to overlap chars
            overlap_text = ""
            for p in reversed(current_chunk):
                if len(overlap_text) + len(p) <= overlap:
                    overlap_text = p + "\n\n" + overlap_text if overlap_text else p
                else:
                    break

            current_chunk = [overlap_text] if overlap_text else []
            current_length = len(overlap_text)

        current_chunk.append(paragraph)
        current_length += len(paragraph)

    # Don't forget the last chunk
    if current_chunk:
        chunks.append("\n\n".join(current_chunk))

    # Handle very long paragraphs: split by sentences if chunk is still too large
    final_chunks: list[str] = []
    for chunk in chunks:
        if len(chunk) > chunk_size * 1.5:
            sentences = re.split(r"(?<=[.!?])\s+", chunk)
            sub_chunk = ""
            for sentence in sentences:
                if len(sub_chunk) + len(sentence) > chunk_size:
                    if sub_chunk:
                        final_chunks.append(sub_chunk.strip())
                    sub_chunk = sentence
                else:
                    sub_chunk += " " + sentence if sub_chunk else sentence
            if sub_chunk:
                final_chunks.append(sub_chunk.strip())
        else:
            final_chunks.append(chunk)

    return final_chunks


# ── Endpoints ───────────────────────────────────────────────────────────────


@router.get("/delta", response_model=DeltaResponse)
async def get_rag_delta():
    """Compare Google Drive state against the database and return the diff.

    Returns three sets: new files, modified files (checksum mismatch),
    and deleted files (in DB but not in Drive).
    """
    db = get_supabase_client()
    # Get current DB state
    db_files = db.table("rag_files").select("gdrive_id, md5_checksum, name, index_complete").execute()
    db_map: dict[str, dict] = {f["gdrive_id"]: f for f in (db_files.data or [])}

    # Get current Drive state
    drive_files = _list_drive_files()
    drive_map: dict[str, dict] = {f["gdrive_id"]: f for f in drive_files}

    new_files: list[FileDelta] = []
    modified_files: list[FileDelta] = []
    deleted_files: list[FileDelta] = []

    # Find new and modified files
    for gdrive_id, drive_file in drive_map.items():
        if gdrive_id not in db_map:
            new_files.append(FileDelta(**drive_file, operation="new"))
        elif (not db_map[gdrive_id]["index_complete"]
              or drive_file["md5_checksum"] != db_map[gdrive_id]["md5_checksum"]):
            modified_files.append(FileDelta(**drive_file, operation="modified"))

    # Find deleted files
    for gdrive_id, db_file in db_map.items():
        if gdrive_id not in drive_map:
            deleted_files.append(
                FileDelta(
                    gdrive_id=gdrive_id,
                    name=db_file["name"],
                    mime_type="",
                    md5_checksum=db_file["md5_checksum"],
                    operation="deleted",
                )
            )

    total = len(new_files) + len(modified_files) + len(deleted_files)
    return DeltaResponse(
        new=new_files,
        modified=modified_files,
        deleted=deleted_files,
        total_changes=total,
    )


@router.post("/sync-file", response_model=SyncResult)
async def sync_single_file(
    gdrive_id: str = Query(..., description="Google Drive file ID to sync"),
):
    """Process a single file: download, extract, chunk, embed, and store.

    Stage one document at a time and publish all chunks in a single transaction.
    """
    db = get_supabase_client()
    llm = get_hybrid_client()

    existing = (db.table("rag_files").select("md5_checksum")
                .eq("gdrive_id", gdrive_id).execute().data)
    expected_revision = existing[0]["md5_checksum"] if existing else None

    # Get file metadata from Drive
    drive_files = _list_drive_files()
    target_file = None
    for f in drive_files:
        if f["gdrive_id"] == gdrive_id:
            target_file = f
            break

    if not target_file:
        raise HTTPException(status_code=404, detail="File not found in Google Drive.")

    # Download and extract text content
    text_content = _download_file_content(gdrive_id, target_file["mime_type"])
    chunks = []
    try:
        for content in _chunk_text(text_content):
            embedding = await llm.embed(content)
            if len(embedding) != 768:
                raise ValueError("Embedding must have 768 dimensions.")
            chunks.append({"content": content, "embedding": embedding})
    except Exception as exc:
        logger.exception("Could not embed file %s", gdrive_id)
        raise HTTPException(status_code=503, detail="Embedding failed. Retry the sync.") from exc

    try:
        file_id = db.rpc("replace_rag_index", {
            "drive_id": gdrive_id, "file_name": target_file["name"],
            "file_mime": target_file["mime_type"], "revision": target_file["md5_checksum"],
            "expected_revision": expected_revision, "chunks": chunks,
        }).execute().data
    except APIError as exc:
        if exc.code == "40001":
            raise HTTPException(status_code=409, detail=exc.message) from exc
        raise

    return SyncResult(
        file_id=file_id,
        gdrive_id=gdrive_id,
        name=target_file["name"],
        chunks_created=len(chunks),
        status="synced" if chunks else "empty_file",
    )


@router.delete("/file")
async def delete_rag_file(
    gdrive_id: str = Query(..., description="Google Drive file ID to remove"),
):
    """Remove a file and all its chunks from the RAG index."""
    if any(f["gdrive_id"] == gdrive_id for f in _list_drive_files()):
        raise HTTPException(status_code=409, detail="File is still present in the Drive folder. Scan again.")
    db = get_supabase_client()
    result = db.table("rag_files").delete().eq("gdrive_id", gdrive_id).execute()

    if not result.data:
        raise HTTPException(status_code=404, detail="File not found in RAG index.")

    return {"status": "deleted", "gdrive_id": gdrive_id}


@router.get("/stats", response_model=RagStatsResponse)
async def get_rag_stats():
    """Return RAG pipeline statistics: file count, chunk count, per-file details."""
    db = get_supabase_client()

    files = db.table("rag_files").select("id, name, mime_type, updated_at").execute()
    total_files = len(files.data) if files.data else 0

    # Count total chunks
    chunks = db.table("rag_chunks").select("id", count="exact").execute()
    total_chunks = chunks.count if hasattr(chunks, "count") and chunks.count else 0

    # Per-file chunk counts
    file_details: list[dict[str, Any]] = []
    for f in (files.data or []):
        chunk_count = (
            db.table("rag_chunks")
            .select("id", count="exact")
            .eq("file_id", f["id"])
            .execute()
        )
        file_details.append({
            "id": f["id"],
            "name": f["name"],
            "mime_type": f["mime_type"],
            "updated_at": f["updated_at"],
            "chunk_count": chunk_count.count if hasattr(chunk_count, "count") and chunk_count.count else 0,
        })

    return RagStatsResponse(
        total_files=total_files,
        total_chunks=total_chunks,
        files=file_details,
    )

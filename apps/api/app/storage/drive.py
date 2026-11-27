from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from urllib.parse import quote

import httpx
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.errors import NotFoundError, ValidationFailedError
from app.identity.service import get_valid_google_access_token
from app.storage.local import local_metadata, start_upload, validate_upload_key, vault_path

FILES = "https://www.googleapis.com/drive/v3/files"
UPLOAD = "https://www.googleapis.com/upload/drive/v3/files"


@dataclass
class UploadSession:
    provider: str
    storage_key: str
    session_uri: str
    upload_headers: dict = field(default_factory=dict)
    token_refresh_url: str | None = None
    chunk_size_bytes: int = 8 * 1024 * 1024


@dataclass
class VerifiedFile:
    storage_key: str
    drive_file_id: str | None
    size_bytes: int
    sha256: str | None


class StorageProvider:
    async def verify_uploaded_file(
        self,
        *,
        user_id,
        storage_key,
        drive_file_id,
        expected_size,
        expected_sha256: str | None = None,
    ):
        meta = await self.metadata(user_id, storage_key, drive_file_id, checksum=True)
        if int(meta["size"]) != expected_size:
            raise ValidationFailedError("Stored file size does not match upload")
        actual_sha256 = meta.get("sha256Checksum")
        if expected_sha256 and actual_sha256 and expected_sha256.lower() != actual_sha256.lower():
            raise ValidationFailedError("Stored file checksum does not match upload")
        return VerifiedFile(storage_key, drive_file_id, expected_size, actual_sha256)


class LocalVaultProvider(StorageProvider):
    name = "local"

    async def ensure_project_space(self, project_id, slug, user_id):
        vault_path(project_id).mkdir(parents=True, exist_ok=True)
        return project_id

    async def create_upload_session(self, *, project_id, storage_key, total_bytes, filename, **kwargs):
        validate_upload_key(storage_key, project_id, filename)
        token = await asyncio.to_thread(start_upload, storage_key, total_bytes)
        base = get_settings().public_api_url.rstrip("/")
        return UploadSession(self.name, storage_key, f"{base}/v1/storage/uploads/{token}")

    async def metadata(self, user_id, storage_key, drive_file_id, checksum=False):
        return await asyncio.to_thread(local_metadata, storage_key, checksum)

    async def read_range(self, *, user_id, storage_key, drive_file_id, start=0, end=None):
        meta = await self.metadata(user_id, storage_key, drive_file_id)

        def read():
            with vault_path(storage_key).open("rb") as f:
                f.seek(start)
                return f.read(end - start + 1 if end is not None else -1)

        return await asyncio.to_thread(read), int(meta["size"]), meta["mimeType"] or "application/octet-stream"


class GoogleDriveProvider(StorageProvider):
    name = "drive"

    def __init__(self, db: Session):
        self.db = db

    async def _headers(self, user_id):
        token = await get_valid_google_access_token(self.db, user_id)
        return {"Authorization": f"Bearer {token}"}

    async def _folder(self, client, headers, parent, name):
        def escape(value):
            return value.replace("\\", "\\\\").replace("'", "\\'")

        mime = "application/vnd.google-apps.folder"
        query = f"trashed=false and mimeType='{mime}' and name='{escape(name)}' and '{escape(parent)}' in parents"
        res = await client.get(FILES, headers=headers, params={"q": query, "fields": "files(id)", "pageSize": 1})
        res.raise_for_status()
        files = res.json()["files"]
        if files:
            return files[0]["id"]
        res = await client.post(FILES, headers=headers, json={"name": name, "parents": [parent], "mimeType": mime})
        res.raise_for_status()
        return res.json()["id"]

    async def ensure_project_space(self, project_id, slug, user_id):
        headers = await self._headers(user_id)
        async with httpx.AsyncClient(timeout=20) as client:
            root = await self._folder(client, headers, "root", "KinetiqStudio")
            return await self._folder(client, headers, root, f"{slug}_{project_id}")

    async def create_upload_session(
        self,
        *,
        user_id,
        project_id,
        project_folder_id,
        storage_key,
        filename,
        content_type,
        total_bytes,
        app_properties,
    ):
        validate_upload_key(storage_key, project_id, filename)
        headers = await self._headers(user_id)
        async with httpx.AsyncClient(timeout=20) as client:
            parent = project_folder_id
            for folder in storage_key.split("/")[1:-1]:
                parent = await self._folder(client, headers, parent, folder)
            res = await client.post(
                UPLOAD,
                params={"uploadType": "resumable"},
                headers={**headers, "X-Upload-Content-Type": content_type, "X-Upload-Content-Length": str(total_bytes)},
                json={"name": filename, "parents": [parent], "appProperties": app_properties},
            )
            res.raise_for_status()
        base = get_settings().public_api_url.rstrip("/")
        return UploadSession(
            self.name,
            storage_key,
            res.headers["location"],
            headers,
            base + "/v1/workers/jobs/{job_id}/upload-token-refresh",
        )

    async def metadata(self, user_id, storage_key, drive_file_id, checksum=False):
        if not drive_file_id:
            raise NotFoundError("Drive file ID missing")
        async with httpx.AsyncClient(timeout=20) as client:
            res = await client.get(
                f"{FILES}/{quote(drive_file_id, safe='')}",
                headers=await self._headers(user_id),
                params={"fields": "id,size,trashed,mimeType,sha256Checksum,appProperties"},
            )
        if res.status_code == 404:
            raise NotFoundError("Drive file missing")
        res.raise_for_status()
        meta = res.json()
        if meta.get("trashed"):
            raise NotFoundError("Drive file trashed")
        return meta

    async def read_range(self, *, user_id, storage_key, drive_file_id, start=0, end=None):
        if not drive_file_id:
            raise NotFoundError("Drive file ID missing")
        headers = await self._headers(user_id)
        if start or end is not None:
            headers["Range"] = f"bytes={start}-{end if end is not None else ''}"
        async with httpx.AsyncClient(timeout=120) as client:
            res = await client.get(f"{FILES}/{quote(drive_file_id, safe='')}", params={"alt": "media"}, headers=headers)
        res.raise_for_status()
        total = int(res.headers["content-range"].split("/")[-1]) if res.status_code == 206 else len(res.content)
        return res.content, total, res.headers.get("content-type", "application/octet-stream")


def get_storage_provider(db: Session, name: str | None = None) -> StorageProvider:
    name = get_settings().storage_provider if name is None else name
    if name == "drive":
        return GoogleDriveProvider(db)
    if name == "local":
        return LocalVaultProvider()
    raise ValidationFailedError("Unknown storage provider")

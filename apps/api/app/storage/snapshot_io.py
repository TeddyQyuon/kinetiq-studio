"""Small JSON files in a project vault, including lookup after the DB is lost."""

from __future__ import annotations

import asyncio
import json
import os
import tempfile
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import quote

import httpx

from app.core.errors import NotFoundError
from app.storage.local import vault_path

FILES = "https://www.googleapis.com/drive/v3/files"
UPLOAD = "https://www.googleapis.com/upload/drive/v3/files"


def _write_local_json(storage_key, document):
    path = vault_path(storage_key)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=path.parent)
    tmp = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as out:
            json.dump(document, out, ensure_ascii=False)
            out.flush()
            os.fsync(out.fileno())
        os.replace(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)


class LocalSnapshotIO:
    @asynccontextmanager
    async def snapshot_access(self, user_id):
        yield None

    async def write_json(self, client, storage_key, document, *, project_folder_id, revision):
        await asyncio.to_thread(_write_local_json, storage_key, document)
        return storage_key

    async def read_project_json(self, client, project_id):
        try:
            content = await asyncio.to_thread(vault_path(f"{project_id}/project.json").read_text, encoding="utf-8")
        except FileNotFoundError:
            raise NotFoundError("Vault project snapshot missing") from None
        return json.loads(content)


def _escape(value):
    return value.replace("\\", "\\\\").replace("'", "\\'")


async def _find_file(client, query):
    result = await client.get(FILES, params={"q": "trashed=false and " + query, "fields": "files(id)", "pageSize": 1})
    result.raise_for_status()
    files = result.json()["files"]
    return files[0]["id"] if files else None


class DriveSnapshotIO:
    @asynccontextmanager
    async def snapshot_access(self, user_id):
        # Token refresh can commit its own transaction. Finish it before claiming
        # any backup work, and use one short-lived credential for the batch.
        headers = await self._headers(user_id)
        async with httpx.AsyncClient(headers=headers, timeout=20) as client:
            yield client

    async def snapshot_folder(self, client, project):
        if project.drive_folder_id and project.drive_folder_id != project.id:
            return project.drive_folder_id
        slug = "".join(c if c.isalnum() else "-" for c in project.title.lower()).strip("-")[:32] or "project"
        root = await self._folder(client, {}, "root", "KinetiqStudio")
        return await self._folder(client, {}, root, f"{slug}_{project.id}")

    async def write_json(self, client, storage_key, document, *, project_folder_id, revision):
        project_id, *folders, name = storage_key.split("/")
        parent = project_folder_id
        for folder in folders:
            parent = await self._folder(client, {}, parent, folder)
        existing = await _find_file(client, f"name='{_escape(name)}' and '{_escape(parent)}' in parents")
        metadata = {
            "name": name,
            "mimeType": "application/json",
            "appProperties": {
                "project_id": project_id,
                "kind": "project_snapshot" if name == "project.json" else "timeline_snapshot",
                "revision": str(revision),
            },
        }
        url, method = UPLOAD, "POST"
        if existing:
            url, method = f"{UPLOAD}/{quote(existing, safe='')}", "PATCH"
        else:
            metadata["parents"] = [parent]
        body = json.dumps(document, ensure_ascii=False).encode("utf-8")
        session = await client.request(
            method,
            url,
            params={"uploadType": "resumable", "fields": "id"},
            json=metadata,
            headers={"X-Upload-Content-Type": "application/json", "X-Upload-Content-Length": str(len(body))},
        )
        session.raise_for_status()
        # A single final chunk may have any length. The client carries the
        # Authorization header on both the session request and its final PUT.
        response = await client.put(
            session.headers["location"],
            content=body,
            headers={"Content-Type": "application/json", "Content-Range": f"bytes 0-{len(body) - 1}/{len(body)}"},
        )
        response.raise_for_status()
        return response.json()["id"]

    async def read_project_json(self, client, project_id):
        # App properties survive title/folder renames and do not require a
        # cached Drive folder ID in the database being recovered.
        file_id = await _find_file(
            client,
            "name='project.json' and appProperties has { key='kind' and value='project_snapshot' } "
            f"and appProperties has {{ key='project_id' and value='{_escape(project_id)}' }}",
        )
        if not file_id:
            raise NotFoundError("Drive project snapshot missing")
        response = await client.get(f"{FILES}/{quote(file_id, safe='')}", params={"alt": "media"})
        response.raise_for_status()
        return response.json()

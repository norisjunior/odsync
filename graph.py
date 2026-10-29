# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 norisjunior

import time
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from urllib.parse import quote

import requests

import auth
import state


BASE = "https://graph.microsoft.com/v1.0/me/drive"
CHUNK_SIZE = 5 * 1024 * 1024
RETRIES = 5
_known_folders = set()


def _path(path):
    return "/".join(quote(part, safe="") for part in path.split("/") if part)


def _delay(response, attempt):
    value = response.headers.get("Retry-After") if response is not None else None
    if value:
        try:
            return max(0, int(value))
        except ValueError:
            try:
                return max(0, (parsedate_to_datetime(value) - datetime.now(timezone.utc)).total_seconds())
            except (TypeError, ValueError):
                pass
    return min(2 ** attempt, 60)


def _request(method, url, *, token=None, **kwargs):
    headers = kwargs.pop("headers", {}).copy()
    if token:
        headers["Authorization"] = f"Bearer {token}"
    for attempt in range(RETRIES):
        response = None
        try:
            response = requests.request(method, url, headers=headers, timeout=60, **kwargs)
        except requests.RequestException as error:
            if attempt == RETRIES - 1:
                raise RuntimeError(f"Falha de rede em {method}: {type(error).__name__}") from None
        else:
            if response.status_code != 429 and not 500 <= response.status_code <= 599:
                return response
            if attempt == RETRIES - 1:
                break
        time.sleep(_delay(response, attempt))
    raise RuntimeError(f"Falha HTTP {response.status_code} após {RETRIES} tentativas")


def _checked(response):
    if not response.ok:
        raise RuntimeError(f"Graph HTTP {response.status_code}: {response.text[:300]}")
    return response


def _upload_offset(response):
    ranges = _checked(response).json().get("nextExpectedRanges", [])
    if not ranges:
        raise RuntimeError("Sessão de upload sem próximo intervalo")
    return int(ranges[0].split("-")[0])


def remote_file(remote_path, tenant_id, client_id):
    token = auth.get_access_token(tenant_id, client_id)
    response = _request("GET", f"{BASE}/root:/{_path(remote_path)}", token=token)
    if response.status_code == 404:
        return None
    return _checked(response).json()


def remote_tree(remote_dir, tenant_id, client_id, recursive=True):
    result = {}
    pending = [(remote_dir, "")]
    while pending:
        folder, relative_folder = pending.pop()
        url = f"{BASE}/root:/{_path(folder)}:/children?$select=id,name,size,file,folder,eTag"
        while url:
            token = auth.get_access_token(tenant_id, client_id)
            response = _request("GET", url, token=token)
            if response.status_code == 404:
                break
            data = _checked(response).json()
            for item in data.get("value", []):
                relative = "/".join(filter(None, (relative_folder, item["name"])))
                if "folder" in item and recursive:
                    child = "/".join(filter(None, (folder, item["name"])))
                    pending.append((child, relative))
                elif "file" in item:
                    result[relative] = item
            url = data.get("@odata.nextLink")
    return result


def delete_file(item, tenant_id, client_id):
    if "file" not in item or not item.get("id") or not item.get("eTag"):
        raise RuntimeError("Exclusão recusada: arquivo sem ID ou eTag")
    token = auth.get_access_token(tenant_id, client_id)
    response = _request("DELETE", f"{BASE}/items/{quote(item['id'], safe='')}",
                        token=token, headers={"If-Match": item["eTag"]})
    if response.status_code != 404:
        _checked(response)


def ensure_folders(remote_dir, tenant_id, client_id):
    token = auth.get_access_token(tenant_id, client_id)
    parts = [part for part in remote_dir.split("/") if part]
    current = ""
    for part in parts:
        candidate = f"{current}/{part}" if current else part
        if candidate in _known_folders:
            current = candidate
            continue
        response = _request("GET", f"{BASE}/root:/{_path(candidate)}", token=token)
        if response.status_code == 404:
            parent = f"{BASE}/root:/{_path(current)}:/children" if current else f"{BASE}/root/children"
            response = _request(
                "POST", parent, token=token,
                json={"name": part, "folder": {}, "@microsoft.graph.conflictBehavior": "fail"},
            )
            if response.status_code == 409:
                response = _request("GET", f"{BASE}/root:/{_path(candidate)}", token=token)
        _checked(response)
        if "folder" not in response.json():
            raise RuntimeError(f"Destino não é uma pasta: {candidate}")
        _known_folders.add(candidate)
        current = candidate


def upload(file_path, remote_path, size, mtime_ns, sha256, connection, tenant_id, client_id):
    parent = remote_path.rpartition("/")[0]
    ensure_folders(parent, tenant_id, client_id)
    target = f"{BASE}/root:/{_path(remote_path)}"
    if size < 4 * 1024 * 1024:
        token = auth.get_access_token(tenant_id, client_id)
        with open(file_path, "rb") as stream:
            data = stream.read()
        _checked(_request("PUT", target + ":/content", token=token,
                          headers={"Content-Type": "application/octet-stream"}, data=data))
        return

    restarts = 0
    while True:
        saved = state.session(connection, str(file_path))
        if saved and saved[:3] == (size, mtime_ns, sha256):
            upload_url = saved[3]
            response = _request("GET", upload_url)
            if response.status_code in (403, 404):
                state.clear_session(connection, str(file_path))
                saved = None
            else:
                offset = _upload_offset(response)
        else:
            saved = None

        if not saved:
            token = auth.get_access_token(tenant_id, client_id)
            response = _checked(_request(
                "POST", target + ":/createUploadSession", token=token,
                json={"item": {"@microsoft.graph.conflictBehavior": "replace"}},
            ))
            upload_url = response.json()["uploadUrl"]
            state.save_session(connection, str(file_path), size, mtime_ns, sha256, upload_url)
            offset = 0

        restart = False
        with open(file_path, "rb") as stream:
            failures = 0
            while offset < size:
                stream.seek(offset)
                chunk = stream.read(CHUNK_SIZE)
                end = offset + len(chunk) - 1
                try:
                    response = _request(
                        "PUT", upload_url, data=chunk,
                        headers={"Content-Length": str(len(chunk)),
                                 "Content-Range": f"bytes {offset}-{end}/{size}"},
                    )
                except RuntimeError:
                    failures += 1
                    if failures >= RETRIES:
                        raise
                    status = _request("GET", upload_url)
                    if status.status_code in (403, 404):
                        response = status
                        restart = True
                        break
                    offset = _upload_offset(status)
                    continue
                if response.status_code in (200, 201):
                    state.clear_session(connection, str(file_path))
                    return
                if response.status_code == 202:
                    failures = 0
                    offset = _upload_offset(response)
                    continue
                if response.status_code == 416:
                    status = _request("GET", upload_url)
                    if status.status_code in (403, 404):
                        response = status
                        restart = True
                        break
                    offset = _upload_offset(status)
                    continue
                if response.status_code in (403, 404):
                    restart = True
                    break
                _checked(response)
                raise RuntimeError(f"Resposta inesperada da sessão: HTTP {response.status_code}")

        state.clear_session(connection, str(file_path))
        if not restart or restarts >= 1:
            _checked(response)
            raise RuntimeError(f"Resposta inesperada da sessão: HTTP {response.status_code}")
        restarts += 1

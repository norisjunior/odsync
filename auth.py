# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 norisjunior

import base64
import hashlib
import json
import os
import secrets
import tempfile
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urlparse

import requests


SCOPES = "Files.ReadWrite offline_access User.Read"
REDIRECT_URI = "http://localhost:53682/"
TOKEN_PATH = Path.home() / ".config" / "odsync" / "token.json"
REFRESH_MARGIN = 300
HTTP_TIMEOUT = 30
LOGIN_TIMEOUT = 300


def _token_url(tenant_id):
    return f"https://login.microsoftonline.com/{tenant_id}/oauth2/v2.0/token"


def _pkce_pair():
    verifier = secrets.token_urlsafe(64)
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
    return verifier, challenge


def _save_tokens(tokens, previous=None):
    refresh_token = tokens.get("refresh_token") or (previous or {}).get("refresh_token")
    if not refresh_token:
        raise RuntimeError("A resposta do Entra ID não contém refresh_token")

    saved = {
        "access_token": tokens["access_token"],
        "refresh_token": refresh_token,
        "expires_at": int(time.time()) + int(tokens["expires_in"]),
    }
    TOKEN_PATH.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(TOKEN_PATH.parent, 0o700)

    fd, temporary = tempfile.mkstemp(dir=TOKEN_PATH.parent, prefix="token.")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(saved, stream)
            stream.write("\n")
        os.chmod(temporary, 0o600)
        os.replace(temporary, TOKEN_PATH)
        os.chmod(TOKEN_PATH, 0o600)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)

    return saved


def _token_request(tenant_id, data):
    response = requests.post(_token_url(tenant_id), data=data, timeout=HTTP_TIMEOUT)
    if not response.ok:
        try:
            detail = response.json().get("error_description") or response.text
        except ValueError:
            detail = response.text
        raise RuntimeError(f"Falha na autenticação ({response.status_code}): {detail}")
    return response.json()


def _receive_authorization_code(expected_state, authorize_url):
    result = {}

    class CallbackHandler(BaseHTTPRequestHandler):
        def do_GET(self):
            query = parse_qs(urlparse(self.path).query)
            if not secrets.compare_digest(query.get("state", [""])[0], expected_state):
                result["error"] = "state inválido no retorno OAuth"
                self._reply(400, "Falha de autenticação: state inválido.")
                return
            if "error" in query:
                result["error"] = query.get("error_description", query["error"])[0]
                self._reply(400, "Autenticação recusada. Volte ao terminal.")
                return
            code = query.get("code", [None])[0]
            if not code:
                result["error"] = "retorno OAuth sem código de autorização"
                self._reply(400, "Código de autorização ausente.")
                return
            result["code"] = code
            self._reply(200, "Autenticação concluída. Você pode fechar esta aba.")

        def _reply(self, status, message):
            body = message.encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, format, *args):
            pass

    server = HTTPServer(("127.0.0.1", 53682), CallbackHandler)
    server.timeout = LOGIN_TIMEOUT
    try:
        print(f"Abra esta URL para entrar:\n{authorize_url}")
        if os.environ.get("DISPLAY") and not webbrowser.open(authorize_url):
            print("Não foi possível abrir o navegador automaticamente.")
        server.handle_request()
    finally:
        server.server_close()

    if "code" not in result:
        raise RuntimeError(result.get("error", "tempo de login esgotado"))
    return result["code"]


def login(tenant_id, client_id):
    verifier, challenge = _pkce_pair()
    state = secrets.token_urlsafe(32)
    authorize_url = (
        f"https://login.microsoftonline.com/{tenant_id}/oauth2/v2.0/authorize?"
        + urlencode(
            {
                "client_id": client_id,
                "response_type": "code",
                "redirect_uri": REDIRECT_URI,
                "response_mode": "query",
                "scope": SCOPES,
                "state": state,
                "code_challenge": challenge,
                "code_challenge_method": "S256",
            }
        )
    )

    code = _receive_authorization_code(state, authorize_url)
    tokens = _token_request(
        tenant_id,
        {
            "client_id": client_id,
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": REDIRECT_URI,
            "scope": SCOPES,
            "code_verifier": verifier,
        },
    )
    _save_tokens(tokens)
    return tokens["access_token"]


def get_access_token(tenant_id, client_id):
    try:
        with TOKEN_PATH.open(encoding="utf-8") as stream:
            saved = json.load(stream)
    except (FileNotFoundError, json.JSONDecodeError, KeyError) as error:
        raise RuntimeError("Token ausente ou inválido; execute 'odsync login'") from error

    if saved.get("access_token") and saved.get("expires_at", 0) - time.time() >= REFRESH_MARGIN:
        return saved["access_token"]

    tokens = _token_request(
        tenant_id,
        {
            "client_id": client_id,
            "grant_type": "refresh_token",
            "refresh_token": saved["refresh_token"],
            "scope": SCOPES,
        },
    )
    return _save_tokens(tokens, saved)["access_token"]

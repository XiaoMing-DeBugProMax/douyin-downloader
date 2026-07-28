from __future__ import annotations

import secrets
from dataclasses import dataclass, field
from urllib.parse import urlsplit

from fastapi import HTTPException, Request

COOKIE_NAME = "douyin_local_session"


@dataclass(slots=True)
class SessionManager:
    cookie_token: str = field(default_factory=lambda: secrets.token_urlsafe(32))
    management_token: str = field(default_factory=lambda: secrets.token_urlsafe(32))
    _launch_tokens: set[str] = field(default_factory=set)

    def issue_launch_token(self) -> str:
        token = secrets.token_urlsafe(32)
        self._launch_tokens.add(token)
        return token

    def consume_launch_token(self, token: str) -> bool:
        for pending in tuple(self._launch_tokens):
            if secrets.compare_digest(pending, token):
                self._launch_tokens.remove(pending)
                return True
        return False

    def valid_cookie(self, candidate: str | None) -> bool:
        return candidate is not None and secrets.compare_digest(candidate, self.cookie_token)


def require_local_session(request: Request) -> None:
    manager: SessionManager = request.app.state.session_manager
    if not manager.valid_cookie(request.cookies.get(COOKIE_NAME)):
        raise HTTPException(status_code=403, detail="LOCAL_SESSION_REQUIRED")


def require_same_origin(request: Request) -> None:
    origin = request.headers.get("origin")
    if origin is None:
        raise HTTPException(status_code=403, detail="SAME_ORIGIN_REQUIRED")

    parsed = urlsplit(origin)
    if parsed.scheme != request.url.scheme or parsed.netloc != request.url.netloc:
        raise HTTPException(status_code=403, detail="SAME_ORIGIN_REQUIRED")

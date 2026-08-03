"""Shared-password login and signed researcher sessions.

The browser submits one shared password. The backend checks it against a
single configured secret and issues a short-lived HttpOnly cookie. Every
protected page and API request re-validates that cookie so access expires
automatically after the configured window.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import time
from typing import Callable


COOKIE_NAME = "mas_researcher"
DEFAULT_SESSION_SECONDS = 5 * 60 * 60
DEFAULT_SESSION_LABEL = "researcher"


class AuthConfigurationError(RuntimeError):
    pass


class ResearcherAccessDenied(RuntimeError):
    pass


def _b64_encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def _b64_decode(value: str) -> bytes:
    padding = "=" * (-len(value) % 4)
    return base64.urlsafe_b64decode(value + padding)


class ResearcherAuth:
    def __init__(
        self,
        *,
        shared_password: str | None = None,
        session_label: str | None = None,
        cookie_secret: str | None = None,
        session_seconds: int = DEFAULT_SESSION_SECONDS,
        clock: Callable[[], float] | None = None,
    ) -> None:
        self.shared_password = (
            shared_password
            if shared_password is not None
            else os.getenv("ADMIN_SHARED_PASSWORD", "")
        ).strip()
        self.session_label = (
            (
                session_label
                if session_label is not None
                else os.getenv("RESEARCHER_SESSION_LABEL", DEFAULT_SESSION_LABEL)
            ).strip()
            or DEFAULT_SESSION_LABEL
        )
        secret = (
            cookie_secret
            if cookie_secret is not None
            else os.getenv("AUTH_COOKIE_SECRET", "")
        )
        self.cookie_secret = secret.encode("utf-8")
        self.session_seconds = max(60, int(session_seconds))
        self.clock = clock or time.time

    @property
    def configured(self) -> bool:
        return bool(self.shared_password and len(self.cookie_secret) >= 32)

    def _require_configured(self) -> None:
        if not self.configured:
            raise AuthConfigurationError(
                "공용 비밀번호 로그인이 아직 설정되지 않았습니다."
            )

    def verify_password(self, password: str) -> str:
        self._require_configured()
        if not password or len(password) > 256:
            raise ResearcherAccessDenied("비밀번호가 올바르지 않습니다.")
        if not hmac.compare_digest(password, self.shared_password):
            raise ResearcherAccessDenied("비밀번호가 올바르지 않습니다.")
        return self.session_label

    def create_session_value(self) -> str:
        self._require_configured()
        now = int(self.clock())
        payload = _b64_encode(
            json.dumps(
                {
                    "v": 1,
                    "label": self.session_label,
                    "iat": now,
                    "exp": now + self.session_seconds,
                },
                separators=(",", ":"),
                sort_keys=True,
            ).encode("utf-8")
        )
        signature = _b64_encode(
            hmac.new(
                self.cookie_secret,
                payload.encode("ascii"),
                hashlib.sha256,
            ).digest()
        )
        return f"{payload}.{signature}"

    def email_from_cookie_header(self, cookie_header: str | None) -> str | None:
        if not self.configured or not cookie_header:
            return None
        for segment in cookie_header.split(";"):
            name, separator, value = segment.strip().partition("=")
            if not separator or name != COOKIE_NAME:
                continue
            try:
                payload, supplied_signature = value.split(".", 1)
                expected_signature = _b64_encode(
                    hmac.new(
                        self.cookie_secret,
                        payload.encode("ascii"),
                        hashlib.sha256,
                    ).digest()
                )
                if not hmac.compare_digest(
                    supplied_signature,
                    expected_signature,
                ):
                    continue
                claims = json.loads(_b64_decode(payload))
                if (
                    claims.get("v") != 1
                    or int(claims.get("exp", 0)) <= int(self.clock())
                ):
                    continue
                label = str(claims.get("label", "")).strip()
                return label or self.session_label
            except (
                UnicodeEncodeError,
                ValueError,
                TypeError,
                json.JSONDecodeError,
            ):
                continue
        return None

    def session_cookie_header(self) -> str:
        value = self.create_session_value()
        return (
            f"{COOKIE_NAME}={value}; Path=/; Max-Age={self.session_seconds}; "
            "HttpOnly; Secure; SameSite=Lax"
        )

    @staticmethod
    def clear_cookie_header() -> str:
        return (
            f"{COOKIE_NAME}=; Path=/; Max-Age=0; "
            "HttpOnly; Secure; SameSite=Lax"
        )

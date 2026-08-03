"""Access-code verification and Supabase REST persistence without extra packages."""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import queue
import threading
import datetime as dt
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Any


class AccessDenied(RuntimeError):
    pass


class StoreError(RuntimeError):
    pass


def _env_true(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class AccessGrant:
    participant_id: str
    access_code_id: str | None = None
    role: str = "participant"
    researcher_email: str | None = None


@dataclass(frozen=True)
class PersistedExperimentSession:
    id: str
    participant_id: str
    researcher_email: str
    condition: str
    task: str
    status: str
    started_at: str | None = None
    phase: str | None = None


class SupabaseREST:
    def __init__(self, url: str, service_role_key: str) -> None:
        self.url = url.rstrip("/")
        self.service_role_key = service_role_key

    def request(
        self,
        method: str,
        table: str,
        *,
        query: dict[str, str] | None = None,
        body: Any = None,
        prefer: str | None = None,
    ) -> Any:
        encoded_query = urllib.parse.urlencode(query or {}, safe=".*(),:")
        url = f"{self.url}/rest/v1/{table}"
        if encoded_query:
            url += f"?{encoded_query}"
        payload = None if body is None else json.dumps(body, ensure_ascii=False).encode("utf-8")
        headers = {
            "apikey": self.service_role_key,
            "Content-Type": "application/json",
            "Accept": "application/json",
        }
        # New sb_secret_* keys are opaque API keys, not JWTs. Supabase assigns
        # service_role from the apikey header and rejects them as Bearer tokens.
        if not self.service_role_key.startswith("sb_secret_"):
            headers["Authorization"] = f"Bearer {self.service_role_key}"
        if prefer:
            headers["Prefer"] = prefer
        request = urllib.request.Request(url, data=payload, headers=headers, method=method)
        try:
            with urllib.request.urlopen(request, timeout=12) as response:
                raw = response.read()
                return json.loads(raw) if raw else None
        except urllib.error.HTTPError as error:
            detail = error.read().decode("utf-8", errors="replace")
            raise StoreError(f"Supabase {error.code}: {detail[:500]}") from error
        except urllib.error.URLError as error:
            raise StoreError(f"Supabase connection failed: {error.reason}") from error


class StudyStore:
    """Fail-closed access control plus non-blocking experiment logging."""

    def __init__(self) -> None:
        url = os.getenv("SUPABASE_URL", "").strip()
        service_key = os.getenv("SUPABASE_SERVICE_ROLE_KEY", "").strip()
        self.client = SupabaseREST(url, service_key) if url and service_key else None
        self.pepper = os.getenv("ACCESS_CODE_PEPPER", "")
        self.require_access = _env_true(
            "REQUIRE_ACCESS_CODE",
            default=bool(os.getenv("K_SERVICE")),
        )
        self.local_codes = self._parse_local_codes(
            os.getenv("EXPERIMENT_ACCESS_CODES", "")
        )
        self._writes: queue.Queue[tuple[str, str, dict[str, Any], dict[str, str] | None] | None] = queue.Queue()
        self._writer: threading.Thread | None = None
        if self.client is not None:
            self._writer = threading.Thread(
                target=self._writer_loop,
                name="supabase-writer",
                daemon=True,
            )
            self._writer.start()

    @staticmethod
    def _parse_local_codes(raw: str) -> dict[str, str]:
        result: dict[str, str] = {}
        for item in raw.split(","):
            item = item.strip()
            if not item or "=" not in item:
                continue
            participant_id, code = item.split("=", 1)
            participant_id = participant_id.strip().upper()
            code = code.strip()
            if participant_id and code:
                result[participant_id] = code
        return result

    def hash_code(self, code: str) -> str:
        material = f"{self.pepper}:{code}".encode("utf-8")
        return hashlib.sha256(material).hexdigest()

    @staticmethod
    def hash_session_token(token: str) -> str:
        return hashlib.sha256(token.encode("utf-8")).hexdigest()

    def authorize(self, code: str, requested_participant_id: str) -> AccessGrant:
        code = (code or "").strip()
        requested_participant_id = (requested_participant_id or "").strip().upper()
        if not requested_participant_id:
            raise AccessDenied("참가자 번호가 필요합니다.")

        if self.client is not None:
            return self._authorize_supabase(code, requested_participant_id)

        if self.local_codes:
            expected = self.local_codes.get(requested_participant_id)
            if expected and hmac.compare_digest(expected, code):
                return AccessGrant(participant_id=requested_participant_id)
            raise AccessDenied("접근 코드 또는 참가자 번호가 올바르지 않습니다.")

        if self.require_access:
            raise AccessDenied("접근 코드가 아직 설정되지 않았습니다. 연구자에게 문의해 주세요.")

        return AccessGrant(participant_id=requested_participant_id)

    def _authorize_supabase(self, code: str, requested_participant_id: str) -> AccessGrant:
        if not code:
            raise AccessDenied("접근 코드를 입력해 주세요.")
        try:
            rows = self.client.request(
                "GET",
                "access_codes",
                query={
                    "select": "id,participant_id,role,max_sessions,sessions_started,expires_at",
                    "code_hash": f"eq.{self.hash_code(code)}",
                    "active": "eq.true",
                    "limit": "1",
                },
            )
        except StoreError as error:
            raise AccessDenied("접근 코드를 확인할 수 없습니다. 잠시 후 다시 시도해 주세요.") from error

        if not rows:
            raise AccessDenied("접근 코드 또는 참가자 번호가 올바르지 않습니다.")
        row = rows[0]
        participant_id = str(
            row.get("participant_id") or requested_participant_id
        ).strip().upper()
        if participant_id != requested_participant_id:
            raise AccessDenied("접근 코드 또는 참가자 번호가 올바르지 않습니다.")
        if row.get("expires_at"):
            try:
                expires_at = dt.datetime.fromisoformat(
                    str(row["expires_at"]).replace("Z", "+00:00")
                )
                if expires_at <= dt.datetime.now(dt.timezone.utc):
                    raise AccessDenied("만료된 접근 코드입니다.")
            except ValueError as error:
                raise AccessDenied("접근 코드 설정이 올바르지 않습니다.") from error
        if row.get("max_sessions") is not None and (
            row.get("sessions_started", 0) >= row["max_sessions"]
        ):
            raise AccessDenied("이 접근 코드의 사용 가능 횟수를 모두 사용했습니다.")

        return AccessGrant(
            participant_id=participant_id,
            access_code_id=row.get("id"),
            role=row.get("role") or "participant",
        )

    def _enqueue(
        self,
        method: str,
        table: str,
        body: dict[str, Any],
        query: dict[str, str] | None = None,
    ) -> None:
        if self.client is not None:
            self._writes.put((method, table, body, query))

    def _writer_loop(self) -> None:
        while True:
            item = self._writes.get()
            if item is None:
                return
            method, table, body, query = item
            try:
                self.client.request(
                    method,
                    table,
                    query=query,
                    body=body,
                    prefer="return=minimal",
                )
            except StoreError as error:
                print(f"[store_error] {table}: {error}", flush=True)
            finally:
                self._writes.task_done()

    def record_session_start(
        self,
        session: Any,
        grant: AccessGrant | None = None,
    ) -> None:
        access_code_id = grant.access_code_id if grant is not None else None
        researcher_email = (
            session.researcher_email
            or (grant.researcher_email if grant is not None else None)
        )
        body = {
            "id": session.id,
            "participant_id": session.participant_id,
            "researcher_email": researcher_email,
            "access_code_id": access_code_id,
            "condition": session.condition,
            "task": session.task,
            "status": "running",
            "started_at": session.created_at,
            "model": os.getenv("EXPERIMENT_MODEL", "google/gemini-3.1-flash-lite"),
            "app_version": os.getenv("K_REVISION") or os.getenv("APP_VERSION"),
            "counts": {
                "_session_token_hash": self.hash_session_token(session.secret),
            },
        }
        if self.client is not None:
            self.client.request(
                "POST",
                "experiment_sessions",
                body=body,
                prefer="return=minimal",
            )
        if access_code_id:
            self._enqueue(
                "POST",
                "rpc/increment_access_code_usage",
                {"code_id": access_code_id},
            )

    def authorize_experiment_session(
        self,
        session_id: str,
        token: str,
        researcher_email: str,
    ) -> PersistedExperimentSession | None:
        if self.client is None or not session_id or not token or not researcher_email:
            return None
        rows = self.client.request(
            "GET",
            "experiment_sessions",
            query={
                "select": (
                    "id,participant_id,researcher_email,condition,task,status,"
                    "started_at,phase,counts"
                ),
                "id": f"eq.{session_id}",
                "researcher_email": f"eq.{researcher_email}",
                "limit": "1",
            },
        )
        if not isinstance(rows, list) or not rows:
            return None
        row = rows[0]
        counts = row.get("counts")
        expected_hash = (
            counts.get("_session_token_hash")
            if isinstance(counts, dict)
            else None
        )
        if not isinstance(expected_hash, str) or not hmac.compare_digest(
            expected_hash,
            self.hash_session_token(token),
        ):
            return None
        stored_email = str(row.get("researcher_email") or "").strip().lower()
        if not hmac.compare_digest(stored_email, researcher_email.strip().lower()):
            return None
        return PersistedExperimentSession(
            id=str(row.get("id") or session_id),
            participant_id=str(row.get("participant_id") or ""),
            researcher_email=stored_email,
            condition=str(row.get("condition") or ""),
            task=str(row.get("task") or ""),
            status=str(row.get("status") or "running"),
            started_at=row.get("started_at"),
            phase=row.get("phase"),
        )

    def record_event(self, session: Any, event_type: str, data: Any) -> None:
        self._enqueue(
            "POST",
            "experiment_events",
            {
                "session_id": session.id,
                "event_type": event_type,
                "phase": session.phase,
                "data": data,
            },
        )

    def record_message(self, session: Any, speaker: str, content: str) -> None:
        self._enqueue(
            "POST",
            "experiment_messages",
            {
                "session_id": session.id,
                "participant_id": session.participant_id,
                "phase": session.phase,
                "speaker": speaker,
                "content": content,
            },
        )

    def record_idea(self, session: Any, form: dict[str, Any]) -> None:
        features = (form.get("features") or [])[:3]
        self._enqueue(
            "POST",
            "experiment_ideas",
            {
                "session_id": session.id,
                "participant_id": session.participant_id,
                "condition": session.condition,
                "task": session.task,
                "concept": form.get("concept"),
                "features": features,
                "differentiator": form.get("differentiator"),
                "filled_at": form.get("filled_at"),
            },
        )

    def record_recovered_idea(
        self,
        session: PersistedExperimentSession,
        form: dict[str, Any],
    ) -> None:
        if self.client is None:
            raise StoreError("Supabase is not configured")
        existing = self.client.request(
            "GET",
            "experiment_ideas",
            query={
                "select": "session_id",
                "session_id": f"eq.{session.id}",
                "limit": "1",
            },
        )
        if not existing:
            features = (form.get("features") or [])[:3]
            self.client.request(
                "POST",
                "experiment_ideas",
                body={
                    "session_id": session.id,
                    "participant_id": session.participant_id,
                    "condition": session.condition,
                    "task": session.task,
                    "concept": form.get("concept"),
                    "features": features,
                    "differentiator": form.get("differentiator"),
                    "filled_at": form.get("filled_at"),
                },
                prefer="return=minimal",
            )
        self.client.request(
            "PATCH",
            "experiment_sessions",
            query={"id": f"eq.{session.id}"},
            body={
                "status": "completed",
                "ended_at": dt.datetime.now(dt.timezone.utc).isoformat(),
                "error": "Runtime instance ended before final idea submission; idea recovered.",
            },
            prefer="return=minimal",
        )

    def record_session_end(self, session: Any) -> None:
        summary = session.summary()
        self._enqueue(
            "PATCH",
            "experiment_sessions",
            {
                "status": session.status,
                "ended_at": session.ended_at,
                "phase": session.phase,
                "counts": summary["counts"],
                "token_usage": session.token_usage,
                "error": session.error,
            },
            query={"id": f"eq.{session.id}"},
        )

    def record_llm_call(self, session: Any, call: dict[str, Any]) -> None:
        self._enqueue(
            "POST",
            "llm_calls",
            {
                "session_id": session.id,
                **call,
            },
        )

    def submit_survey(self, payload: dict[str, Any]) -> None:
        if self.client is None:
            raise StoreError("Supabase is not configured")
        self.client.request(
            "POST",
            "submissions",
            query={"on_conflict": "participant_id,round_index"},
            body=payload,
            prefer="resolution=merge-duplicates,return=minimal",
        )

    def list_survey_submissions(self) -> list[dict[str, Any]]:
        if self.client is None:
            raise StoreError("Supabase is not configured")
        rows = self.client.request(
            "GET",
            "submissions",
            query={
                "select": (
                    "id,participant_id,environment,round_index,condition,"
                    "gender,age,design_experience,llm_experience,"
                    "submitted_at,responses"
                ),
                "order": "submitted_at.desc",
                "limit": "2000",
            },
        )
        return rows if isinstance(rows, list) else []

    def list_session_summaries(self) -> list[dict[str, Any]]:
        if self.client is None:
            raise StoreError("Supabase is not configured")
        rows = self.client.request(
            "GET",
            "analysis_session_summary",
            query={
                "select": (
                    "session_id,participant_id,condition,task,status,phase,"
                    "started_at,ended_at,duration_seconds,message_count,"
                    "participant_message_count,agent_message_count,"
                    "submitted_concept,idea_filled_at"
                ),
                "order": "started_at.desc",
                "limit": "100",
            },
        )
        return rows if isinstance(rows, list) else []

    def close(self) -> None:
        if self._writer is not None:
            self._writes.join()
            self._writes.put(None)
            self._writer.join(timeout=3)

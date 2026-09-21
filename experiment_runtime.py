"""Cloud Run에서도 동시 참가자를 분리해 처리하는 세션 런타임.

AG2의 IOStream은 ContextVar 기반이므로 각 세션을 별도 스레드에서 실행하고,
HTTP 요청은 세션별 입력 큐와 장기 폴링 이벤트 큐에 연결한다.
"""
from __future__ import annotations

import contextvars
import datetime as dt
import hmac
import json
import os
import queue
import re
import secrets
import threading
import time
import uuid
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any, Iterator

from autogen.io import IOStream

import vr_output


ACTIVE_STATUSES = {"created", "running", "waiting_input", "awaiting_form"}
TERMINAL_STATUSES = {"completed", "cancelled", "error"}
_CURRENT_SESSION: contextvars.ContextVar["ExperimentSession | None"] = contextvars.ContextVar(
    "experiment_session",
    default=None,
)


class SessionCancelled(RuntimeError):
    """Raised inside a session worker after that specific session is cancelled."""


def utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat()


def current_session() -> "ExperimentSession | None":
    return _CURRENT_SESSION.get()


@contextmanager
def session_scope(session: "ExperimentSession") -> Iterator["ExperimentSession"]:
    token = _CURRENT_SESSION.set(session)
    try:
        yield session
    finally:
        _CURRENT_SESSION.reset(token)


def _safe_file_part(value: str) -> str:
    cleaned = re.sub(r"[^0-9A-Za-z가-힣_-]+", "_", value.strip())
    return cleaned[:48] or "unknown"


@dataclass
class ExperimentSession:
    participant_id: str
    condition: str
    task: str
    brief: str
    log_dir: str
    researcher_email: str = ""
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    secret: str = field(default_factory=lambda: secrets.token_urlsafe(32))
    created_at: str = field(default_factory=utc_now)
    status: str = "created"
    phase: str | None = None
    ended_at: str | None = None
    error: str | None = None
    token_usage: dict[str, Any] | None = None
    idea: dict[str, Any] | None = None
    utterances_total: int = 0
    interventions: int = 0
    by_speaker: dict[str, int] = field(default_factory=dict)
    agent_chars_total: int = 0
    chars_by_speaker: dict[str, int] = field(default_factory=dict)
    participant_chars_total: int = 0
    intervention_timings: list[dict[str, Any]] = field(default_factory=list)
    first_intervention_wait_s: float | None = None
    discussion_to_submit_s: float | None = None
    _session_start_monotonic: float = field(default_factory=time.monotonic, repr=False)
    _last_response_monotonic: float | None = field(default=None, repr=False)
    _pending_intervention_monotonic: float | None = field(default=None, repr=False)
    _pending_gap_s: float | None = field(default=None, repr=False)
    _discussion_ended_monotonic: float | None = field(default=None, repr=False)
    thread: threading.Thread | None = field(default=None, repr=False)
    _cancelled: threading.Event = field(default_factory=threading.Event, repr=False)
    _messages: queue.Queue[str] = field(default_factory=queue.Queue, repr=False)
    _forms: queue.Queue[dict[str, Any]] = field(default_factory=queue.Queue, repr=False)
    _event_condition: threading.Condition = field(
        default_factory=lambda: threading.Condition(threading.RLock()),
        repr=False,
    )
    _events: list[dict[str, Any]] = field(default_factory=list, repr=False)
    _next_sequence: int = field(default=1, repr=False)
    _state_lock: threading.RLock = field(default_factory=threading.RLock, repr=False)
    _log_file: Any = field(default=None, repr=False)
    _message_file: Any = field(default=None, repr=False)

    def __post_init__(self) -> None:
        stamp = dt.datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        short_id = self.id.split("-")[0]
        self.base = (
            f"{_safe_file_part(self.participant_id)}_{self.condition}_"
            f"task{self.task}_{stamp}_{short_id}"
        )
        os.makedirs(self.log_dir, exist_ok=True)
        self._log_file = open(
            os.path.join(self.log_dir, f"{self.base}_log.jsonl"),
            "w",
            encoding="utf-8",
        )
        self._message_file = open(
            os.path.join(self.log_dir, f"{self.base}_messages.jsonl"),
            "w",
            encoding="utf-8",
        )

    @property
    def is_current(self) -> bool:
        with self._state_lock:
            return self.status in ACTIVE_STATUSES and not self._cancelled.is_set()

    @property
    def cancelled(self) -> bool:
        return self._cancelled.is_set()

    def set_status(self, status: str) -> None:
        with self._state_lock:
            if self.status in TERMINAL_STATUSES:
                return
            self.status = status
        with self._event_condition:
            self._event_condition.notify_all()

    def emit(self, payload: dict[str, Any]) -> int:
        """Append one browser event and wake any long-polling request."""
        with self._event_condition:
            sequence = self._next_sequence
            self._next_sequence += 1
            self._events.append(
                {
                    "seq": sequence,
                    "time": utc_now(),
                    "payload": payload,
                }
            )
            self._event_condition.notify_all()
        # 메타휴먼에게 넘기는 건 락을 놓은 다음에 — 여기서 막히면 poll()까지 같이 멈춘다.
        vr_output.dispatch(payload)
        return sequence

    def poll(self, after: int, wait_seconds: float = 20) -> list[dict[str, Any]]:
        deadline = time.monotonic() + max(0, min(wait_seconds, 25))
        with self._event_condition:
            while not any(event["seq"] > after for event in self._events):
                if self.status in TERMINAL_STATUSES:
                    break
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    break
                self._event_condition.wait(remaining)
            return [event for event in self._events if event["seq"] > after]

    def submit_message(self, message: str) -> None:
        if not self.is_current:
            raise SessionCancelled("세션이 이미 종료되었습니다.")
        self._messages.put(message)

    def next_message(self) -> str:
        while True:
            if self.cancelled:
                raise SessionCancelled("세션이 취소되었습니다.")
            try:
                return self._messages.get(timeout=0.5)
            except queue.Empty:
                continue

    def submit_form(self, form: dict[str, Any]) -> None:
        if not self.is_current:
            raise SessionCancelled("세션이 이미 종료되었습니다.")
        self._forms.put(form)

    def next_form(self) -> dict[str, Any]:
        while True:
            if self.cancelled:
                raise SessionCancelled("세션이 취소되었습니다.")
            try:
                return self._forms.get(timeout=0.5)
            except queue.Empty:
                continue

    def record_event(self, event_type: str, data: Any) -> None:
        entry = {"time": utc_now(), "event": event_type, "data": data}
        with self._state_lock:
            if event_type == "phase_start" and isinstance(data, dict):
                self.phase = data.get("phase")
            if self._log_file:
                self._log_file.write(json.dumps(entry, ensure_ascii=False) + "\n")
                self._log_file.flush()

    def record_message(self, speaker: str, content: str) -> dict[str, Any]:
        entry = {
            "time": utc_now(),
            "speaker": speaker,
            "content": content,
            "phase": self.phase,
        }
        with self._state_lock:
            if self._message_file:
                self._message_file.write(json.dumps(entry, ensure_ascii=False) + "\n")
                self._message_file.flush()
            if speaker == "Participant":
                self.interventions += 1
                self.participant_chars_total += len(content)
            else:
                self.utterances_total += 1
                self.by_speaker[speaker] = self.by_speaker.get(speaker, 0) + 1
                self.agent_chars_total += len(content)
                self.chars_by_speaker[speaker] = self.chars_by_speaker.get(speaker, 0) + len(content)
        return entry

    def record_intervention_wait_start(self) -> None:
        """참가자 입력 대기 시작 — 프론트에서 렌더 큐가 밀린 발화를 다 타이핑해서
        입력창을 실제로 여는 순간(input-ready 신호) 호출된다. 같은 개입 기회에
        신호가 중복으로 와도(새로고침 등) 이미 대기 중이면 시작 시각을 안 덮어쓴다."""
        with self._state_lock:
            if self._pending_intervention_monotonic is not None:
                return
            now = time.monotonic()
            if self._last_response_monotonic is None:
                self.first_intervention_wait_s = round(now - self._session_start_monotonic, 3)
            else:
                self._pending_gap_s = round(now - self._last_response_monotonic, 3)
            self._pending_intervention_monotonic = now

    def record_intervention_response(self, content: str) -> None:
        """참가자 응답 수신 — SessionIOStream.input()이 next_message()를 받은 직후 호출.
        스킵(빈 입력)이면 대기 시간은 0으로 기록한다."""
        with self._state_lock:
            now = time.monotonic()
            skipped = not bool((content or "").strip())
            started = self._pending_intervention_monotonic
            wait_s = 0.0 if skipped or started is None else round(now - started, 3)
            self.intervention_timings.append({
                "index": len(self.intervention_timings) + 1,
                "wait_s": wait_s,
                "skipped": skipped,
                "gap_from_prev_response_s": self._pending_gap_s,
            })
            self._last_response_monotonic = now
            self._pending_intervention_monotonic = None
            self._pending_gap_s = None

    def record_discussion_end(self) -> None:
        """토론(에이전트 회의) 종료 — 아이디어 제출 폼이 뜨기 직전에 호출."""
        with self._state_lock:
            self._discussion_ended_monotonic = time.monotonic()

    def record_form_submitted(self) -> None:
        """아이디어 제출 폼 제출 완료 시 호출."""
        with self._state_lock:
            if self._discussion_ended_monotonic is not None:
                self.discussion_to_submit_s = round(time.monotonic() - self._discussion_ended_monotonic, 3)

    def summary(self) -> dict[str, Any]:
        return {
            "meta": {
                "session_id": self.id,
                "participant_id": self.participant_id,
                "researcher_email": self.researcher_email,
                "condition": self.condition,
                "task": self.task,
                "started_at": self.created_at,
                "ended_at": self.ended_at,
                "base": self.base,
                "status": self.status,
                "error": self.error,
            },
            "counts": {
                "utterances_total": self.utterances_total,
                "by_speaker": dict(self.by_speaker),
                "interventions": self.interventions,
                "chars_by_speaker": dict(self.chars_by_speaker),
                "agent_chars_total": self.agent_chars_total,
                "participant_chars_total": self.participant_chars_total,
                "chars_total": self.agent_chars_total + self.participant_chars_total,
                "intervention_timings": list(self.intervention_timings),
                "first_intervention_wait_s": self.first_intervention_wait_s,
                "discussion_to_submit_s": self.discussion_to_submit_s,
            },
            "token_usage": self.token_usage,
            "form": self.idea,
        }

    def _write_summary(self) -> None:
        path = os.path.join(self.log_dir, f"{self.base}_summary.json")
        with open(path, "w", encoding="utf-8") as output:
            json.dump(self.summary(), output, ensure_ascii=False, indent=2)
        if self.idea:
            idea_path = os.path.join(self.log_dir, f"{self.base}_idea.json")
            with open(idea_path, "w", encoding="utf-8") as output:
                json.dump(self.idea, output, ensure_ascii=False, indent=2)

    def _close_files(self) -> None:
        with self._state_lock:
            for handle_name in ("_log_file", "_message_file"):
                handle = getattr(self, handle_name)
                if handle:
                    handle.close()
                    setattr(self, handle_name, None)

    def finish(
        self,
        status: str,
        *,
        idea: dict[str, Any] | None = None,
        token_usage: dict[str, Any] | None = None,
        error: str | None = None,
    ) -> None:
        with self._state_lock:
            if self.status in TERMINAL_STATUSES and self.ended_at:
                return
            self.idea = idea
            self.token_usage = token_usage
            self.error = error
            self.status = status
            self.ended_at = utc_now()
        self._write_summary()
        self._close_files()
        with self._event_condition:
            self._event_condition.notify_all()

    def mark_completed(self) -> None:
        self.finish("completed", idea=self.idea, token_usage=self.token_usage)

    def cancel(self) -> None:
        self._cancelled.set()
        self.finish("cancelled", idea=self.idea, token_usage=self.token_usage)


class SessionIOStream(IOStream):
    """AG2 IOStream implementation backed by HTTP-friendly queues."""

    def __init__(self, session: ExperimentSession) -> None:
        self.session = session

    def send(self, message: Any) -> None:
        if hasattr(message, "model_dump"):
            payload = message.model_dump(mode="json")
        elif isinstance(message, dict):
            payload = message
        else:
            payload = {"type": "print", "content": {"objects": [str(message)], "sep": " ", "end": "\n"}}
        self.session.emit(payload)

    def print(
        self,
        *objects: Any,
        sep: str = " ",
        end: str = "\n",
        flush: bool = False,
    ) -> None:
        del flush
        self.session.emit(
            {
                "type": "print",
                "content": {
                    "objects": [str(item) for item in objects],
                    "sep": sep,
                    "end": end,
                },
            }
        )

    def send_text(
        self,
        sender: str,
        content: str,
        *,
        recipient: str = "PM",
        summary: str = "",
    ) -> None:
        body = {"sender": sender, "recipient": recipient, "content": content}
        if summary:
            body["summary"] = summary
        self.session.emit({"type": "text", "content": body})

    def input(self, prompt: str = "", *, password: bool = False) -> str:
        del password
        # 메타휴먼이 아직 말하는 중이면 참가자 차례를 열지 않는다 — 웹에서 렌더 큐가
        # 밀린 발화를 다 타이핑한 뒤에야 입력창이 열리는 것과 같은 규칙이다.
        vr_output.wait_until_idle()
        # 대기 시작 시각은 여기서 안 재고 프론트의 input-ready 신호(POST
        # /api/sessions/{id}/input-ready)로 기록한다 — 렌더 큐가 밀린 발화를
        # 다 타이핑하기 전까지는 입력창이 실제로 안 열리므로, 여기서 재면
        # "화면에 다 뜨길 기다린 시간"까지 참가자 응답 시간에 섞여 들어간다.
        self.session.set_status("waiting_input")
        self.session.emit(
            {
                "type": "input_request",
                "content": {"prompt": prompt},
            }
        )
        message = self.session.next_message()
        self.session.record_intervention_response(message)
        self.session.set_status("running")
        return message


class ExperimentSessionRegistry:
    def __init__(self, log_dir: str, max_active_sessions: int = 3) -> None:
        self.log_dir = log_dir
        self.max_active_sessions = max(1, max_active_sessions)
        self._sessions: dict[str, ExperimentSession] = {}
        self._lock = threading.RLock()

    def create(
        self,
        *,
        participant_id: str,
        condition: str,
        task: str,
        brief: str,
        researcher_email: str = "",
    ) -> ExperimentSession:
        with self._lock:
            active_count = sum(session.is_current for session in self._sessions.values())
            if active_count >= self.max_active_sessions:
                raise RuntimeError(
                    f"동시 세션 상한({self.max_active_sessions}개)에 도달했습니다."
                )
            session = ExperimentSession(
                participant_id=participant_id,
                condition=condition,
                task=task,
                brief=brief,
                log_dir=self.log_dir,
                researcher_email=researcher_email,
            )
            self._sessions[session.id] = session
            return session

    def get(self, session_id: str) -> ExperimentSession | None:
        with self._lock:
            return self._sessions.get(session_id)

    def authorize(self, session_id: str, secret: str) -> ExperimentSession | None:
        session = self.get(session_id)
        if not session or not secret:
            return None
        if not hmac.compare_digest(session.secret, secret):
            return None
        return session

    def close_all(self) -> None:
        with self._lock:
            sessions = list(self._sessions.values())
        for session in sessions:
            if session.is_current:
                session.cancel()

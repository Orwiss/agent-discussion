"""Process-wide AG2 logger that routes each LLM call to its ContextVar session."""
from __future__ import annotations

import datetime as dt
import uuid
from typing import Any

from autogen.logger.base_logger import BaseLogger

from experiment_runtime import current_session


def _value(source: Any, name: str, default: Any = None) -> Any:
    if isinstance(source, dict):
        return source.get(name, default)
    return getattr(source, name, default)


def _as_dict(value: Any) -> dict[str, Any]:
    if value is None:
        return {}
    if isinstance(value, dict):
        return value
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    return {}


class ContextRuntimeLogger(BaseLogger):
    def __init__(self, store: Any) -> None:
        self.store = store
        self.session_id = str(uuid.uuid4())

    def start(self) -> str:
        return self.session_id

    def log_chat_completion(
        self,
        invocation_id,
        client_id,
        wrapper_id,
        source,
        request,
        response,
        is_cached,
        cost,
        start_time,
    ) -> None:
        # source(에이전트 객체)에 직접 붙은 세션 태그를 우선 쓴다 — centralized는
        # LLM 호출이 run_in_executor로 별도 스레드에서 실행되는데, contextvars는
        # 스레드 경계를 못 넘어가서 current_session()이 거기선 None만 반환한다.
        # 객체 속성은 스레드가 바뀌어도 그대로라 이 경로로 유실을 우회한다.
        session = getattr(source, "session", None) or current_session()
        if session is None:
            return
        response_dict = _as_dict(response)
        usage = _as_dict(_value(response, "usage")) or _as_dict(response_dict.get("usage"))
        prompt_details = _as_dict(usage.get("prompt_tokens_details"))
        completion_details = _as_dict(usage.get("completion_tokens_details"))
        source_name = getattr(source, "name", None) or str(source)
        model = _value(response, "model") or response_dict.get("model") or request.get("model")
        response_id = _value(response, "id") or response_dict.get("id")
        completed_at = dt.datetime.now(dt.timezone.utc)
        latency_ms = None
        try:
            started = dt.datetime.fromisoformat(str(start_time).replace("Z", "+00:00"))
            if started.tzinfo is None:
                started = started.replace(tzinfo=dt.timezone.utc)
            latency_ms = round((completed_at - started).total_seconds() * 1000)
        except (TypeError, ValueError):
            pass

        self.store.record_llm_call(
            session,
            {
                "invocation_id": str(invocation_id),
                "agent": source_name,
                "model": model,
                "prompt_tokens": usage.get("prompt_tokens", 0),
                "completion_tokens": usage.get("completion_tokens", 0),
                "total_tokens": usage.get("total_tokens", 0),
                "cached_tokens": prompt_details.get("cached_tokens", 0),
                "reasoning_tokens": completion_details.get("reasoning_tokens", 0),
                "is_cached": bool(is_cached),
                "reported_cost": cost,
                "provider_request_id": response_id,
                "latency_ms": latency_ms,
                "started_at": start_time,
                "completed_at": completed_at.isoformat(),
                "raw_usage": usage,
            },
        )

    def log_new_agent(self, agent, init_args) -> None:
        pass

    def log_event(self, source, name, **kwargs) -> None:
        pass

    def log_new_wrapper(self, wrapper, init_args) -> None:
        pass

    def log_new_client(self, client, wrapper, init_args) -> None:
        pass

    def log_function_use(self, source, function, args, returns) -> None:
        pass

    def stop(self) -> None:
        pass

    def get_connection(self):
        return None

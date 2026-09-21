"""VR 출력 레이어 연결부 — 화면에 뜨는 발화를 그대로 메타휴먼에게 넘긴다.

환경변수 VR_OUTPUT이 켜져 있을 때만 동작한다. 꺼져 있으면 tts_pipeline을
import조차 하지 않는다 — Cloud Run 컨테이너에는 elevenlabs·grpc·numpy가
없으므로 모듈 로드 시점에 import하면 서비스가 시작부터 죽는다.

발화를 고르는 기준은 프론트엔드와 똑같이 맞췄다(web.py의 handleServerMessage).
화면에 말풍선으로 뜨는 것만 메타휴먼이 말한다.

tts_pipeline.trigger()는 직전 발화 재생이 끝날 때까지 호출한 쪽을 붙잡는다.
그 호출을 ExperimentSession.emit() 안에서 직접 하면 이벤트 큐 락을 쥔 채로
멈춰서 브라우저 폴링까지 같이 멈춘다. 그래서 emit()은 큐에 넣기만 하고,
전용 워커 스레드가 꺼내서 trigger()를 호출한다.
"""
from __future__ import annotations

import logging
import os
import queue
import re
import threading

logger = logging.getLogger(__name__)

# 프론트엔드가 말풍선으로 안 그리는 발신자 (web.py의 handleServerMessage와 동일)
SKIP_SENDERS = {"chat_manager", "Participant"}

_THINK_RE = re.compile(r"<think>[\s\S]*?</think>")
_TERMINATE_RE = re.compile(r"\s*TERMINATE\s*")

# 참가자 차례를 열기 전에 발화가 끝나길 기다리는 최대 시간(초).
# TTS나 A2F가 멈춰도 참가자가 영영 못 기다리게 되지는 않도록 상한을 둔다.
DRAIN_TIMEOUT = float(os.getenv("VR_DRAIN_TIMEOUT", "180"))

_queue: "queue.Queue[tuple[str, str]]" = queue.Queue()
_worker: threading.Thread | None = None
_worker_lock = threading.Lock()
_enabled_cache: bool | None = None


def enabled() -> bool:
    """VR_OUTPUT이 켜져 있는지. 프로세스 수명 동안 한 번만 읽는다."""
    global _enabled_cache
    if _enabled_cache is None:
        _enabled_cache = os.getenv("VR_OUTPUT", "").strip().lower() in {"1", "true", "yes", "on"}
    return _enabled_cache


def extract(payload: dict) -> tuple[str, str] | None:
    """브라우저 이벤트 하나에서 (발신자, 말할 텍스트)를 뽑는다.
    화면에 안 뜨는 이벤트면 None."""
    if payload.get("type") not in ("text", "tool_call"):
        return None
    content = payload.get("content")
    if not isinstance(content, dict):
        return None
    sender = content.get("sender")
    raw = content.get("content") or ""
    if not sender or sender in SKIP_SENDERS:
        return None
    text = _THINK_RE.sub("", _TERMINATE_RE.sub(" ", raw)).strip()
    if not text:
        return None
    return sender, text


def _run_worker() -> None:
    from tts_pipeline import trigger  # 플래그가 켜진 뒤에만 import

    while True:
        sender, text = _queue.get()
        try:
            trigger(sender, text)
        except Exception:
            logger.exception("[VR] %s 발화 전달 실패", sender)
        finally:
            _queue.task_done()


def _ensure_worker() -> None:
    global _worker
    if _worker is not None:
        return
    with _worker_lock:
        if _worker is not None:
            return
        _worker = threading.Thread(target=_run_worker, daemon=True, name="vr-output")
        _worker.start()


def dispatch(payload: dict) -> None:
    """emit()에서 호출. 절대 막히지 않고 절대 예외를 올리지 않는다."""
    if not enabled():
        return
    try:
        item = extract(payload)
        if item is None:
            return
        _ensure_worker()
        _queue.put(item)
    except Exception:
        logger.exception("[VR] 발화 전달 준비 실패")


def wait_until_idle(timeout: float | None = None) -> bool:
    """대기 중인 발화가 전부 재생될 때까지 기다린다.
    참가자 차례를 열기 직전에 호출한다 — 메타휴먼이 아직 말하는 중인데
    입력창이 열리면 참가자가 말을 끊고 들어가게 된다.
    시간 안에 안 끝나면 False를 돌려주고 그냥 진행한다."""
    if not enabled() or _worker is None:
        return True
    limit = DRAIN_TIMEOUT if timeout is None else timeout
    drained = threading.Event()

    def _join() -> None:
        _queue.join()
        drained.set()

    threading.Thread(target=_join, daemon=True, name="vr-drain").start()
    if not drained.wait(limit):
        logger.warning("[VR] 대기 중인 발화가 %.1f초 안에 안 끝나 그냥 진행", limit)
        return False

    try:
        from tts_pipeline import wait_until_idle as _tts_idle
    except Exception:
        logger.exception("[VR] tts_pipeline 상태 확인 실패")
        return False
    if not _tts_idle(limit):
        logger.warning("[VR] 마지막 발화 재생이 %.1f초 안에 안 끝나 그냥 진행", limit)
        return False
    return True

"""VR 출력 레이어 연결부 — 화면에 뜨는 발화를 그대로 메타휴먼에게 넘긴다.

환경변수 VR_OUTPUT이 켜져 있을 때만 동작한다. 꺼져 있으면 tts_pipeline을
import조차 하지 않는다 — Cloud Run 컨테이너에는 elevenlabs·grpc·numpy가
없으므로 모듈 로드 시점에 import하면 서비스가 시작부터 죽는다.

발화를 고르는 기준은 프론트엔드와 똑같이 맞췄다(web.py의 handleServerMessage).
화면에 말풍선으로 뜨는 것만 메타휴먼이 말한다.

tts_pipeline.trigger()는 문장마다 TTS+A2F를 만드느라 몇 초씩 걸리고, 재생 대기열이
차면 자리가 날 때까지 호출한 쪽을 붙잡는다. 그래서 emit()은 큐에 넣기만 하고,
전용 워커 스레드가 꺼내서 trigger()를 호출한다. emit()이 기다리는 건 대화 생성이
VR_MAX_AHEAD개 넘게 앞서갈 때뿐이고, 이벤트 큐 락을 놓은 뒤라 브라우저 폴링은 안 막힌다.
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

# 대화 생성이 앞서갈 수 있는 발화 수 (TTS를 아직 시작 못 한 것 기준). 0이면 제한 없음.
MAX_AHEAD = int(os.getenv("VR_MAX_AHEAD", "1"))

# (epoch, 발신자, 텍스트, 받는 사람). epoch이 지금과 다르면 reset() 전에 들어온 것이라 버린다.
_queue: "queue.Queue[tuple[int, str, str, str]]" = queue.Queue()
_room = threading.Condition()
_epoch = 0
_owner: str | None = None          # 지금 VR로 말하고 있는 세션 id
_dead_owners: set[str] = set()     # 취소된 세션 id
_worker: threading.Thread | None = None
_worker_lock = threading.Lock()
_enabled_cache: bool | None = None


def enabled() -> bool:
    """VR_OUTPUT이 켜져 있는지. 프로세스 수명 동안 한 번만 읽는다."""
    global _enabled_cache
    if _enabled_cache is None:
        _enabled_cache = os.getenv("VR_OUTPUT", "").strip().lower() in {"1", "true", "yes", "on"}
    return _enabled_cache


def extract(payload: dict) -> tuple[str, str, str] | None:
    """브라우저 이벤트 하나에서 (발신자, 말할 텍스트, 받는 사람)을 뽑는다.
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
    return sender, text, str(content.get("recipient") or "")


def _run_worker() -> None:
    from tts_pipeline import trigger  # 플래그가 켜진 뒤에만 import

    q = _queue  # get과 task_done이 같은 큐를 보게 붙잡아 둔다 (테스트가 모듈을 다시 불러와도)
    while True:
        epoch, sender, text, recipient = q.get()
        with _room:
            _room.notify_all()  # 자리가 났다 — dispatch()에서 기다리던 대화 생성이 이어간다
        try:
            if epoch == _epoch:  # reset() 전에 들어온 발화는 버린다
                trigger(sender, text, recipient)
        except Exception:
            logger.exception("[VR] %s 발화 전달 실패", sender)
        finally:
            q.task_done()


def _ensure_worker() -> None:
    global _worker
    if _worker is not None:
        return
    with _worker_lock:
        if _worker is not None:
            return
        _worker = threading.Thread(target=_run_worker, daemon=True, name="vr-output")
        _worker.start()


def dispatch(payload: dict, owner: str | None = None) -> None:
    """emit()에서 호출 (이벤트 큐 락을 놓은 뒤라 브라우저 폴링은 안 막힌다). 예외를 올리지 않는다.
    owner는 세션 id — 취소된 세션의 발화는 더 받지 않는다.

    대화 생성이 음성보다 너무 앞서가지 않게, TTS를 아직 시작 못 한 발화가 VR_MAX_AHEAD개
    쌓여 있으면 자리가 날 때까지 여기서 기다린다 (= 대화 생성 스레드가 잠깐 멈춘다).
    tts_pipeline이 다음 발화를 미리 만들어 두므로 기다려도 재생 공백은 안 생긴다."""
    if not enabled():
        return
    try:
        item = extract(payload)
        if item is None:
            return
        _ensure_worker()
        global _owner
        with _room:
            if owner is not None:
                if owner in _dead_owners:
                    return
                _owner = owner
            epoch = _epoch
            if MAX_AHEAD > 0:
                ok = _room.wait_for(
                    lambda: _queue.qsize() < MAX_AHEAD or _epoch != epoch, timeout=DRAIN_TIMEOUT
                )
                if not ok:
                    logger.warning("[VR] 발화가 %.0f초 동안 안 빠져서 그냥 넣는다", DRAIN_TIMEOUT)
            if _epoch != epoch:  # 기다리는 동안 세션이 취소됐다
                return
            _queue.put((epoch,) + item)
    except Exception:
        logger.exception("[VR] 발화 전달 준비 실패")


def reset(owner: str | None = None) -> None:
    """세션 취소 시 호출. 아직 말하지 않은 발화를 전부 버리고, 그 세션의 발화를 더 받지 않고,
    참가자 차례를 닫는다. 지금 재생 중인 문장 하나는 끝까지 간다 (UE에 멈춤 신호가 없다).
    다른 세션이 VR을 쓰고 있으면 건드리지 않는다. 예외를 올리지 않는다."""
    if not enabled():
        return
    global _epoch
    try:
        with _room:
            if owner is not None:
                _dead_owners.add(owner)
                if _owner not in (None, owner):
                    return
            _epoch += 1
            while True:
                try:
                    _queue.get_nowait()
                except queue.Empty:
                    break
                _queue.task_done()
            _room.notify_all()
        if _worker is not None:
            from tts_pipeline import reset as _tts_reset
            _tts_reset()
    except Exception:
        logger.exception("[VR] 취소 정리 실패")
    set_turn("agents")


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


def set_turn(state: str, owner: str | None = None) -> None:
    """참가자 차례가 열리고 닫힐 때 UE에 알린다 ("participant" / "agents").
    메타휴먼 시선용 — 참가자 차례면 세 명 모두 참가자를 본다. owner는 차례를 연 세션 id.
    STT 프로그램이 읽는 차례 상태(turn_state)도 여기서 바뀐다. 절대 예외를 올리지 않는다."""
    _mark_turn(state, owner)
    if not enabled():
        return
    try:
        from tts_pipeline import send_turn
        send_turn(state)
    except Exception:
        logger.exception("[VR] 차례 전달 실패")


# -- 참가자 차례 상태: PC의 STT 프로그램(stt_client.py)이 web.py의 /api/stt/*로 읽고 쓴다 --
# 차례가 열릴 때마다 turn_id가 1씩 오른다. STT 문장은 자기가 받아 적은 turn_id를 달고 오고,
# 그 차례가 아직 열려 있을 때만 받아들인다 — 지난 차례 문장이 늦게 와서 다음 차례에 섞이지 않게.
_turn_cv = threading.Condition()
_turn_id = 0
_turn_open = False
_turn_owner: str | None = None


def _mark_turn(state: str, owner: str | None) -> None:
    global _turn_id, _turn_open, _turn_owner
    with _turn_cv:
        if state == "participant":
            _turn_id += 1
            _turn_open = True
            _turn_owner = owner
        else:
            _turn_open = False
        _turn_cv.notify_all()


def turn_state() -> dict:
    with _turn_cv:
        return {"turn_id": _turn_id, "open": _turn_open, "owner": _turn_owner}


def wait_turn_change(turn_id: int, is_open: bool, timeout: float) -> dict:
    """차례 상태가 (turn_id, is_open)과 달라질 때까지 최대 timeout초 기다렸다가 지금 상태를 돌려준다."""
    with _turn_cv:
        _turn_cv.wait_for(lambda: (_turn_id, _turn_open) != (turn_id, is_open), timeout)
        return {"turn_id": _turn_id, "open": _turn_open, "owner": _turn_owner}


def claim_turn(turn_id: int | None = None, owner: str | None = None) -> str | None:
    """열린 참가자 차례를 하나의 답이 차지한다 (STT 문장이든 실험자가 친 글이든 먼저 온 쪽).
    turn_id나 owner가 지금 열린 차례와 맞으면 차례를 닫고 owner(세션 id)를 돌려준다.
    이미 닫혔거나 다른 차례면 None — 같은 차례에 답이 두 번 들어가지 않게 한다."""
    global _turn_open
    with _turn_cv:
        if not _turn_open:
            return None
        if turn_id is not None and turn_id != _turn_id:
            return None
        if owner is not None and owner != _turn_owner:
            return None
        _turn_open = False
        _turn_cv.notify_all()
        return _turn_owner

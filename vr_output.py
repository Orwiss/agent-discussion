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

centralized 조건만 예외가 하나 있다. 디자이너·엔지니어가 PM에게만 하는 답(sub-chat, 웹에서는
접힌 말풍선)은 말하지 않고 노트북으로 친다. PM 라우팅 발화가 끝나면 둘이 같이 타이핑을 시작하고,
각자 그 답을 말했다면 걸렸을 시간만큼 친 뒤 끝내면서 답 요약을 자막으로 띄운다. PM 종합 발화는
둘 다 끝난 뒤에 재생된다. centralized.py가 이벤트에 붙이는 step("routing"/"subchat")으로 알아본다.
"""
from __future__ import annotations

import logging
import os
import queue
import re
import threading
import time

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

# centralized에서 PM 라우팅 뒤에 타이핑하는 사람 (PM이 sub-chat으로 부르는 두 사람)
SUBCHAT_AGENTS = ("Designer", "Engineer")

# 타이핑 시간 = 답 글자 수(공백 포함) ÷ 초당 글자 수 — 그 답을 decentralized처럼 말했다면 걸렸을 시간.
# recordings/20261001_155948 녹화(문장 19개 = 사람별 발화 2개씩)에서 쟀다: 같은 사람이 이어 말한
# 문장을 한 발화로 묶고, 발화마다 글자 수 ÷ (오디오 길이 합 + 문장 사이 공백 0.3초 × (문장 수 - 1)).
# 디자이너 목소리가 확연히 느려서 사람별로 둔다. 목록에 없는 이름은 셋을 합친 값("*")을 쓴다.
TTS_CHARS_PER_SEC = {"PM": 7.60, "Designer": 6.29, "Engineer": 8.36, "*": 7.39}


# 실제로 보니 말하는 시간 그대로는 타이핑이 길게 느껴져서 조금 줄인다 (2026-10-10).
TYPING_TIME_SCALE = 0.85

# 요약 자막이 하나씩 보일 틈 (초). 디자이너·엔지니어 타이핑 끝이 이보다 붙으면 먼저 끝나는 쪽을
# 당기고(못 당기면 뒤쪽을 미룬다), 마지막 요약 뒤에도 이만큼 기다렸다가 PM 종합을 재생한다.
SUMMARY_GAP = 3.0
# 당기더라도 타이핑은 최소 이만큼은 한다 (초) — 타이핑 시작·끝 동작만 해도 2~3초다.
MIN_TYPING_SEC = 4.0


def speaking_seconds(agent: str, text: str) -> float:
    rate = TTS_CHARS_PER_SEC.get(agent) or TTS_CHARS_PER_SEC["*"]
    return len(text) / rate * TYPING_TIME_SCALE


# (epoch, 발신자, 텍스트, 받는 사람, step, 요약, 들어온 시각). epoch이 지금과 다르면 reset() 전에 들어온 것이라 버린다.
_queue: "queue.Queue[tuple[int, str, str, str, str, str, float]]" = queue.Queue()
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
    import tts_pipeline as tts  # 플래그가 켜진 뒤에만 import

    q = _queue  # get과 task_done이 같은 큐를 보게 붙잡아 둔다 (테스트가 모듈을 다시 불러와도)
    while True:
        epoch, sender, text, recipient, step, summary, arrived = q.get()
        with _room:
            _room.notify_all()  # 자리가 났다 — dispatch()에서 기다리던 대화 생성이 이어간다
        try:
            if epoch != _epoch:  # reset() 전에 들어온 발화는 버린다
                continue
            if step == "subchat":
                _type_instead_of_speaking(tts, epoch, sender, text, summary, arrived)
                continue
            _end_orphan_typing(tts)
            if step == "routing":  # PM이 문장마다 디자이너→엔지니어를 보며 묻는다 (/mh/speaker 받는 사람)
                tts.trigger(sender, text, recipient, routing=True)
            else:
                tts.trigger(sender, text, recipient)
            if step == "routing":
                # 라우팅 발화가 VR에서 다 끝난 뒤에 타이핑을 시작한다. 여기서 워커가 멈춰도 그 뒤에 올
                # sub-chat 답과 PM 종합은 어차피 이 발화 뒤에 나온다.
                tts.wait_until_idle(DRAIN_TIMEOUT)
                _start_typing(tts, epoch)
        except Exception:
            logger.exception("[VR] %s 발화 전달 실패", sender)
        finally:
            q.task_done()


# -- centralized: 디자이너·엔지니어 타이핑 --
# 타이핑 중인 사람 → {"since": 시작 시각, "timer": 끝낼 타이머 (답이 아직 안 왔으면 None)}
_typing: dict[str, dict] = {}
_typing_cv = threading.Condition()


def _start_typing(tts, epoch: int) -> None:
    """디자이너·엔지니어 타이핑을 같이 시작하고, 끝날 때까지 PM 종합 발화 재생을 막아 둔다."""
    with _typing_cv:
        if epoch != _epoch:
            return
        tts.hold_playback()
        now = time.monotonic()
        started = [agent for agent in SUBCHAT_AGENTS if agent not in _typing]
        for agent in started:
            _typing[agent] = {"since": now, "timer": None}
        for agent in started:  # 보내다 실패해도 위의 기록은 이미 끝나 있다 — 끝내기는 그대로 돈다
            try:
                tts.send_typing(agent, True)
            except Exception:
                logger.exception("[VR] %s 타이핑 시작 전달 실패", agent)


def _type_instead_of_speaking(tts, epoch: int, agent: str, text: str, summary: str, arrived: float) -> None:
    """sub-chat 답을 말하지 않고, 그 답을 말했다면 걸렸을 시간만큼 타이핑한 뒤 요약을 자막으로 띄운다.
    답이 그 시간이 지나서야 왔으면 온 순간에 끝낸다. 다른 사람과 상관없이 자기 시간에 끝난다."""
    with _typing_cv:
        typing = agent in _typing
    if not typing:
        # 라우팅 발화가 비어서 타이핑이 아직 안 시작됐다 — 하던 말이 끝나면 지금부터 시작한다
        tts.wait_until_idle(DRAIN_TIMEOUT)
        _start_typing(tts, epoch)
    with _typing_cv:
        state = _typing.get(agent)
        if state is None or epoch != _epoch or state["timer"] is not None:
            return
        end_at = max(state["since"] + speaking_seconds(agent, text), arrived)
        # 다른 사람의 끝과 SUMMARY_GAP보다 가까우면 벌린다 — 먼저 끝나는 쪽을 당기는 게 우선
        for other, ost in _typing.items():
            if other == agent or ost.get("end_at") is None:
                continue
            o_end = ost["end_at"]
            if abs(end_at - o_end) >= SUMMARY_GAP:
                continue
            if end_at >= o_end:
                pulled = max(end_at - SUMMARY_GAP, ost["since"] + MIN_TYPING_SEC, ost["arrived"])
                if pulled <= end_at - SUMMARY_GAP + 1e-6 and pulled > time.monotonic():
                    _reschedule(tts, epoch, other, ost, pulled)
                else:
                    end_at = o_end + SUMMARY_GAP
            else:
                pulled = max(o_end - SUMMARY_GAP, state["since"] + MIN_TYPING_SEC, arrived)
                end_at = pulled if pulled <= o_end - SUMMARY_GAP + 1e-6 else o_end + SUMMARY_GAP
        state["arrived"] = arrived
        state["subtitle"] = summary or text
        _reschedule(tts, epoch, agent, state, end_at)


def _reschedule(tts, epoch: int, agent: str, state: dict, end_at: float) -> None:
    """(_typing_cv 안에서) 이 사람의 타이핑 끝 타이머를 end_at에 다시 건다."""
    if state.get("timer") is not None:
        state["timer"].cancel()
    timer = threading.Timer(
        max(0.0, end_at - time.monotonic()), _end_typing, args=(tts, epoch, agent, state.get("subtitle", ""))
    )
    timer.daemon = True
    state["timer"] = timer
    state["end_at"] = end_at
    timer.start()


def _end_typing(tts, epoch: int, agent: str, subtitle: str) -> None:
    """타이핑을 끝내고 (subtitle이 있으면) 바로 요약 자막을 띄운다. 마지막 사람이면 PM 재생을 풀어 준다."""
    with _typing_cv:
        if epoch != _epoch or agent not in _typing:
            return
        del _typing[agent]
        try:
            tts.send_typing(agent, False)
            if subtitle:
                tts.send_summary_subtitle(agent, subtitle)
        except Exception:
            logger.exception("[VR] %s 타이핑 끝내기 전달 실패", agent)
        finally:
            # 보내다 실패해도 PM 재생과 참가자 차례는 풀어 준다 — 안 그러면 180초씩 멈춘다.
            # 마지막 요약이 화면에서 보일 틈(SUMMARY_GAP)을 두고 푼다 (자막 없이 끝났으면 바로).
            if not _typing:
                if subtitle and SUMMARY_GAP > 0:
                    t = threading.Timer(SUMMARY_GAP, tts.release_playback)
                    t.daemon = True
                    t.start()
                else:
                    tts.release_playback()
            _typing_cv.notify_all()


def _end_orphan_typing(tts) -> None:
    """답이 비어서 sub-chat 이벤트가 안 온 사람의 타이핑을 끝낸다 (자막 없이).
    그 라운드의 sub-chat 이벤트는 다음 발화보다 먼저 오므로, 다음 발화 때 타이머가 없으면 영영 안 끝난다."""
    with _typing_cv:
        orphans = [agent for agent, state in _typing.items() if state["timer"] is None]
        epoch = _epoch
    for agent in orphans:
        _end_typing(tts, epoch, agent, "")


def _clear_typing() -> list[str]:
    """reset()용 — 타이머를 멈추고 타이핑 상태를 비운다. 타이핑하던 사람을 돌려준다."""
    with _typing_cv:
        agents = list(_typing)
        for state in _typing.values():
            if state["timer"] is not None:
                state["timer"].cancel()
        _typing.clear()
        _typing_cv.notify_all()
    return agents


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
        arrived = time.monotonic()  # sub-chat 답이 준비된 시각 — 아래에서 기다리는 시간은 빼고 잰다
        item = extract(payload)
        if item is None:
            return
        content = payload["content"]
        item = item + (str(content.get("step") or ""), str(content.get("summary") or ""), arrived)
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
        _clear_typing()
        if _worker is not None:
            from tts_pipeline import reset as _tts_reset
            _tts_reset()
    except Exception:
        logger.exception("[VR] 취소 정리 실패")
    set_turn("agents")
    show_hud("")
    _send_typing_off()
    _stop_heartbeat(owner)
    set_condition("decentralized")  # 세션이 없을 때는 UE 기본값(= 조건 신호를 못 받았을 때)으로 돌려 둔다


def _send_typing_off() -> None:
    try:
        from tts_pipeline import send_typing
        for agent in SUBCHAT_AGENTS:
            send_typing(agent, False)
    except Exception:
        logger.exception("[VR] 타이핑 끄기 실패")


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
        import tts_pipeline as tts
        _tts_idle = tts.wait_until_idle
    except Exception:
        logger.exception("[VR] tts_pipeline 상태 확인 실패")
        return False
    # centralized: 디자이너·엔지니어가 아직 타이핑 중이면 그것도 끝나야 참가자 차례다
    with _typing_cv:
        typing = bool(_typing)
    if typing:
        _end_orphan_typing(tts)
        with _typing_cv:
            if not _typing_cv.wait_for(lambda: not _typing, limit):
                logger.warning("[VR] 타이핑이 %.1f초 안에 안 끝나 그냥 진행", limit)
                return False
    if not _tts_idle(limit):
        logger.warning("[VR] 마지막 발화 재생이 %.1f초 안에 안 끝나 그냥 진행", limit)
        return False
    return True


# 회의가 끝나면 VR 참가자 눈앞에 띄우는 안내의 신호. 문구 자체("회의가 종료되었습니다. VR 기기를 벗고,
# 최종 아이디어를 작성해주세요.")는 UE의 WBP_HUDMessage에 들어 있다 — UE OSC가 문자열을 바이트 단위로
# 읽어서 한글을 보내면 깨지므로 영문 신호만 보낸다 (빈 문자열 = 숨김).
END_MESSAGE = "end"


def participant_speaking() -> None:
    """참가자가 말하는 중이라고 UE에 알린다 (STT 글자가 늘어날 때마다). 절대 예외를 올리지 않는다."""
    if not enabled():
        return
    try:
        from tts_pipeline import send_pt_speaking
        send_pt_speaking()
    except Exception:
        logger.exception("[VR] 말하는 중 신호 전달 실패")


def show_hud(text: str) -> None:
    """VR 참가자 눈앞에 안내를 띄운다 (text는 영문 신호, 빈 문자열이면 숨긴다). 절대 예외를 올리지 않는다."""
    if not enabled():
        return
    try:
        from tts_pipeline import send_hud
        send_hud(text)
    except Exception:
        logger.exception("[VR] 안내 문구 전달 실패")


# 세션 중 조건을 다시 보내는 주기(초). UE가 도중에 다시 켜지거나 죽었다 살아나도 이 안에 조건을 안다.
# (타이핑 시작·발화 첫 자막 직전에도 tts_pipeline이 한 번씩 다시 보낸다.)
CONDITION_INTERVAL = float(os.getenv("VR_CONDITION_INTERVAL", "5"))
_heartbeat_lock = threading.Lock()
_heartbeat_stop: threading.Event | None = None
_heartbeat_owner: str | None = None


def set_condition(condition: str, owner: str | None = None) -> None:
    """실험 조건을 UE에 알린다 ("centralized" / "decentralized"). 세션이 시작될 때와 취소될 때 보낸다.
    owner(세션 id)를 주면 그 세션이 끝날 때까지(end_condition / reset) CONDITION_INTERVAL마다 다시 보낸다.
    UE는 이걸 못 받으면 decentralized(지금 동작)로 둔다. 절대 예외를 올리지 않는다."""
    if not enabled():
        return
    try:
        from tts_pipeline import send_condition
        send_condition(condition)
    except Exception:
        logger.exception("[VR] 조건 전달 실패")
    if owner is not None:
        _start_heartbeat(owner, condition)


def end_condition(owner: str) -> None:
    """세션이 끝났다 — 그 세션이 걸어 둔 조건 주기 신호를 멈춘다 (다른 세션 것이면 그대로 둔다)."""
    _stop_heartbeat(owner)


def _start_heartbeat(owner: str, condition: str) -> None:
    global _heartbeat_stop, _heartbeat_owner
    stop = threading.Event()
    with _heartbeat_lock:
        if _heartbeat_stop is not None:
            _heartbeat_stop.set()  # 새 세션이 시작됐다 — 앞 세션의 주기 신호는 멈춘다
        _heartbeat_stop, _heartbeat_owner = stop, owner
    threading.Thread(target=_heartbeat, args=(stop, condition), daemon=True, name="vr-condition").start()


def _stop_heartbeat(owner: str | None = None) -> None:
    global _heartbeat_stop, _heartbeat_owner
    with _heartbeat_lock:
        if _heartbeat_stop is None or owner not in (None, _heartbeat_owner):
            return
        _heartbeat_stop.set()
        _heartbeat_stop, _heartbeat_owner = None, None


def _heartbeat(stop: threading.Event, condition: str) -> None:
    """조건과, 지금 타이핑 중인 사람의 /mh/typing 1을 주기적으로 다시 보낸다."""
    while not stop.wait(CONDITION_INTERVAL):
        try:
            import tts_pipeline as tts
            tts.resend_condition(condition)
            # 락을 쥔 채 보낸다 — 타이핑 끝(0)을 보낸 뒤에 1이 늦게 가서 다시 켜지는 일이 없게
            with _typing_cv:
                for agent in _typing:
                    tts.resend_typing(agent)
        except Exception:
            logger.exception("[VR] 조건 주기 신호 실패")


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

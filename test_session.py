"""
Headless 세션 테스트 환경.

web.py의 실제 프로덕션 경로(SESSION_REGISTRY.create + session_scope + _run_session)를
그대로 재사용하되, HTTP 롱폴링 대신 FakeIOStream을 끼워서 브라우저 없이 토론 단계만
실행한다. 토론 종료 후 양식(아이디어 제출) 단계는 건너뛴다 — 그건 프론트 없이는
어차피 못 채우고(session.next_form()이 영원히 블록), 이 하네스가 검증하려는 건
에이전트 토론 메커니즘이지 양식 UI가 아니다.

사용법:
    python test_session.py --condition centralized --task A
    python test_session.py --condition decentralized --task B --seed 42

로그 저장: logs/test/ (파일명 규칙은 ExperimentSession.base 그대로:
    {참가자ID}_{condition}_task{task}_{타임스탬프}_{세션ID앞8자리}_*.jsonl)
세션 종료 후: 발화 통계 + 페이즈별 타이밍 자동 출력 (+ *_summary.json 저장, token_usage 포함)
"""
import argparse
import datetime
import json
import os
import random
import re
import sys
import time
from typing import Any
from uuid import UUID


def _json_safe(obj: Any) -> Any:
    """JSON 직렬화 불가능한 객체(UUID, datetime 등)를 문자열/기본형으로 변환."""
    if isinstance(obj, (str, int, float, bool)) or obj is None:
        return obj
    if isinstance(obj, UUID):
        return str(obj)
    if isinstance(obj, (datetime.datetime, datetime.date)):
        return obj.isoformat()
    if isinstance(obj, dict):
        return {str(k): _json_safe(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_json_safe(v) for v in obj]
    return str(obj)


# web.py의 LOG_DIR을 logs/test/로 바꾸고 나서 import (순서 중요)
_TEST_LOG_DIR = os.path.join(os.path.dirname(__file__), "logs", "test")
os.makedirs(_TEST_LOG_DIR, exist_ok=True)

import web  # noqa: E402

web.LOG_DIR = _TEST_LOG_DIR
# SESSION_REGISTRY/CSV 경로들은 import 시점의 원래 LOG_DIR을 이미 캡처해뒀으므로
# web.LOG_DIR 재대입만으론 안 바뀐다 — 안 하면 테스트 세션도 진짜
# logs/experiment/에 파일을 쓰고 누적 CSV까지 오염시킨다.
web.SESSION_REGISTRY.log_dir = _TEST_LOG_DIR
web.MESSAGES_CSV = os.path.join(_TEST_LOG_DIR, "messages.csv")
web.IDEAS_CSV = os.path.join(_TEST_LOG_DIR, "ideas.csv")
web.SESSIONS_CSV = os.path.join(_TEST_LOG_DIR, "sessions.csv")

from experiment_runtime import session_scope  # noqa: E402
from autogen.io import IOStream  # noqa: E402


# ──────────────────────────────────────────────────────────────────────
# 타이밍 트래커
# ──────────────────────────────────────────────────────────────────────
class PhaseTimer:
    """세션 전체 + 페이즈별 경과 시간 추적."""

    def __init__(self):
        self.session_start: float = time.time()
        self.phases: list[dict] = []
        self._current_phase: str | None = None
        self._phase_start: float = 0.0

    def elapsed(self) -> float:
        """세션 시작 이후 경과 초."""
        return time.time() - self.session_start

    def elapsed_str(self) -> str:
        """[MM:SS] 포맷 경과 시간."""
        e = self.elapsed()
        m, s = divmod(int(e), 60)
        return f"[{m:02d}:{s:02d}]"

    def start_phase(self, name: str):
        """새 페이즈 시작 기록 (이전 페이즈가 열려있으면 먼저 닫는다)."""
        if self._current_phase:
            self.end_phase()
        self._current_phase = name
        self._phase_start = time.time()

    def end_phase(self):
        """현재 페이즈 종료 기록."""
        if not self._current_phase:
            return
        duration = time.time() - self._phase_start
        self.phases.append({
            "phase": self._current_phase,
            "duration_s": round(duration, 1),
            "session_elapsed_s": round(self.elapsed(), 1),
        })
        self._current_phase = None

    def summary(self) -> dict:
        """타이밍 요약 반환."""
        total = self.elapsed()
        return {
            "total_s": round(total, 1),
            "total_str": f"{int(total // 60)}분 {int(total % 60)}초",
            "phases": self.phases,
        }


# 전역 타이머 인스턴스 (세션마다 리셋)
_timer: PhaseTimer | None = None


def _install_timing_hook():
    """web.log_event를 감싸서 phase_start 이벤트가 지나갈 때마다 타이머를 갱신한다.

    예전엔 meeting.stage2.run_stage2_discussion / meeting.independent.run_stage2_independent
    함수 자체를 monkey-patch했는데, 그 모듈들이 6e96af4 리팩터링으로 centralized/decentralized로
    재편되며 이름째 없어졌다. 대신 phase_start 이벤트는 조건과 무관하게 항상
    meeting/common.py의 _log() → web.log_event()라는 단일 지점을 거치므로, 여기 하나만
    감싸두면 내부 함수 이름이나 시그니처가 나중에 또 바뀌어도 안 깨진다."""
    if getattr(web.log_event, "_test_timing_wrapped", False):
        return
    _orig_log_event = web.log_event

    def _wrapped(event_type, data):
        if _timer is not None and event_type == "phase_start" and isinstance(data, dict):
            phase = data.get("phase")
            if phase:
                _timer.start_phase(phase)
        return _orig_log_event(event_type, data)

    _wrapped._test_timing_wrapped = True
    web.log_event = _wrapped


# ──────────────────────────────────────────────────────────────────────
# 랜덤 참가자 피드백 풀
# ──────────────────────────────────────────────────────────────────────
FEEDBACK_POOL = [
    # skip (40%) — 실제 참가자 로그(P66)도 40+ 턴 중 개입은 2~3회뿐이었음
    ("", 40),
    # 짧은 동의/반응 (20%)
    ("좋네요", 7),
    ("계속 해주세요", 7),
    ("음, 그렇군요", 6),
    # 방향 지시 — 부드러운 조정 (15%)
    ("좀 더 구체적으로 말씀해주세요", 5),
    ("다른 방향도 생각해볼까요", 5),
    ("실현 가능성도 고려해주세요", 5),
    # 실질적 반박 — 실제 참가자가 썼던 패턴 기반 (25%)
    ("지금 얘기가 다 비슷한 방향인 것 같은데, 완전히 다른 아이디어는 없을까요?", 5),
    ("이게 정말 사용자에게 필요한 기능일까요? 실제로 원할지 잘 모르겠어요", 5),
    ("방금 그 아이디어가 실제로 문제를 해결해주는 게 맞나요? 다시 짚어주세요", 5),
    ("개인정보나 프라이버시 문제는 괜찮을까요?", 5),
    ("제 생각엔 신입생들이 그것보다 다른 걸 더 필요로 할 것 같은데요", 5),
]


def _pick_feedback(rng: random.Random) -> str:
    """가중치 기반 랜덤 피드백 선택."""
    total = sum(w for _, w in FEEDBACK_POOL)
    r = rng.randint(1, total)
    acc = 0
    for text, weight in FEEDBACK_POOL:
        acc += weight
        if r <= acc:
            return text
    return ""


# ──────────────────────────────────────────────────────────────────────
# FakeIOStream — AG2의 IOStream 프로토콜 + centralized._push_to_ui가 찾는 send_text
# ──────────────────────────────────────────────────────────────────────
class FakeIOStream:
    """브라우저 없이 _run_session을 돌리기 위한 가짜 입출력.

    - print(): AG2 기본 출력 경로 (decentralized의 GroupChat 자동 출력 등)
    - send_text(): centralized.py의 _push_to_ui가 iostream에서 먼저 찾는 메서드.
      없으면 "no_supported_transport"로 조용히 버려지기만 하고(messages.jsonl에는
      _log_msg로 어차피 다 쌓이니 데이터 유실은 아님) 콘솔에 실시간으로는 안 보이는데,
      그러면 centralized 조건은 테스트 중 아무것도 안 보여서 디버깅하기 나쁘므로 구현해둔다.
    - input(): 미리 준비된 랜덤 피드백을 즉시 반환.
    """

    def __init__(self, rng: random.Random, verbose: bool = True):
        self.rng = rng
        self.verbose = verbose
        self.input_count = 0

    def _prefixed_print(self, text: str) -> None:
        if not self.verbose:
            return
        prefix = _timer.elapsed_str() if _timer else ""
        print(f"{prefix} {text}")

    def print(self, *objects: Any, sep: str = " ", end: str = "\n", flush: bool = False) -> None:
        text = sep.join(str(o) for o in objects) + (end if end != "\n" else "")
        self._prefixed_print(text.rstrip("\n"))

    def send_text(self, sender: str, content: str, recipient: str = "PM", summary: str = "") -> None:
        self._prefixed_print(f"[{sender} → {recipient}] {content}")

    def send(self, message: Any) -> None:
        """구조화된 이벤트(BaseEvent 등) 수신 — 콘솔에만 찍는다."""
        if not self.verbose or not hasattr(message, "print"):
            return
        try:
            prefix = _timer.elapsed_str() if _timer else ""
            sys.stdout.write(f"{prefix} ")
            message.print()
        except Exception:
            pass

    def input(self, prompt: str = "", *, password: bool = False) -> str:
        self.input_count += 1
        feedback = _pick_feedback(self.rng)
        self._prefixed_print(f"\n>>> [참가자 #{self.input_count}] {feedback if feedback else '(skip)'}")
        return feedback


# ──────────────────────────────────────────────────────────────────────
# 세션 실행
# ──────────────────────────────────────────────────────────────────────
def run_test_session(condition: str, task: str, seed: int | None = None, quiet: bool = False) -> str:
    """테스트 세션 1회 실행. *_log.jsonl 경로 반환."""
    global _timer
    _timer = PhaseTimer()
    _install_timing_hook()

    rng = random.Random(seed) if seed is not None else random.Random()
    brief = web.BRIEFS.get(task, web.BRIEFS["A"])
    pid = f"TEST_{datetime.datetime.now().strftime('%H%M%S')}"

    # 실제 web._session_worker와 동일한 순서: 세션 등록 → session_scope 진입 →
    # 상태를 running으로 → _run_session 실행. start_session()을 안 쓰는 이유는
    # 그건 STUDY_STORE.record_session_start까지 태우는 참가자 입구용 함수라(Supabase
    # 미설정이면 어차피 no-op이지만) 테스트에선 SESSION_REGISTRY만 직접 쓰는 게 더 명확하다.
    session = web.SESSION_REGISTRY.create(
        participant_id=pid, condition=condition, task=task, brief=brief,
    )
    print(f"[세션 생성] id={session.id} base={session.base}")

    io = FakeIOStream(rng, verbose=not quiet)

    with session_scope(session), IOStream.set_default(io):
        session.set_status("running")
        web.log_event("session_start", {
            "session_id": session.id,
            "participant_id": pid,
            "condition": condition,
            "task": task,
            "seed": seed,
            "test_mode": True,
        })
        try:
            if condition == "centralized":
                io.print("[시스템] 이번 세션에서는 PM이 회의를 이끕니다.")
            else:
                io.print("[시스템] 이번 세션에서는 에이전트들이 각각 동등하게 참여합니다.")
            io.print(f"\n[주제]\n{brief}\n")

            web._run_session(io, condition, brief, session.id)
        except Exception as e:
            web.log_event("error", {"message": str(e)})
            import traceback
            traceback.print_exc()
        finally:
            if _timer and _timer._current_phase:
                _timer.end_phase()
            web.log_event("session_end", {})
            # _run_session이 끝나면 session.token_usage가 이미 채워져 있다
            # (web._run_session 안에서 gather_usage_summary로 채움) — 여기선 그대로 저장만.
            session.finish("completed", token_usage=session.token_usage)

    return os.path.join(_TEST_LOG_DIR, f"{session.base}_log.jsonl")


# ──────────────────────────────────────────────────────────────────────
# 발화 통계
# ──────────────────────────────────────────────────────────────────────
def analyze_utterances(log_path: str) -> dict:
    """{base}_messages.jsonl에서 에이전트 발화만 추출해 통계 계산.

    (예전엔 *_log.jsonl 안의 ws_send/print 이벤트를 파싱했는데, 지금은
    ExperimentSession.record_message가 발화를 {base}_messages.jsonl에 별도로
    {speaker, content, phase} 형태로 쌓아주므로 그쪽이 훨씬 안정적이다.)"""
    messages_path = log_path.replace("_log.jsonl", "_messages.jsonl")
    if not os.path.exists(messages_path):
        return {"count": 0}

    utterances = []
    with open(messages_path, encoding="utf-8") as f:
        for line in f:
            try:
                d = json.loads(line)
            except json.JSONDecodeError:
                continue
            speaker = d.get("speaker", "")
            text = (d.get("content") or "").strip()
            if not text or speaker == "Participant":
                continue
            utterances.append(text)

    if not utterances:
        return {"count": 0}

    chars = [len(u) for u in utterances]
    sent_counts = []
    for u in utterances:
        sents = re.split(r'(?<=[.!?。])\s+', u.replace("\n", " "))
        sent_counts.append(len([s for s in sents if s.strip()]))

    return {
        "count": len(utterances),
        "mean_chars": sum(chars) / len(chars),
        "median_chars": sorted(chars)[len(chars) // 2],
        "min_chars": min(chars),
        "max_chars": max(chars),
        "mean_sentences": sum(sent_counts) / len(sent_counts),
        "max_sentences": max(sent_counts),
    }


def print_stats(stats: dict, timing: dict | None = None) -> None:
    if stats.get("count", 0) == 0:
        print("\n[통계] 발화 없음 (세션이 시작 전/직후에 종료됨)")
        return
    print()
    print("=" * 60)
    print("  발화 통계")
    print("=" * 60)
    print(f"  총 발화 수        : {stats['count']}")
    print(f"  평균 글자 수      : {stats['mean_chars']:.0f}")
    print(f"  중간값 글자 수    : {stats['median_chars']}")
    print(f"  최소 / 최대 글자  : {stats['min_chars']} / {stats['max_chars']}")
    print(f"  평균 문장 수      : {stats['mean_sentences']:.1f}")
    print(f"  최대 문장 수      : {stats['max_sentences']}")

    if timing:
        print()
        print("-" * 60)
        print("  타이밍")
        print("-" * 60)
        print(f"  총 소요 시간      : {timing['total_str']}")
        for p in timing.get("phases", []):
            dur = p["duration_s"]
            m, s = divmod(int(dur), 60)
            print(f"  {p['phase']:20s}: {m}분 {s}초 ({dur:.0f}s)")

    print("=" * 60)


def analyze_timing_from_log(log_path: str) -> dict | None:
    """*_log.jsonl의 phase_start 이벤트 타임스탬프로 페이즈별 소요시간을 역산한다.

    예전엔 PhaseTimer가 phase_end 이벤트를 별도로 로그에 남겨서 그걸 읽었는데, 그건
    monkey-patch가 만들어 붙이던 테스트 전용 이벤트라 지금은 로그에 없다. 대신
    phase_start만으로 "다음 phase_start(또는 session_end)까지"를 그 페이즈 길이로
    계산한다 — 실제 코드가 남기는 이벤트만 근거로 삼으므로 내부 함수가 또 바뀌어도 안 깨진다."""
    starts: list[tuple[str, str]] = []
    session_end = None
    with open(log_path, encoding="utf-8") as f:
        for line in f:
            try:
                d = json.loads(line)
            except json.JSONDecodeError:
                continue
            evt = d.get("event")
            t = d.get("time", "")
            if evt == "session_end":
                session_end = t
            elif evt == "phase_start":
                phase = (d.get("data") or {}).get("phase")
                if phase and t:
                    starts.append((phase, t))
    if not starts:
        return None

    def _parse(ts: str) -> datetime.datetime:
        return datetime.datetime.fromisoformat(ts.replace("Z", "+00:00"))

    phases = []
    for i, (phase, t_start) in enumerate(starts):
        t_end = starts[i + 1][1] if i + 1 < len(starts) else (session_end or t_start)
        try:
            duration = (_parse(t_end) - _parse(t_start)).total_seconds()
        except (ValueError, TypeError):
            duration = 0.0
        phases.append({"phase": phase, "duration_s": round(max(duration, 0.0), 1)})

    total = sum(p["duration_s"] for p in phases)
    return {
        "total_s": round(total, 1),
        "total_str": f"{int(total // 60)}분 {int(total % 60)}초",
        "phases": phases,
    }


# ──────────────────────────────────────────────────────────────────────
# CLI
# ──────────────────────────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(description="Headless 세션 테스트 환경")
    parser.add_argument("--condition", choices=["centralized", "decentralized"], default="centralized")
    parser.add_argument("--task", choices=["A", "B"], default="A")
    parser.add_argument("--seed", type=int, default=None, help="랜덤 시드 (재현 가능)")
    parser.add_argument("--quiet", action="store_true", help="콘솔 출력 최소화")
    parser.add_argument("--analyze", type=str, default=None,
                        help="기존 로그 파일(*_log.jsonl) 분석만 수행 (세션 실행 안 함)")
    args = parser.parse_args()

    if args.analyze:
        # 기존 로그 분석 모드
        print(f"[분석] {args.analyze}")
        stats = analyze_utterances(args.analyze)
        timing = analyze_timing_from_log(args.analyze)
        print_stats(stats, timing)
        return

    print(f"[세션 시작] condition={args.condition}, task={args.task}, seed={args.seed}")
    log_path = run_test_session(args.condition, args.task, seed=args.seed, quiet=args.quiet)
    print(f"\n[세션 종료] 로그: {log_path}")

    stats = analyze_utterances(log_path)
    timing = _timer.summary() if _timer else None
    print_stats(stats, timing)

    # 타이밍 데이터도 별도 JSON으로 저장
    if timing:
        timing_path = log_path.replace("_log.jsonl", "_timing.json")
        with open(timing_path, "w", encoding="utf-8") as f:
            json.dump(timing, f, ensure_ascii=False, indent=2)
        print(f"\n[타이밍 저장] {timing_path}")


if __name__ == "__main__":
    main()

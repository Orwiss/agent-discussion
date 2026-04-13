"""
Headless 세션 테스트 환경.

web.py의 _run_session을 그대로 재사용하되, WebSocket 대신 FakeIOStream을
끼워서 브라우저 없이 실험 세션을 실행한다.

사용법:
    python test_session.py --condition discussion --task A
    python test_session.py --condition independent --task B --seed 42

로그 저장: logs/test/
세션 종료 후: 발화 통계 자동 출력
"""
import argparse
import datetime
import json
import os
import random
import re
import sys
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
    # 기타: 문자열로
    return str(obj)

# web.py의 LOG_DIR을 logs/test/로 바꾸고 나서 import (순서 중요)
_TEST_LOG_DIR = os.path.join(os.path.dirname(__file__), "logs", "test")
os.makedirs(_TEST_LOG_DIR, exist_ok=True)

import web  # noqa: E402
web.LOG_DIR = _TEST_LOG_DIR

import autogen  # noqa: E402
import autogen.runtime_logging as ag2_logging  # noqa: E402
from autogen.io import IOStream  # noqa: E402


# ──────────────────────────────────────────────────────────────────────
# 랜덤 참가자 피드백 풀
# ──────────────────────────────────────────────────────────────────────
FEEDBACK_POOL = [
    # skip (40%)
    ("", 40),
    # 짧은 동의/반응 (30%)
    ("좋네요", 10),
    ("계속 해주세요", 10),
    ("음, 그렇군요", 10),
    # 방향 지시 (20%)
    ("좀 더 구체적으로 말씀해주세요", 7),
    ("다른 방향도 생각해볼까요", 7),
    ("실현 가능성도 고려해주세요", 6),
    # 주제 좁히기 (10%)
    ("핵심 기능 한 가지에 집중해볼까요", 5),
    ("사용자 입장에서 가장 중요한 건 뭘까요", 5),
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
# FakeIOStream — AG2의 IOStream 프로토콜을 흉내내는 가짜 입출력
# ──────────────────────────────────────────────────────────────────────
class FakeIOStream:
    """브라우저 없이 _run_session을 돌리기 위한 가짜 iostream.

    - print(): 출력을 파일 로그 + 콘솔에 기록 (web.py가 WebSocket에 보내는 것과 동일한 포맷)
    - input(): 미리 준비된 랜덤 피드백을 즉시 반환
    - send(): 구조화된 이벤트 전달 시 로그만 남김
    """

    def __init__(self, rng: random.Random, verbose: bool = True):
        self.rng = rng
        self.verbose = verbose
        self.input_count = 0

    def print(self, *objects: Any, sep: str = " ", end: str = "\n", flush: bool = False) -> None:
        text = sep.join(str(o) for o in objects) + (end if end != "\n" else "")
        content = {
            "objects": [str(o) for o in objects],
            "sep": sep,
            "end": end,
        }
        web.log_event("ws_send", _json_safe({"type": "print", "content": content}))
        if self.verbose:
            print(text.rstrip("\n"))

    def send(self, message: Any) -> None:
        """구조화된 이벤트(BaseEvent 등) 수신. 로그에 기록."""
        try:
            if hasattr(message, "model_dump"):
                data = message.model_dump()
            elif hasattr(message, "__dict__"):
                data = {k: v for k, v in message.__dict__.items() if not k.startswith("_")}
            else:
                data = str(message)
        except Exception:
            data = repr(message)
        try:
            web.log_event("ws_send", _json_safe({"type": "event", "content": data}))
        except Exception as e:
            # 로그 실패는 세션을 죽이지 않음
            print(f"[FakeIOStream] log_event 실패: {e}", file=sys.stderr)
        if self.verbose and hasattr(message, "print"):
            try:
                message.print()
            except Exception:
                pass

    def input(self, prompt: str = "", *, password: bool = False) -> str:
        self.input_count += 1
        feedback = _pick_feedback(self.rng)
        web.log_event("participant_input", {
            "prompt": prompt[:100],
            "feedback": feedback,
            "count": self.input_count,
        })
        if self.verbose:
            print(f"\n>>> [참가자 #{self.input_count}] {feedback if feedback else '(skip)'}")
        return feedback


# ──────────────────────────────────────────────────────────────────────
# 세션 실행
# ──────────────────────────────────────────────────────────────────────
def run_test_session(condition: str, task: str, seed: int | None = None) -> str:
    """테스트 세션 1회 실행. 로그 파일 경로 반환."""
    rng = random.Random(seed) if seed is not None else random.Random()
    brief = web.BRIEFS.get(task, web.BRIEFS["A"])

    # 로그 파일 초기화 (web.py의 _init_log 재사용, LOG_DIR은 이미 test로 바꿈)
    pid = f"TEST_{datetime.datetime.now().strftime('%H%M%S')}"
    log_filename = web._init_log(pid, condition, task)
    web.log_event("session_start", {
        "participant_id": pid,
        "condition": condition,
        "task": task,
        "log_file": log_filename,
        "seed": seed,
        "test_mode": True,
    })

    # AG2 runtime logging도 같은 폴더로
    ag2_logname = f"ag2_{pid}_{condition}_{datetime.datetime.now().strftime('%Y%m%d_%H%M%S')}.log"
    ag2_logging.start(logger_type="file", config={"filename": os.path.join(_TEST_LOG_DIR, ag2_logname)})

    io = FakeIOStream(rng)
    # AG2 global default로 등록
    IOStream.set_global_default(io)

    try:
        # 조건 안내 출력 (web.on_connect가 하던 것)
        if condition == "discussion":
            io.print("[시스템] 이번 세션에서는 에이전트들이 서로의 의견을 참고하며 작업합니다.")
        else:
            io.print("[시스템] 이번 세션에서는 에이전트들이 각각 독립적으로 작업합니다.")
        io.print(f"\n[주제]\n{brief}\n")
        io.print("[시스템] 에이전트들이 각자의 전문 영역에서 아이디어를 제안합니다. 6발화마다 의견을 입력할 수 있습니다.")

        web._run_session(io, condition, brief)
    except Exception as e:
        web.log_event("error", {"message": str(e)})
        import traceback
        traceback.print_exc()
    finally:
        ag2_logging.stop()
        web.log_event("session_end", {})
        web._close_log()

    return os.path.join(_TEST_LOG_DIR, log_filename)


# ──────────────────────────────────────────────────────────────────────
# 발화 통계
# ──────────────────────────────────────────────────────────────────────
def analyze_utterances(log_path: str) -> dict:
    """로그에서 에이전트 발화만 추출해 통계 계산."""
    utterances = []
    with open(log_path, encoding="utf-8") as f:
        for line in f:
            try:
                d = json.loads(line)
            except json.JSONDecodeError:
                continue
            if d.get("event") != "ws_send":
                continue
            data = d.get("data", {})
            if data.get("type") != "print":
                continue
            content = data.get("content", {})
            objects = content.get("objects", [])
            if not objects:
                continue
            text = objects[0] if isinstance(objects[0], str) else ""
            # 시스템/주제/참가자 메시지는 제외
            if not text or text.startswith("[시스템]") or text.startswith("[주제]") or text.startswith(">>>"):
                continue
            if "===" in text[:5]:  # === 디자인 아이디에이션 ===
                continue
            utterances.append(text.strip())

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


def print_stats(stats: dict) -> None:
    if stats.get("count", 0) == 0:
        print("\n[통계] 발화 없음 (세션이 시작 전/직후에 종료됨)")
        return
    print()
    print("=" * 60)
    print("발화 통계")
    print("=" * 60)
    print(f"총 발화 수        : {stats['count']}")
    print(f"평균 글자 수      : {stats['mean_chars']:.0f}")
    print(f"중간값 글자 수    : {stats['median_chars']}")
    print(f"최소 / 최대 글자  : {stats['min_chars']} / {stats['max_chars']}")
    print(f"평균 문장 수      : {stats['mean_sentences']:.1f}")
    print(f"최대 문장 수      : {stats['max_sentences']}")
    print("=" * 60)


# ──────────────────────────────────────────────────────────────────────
# CLI
# ──────────────────────────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(description="Headless 세션 테스트 환경")
    parser.add_argument("--condition", choices=["discussion", "independent"], default="discussion")
    parser.add_argument("--task", choices=["A", "B"], default="A")
    parser.add_argument("--seed", type=int, default=None, help="랜덤 시드 (재현 가능)")
    parser.add_argument("--quiet", action="store_true", help="콘솔 출력 최소화")
    args = parser.parse_args()

    print(f"[세션 시작] condition={args.condition}, task={args.task}, seed={args.seed}")
    log_path = run_test_session(args.condition, args.task, seed=args.seed)
    print(f"\n[세션 종료] 로그: {log_path}")

    stats = analyze_utterances(log_path)
    print_stats(stats)


if __name__ == "__main__":
    main()

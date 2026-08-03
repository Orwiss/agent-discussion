"""Centralized / Decentralized 두 모듈이 공유하는 인프라.

- Phase prefix 동적 주입 (system_message 끝에 부착, 조건 블록 본체는 안 건드림)
- Opening message 빌더
"""
import re


def collapse_blank_lines(text: str) -> str:
    """발화의 줄바꿈·빈 줄을 공백 하나로 합쳐 한 문단으로 만든다.
    단어(내용)는 그대로 두고 단락 간격만 없앤다 — 잘라내기가 아님."""
    if not text:
        return text
    return re.sub(r"\s*\n\s*", " ", text).strip()


def _log(event, data):
    """로그 이벤트. web.py의 log_event가 있으면 거기로, 없으면 콘솔."""
    try:
        from web import log_event
        log_event(event, data)
    except ImportError:
        print(f"  [{event}] {data}")


# === 페이즈 전환 시 에이전트 system_message 끝에 동적 주입되는 prefix ===
# 조건 블록은 페르소나 본체에 영구 박힘 → PHASE_PREFIX_SENTINEL 이후만 교체되므로 안 깨짐
# 세 phase 문구는 DCIM(발산·정교화·수렴 집단 창의 모델)의 정의에서 도출.
PHASE_PREFIX_SENTINEL = "\n\n[현재 페이즈]"
PHASE_PREFIXES = {
    "divergence": (
        f"{PHASE_PREFIX_SENTINEL}\n"
        "이 회의의 주제는 <{brief}>입니다. 회의의 목표를 잊지 마세요.\n"
        "지금부터는 판단을 미루고 다양한 아이디어를 내는 단계입니다. 실현 가능성이나 좋고 나쁨을 따지기 전에, "
        "지금까지 언급되지 않은 새로운 방향을 꺼내세요. 구체적으로 말하기보다는, 생각나는 아이디어를 한번 던져보세요. "
        "인사나 감사 없이 바로 본인 의견부터 말하세요."
    ),
    "elaboration": (
        f"{PHASE_PREFIX_SENTINEL}\n"
        "이 회의의 주제는 <{brief}>입니다. 회의의 목표를 잊지 마세요.\n"
        "지금부터는 나온 아이디어를 서로 주고받으며 발전시키는 단계입니다. 남이 낸 아이디어에 정보나 다른 관점을 더해 "
        "더 새롭게 만드세요. 구체적인 시나리오나 사용 흐름까지 그려보면 좋습니다."
    ),
    "convergence": (
        f"{PHASE_PREFIX_SENTINEL}\n"
        "이 회의의 주제는 <{brief}>입니다. 회의의 목표를 잊지 마세요.\n"
        "지금부터는 지금까지 나왔던 아이디어나 기능을 놓고 어떤 것이 더 나은지 논의를 통해 좁혀가는 단계입니다. "
        "각 아이디어를 선택했을 때 장단점을 충분히 구체적으로 검토하고, 검토 내용을 근거로 아이디어를 천천히 좁혀 나가세요."
    ),
}


def inject_phase_prefix(agent, phase: str, brief: str) -> None:
    """페이즈 전환 시 에이전트 system_message 끝에 phase prefix를 (재)부착.
    기존 prefix가 있으면 잘라내고 새 걸로 교체 — 누적 방지."""
    current = agent.system_message
    idx = current.find(PHASE_PREFIX_SENTINEL)
    if idx >= 0:
        current = current[:idx]
    agent.update_system_message(current + PHASE_PREFIXES[phase].format(brief=brief))


def build_opening_message(brief: str, phase: str = "divergence", carryover: str = "") -> str:
    """페이즈 opening message — 화면에 Participant 이름으로 한 번 찍히는 시스템 안내.

    divergence: 회의 시작 + 과제(brief) 고지.
    elaboration/convergence: 이전 페이즈 요약(carryover)으로 이어받기.
    """
    if phase == "divergence":
        body = f"디자인 아이디에이션 회의를 시작합니다. 오늘 다룰 과제는 다음과 같습니다.\n{brief}"
    else:
        body = "지금까지 논의된 내용입니다."
    return body + (carryover or "")

"""Centralized / Decentralized 두 모듈이 공유하는 인프라.

- DedupGroupChat: 중복 메시지 차단
- Phase prefix 동적 주입 (system_message 끝에 부착, 조건 블록 본체는 안 건드림)
- Opening message 빌더
"""
import re

import autogen


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


# === 페이즈별 안내 메시지 (opening에 들어감) ===
PHASE_MESSAGES = {
    "generate": (
        "=== 발산 페이즈 ===\n"
        "자유롭게 다양한 아이디어를 내주세요. 어떤 방향이든 환영합니다.\n"
        "기존 아이디어와 다른 새로운 방향을 제안하세요."
    ),
    "deepen": (
        "=== 심화 페이즈 ===\n"
        "지금까지 나온 아이디어 중 유망한 것을 골라 깊게 발전시키세요.\n"
        "구체적인 시나리오, 기능 구조, 사용 흐름을 제안하세요."
    ),
    "converge": (
        "=== 수렴 페이즈 ===\n"
        "지금까지 논의를 정리하세요.\n"
        "최종 컨셉을 하나로 모으고, 핵심 기능과 차별점을 명확히 하세요."
    ),
}


# === 페이즈 전환 시 에이전트 system_message 끝에 동적 주입되는 prefix ===
# 조건 블록은 페르소나 본체에 영구 박힘 → PHASE_PREFIX_SENTINEL 이후만 교체되므로 안 깨짐
PHASE_PREFIX_SENTINEL = "\n\n[현재 페이즈]"
PHASE_PREFIXES = {
    "generate": (
        f"{PHASE_PREFIX_SENTINEL}\n"
        "지금까지 나온 것과 다른 각도의 아이디어를 하나 꺼내세요. "
        "폭을 넓히는 게 목표입니다. 깊이 파지 말고 새로운 방향을 여세요. "
        "빈 줄 없이 한 문단으로, 2~3문장만 말하세요."
    ),
    "deepen": (
        f"{PHASE_PREFIX_SENTINEL}\n"
        "발산에서 나온 아이디어 중 하나를 골라 구체적으로 풀어보세요. "
        "사용자가 실제로 어떻게 쓸지 그림을 그리되, 한 번에 다 풀지 말고 "
        "빈 줄 없이 한 문단으로, 2~3문장만 말하세요."
    ),
    "converge": (
        f"{PHASE_PREFIX_SENTINEL}\n"
        "지금까지 나온 것 중 핵심만 추려 한 방향으로 정리하세요. "
        "새로운 제안보다 지금 있는 것들의 합의에 집중하세요. "
        "빈 줄 없이 한 문단으로, 2~3문장만 말하세요."
    ),
}


def inject_phase_prefix(agent, phase: str) -> None:
    """페이즈 전환 시 에이전트 system_message 끝에 phase prefix를 (재)부착.
    기존 prefix가 있으면 잘라내고 새 걸로 교체 — 누적 방지."""
    current = agent.system_message
    idx = current.find(PHASE_PREFIX_SENTINEL)
    if idx >= 0:
        current = current[:idx]
    agent.update_system_message(current + PHASE_PREFIXES[phase])


class DedupGroupChat(autogen.GroupChat):
    """메시지 중복을 자동 필터링하는 GroupChat.
    같은 에이전트가 직전 메시지(앞 200자)를 그대로 반복하면 차단."""

    def append(self, message, speaker):
        content = message.get("content", "") if isinstance(message, dict) else str(message)
        if content and self.messages:
            for prev in self.messages[-3:]:
                prev_content = prev.get("content", "")
                if (prev_content
                        and prev.get("name") == message.get("name")
                        and content[:200] == prev_content[:200]):
                    return
        super().append(message, speaker)


def build_opening_message(brief: str, phase: str = "generate", carryover: str = "") -> str:
    """페이즈 opening message — 페이즈 안내 (+ 이전 페이즈 요약).

    주제(brief)는 각 에이전트 system_message에 이미 들어있고 화면 헤더에도 표시되므로
    여기서 다시 반복하지 않는다. carryover(이전 페이즈 요약)만 뒤에 붙인다.
    """
    phase_msg = PHASE_MESSAGES.get(phase, PHASE_MESSAGES["generate"])

    if phase == "generate":
        body = (
            "=== 디자인 아이디에이션 시작 ===\n\n"
            "- 이 앱의 핵심 가치는 무엇이어야 할까요?\n"
            "- 어떤 기능이 사용자에게 가장 필요할까요?\n"
            "- 기존 서비스와 어떻게 차별화할 수 있을까요?\n\n"
            f"{phase_msg}"
        )
    else:
        body = phase_msg
    return body + (carryover or "")

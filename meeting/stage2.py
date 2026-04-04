"""
토론 조건 (Discussion) — GroupChat + round_robin
3개 페이즈 × 18발화 = 54발화, 페이즈당 유저 개입 2회
AG2 기본 기능만 사용.
"""
import autogen
from autogen.agentchat.contrib.capabilities.transform_messages import TransformMessages
from autogen.agentchat.contrib.capabilities.transforms import MessageHistoryLimiter

def _log(event, data):
    """로그 이벤트 기록 (web.py의 log_event 사용, import 실패 시 콘솔만)"""
    try:
        from web import log_event
        log_event(event, data)
    except ImportError:
        print(f"  [{event}] {data}")


# 페이즈별 시스템 메시지
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


class DedupGroupChat(autogen.GroupChat):
    """메시지 중복을 자동 필터링하는 GroupChat"""

    def append(self, message, speaker):
        content = message.get("content", "") if isinstance(message, dict) else str(message)
        if content and self.messages:
            for prev in self.messages[-3:]:
                prev_content = prev.get("content", "")
                # 같은 에이전트의 완전 동일 메시지만 차단 (앞 100자 → 200자로 완화)
                if (prev_content and prev.get("name") == message.get("name")
                        and content[:200] == prev_content[:200]):
                    return
        super().append(message, speaker)


def _create_6turn_speaker_selection(agents, user):
    """6발화마다 유저 개입하는 speaker selection.
    A→B→C→A→B→C→User→A→B→C→A→B→C→User→...
    양 조건 동일하게 적용되는 결정론적 순서."""
    state = {"agent_count": 0, "total": 0}

    def select_speaker(last_speaker, groupchat):
        state["total"] += 1
        last_name = last_speaker.name if last_speaker else "None"

        if last_speaker == user:
            state["agent_count"] = 0
            next_agent = agents[0]
            _log("speaker", f"턴{state['total']}: {last_name} → {next_agent.name} (유저 후 리셋)")
            return next_agent

        state["agent_count"] += 1
        if state["agent_count"] >= 6:
            _log("speaker", f"턴{state['total']}: {last_name} → Participant (6발화 도달)")
            return user

        idx = state["agent_count"] % len(agents)
        next_agent = agents[idx]
        _log("speaker", f"턴{state['total']}: {last_name} → {next_agent.name} ({state['agent_count']}/6)")
        return next_agent

    return select_speaker


def _create_phase_groupchat(agents, user, max_round=24):
    """한 페이즈용 GroupChat 생성 (18 에이전트 발화 + 유저 개입 2회 포함)"""
    speaker_fn = _create_6turn_speaker_selection(agents, user)

    groupchat = DedupGroupChat(
        agents=[user] + agents,
        messages=[],
        max_round=max_round,
        send_introductions=True,
        speaker_selection_method=speaker_fn,
    )

    manager = autogen.GroupChatManager(
        groupchat=groupchat,
        llm_config=agents[0].llm_config,
        is_termination_msg=lambda msg: "MEETING_END" in msg.get("content", ""),
    )

    # 히스토리 제한
    transform = TransformMessages(
        transforms=[MessageHistoryLimiter(max_messages=30)]
    )
    for agent in agents:
        transform.add_to_agent(agent)

    return groupchat, manager


def build_opening_message(brief, phase="generate"):
    """브리프 + 페이즈 안내 메시지"""
    phase_msg = PHASE_MESSAGES.get(phase, PHASE_MESSAGES["generate"])

    if phase == "generate":
        return (
            f"=== 디자인 아이디에이션 시작 ===\n\n"
            f"주제: {brief}\n\n"
            f"각자의 전문 영역에서 이 주제에 대한 아이디어를 제안해주세요.\n"
            f"- 이 앱의 핵심 가치는 무엇이어야 할까요?\n"
            f"- 어떤 기능이 사용자에게 가장 필요할까요?\n"
            f"- 기존 서비스와 어떻게 차별화할 수 있을까요?\n\n"
            f"{phase_msg}"
        )
    else:
        return f"{phase_msg}\n\n주제: {brief}"


def run_stage2_discussion(agents, user, brief, iostream=None):
    """
    토론 조건 실행 — 3페이즈 sequential.
    각 페이즈: 6발화 → 유저 개입 → 6발화 → 유저 개입 → 6발화 = 18발화
    """
    phases = ["generate", "deepen", "converge"]
    phase_labels = {"generate": "발산", "deepen": "심화", "converge": "수렴"}
    all_results = []

    for i, phase in enumerate(phases):
        # 이전 페이즈 요약을 carryover로 전달
        carryover = ""
        if all_results:
            prev_summary = all_results[-1].summary if all_results[-1].summary else ""
            carryover = f"\n\n=== 이전 페이즈 요약 ===\n{prev_summary}\n==================\n"

        opening = build_opening_message(brief + carryover, phase)

        # 페이즈 전환은 에이전트에게만 전달 (참가자 UI에는 안 보임)
        # iostream으로는 보내지 않음

        # 페이즈용 GroupChat 생성
        groupchat, manager = _create_phase_groupchat(agents, user, max_round=24)

        result = user.initiate_chat(
            manager,
            message=opening,
            summary_method="reflection_with_llm",
            summary_args={
                "summary_prompt": (
                    "지금까지 논의된 내용을 간결하게 요약하세요:\n"
                    "1. 제안된 주요 아이디어\n"
                    "2. 합의된 방향\n"
                    "3. 미해결 이슈"
                ),
            },
        )

        all_results.append(result)

    return all_results[-1]

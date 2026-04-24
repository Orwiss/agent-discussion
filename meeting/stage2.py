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
# 페이즈당 라운드 예산.
# 구조: 1 (initial msg) + 3 cycles * (6 agent + 1 user) = 22.
# 마지막 사용자 개입 후 한 번 더 응답 사이클을 보장하려면 +6 = 28.
# 24로 두면 마지막 사이클이 중간에 끊겨 SoftwareEngineer가 누락되는 케이스가 생긴다.
PHASE_MAX_ROUND = 28


def confirm_convergence(iostream):
    """수렴 페이즈 종료 직전 사용자에게 추가 의견을 묻는다.
    빈 입력이면 None 반환, 비어 있지 않으면 입력 문자열을 그대로 반환."""
    iostream.print(
        "[시스템] 마지막으로 추가하실 의견이 있으면 입력해주세요. (빈칸이면 요약을 생성합니다)"
    )
    raw = iostream.input("최종 의견: ")
    text = (raw or "").strip()
    return text if text else None


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

# 페이즈 전환 시 에이전트 system_message에 주입되는 prefix.
# 희석 방지: initial msg가 아닌 system_message에 직접 박아 매 턴 유효.
PHASE_PREFIX_SENTINEL = "\n\n[현재 페이즈]"
PHASE_PREFIXES = {
    "generate": (
        f"{PHASE_PREFIX_SENTINEL}\n"
        "지금까지 나온 것과 다른 각도의 아이디어를 하나 꺼내세요. "
        "폭을 넓히는 게 목표입니다. 깊이 파지 말고 새로운 방향을 여세요."
    ),
    "deepen": (
        f"{PHASE_PREFIX_SENTINEL}\n"
        "발산에서 나온 아이디어 중 하나를 골라 구체적으로 풀어보세요. "
        "사용자가 실제로 어떻게 쓸지, 어떻게 만들지 그림을 그리세요."
    ),
    "converge": (
        f"{PHASE_PREFIX_SENTINEL}\n"
        "지금까지 나온 것 중 핵심만 추려 한 방향으로 정리하세요. "
        "새로운 제안보다 지금 있는 것들의 합의에 집중하세요."
    ),
}


def _inject_phase_prefix(agent, phase):
    """페이즈 전환 시 에이전트 system_message에 페이즈 prefix를 (재)부착한다.
    기존 prefix가 있으면 제거 후 교체 (누적 방지)."""
    current = agent.system_message
    idx = current.find(PHASE_PREFIX_SENTINEL)
    if idx >= 0:
        current = current[:idx]
    agent.update_system_message(current + PHASE_PREFIXES[phase])


# 턴마다 에이전트 system_message 끝에 부착되는 리마인더.
# 센티넬로 기존 리마인더 부분을 식별하여 교체한다 (중복/누적 방지).
TURN_REMINDER_SENTINEL = "\n\n[턴 리마인더]"
TURN_REMINDER = (
    f"{TURN_REMINDER_SENTINEL}\n"
    "3문장 내외로 말하세요. 어떤 경우에도 5문장을 넘기면 안 됩니다."
)


def _inject_turn_reminder(agent):
    """에이전트의 system_message 끝에 턴 리마인더를 (재)부착한다.
    기존 리마인더가 있으면 제거 후 새로 붙인다 (누적 방지).
    페이즈 전환 등으로 시스템 메시지 앞쪽에 prefix가 붙은 상태에서도
    뒤쪽의 리마인더 부분만 정확히 갱신된다."""
    current = agent.system_message
    idx = current.find(TURN_REMINDER_SENTINEL)
    if idx >= 0:
        current = current[:idx]
    agent.update_system_message(current + TURN_REMINDER)


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
    양 조건 동일하게 적용되는 결정론적 순서.
    각 에이전트 턴 직전에 턴 리마인더를 system_message에 (재)부착한다."""
    state = {"agent_count": 0, "total": 0}

    def select_speaker(last_speaker, groupchat):
        state["total"] += 1
        last_name = last_speaker.name if last_speaker else "None"

        if last_speaker == user:
            state["agent_count"] = 0
            next_agent = agents[0]
            _log("speaker", f"턴{state['total']}: {last_name} → {next_agent.name} (유저 후 리셋)")
            _inject_turn_reminder(next_agent)
            return next_agent

        state["agent_count"] += 1
        if state["agent_count"] >= 6:
            _log("speaker", f"턴{state['total']}: {last_name} → Participant (6발화 도달)")
            return user

        idx = state["agent_count"] % len(agents)
        next_agent = agents[idx]
        _log("speaker", f"턴{state['total']}: {last_name} → {next_agent.name} ({state['agent_count']}/6)")
        _inject_turn_reminder(next_agent)
        return next_agent

    return select_speaker


def _create_phase_groupchat(agents, user, max_round=PHASE_MAX_ROUND):
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

        # 페이즈 prefix를 각 에이전트 system_message에 주입 (매 턴 유효하도록)
        for agent in agents:
            _inject_phase_prefix(agent, phase)

        # 페이즈용 GroupChat 생성
        groupchat, manager = _create_phase_groupchat(agents, user)

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

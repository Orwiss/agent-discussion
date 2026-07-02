"""Decentralized 조건 — round-robin peer-to-peer.

세 에이전트가 동등하게 A→B→C→A→B→C→User→... 순서로 발언.
3 phase × N 사이클, phase당 디자이너 개입 2회 (6발화마다).
"""
import autogen
from autogen.agentchat.contrib.capabilities.transform_messages import TransformMessages
from autogen.agentchat.contrib.capabilities.transforms import MessageHistoryLimiter

from meeting.common import (
    DedupGroupChat,
    inject_phase_prefix,
    build_opening_message,
    collapse_blank_lines,
    _log,
)


def _collapse_hook(*, sender, message, recipient, silent):
    """에이전트 발화의 빈 줄·줄바꿈을 한 문단으로 합친다 (내용 보존)."""
    content = message if isinstance(message, str) else message.get("content", "")
    if not content:
        return message
    cleaned = collapse_blank_lines(content)
    if isinstance(message, str):
        return cleaned
    message["content"] = cleaned
    return message

# 페이즈별 라운드 예산
# 라운드 4 (D-E-PM × 4 = 12 에이전트 발화) + user 차례 (6 발화마다 = 라운드 2 끝)
# 발산·심화: user 2번. opening 1 + 6 agent + user + 6 agent + user = 15
# 수렴: user 1번. opening 1 + 6 agent + user + 6 agent = 14
PHASE_MAX_ROUNDS = {
    "generate": 15,
    "deepen": 15,
    "converge": 14,
}


def confirm_convergence(iostream):
    """수렴 페이즈 종료 직전 사용자에게 추가 의견을 묻는다.
    빈 입력이면 None, 비어 있지 않으면 입력 문자열 그대로."""
    iostream.print(
        "[시스템] 마지막으로 추가하실 의견이 있으면 입력해주세요. (빈칸이면 다음으로 진행)"
    )
    raw = iostream.input("최종 의견: ")
    text = (raw or "").strip()
    return text if text else None


def _create_6turn_speaker_selection(agents, user):
    """6발화마다 유저 개입하는 speaker selection.
    A→B→C→A→B→C→User→A→B→C→A→B→C→User→..."""
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


def _create_phase_groupchat(agents, user, phase):
    """한 페이즈용 GroupChat 생성. phase별 max_round 적용."""
    speaker_fn = _create_6turn_speaker_selection(agents, user)
    max_round = PHASE_MAX_ROUNDS.get(phase, 15)

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

    transform = TransformMessages(
        transforms=[MessageHistoryLimiter(max_messages=30)]
    )
    for agent in agents:
        transform.add_to_agent(agent)

    return groupchat, manager


def run_decentralized_discussion(agents, user, brief, iostream=None):
    """Decentralized 토론 실행 — 3 phase sequential."""
    phases = ["generate", "deepen", "converge"]
    all_results = []

    # 발화의 빈 줄을 한 문단으로 합치는 hook을 에이전트마다 1회 등록
    for agent in agents:
        agent.register_hook("process_message_before_send", _collapse_hook)

    for i, phase in enumerate(phases):
        carryover = ""
        if all_results:
            prev_summary = all_results[-1].summary if all_results[-1].summary else ""
            prev_user = getattr(all_results[-1], "trailing_user", "") or ""
            carryover = f"\n\n=== 이전 페이즈 요약 ===\n{prev_summary}\n==================\n"
            if prev_user:
                carryover += (
                    f"\n참가자가 직전에 이런 의견을 냈습니다: \"{prev_user}\"\n"
                    "이 의견부터 짚고 이번 페이즈를 시작하세요.\n"
                )

        opening = build_opening_message(brief, phase, carryover)

        for agent in agents:
            inject_phase_prefix(agent, phase)

        groupchat, manager = _create_phase_groupchat(agents, user, phase)

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

        # 이번 페이즈에서 사용자가 마지막으로 말했고 아무도 안 받았으면, 그 발언을 다음 페이즈로 넘김
        trailing_user = ""
        for m in reversed(groupchat.messages):
            nm = m.get("name")
            if nm and nm != user.name:
                break  # 에이전트가 마지막 → 이미 응답됨, 넘길 필요 없음
            if nm == user.name:
                trailing_user = m.get("content", "") or ""
                break
        all_results.append(type("Result", (), {
            "summary": result.summary if result.summary else "",
            "trailing_user": trailing_user,
        })())

    return all_results[-1]

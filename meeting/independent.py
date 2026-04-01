"""
독립 조건 (Independent) — GroupChat + round_robin + 컨텍스트 격리
3개 페이즈 × 18발화 = 54발화, 페이즈당 유저 개입 2회
에이전트 간 컨텍스트 공유 없음: 각 에이전트는 자기 발화 + 유저 메시지만 참조.
AG2 GroupChat을 사용하되, process_all_messages_before_reply 훅으로 격리.
"""
import autogen
from autogen.agentchat.contrib.capabilities.transform_messages import TransformMessages
from autogen.agentchat.contrib.capabilities.transforms import MessageHistoryLimiter
from meeting.stage2 import DedupGroupChat, PHASE_MESSAGES, build_opening_message


def _create_isolation_hook(agent_name):
    """에이전트별 컨텍스트 격리 훅 생성.
    해당 에이전트는 자기 발화 + 유저 메시지 + 시스템 메시지만 볼 수 있음.
    다른 에이전트의 발화는 제거."""
    def isolation_hook(messages):
        if not messages:
            return messages
        filtered = []
        for m in messages:
            role = m.get("role", "")
            name = m.get("name", "")
            # 시스템 메시지, 유저 메시지, 자기 발화만 유지
            if role == "system" or name == "Participant" or name == agent_name or not name:
                filtered.append(m)
        return filtered if filtered else messages[:1]  # 최소 1개는 유지
    return isolation_hook


def _create_phase_groupchat_independent(agents, user, max_round=24):
    """독립 조건용 GroupChat — 6발화마다 유저 + 컨텍스트 격리"""
    from meeting.stage2 import _create_6turn_speaker_selection
    speaker_fn = _create_6turn_speaker_selection(agents, user)

    groupchat = DedupGroupChat(
        agents=[user] + agents,
        messages=[],
        max_round=max_round,
        send_introductions=False,  # 독립 조건: 에이전트가 서로를 모름
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

    # 컨텍스트 격리: 각 에이전트는 자기 발화 + 유저 메시지만 봄
    for agent in agents:
        hook = _create_isolation_hook(agent.name)
        agent.register_hook("process_all_messages_before_reply", hook)

    return groupchat, manager


def run_stage2_independent(agents, user, brief, iostream=None):
    """
    독립 조건 실행 — 3페이즈 sequential.
    GroupChat을 사용하되 에이전트 간 컨텍스트 격리.
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

        # 페이즈용 GroupChat 생성 (컨텍스트 격리 포함)
        groupchat, manager = _create_phase_groupchat_independent(agents, user, max_round=24)

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

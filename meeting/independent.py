"""
독립 조건 (Independent) — GroupChat + round_robin + 컨텍스트 격리
3개 페이즈 × 18발화 = 54발화, 페이즈당 유저 개입 2회
에이전트 간 컨텍스트 공유 없음: 각 에이전트는 자기 발화 + 유저 메시지만 참조.
AG2 GroupChat을 사용하되, process_all_messages_before_reply 훅으로 격리.

핵심: 독립 조건에서는 이전 페이즈 요약도 에이전트별로 분리.
각 에이전트는 자신의 기여만 담은 요약을 받음.
"""
import autogen
from autogen.agentchat.contrib.capabilities.transform_messages import TransformMessages
from autogen.agentchat.contrib.capabilities.transforms import MessageHistoryLimiter
from meeting.stage2 import DedupGroupChat, PHASE_MESSAGES, build_opening_message


def _extract_agent_messages(groupchat, agent_name):
    """GroupChat 메시지 히스토리에서 특정 에이전트의 발화만 추출.

    Args:
        groupchat: DedupGroupChat 인스턴스
        agent_name: 추출할 에이전트 이름 (예: "UXResearcher")

    Returns:
        해당 에이전트의 발화 메시지 리스트 (dict)
    """
    if not hasattr(groupchat, 'messages'):
        return []

    agent_messages = []
    for msg in groupchat.messages:
        if isinstance(msg, dict) and msg.get("name") == agent_name:
            agent_messages.append(msg)

    return agent_messages


def _build_agent_summary(groupchat, agent_name, llm_config):
    """특정 에이전트의 기여만 요약.

    독립 조건에서는 각 에이전트가 자신의 기여만 담은 요약을 받아야 함.
    이전 페이즈에서 해당 에이전트가 말한 내용만 추출하여 요약.

    Args:
        groupchat: DedupGroupChat 인스턴스
        agent_name: 요약할 에이전트 이름
        llm_config: LLM 설정

    Returns:
        해당 에이전트의 기여 요약 문자열
    """
    agent_messages = _extract_agent_messages(groupchat, agent_name)

    if not agent_messages:
        return ""

    # 에이전트의 발화들을 텍스트로 조합
    contributions = []
    for msg in agent_messages:
        content = msg.get("content", "")
        if content:
            contributions.append(f"- {content}")

    if not contributions:
        return ""

    # 간단한 텍스트 기반 요약 (LLM 호출 없이 구성)
    # 복잡한 요약이 필요하면 LLM을 호출할 수 있으나,
    # 여기서는 에이전트의 기여 목록을 명확히 제시
    contributions_text = "\n".join(contributions)
    summary = f"[{agent_name}의 제안]\n{contributions_text}"

    return summary


def _get_per_agent_summaries(groupchat, agent_names, llm_config):
    """모든 에이전트의 개별 요약을 생성.

    Args:
        groupchat: DedupGroupChat 인스턴스
        agent_names: 에이전트 이름 리스트
        llm_config: LLM 설정

    Returns:
        {agent_name: summary_string} 딕셔너리
    """
    summaries = {}
    for agent_name in agent_names:
        summary = _build_agent_summary(groupchat, agent_name, llm_config)
        summaries[agent_name] = summary

    return summaries


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

    핵심 차이:
    - 각 페이즈 후, 그룹 요약 대신 에이전트별 요약 추출
    - 각 에이전트는 자신의 기여만 담은 요약을 다음 페이즈에서 받음
    - 다른 에이전트의 아이디어는 보지 못함 (격리 유지)
    """
    phases = ["generate", "deepen", "converge"]
    phase_labels = {"generate": "발산", "deepen": "심화", "converge": "수렴"}
    all_results = []
    per_agent_summaries = {agent.name: "" for agent in agents}

    for i, phase in enumerate(phases):
        # 개별 carryover 구성: 각 에이전트에게는 자신의 이전 요약만 전달
        carryover = ""
        if per_agent_summaries:
            # 현재 에이전트를 위한 요약만 포함할 예정
            # (이는 에이전트마다 다른 opening 메시지가 필요함을 의미)
            carryover = "=== 이전 페이즈 요약 ===\n[개별 요약은 에이전트 시스템 메시지에 포함됨]\n"

        opening = build_opening_message(brief, phase)

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
                    "2. 합의된 방향 (독립 조건: 본인이 제안한 주요 방향)\n"
                    "3. 미해결 이슈"
                ),
            },
        )

        # 이 페이즈의 GroupChat에서 에이전트별 요약 추출
        if hasattr(result, 'chat_history') and result.chat_history:
            # result에는 chat_history가 있지만, groupchat.messages를 직접 사용하는 게 더 정확
            per_agent_summaries = _get_per_agent_summaries(
                groupchat,
                [agent.name for agent in agents],
                agents[0].llm_config
            )

        all_results.append(result)

        # 다음 페이즈를 위해 각 에이전트의 시스템 메시지 업데이트
        # (자신의 요약을 포함하도록)
        if i < len(phases) - 1:  # 마지막 페이즈가 아니면
            for agent in agents:
                agent_summary = per_agent_summaries.get(agent.name, "")
                if agent_summary:
                    # 시스템 메시지에 이전 페이즈 자신의 요약 추가
                    # 기존 시스템 메시지 유지하고 요약만 prefix로 추가
                    prev_context = (
                        f"=== 이전 페이즈 나의 기여 ===\n{agent_summary}\n==================\n\n"
                    )
                    # 시스템 메시지 업데이트
                    # 주의: 매 페이즈마다 누적되지 않도록, 기존 메시지에서 이전 페이즈 부분만 교체
                    current_sys_msg = agent.system_message
                    # 기존 "=== 이전 페이즈" 섹션 제거 후 새로 추가
                    if "=== 이전 페이즈 나의 기여 ===" in current_sys_msg:
                        # 이전 페이즈 섹션만 제거
                        start_idx = current_sys_msg.find("=== 이전 페이즈 나의 기여 ===")
                        end_idx = current_sys_msg.find("\n==================\n") + len("\n==================\n")
                        if start_idx >= 0 and end_idx > start_idx:
                            current_sys_msg = current_sys_msg[:start_idx] + current_sys_msg[end_idx:]
                    agent.update_system_message(prev_context + current_sys_msg)

    return all_results[-1]

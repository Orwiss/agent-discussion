import autogen
from autogen.agentchat.contrib.capabilities.transform_messages import TransformMessages
from autogen.agentchat.contrib.capabilities.transforms import MessageHistoryLimiter
from meeting.speaker import create_phase_speaker_selection, SPEAKER_SELECTION_PROMPT


class DedupGroupChat(autogen.GroupChat):
    """메시지 중복을 자동 필터링하는 GroupChat"""

    def append(self, message, speaker):
        """메시지 추가 시 최근 메시지와 중복이면 무시"""
        content = message.get("content", "") if isinstance(message, dict) else str(message)
        if content and self.messages:
            # 최근 3개 메시지와 앞 100자 비교
            for prev in self.messages[-3:]:
                prev_content = prev.get("content", "")
                if prev_content and content[:100] == prev_content[:100]:
                    return  # 중복 무시
        super().append(message, speaker)


def create_meeting_groupchat(agents, user, llm_config_selector, max_round=40):
    """단일 GroupChat + 페이즈 전환 방식 (StateFlow 패턴)"""

    # 페이즈별 speaker selection
    speaker_fn = create_phase_speaker_selection(agents, user)

    groupchat = DedupGroupChat(
        agents=[user] + agents,
        messages=[],
        max_round=max_round,
        send_introductions=True,
        speaker_selection_method=speaker_fn,
        select_speaker_prompt_template=SPEAKER_SELECTION_PROMPT,
        select_speaker_auto_llm_config=llm_config_selector,
        allow_repeat_speaker=True,
    )

    manager = autogen.GroupChatManager(
        groupchat=groupchat,
        llm_config=llm_config_selector,
        is_termination_msg=lambda msg: "MEETING_END" in msg.get("content", ""),
    )

    # TransformMessages: 토큰 절약 — 일단 40개로 넉넉하게 (품질 우선)
    transform = TransformMessages(
        transforms=[MessageHistoryLimiter(max_messages=40)]
    )
    for agent in agents:
        transform.add_to_agent(agent)

    return groupchat, manager


def _extract_constraints(brief):
    """브리프에서 제약 조건을 추출하여 강조 블록으로 반환"""
    import re
    constraints = []
    # "~ 안 함", "~ 제외", "~ 없음", "~ 금지" 등의 패턴
    patterns = [
        r'[^.。\n]*(?:안\s*함|하지\s*않|제외|없음|금지|불필요|논의\s*안|다루지\s*않)[^.。\n]*',
        r'[^.。\n]*(?:don\'t|no\s+need|exclude|skip)[^.。\n]*',
    ]
    for pat in patterns:
        matches = re.findall(pat, brief, re.IGNORECASE)
        constraints.extend(m.strip() for m in matches if len(m.strip()) > 3)
    return list(set(constraints))


def build_opening_message(meeting_type, brief):
    """회의 유형별 시작 메시지 — 브리프 제약을 눈에 띄게 표시"""

    # 브리프에서 제약 조건 추출
    constraints = _extract_constraints(brief)
    constraint_block = ""
    if constraints:
        items = "\n".join(f"  - {c}" for c in constraints)
        constraint_block = (
            f"\n\n★★★ 제약 조건 (반드시 준수) ★★★\n"
            f"{items}\n"
            f"위 제약을 어기는 발언은 하지 마세요.\n"
            f"★★★★★★★★★★★★★★★★★★★★★★★"
        )

    phase_guide = (
        "\n\n[페이즈 안내]\n"
        "이 회의는 하나의 연속된 대화로 진행됩니다.\n"
        "- 발산: 자유롭게 아이디어를 내세요\n"
        "- 심화: Synthesizer가 전환하면, 유망한 아이디어를 깊게 발전시키세요\n"
        "- 수렴: Synthesizer가 전환하면, 최종 정리를 합니다\n"
        "Synthesizer가 페이즈 전환을 관리합니다."
    )

    agent_rules = (
        "\n\n[에이전트 규칙]\n"
        "- 각 에이전트는 자기 전문 영역의 관점만 제시하세요\n"
        "- 다른 에이전트의 발언을 복사하거나 반복하지 마세요\n"
        "- [보드:] 태그와 PHASE_ADVANCE는 Synthesizer만 사용합니다\n"
        "- 사용자(Orwiss)의 피드백은 최우선으로 따르세요\n"
        "- 3~5문장 이내로 짧게 쓰세요"
    )

    if meeting_type == "개선형":
        opening = (
            f"=== 디자인 회의 시작 ===\n\n{brief}"
            f"{constraint_block}\n\n"
            "먼저 현재 디자인의 문제점을 찾아주세요. "
            "'목표 X에 비추어 Y가 문제다. 왜냐하면 Z' 형식으로."
        )
    elif meeting_type == "선택형":
        opening = (
            f"=== 디자인 회의 시작 ===\n\n{brief}"
            f"{constraint_block}\n\n"
            "각 후보에 대해 각자의 관점에서 평가해주세요. "
            "장점과 단점을 모두 찾으세요."
        )
    else:  # 발산형 (기본)
        opening = (
            f"=== 디자인 회의 시작 ===\n\n{brief}"
            f"{constraint_block}\n\n"
            "자유롭게 아이디어를 내주세요. 어떤 방향이든 환영합니다."
        )

    return opening + phase_guide + agent_rules


def run_stage2(agents, user, brief, meeting_type, llm_config_selector):
    """Stage 2 실행 — 단일 GroupChat"""
    groupchat, manager = create_meeting_groupchat(
        agents, user, llm_config_selector
    )

    opening = build_opening_message(meeting_type, brief)

    result = user.initiate_chat(
        manager,
        message=opening,
        summary_method="reflection_with_llm",
        summary_args={
            "summary_prompt": (
                "최종 회의록을 작성하세요:\n"
                "1. 논의된 주요 아이디어 (발전 과정 포함)\n"
                "2. 최종 선택지 A/B (각각 얻는 것, 잃는 것)\n"
                "3. 권고 (있다면)\n"
                "4. 미해결 이슈와 다음 단계"
            ),
        },
    )

    return result

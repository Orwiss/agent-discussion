from agents.moderator import create_moderator


def run_stage1(user, llm_config_main):
    """Stage 1: Moderator와 1:1로 주제 정제 → 회의 브리프 생성

    Returns:
        (result, moderator) — 결과 + Moderator 인스턴스 (Stage 2에서 재사용)
    """
    moderator = create_moderator(llm_config_main)

    result = user.initiate_chat(
        moderator,
        message="디자인 회의를 시작하려고 합니다.",
        max_turns=20,
        summary_method="reflection_with_llm",
        summary_args={
            "summary_prompt": (
                "위 대화에서 합의된 내용을 바탕으로 회의 브리프를 자연어로 정리하세요.\n"
                "반드시 첫 줄에 회의 유형을 명시하세요: [회의유형: 발산형] 또는 [회의유형: 개선형] 또는 [회의유형: 선택형]\n"
                "그 다음 디자인 목표, 현재 상태, 제약 조건, 피드백 범위, 참여자 기대를 "
                "자연스러운 문장으로 서술하세요."
            )
        },
    )

    return result, moderator

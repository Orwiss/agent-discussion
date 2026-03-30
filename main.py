import autogen
from config import (
    llm_config_main, llm_config_ux, llm_config_3d,
    llm_config_unreal, llm_config_inner, llm_config_inner_alt,
    llm_config_selector,
)
from agents.ux_researcher import create_ux_researcher
from agents.designer_3d import create_designer_3d
from agents.unreal_dev import create_unreal_dev
from agents.synthesizer import create_synthesizer, set_idea_board_ref, synthesizer_post_hook
from meeting.stage1 import run_stage1
from meeting.stage2 import run_stage2
from meeting.idea_board import create_idea_board
from meeting.guardrails import clean_message_hook, clean_history_hook, diversity_guard_hook, board_exclusion_hook, set_banned_keywords


def main():
    # === 에이전트 생성 (모델 이질성 적용) ===
    user = autogen.UserProxyAgent(
        name="Orwiss",
        human_input_mode="ALWAYS",
        code_execution_config=False,
        is_termination_msg=lambda msg: "TERMINATE" in msg.get("content", ""),
    )

    # === Stage 1: 사전 정제 ===
    print("=" * 60)
    print("Stage 1: 사전 정제 — Facilitator와 1:1 대화")
    print("=" * 60)

    stage1_result = run_stage1(user, llm_config_main)

    # 자연어 브리프에서 회의 유형 추출
    brief_text = stage1_result.summary or ""
    meeting_type = "발산형"
    for mt in ["발산형", "개선형", "선택형"]:
        if mt in brief_text:
            meeting_type = mt
            break

    print(f"\n[시스템] 회의 유형: {meeting_type}")
    print(f"[시스템] 브리프: {brief_text[:200]}...")

    # 브리프 확보 후 에이전트 생성
    ux_researcher = create_ux_researcher(llm_config_ux, llm_config_inner, brief=brief_text)
    designer_3d = create_designer_3d(llm_config_3d, llm_config_inner, brief=brief_text)
    unreal_dev = create_unreal_dev(llm_config_unreal, llm_config_inner, llm_config_inner_alt, brief=brief_text)
    synthesizer = create_synthesizer(llm_config_main)

    agents = [ux_researcher, designer_3d, unreal_dev, synthesizer]

    # 가드레일 등록 (반복 방지: 키워드 추적)
    # 브리프에서 금지 키워드 자동 추출
    import re
    ban_map = {
        "성능": ["드로우콜", "LOD", "폴리곤", "Nanite", "최적화", "FPS", "프레임"],
        "접근성": ["접근성", "색약", "저시력", "장애"],
        "애니메이션": ["애니메이션", "모션"],
        "동적": ["동적", "실시간 변화", "실시간으로 변"],
    }
    banned = []
    for key, keywords in ban_map.items():
        if re.search(rf'{key}\s*(논의\s*)?(안\s*함|제외|금지|불필요)', brief_text):
            banned.extend(keywords)
    if banned:
        set_banned_keywords(banned)

    for agent in agents:
        agent.register_hook("process_message_before_send", clean_message_hook)
        agent.register_hook("process_all_messages_before_reply", clean_history_hook)
    for agent in [ux_researcher, designer_3d, unreal_dev]:
        agent.register_hook("process_all_messages_before_reply", diversity_guard_hook)
        agent.register_hook("update_agent_state", board_exclusion_hook)
    synthesizer.register_hook("process_message_before_send", synthesizer_post_hook)

    # === 아이디어 보드 초기화 ===
    idea_board = create_idea_board(brief_text, meeting_type)
    set_idea_board_ref(idea_board)
    for agent in agents + [user]:
        agent.context_variables = idea_board

    # === Stage 2: 디자인 회의 (단일 GroupChat) ===
    print("\n" + "=" * 60)
    print(f"Stage 2: 디자인 회의 ({meeting_type})")
    print("=" * 60)

    result = run_stage2(
        agents=agents,
        user=user,
        brief=brief_text,
        meeting_type=meeting_type,
        llm_config_selector=llm_config_selector,
    )

    # === 최종 회의록 출력 ===
    print("\n" + "=" * 60)
    print("최종 회의록")
    print("=" * 60)
    print(result.summary if result.summary else "요약 없음")

    # 아이디어 보드 최종 상태
    print("\n" + "=" * 60)
    print("최종 아이디어 보드")
    print("=" * 60)
    print(idea_board.get("idea_board", "없음"))


if __name__ == "__main__":
    main()

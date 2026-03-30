"""Stage 2 단독 테스트 — A/B 모델 비교
사용법: python test_run.py [a|b]
  a = 현재 모델 (기본)
  b = 프리미엄 모델
"""
import sys
import autogen

USE_PREMIUM = len(sys.argv) > 1 and sys.argv[1].lower() == 'b'

if USE_PREMIUM:
    from config_premium import (
        llm_config_main, llm_config_ux, llm_config_3d,
        llm_config_unreal, llm_config_inner, llm_config_inner_alt,
        llm_config_selector,
    )
    print("[B안: 프리미엄 모델]")
else:
    from config import (
        llm_config_main, llm_config_ux, llm_config_3d,
        llm_config_unreal, llm_config_inner, llm_config_inner_alt,
        llm_config_selector,
    )
    print("[A안: 현재 모델]")
from agents.ux_researcher import create_ux_researcher
from agents.designer_3d import create_designer_3d
from agents.unreal_dev import create_unreal_dev
from agents.synthesizer import create_synthesizer, set_idea_board_ref, synthesizer_post_hook
from meeting.stage2 import run_stage2
from meeting.idea_board import create_idea_board
from meeting.guardrails import clean_message_hook, clean_history_hook, phase_aware_context_hook, board_exclusion_hook, set_banned_keywords


# 테스트용 브리프
BRIEF = """[회의유형: 발산형]
VR 공간에서 사용자를 안내하는 AI 에이전트의 전체 컨셉을 정하고 싶음.
사람형인지, 로봇인지, 추상적 존재인지부터 열어두고 시작.
언리얼 엔진 5 기반이고 한 달 안에 프로토타입 가능한 수준이면 좋겠음.
외형(형태, 비율, 색감, 질감, 스타일)에 집중. 기능이나 인터랙션보다는 '어떻게 생겼는지'.
성능 최적화 이야기는 안 해도 됨. 구현 방법은 마지막에 간단히만."""

MEETING_TYPE = "발산형"

BANNED = [
    "드로우콜", "LOD", "폴리곤", "Nanite", "최적화", "FPS", "프레임",
]


def main():
    # 금지 키워드 설정 (서버단 강제)
    set_banned_keywords(BANNED)

    user = autogen.UserProxyAgent(
        name="Orwiss",
        human_input_mode="NEVER",
        code_execution_config=False,
        max_consecutive_auto_reply=0,  # 유저 자동응답 없이 바로 다음 에이전트로
    )

    # 에이전트 생성
    ux = create_ux_researcher(llm_config_ux, llm_config_inner, brief=BRIEF)
    d3d = create_designer_3d(llm_config_3d, llm_config_inner, brief=BRIEF)
    unreal = create_unreal_dev(llm_config_unreal, llm_config_inner, llm_config_inner_alt, brief=BRIEF)
    synth = create_synthesizer(llm_config_main)
    agents = [ux, d3d, unreal, synth]

    # Hook 등록
    for a in agents:
        a.register_hook("process_message_before_send", clean_message_hook)
        a.register_hook("process_all_messages_before_reply", clean_history_hook)
    # creative agents: 페이즈별 컨텍스트 제어 + 보드 제외목록
    for a in [ux, d3d, unreal]:
        a.register_hook("process_all_messages_before_reply", phase_aware_context_hook)
        a.register_hook("update_agent_state", board_exclusion_hook)

    synth.register_hook("process_message_before_send", synthesizer_post_hook)

    idea_board = create_idea_board(BRIEF, MEETING_TYPE)
    set_idea_board_ref(idea_board)
    for a in agents + [user]:
        a.context_variables = idea_board

    print("=" * 60)
    print("테스트: Stage 2 단독 실행")
    print("=" * 60)

    result = run_stage2(
        agents=agents,
        user=user,
        brief=BRIEF,
        meeting_type=MEETING_TYPE,
        llm_config_selector=llm_config_selector,
    )

    print("\n" + "=" * 60)
    print("최종 회의록")
    print("=" * 60)
    print(result.summary if result.summary else "요약 없음")

    print("\n" + "=" * 60)
    print("아이디어 보드")
    print("=" * 60)
    print(idea_board.get("idea_board", "없음"))

    print("\n" + "=" * 60)
    print("페이즈 최종 상태:", idea_board.get("current_phase", "?"))
    print("=" * 60)


if __name__ == "__main__":
    main()

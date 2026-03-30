"""
A/B 비교 테스트
  A: SocietyOfMind + 프리미엄 모델
  B: 단순 에이전트 + 프리미엄 모델 (내부 회의 없음)

사용법: python test_compare.py [a|b|both] [vr_agent|game_lobby|app_mascot]
결과: logs/compare_{A|B}_{topic}_{timestamp}.txt
"""
import sys
import os
import re
import datetime
import autogen

from config_premium import (
    llm_config_main, llm_config_ux, llm_config_3d,
    llm_config_unreal, llm_config_inner, llm_config_inner_alt,
    llm_config_selector,
)
from agents.moderator import create_moderator, set_idea_board_ref, moderator_post_hook, transition_to_stage2
from meeting.stage2 import run_stage2
from meeting.idea_board import create_idea_board
from meeting.guardrails import (
    clean_message_hook, clean_history_hook, phase_aware_context_hook,
    board_exclusion_hook, set_banned_keywords,
)

TOPICS = {
    "vr_agent": {
        "brief": """[회의유형: 발산형]
VR 공간에서 사용자를 안내하는 AI 에이전트의 전체 컨셉을 정하고 싶음.
사람형인지, 로봇인지, 추상적 존재인지부터 열어두고 시작.
언리얼 엔진 5 기반이고 한 달 안에 프로토타입 가능한 수준이면 좋겠음.
외형에 집중 — 어떻게 생겼는지, 만지면 어떤 느낌인지, 어떤 분위기인지.
성능 최적화 이야기는 안 해도 됨. 구현 방법은 마지막에 간단히만.""",
        "banned": ["드로우콜", "LOD", "폴리곤", "Nanite", "최적화", "FPS"],
    },
    "game_lobby": {
        "brief": """[회의유형: 발산형]
멀티플레이 게임의 로비 공간을 디자인하고 싶음.
플레이어들이 대기하면서 자연스럽게 어울릴 수 있는 공간.
분위기, 구조, 시각 스타일을 정하고 싶음. 기능보다는 '어떤 느낌의 공간인지'.
장르는 아직 안 정했음. SF, 판타지, 현실 다 열어두고.
성능이나 기술 이야기는 나중에.""",
        "banned": ["드로우콜", "LOD", "폴리곤", "최적화", "FPS"],
    },
    "app_mascot": {
        "brief": """[회의유형: 발산형]
학습 앱의 마스코트 캐릭터를 만들고 싶음.
10대~20대 타겟. 공부하기 싫을 때 "그래도 한번 해볼까" 하게 만드는 존재.
귀엽기만 한 건 싫고, 약간 엣지 있으면서도 다가가기 쉬운.
3D가 아니라 2D일 수도 있음. 형태/스타일/분위기에 집중.""",
        "banned": [],
    },
    "improve_lobby": {
        "brief": """[회의유형: 개선형]
지금 우리 VR 게임 로비가 있는데 너무 밋밋함.
현재 상태: 회색 콘크리트 바닥에 네온사인 몇 개, 중앙에 둥근 테이블 하나.
플레이어들이 그냥 서서 대기만 하고 서로 안 어울림.
문제점을 찾고 어떻게 고치면 좋을지 논의하고 싶음.
기능적인 거보다는 '분위기'와 '공간감'에 집중.""",
        "banned": [],
    },
    "choose_character": {
        "brief": """[회의유형: 선택형]
AI 안내 에이전트 후보가 3개 있음:
A. 반투명 젤리 구체 — 둥글고 말랑한 느낌, 표면이 물결침
B. 떠다니는 로봇 팔 — 몸통 없이 팔 3개만, 기계적이지만 세련됨
C. 빛으로 만든 여우 — 동물형, 따뜻하지만 비현실적
각 후보의 장단점을 비교하고 어떤 게 가장 나을지 논의하고 싶음.""",
        "banned": [],
    },
}

# 기본 주제 또는 커맨드라인 지정
import sys
topic_key = "vr_agent"
for arg in sys.argv[1:]:
    if arg in TOPICS:
        topic_key = arg

topic = TOPICS[topic_key]
BRIEF = topic["brief"]
MEETING_TYPE = "발산형"
BANNED = topic["banned"]
LOG_DIR = os.path.join(os.path.dirname(__file__), "logs")
os.makedirs(LOG_DIR, exist_ok=True)


def run_variant(variant):
    """A 또는 B 변형을 실행하고 대화를 파일로 저장"""
    set_banned_keywords(BANNED)

    user = autogen.UserProxyAgent(
        name="Orwiss",
        human_input_mode="NEVER",
        code_execution_config=False,
        max_consecutive_auto_reply=0,
    )

    if variant == "a":
        # A: SocietyOfMind (내부 회의 있음)
        from agents.ux_researcher import create_ux_researcher
        from agents.designer_3d import create_designer_3d
        from agents.unreal_dev import create_unreal_dev
        ux = create_ux_researcher(llm_config_ux, llm_config_inner, brief=BRIEF)
        d3d = create_designer_3d(llm_config_3d, llm_config_inner, brief=BRIEF)
        unreal = create_unreal_dev(llm_config_unreal, llm_config_inner, llm_config_inner_alt, brief=BRIEF)
        label = "A: SocietyOfMind + 프리미엄"
    else:
        # B: 단순 에이전트 (내부 회의 없음)
        from agents.simple_agents import create_simple_ux, create_simple_3d, create_simple_unreal
        ux = create_simple_ux(llm_config_ux, brief=BRIEF)
        d3d = create_simple_3d(llm_config_3d, brief=BRIEF)
        unreal = create_simple_unreal(llm_config_unreal, brief=BRIEF)
        label = "B: 단순 에이전트 + 프리미엄"

    mod = create_moderator(llm_config_main)
    transition_to_stage2(mod, BRIEF)
    agents = [ux, d3d, unreal, mod]

    for a in agents:
        a.register_hook("process_message_before_send", clean_message_hook)
        a.register_hook("process_all_messages_before_reply", clean_history_hook)
    for a in [ux, d3d, unreal]:
        a.register_hook("process_all_messages_before_reply", phase_aware_context_hook)
        a.register_hook("update_agent_state", board_exclusion_hook)
    mod.register_hook("process_message_before_send", moderator_post_hook)

    idea_board = create_idea_board(BRIEF, MEETING_TYPE)
    set_idea_board_ref(idea_board)
    for a in agents + [user]:
        a.context_variables = idea_board

    print(f"\n{'='*50}")
    print(f" {label}")
    print(f"{'='*50}\n")

    result = run_stage2(
        agents=agents, user=user, brief=BRIEF,
        meeting_type=MEETING_TYPE, llm_config_selector=llm_config_selector,
    )

    # 대화 추출 및 정리
    inner_names = {
        '시나리오분석가', '인상평가자', '탈선유도자',
        '조형분석가', '시각레퍼런스전문가',
        '퍼포먼스분석가', '구현전문가', 'chat_manager',
    }
    display = {
        'UXResearcher': 'UX 리서처',
        'Designer3D': '3D 디자이너',
        'UnrealDev': '언리얼 개발자',
        'Moderator': '진행자',
        'Orwiss': '유저',
    }

    # GroupChat 메시지에서 메인 대화만 추출
    all_msgs = []
    if hasattr(result, 'chat_history'):
        all_msgs = result.chat_history

    # 파일로 저장
    timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    outpath = os.path.join(LOG_DIR, f"compare_{variant.upper()}_{topic_key}_{timestamp}.txt")

    with open(outpath, "w", encoding="utf-8") as f:
        f.write(f"{'='*50}\n")
        f.write(f" {label}\n")
        f.write(f" 시간: {timestamp}\n")
        f.write(f"{'='*50}\n\n")

        seen = set()
        turn = 0
        for msg in all_msgs:
            name = msg.get("name", "")
            content = msg.get("content", "")
            if not content or not name: continue
            if name in inner_names: continue

            # 정리
            clean = re.sub(r'<think>[\s\S]*?</think>', '', content).strip()
            clean = re.sub(r'\[직전:.*?\]', '', clean, flags=re.DOTALL).strip()
            if not clean or len(clean) < 20: continue
            if '디자인 회의 시작' in clean: continue

            # 중복 제거
            key = clean[:100]
            if key in seen: continue
            seen.add(key)

            turn += 1
            dn = display.get(name, name)
            f.write(f"--- [{turn}] {dn} ---\n")
            f.write(f"{clean[:400]}\n\n")

        f.write(f"\n{'='*50}\n")
        f.write(f" 총 {turn}개 발언\n")
        if result.summary:
            f.write(f"\n--- 회의록 요약 ---\n{result.summary[:500]}\n")
        f.write(f"{'='*50}\n")

    print(f"\n결과 저장: {outpath}")
    return outpath


if __name__ == "__main__":
    mode = "both"
    for arg in sys.argv[1:]:
        if arg in ("a", "b", "both"):
            mode = arg

    print(f"주제: {topic_key}")
    print(f"모드: {mode}")

    if mode == "a":
        run_variant("a")
    elif mode == "b":
        run_variant("b")
    else:
        print("\nA안 실행 중...")
        path_a = run_variant("a")
        from meeting.guardrails import _recent_fingerprints, _agent_recent_keywords
        _recent_fingerprints.clear()
        _agent_recent_keywords.clear()
        print("\nB안 실행 중...")
        path_b = run_variant("b")
        print(f"\n비교:\n  A: {path_a}\n  B: {path_b}")

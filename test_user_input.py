"""
유저 입력 시뮬레이션 테스트.
회의 중간에 유저가 방향을 바꾸거나 피드백을 주는 상황을 테스트.
"""
import sys
import os
import re
import datetime
import autogen

from config_premium import (
    llm_config_main, llm_config_ux, llm_config_3d,
    llm_config_unreal, llm_config_selector,
)
from agents.simple_agents import create_simple_ux, create_simple_3d, create_simple_unreal
from agents.moderator import create_moderator, set_idea_board_ref, moderator_post_hook, transition_to_stage2
from meeting.stage2 import run_stage2
from meeting.idea_board import create_idea_board
from meeting.guardrails import (
    clean_message_hook, clean_history_hook, phase_aware_context_hook,
    board_exclusion_hook, set_banned_keywords,
)

BRIEF = """[회의유형: 발산형]
VR 공간에서 사용자를 안내하는 AI 에이전트의 전체 컨셉을 정하고 싶음.
사람형인지, 로봇인지, 추상적 존재인지부터 열어두고 시작.
언리얼 엔진 5 기반이고 한 달 안에 프로토타입 가능한 수준이면 좋겠음.
외형에 집중 — 어떻게 생겼는지, 만지면 어떤 느낌인지, 어떤 분위기인지."""

BANNED = ["드로우콜", "LOD", "폴리곤", "Nanite", "최적화", "FPS"]

# 유저 개입 시나리오: 특정 턴에 미리 정한 메시지를 보냄
USER_INTERVENTIONS = {
    8:  "인간형은 빼고 생각해보면 어때? 좀 더 추상적인 방향으로",
    18: "지금 나온 것 중에 제일 마음에 드는 건 빛으로 된 존재인데, 좀 더 구체적으로 해볼 수 있을까?",
    28: "성능 이야기는 안 해도 돼. 느낌에 집중하자",
}

_msg_counter = {"count": 0}
_user_queue = list(USER_INTERVENTIONS.values())
_user_idx = {"i": 0}


def _user_injection_hook(messages):
    """process_all_messages_before_reply — 특정 메시지 수에 유저 발언 주입"""
    _msg_counter["count"] = len(messages)

    # 8, 18, 28번째 메시지 근처에서 유저 발언 주입
    thresholds = sorted(USER_INTERVENTIONS.keys())
    idx = _user_idx["i"]
    if idx < len(thresholds) and len(messages) >= thresholds[idx]:
        msg_text = _user_queue[idx]
        _user_idx["i"] += 1
        messages.append({
            "role": "user",
            "name": "Orwiss",
            "content": msg_text,
        })
        print(f"\n[유저 개입 @ msg#{len(messages)}] {msg_text}\n")

    return messages


def main():
    set_banned_keywords(BANNED)

    user = autogen.UserProxyAgent(
        name="Orwiss",
        human_input_mode="NEVER",
        code_execution_config=False,
        max_consecutive_auto_reply=0,
    )

    ux = create_simple_ux(llm_config_ux, brief=BRIEF)
    d3d = create_simple_3d(llm_config_3d, brief=BRIEF)
    unreal = create_simple_unreal(llm_config_unreal, brief=BRIEF)
    mod = create_moderator(llm_config_main)
    transition_to_stage2(mod, BRIEF)
    agents = [ux, d3d, unreal, mod]

    for a in agents:
        a.register_hook("process_message_before_send", clean_message_hook)
        a.register_hook("process_all_messages_before_reply", clean_history_hook)
        # 유저 발언 주입
        a.register_hook("process_all_messages_before_reply", _user_injection_hook)
    for a in [ux, d3d, unreal]:
        a.register_hook("process_all_messages_before_reply", phase_aware_context_hook)
        a.register_hook("update_agent_state", board_exclusion_hook)
    mod.register_hook("process_message_before_send", moderator_post_hook)

    idea_board = create_idea_board(BRIEF, "발산형")
    set_idea_board_ref(idea_board)
    for a in agents + [user]:
        a.context_variables = idea_board

    print("=" * 50)
    print(" 유저 입력 시뮬레이션 테스트")
    print(f" 개입 예정: 턴 {list(USER_INTERVENTIONS.keys())}")
    print("=" * 50)

    result = run_stage2(
        agents=agents, user=user, brief=BRIEF,
        meeting_type="발산형", llm_config_selector=llm_config_selector,
    )

    # 결과 저장
    LOG_DIR = os.path.join(os.path.dirname(__file__), "logs", "clean")
    os.makedirs(LOG_DIR, exist_ok=True)
    timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    outpath = os.path.join(LOG_DIR, f"user_input_test_{timestamp}.txt")

    display = {
        'UXResearcher': 'UX 리서처',
        'Designer3D': '3D 디자이너',
        'UnrealDev': '언리얼 개발자',
        'Moderator': '진행자',
        'Orwiss': '★ 유저',
    }

    with open(outpath, "w", encoding="utf-8") as f:
        f.write(f"{'='*40}\n 유저 입력 시뮬레이션 테스트\n{'='*40}\n\n")
        f.write(f"유저 개입:\n")
        for turn, msg in USER_INTERVENTIONS.items():
            f.write(f"  턴 {turn}: \"{msg}\"\n")
        f.write(f"\n{'='*40}\n\n")

        if hasattr(result, 'chat_history'):
            seen = set()
            count = 0
            for msg in result.chat_history:
                name = msg.get("name", "")
                content = msg.get("content", "")
                if not content or not name or len(content) < 10:
                    continue
                if name in {'시나리오분석가','인상평가자','탈선유도자','조형분석가','시각레퍼런스전문가','퍼포먼스분석가','구현전문가','chat_manager'}:
                    continue
                clean = re.sub(r'<think>[\s\S]*?</think>', '', content).strip()
                if not clean or '디자인 회의 시작' in clean:
                    continue
                key = clean[:100]
                if key in seen:
                    continue
                seen.add(key)
                count += 1
                dn = display.get(name, name)
                f.write(f"[{dn}]\n{clean[:400]}\n\n")

        f.write(f"{'='*40}\n 총 발언 수\n{'='*40}\n")

    print(f"\n결과: {outpath}")


if __name__ == "__main__":
    main()

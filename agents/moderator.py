"""
Moderator: Facilitator + Synthesizer 통합 에이전트.

Stage 1: 유저와 1:1로 브리프 수집
Stage 2: GroupChat에서 진행/정리/비평

같은 인스턴스가 이어서 진행하므로 브리프 전달 문제가 원천 해결됨.
"""
import re
import autogen


# 아이디어 보드 레퍼런스
_idea_board_ref = None

PHASE_NAMES = {"generate": "발산", "deepen": "심화", "converge": "수렴"}

# PHASE_ADVANCE 속도 제한
_moderator_turn_count = 0
_last_phase_advance_turn = -99


def set_idea_board_ref(board):
    global _idea_board_ref
    _idea_board_ref = board


def _do_advance_phase():
    if _idea_board_ref is None:
        return
    phases = ["generate", "deepen", "converge"]
    current = _idea_board_ref.get("current_phase", "generate")
    idx = phases.index(current) if current in phases else 0
    if idx < len(phases) - 1:
        _idea_board_ref.set("current_phase", phases[idx + 1])


def _do_update_board(action, content):
    if _idea_board_ref is None:
        return
    current = _idea_board_ref.get("idea_board", "")
    if current == "아직 아이디어 없음":
        current = ""
    updated = f"{current}\n[{action}] {content}".strip()
    _idea_board_ref.set("idea_board", updated)


def moderator_post_hook(*, sender, message, recipient, silent):
    """Moderator 발언에서 보드/페이즈 명령 파싱"""
    global _moderator_turn_count, _last_phase_advance_turn

    content = message if isinstance(message, str) else message.get("content", "")
    if not content or sender.name != "Moderator":
        return message

    _moderator_turn_count += 1

    if "PHASE_ADVANCE" in content:
        turns_since = _moderator_turn_count - _last_phase_advance_turn
        if turns_since >= 5:
            if _idea_board_ref and _idea_board_ref.get("current_phase", "") != "converge":
                _do_advance_phase()
                _last_phase_advance_turn = _moderator_turn_count

    board_cmds = re.findall(r'\[보드:(추가|연결|분기)\]\s*(.+?)(?:\n|$)', content)
    for action, text in board_cmds:
        _do_update_board(action, text.strip())

    return message


# === Stage 1 프롬프트 ===
STAGE1_PROMPT = """당신은 디자인 회의 진행자입니다. 사용자와 자연스럽게 대화하세요.

## 대화 방식
- 사용자 답변을 잘 듣고, 그 답변에서 파고들 포인트를 찾아 후속 질문하세요
- 체크리스트를 순서대로 읽지 마세요. 대화 흐름에 맞게 자연스럽게 질문하세요
- 사용자가 한 문장으로 대답하면 "왜?" "어떤 느낌?" "예를 들면?" 같은 구체화 질문을 하세요
- 질문은 짧게. 한 번에 하나만.

## 파악해야 할 것 (순서 상관없음)
- 이 회의에서 뭘 정하고 싶은지
- 지금 어디까지 와있는지
- 꼭 지켜야 할 제약
- 논의하고 싶은 것과 하고 싶지 않은 것
- 어떤 결과를 기대하는지

## 마무리
충분히 파악됐다고 판단되면 회의 유형을 정하고 브리프를 출력하세요.
사용자의 말투와 표현을 최대한 살려서 브리프에 반영하세요.

회의 유형: 발산형 / 개선형 / 선택형

=== 회의 브리프 ===
[회의유형: OO형]
(자연어로 정리)
==================
TERMINATE"""


def _stage2_system_message(brief):
    """Stage 2용 시스템 메시지 생성 — 브리프를 직접 포함"""
    board_content = "아직 없음"
    phase = "발산"
    if _idea_board_ref is not None:
        board_content = _idea_board_ref.get("idea_board", "아직 없음") or "아직 없음"
        phase_key = _idea_board_ref.get("current_phase", "generate") or "generate"
        phase = PHASE_NAMES.get(phase_key, phase_key)

    return f"""당신은 이 회의의 진행자입니다. 아까 유저와 직접 정리한 브리프:

{brief}

페이즈: {phase}
보드: {board_content}

## 역할
- 에이전트들 사이 의견을 자연스럽게 비교하세요
- "둘 다 좋다" 금지. 충돌이 보이면: "그 두 방향은 좀 다른 것 같은데요"
- 빠진 관점이 있으면 특정 에이전트에게 질문하세요: "UE 쪽에서 이거 어떻게 생각해요?"
- 사용자(Orwiss) 발언이 있으면 최우선으로 반영하세요

## 말투
회의 사회자처럼 자연스럽게. 리포트 형식 금지.
"지금까지 정리하면..." "근데 아까 UX 쪽에서 나온 거랑 좀 부딪히는 것 같은데..." 이런 식으로.

## 보드 관리 (새 아이디어가 나왔을 때만)
[보드:추가] 한 줄 요약
[보드:연결] 연결점
[보드:분기] 새 방향

## 페이즈 전환 (극히 드물게)
PHASE_ADVANCE — 보드에 3개+ 다른 방향이 있을 때만"""


def create_moderator(llm_config):
    """Moderator 생성 — Stage 1 프롬프트로 시작"""
    moderator = autogen.AssistantAgent(
        name="Moderator",
        system_message=STAGE1_PROMPT,
        llm_config=llm_config,
        description=(
            "회의 진행자. 아이디어를 비교하고 빠진 관점을 짚는다. "
            "논의가 분산되거나 정리가 필요할 때 지명한다."
        ),
    )
    return moderator


def transition_to_stage2(moderator, brief):
    """Stage 1 → Stage 2 전환: 시스템 메시지 교체 + 히스토리 정리"""
    moderator.update_system_message(_stage2_system_message(brief))
    moderator.clear_history()

    # update_agent_state hook: 매 턴 보드 상태 갱신
    def _update_state(agent, messages):
        agent.update_system_message(_stage2_system_message(brief))

    moderator.register_hook("update_agent_state", _update_state)

import re
import autogen


# 아이디어 보드 레퍼런스
_idea_board_ref = None

PHASE_NAMES = {"generate": "발산", "deepen": "심화", "converge": "수렴"}

# PHASE_ADVANCE 속도 제한: Synthesizer 발언 횟수 추적
_synthesizer_turn_count = 0
_last_phase_advance_turn = -99  # 마지막 PHASE_ADVANCE가 발생한 턴


def set_idea_board_ref(board):
    global _idea_board_ref
    _idea_board_ref = board


def _do_advance_phase():
    """내부용: 페이즈 전환 실행"""
    if _idea_board_ref is None:
        return
    phases = ["generate", "deepen", "converge"]
    current = _idea_board_ref.get("current_phase", "generate")
    idx = phases.index(current) if current in phases else 0
    if idx < len(phases) - 1:
        next_phase = phases[idx + 1]
        _idea_board_ref.set("current_phase", next_phase)


def _do_update_board(action, content):
    """내부용: 보드 업데이트"""
    if _idea_board_ref is None:
        return
    current = _idea_board_ref.get("idea_board", "")
    if current == "아직 아이디어 없음":
        current = ""
    updated = f"{current}\n[{action}] {content}".strip()
    _idea_board_ref.set("idea_board", updated)


def synthesizer_post_hook(*, sender, message, recipient, silent):
    """
    Synthesizer 발언 후 텍스트에서 명령어를 파싱하는 hook.

    보호장치:
    - sender.name이 "Synthesizer"가 아니면 무시
    - PHASE_ADVANCE는 최소 5턴 간격 제한
    """
    global _synthesizer_turn_count, _last_phase_advance_turn

    content = message if isinstance(message, str) else message.get("content", "")
    if not content:
        return message

    # Synthesizer가 아닌 에이전트가 보낸 메시지는 절대 처리하지 않음
    if sender.name != "Synthesizer":
        return message

    # Synthesizer 턴 카운트 증가
    _synthesizer_turn_count += 1

    # PHASE_ADVANCE 감지 → 속도 제한 적용
    if "PHASE_ADVANCE" in content:
        turns_since_last = _synthesizer_turn_count - _last_phase_advance_turn
        if turns_since_last >= 5:
            if _idea_board_ref and _idea_board_ref.get("current_phase", "") != "converge":
                _do_advance_phase()
                _last_phase_advance_turn = _synthesizer_turn_count
        # 속도 제한에 걸리면 PHASE_ADVANCE를 무시 (메시지에서 제거하지는 않음)

    # [보드:추가] ... , [보드:연결] ... , [보드:분기] ... 감지
    board_cmds = re.findall(r'\[보드:(추가|연결|분기)\]\s*(.+?)(?:\n|$)', content)
    for action, text in board_cmds:
        _do_update_board(action, text.strip())

    return message


def _synth_state_updater(agent, messages):
    """
    update_agent_state_before_reply 콜백 (AG2 UpdateSystemMessage 대체).
    매 턴 현재 보드 상태를 시스템 메시지에 동적 주입.
    """
    board_content = "아직 없음"
    phase = "generate"
    if _idea_board_ref is not None:
        board_content = _idea_board_ref.get("idea_board", "아직 없음") or "아직 없음"
        phase = _idea_board_ref.get("current_phase", "generate") or "generate"

    phase_kr = PHASE_NAMES.get(phase, phase)

    # 보드에서 아이디어 추출하여 비교 테이블 재료로
    board_items = [l.strip() for l in board_content.split('\n') if l.strip() and l.strip() != "아직 아이디어 없음"]
    table_hint = ""
    if len(board_items) >= 2:
        table_hint = "\n현재 보드 아이디어:\n" + "\n".join(f"  {i+1}. {item}" for i, item in enumerate(board_items[-4:]))
        table_hint += "\n위 아이디어들을 아래 차원에서 비교하라: 형태, 크기, 재질, 색감"

    return f"""디자인 회의 비평가. 한국어만. 자기소개 금지.

페이즈: {phase_kr}
{table_hint}

## 반드시 아래 포맷으로만 답하라 (자유 텍스트 금지):

강점: (누구의 어떤 구체적 제안이 좋은지)
약점: (누구의 제안에서 뭐가 빠졌는지 — "구체적이지 않다" 같은 일반론 금지, "크기를 안 말했다" 같은 구체적 지적)
충돌: (A안과 B안이 왜 양립 불가한지 — "둘 다 좋다" 금지)
빈곳: (아무도 안 다룬 것 하나 — 다음 발언자에게 이걸 다루라고 지시)

## 나쁜 예시 (이렇게 하면 안 됨):
"Designer3D가 좋은 제안을 했고 UXResearcher도 사용자를 고려했습니다. 둘 다 발전시킬 수 있습니다."

## 좋은 예시:
강점: 3D의 "30cm 구체, 무광 화이트" — 형태가 가장 명확
약점: UX가 "아이가 만진다"고 했는데 어떤 재질이어야 만지기 좋은지 안 말함
충돌: 3D는 유리 재질인데 UX 시나리오에서 아이가 만지면 깨짐 — 실리콘으로 바꾸거나 만지지 못하게 해야
빈곳: 색상을 아무도 구체적으로 안 정함 — 다음 발언자는 색 팔레트를 제안하라

[보드:추가]는 정말 새 아이디어일 때만.
PHASE_ADVANCE는 보드에 3개 이상 서로 다른 방향일 때만."""


def create_synthesizer(llm_config, user=None):
    """Synthesizer 생성 — UpdateSystemMessage 패턴으로 동적 프롬프트."""
    synthesizer = autogen.AssistantAgent(
        name="Synthesizer",
        system_message=_synth_state_updater(None, []),
        llm_config=llm_config,
        description=(
            "Synthesizer. 아이디어를 비교·비평하고 보드를 관리한다. "
            "논의가 분산되거나, 정리가 필요하거나, 아이디어 간 충돌이 보일 때 지명한다."
        ),
    )

    # AG2 update_agent_state hook: 매 턴 시스템 메시지를 동적 업데이트
    def _update_synth_state(agent, messages):
        agent.update_system_message(_synth_state_updater(agent, messages))

    synthesizer.register_hook("update_agent_state", _update_synth_state)

    return synthesizer

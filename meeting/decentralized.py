"""Decentralized 조건 — round-robin peer-to-peer.

세 에이전트가 동등하게 A→B→C→A→B→C→User→... 순서로 발언.
3 phase × N 사이클, phase당 디자이너 개입 2회 (6발화마다).
"""
import autogen
from autogen.agentchat.contrib.capabilities.transform_messages import TransformMessages
from autogen.agentchat.contrib.capabilities.transforms import MessageHistoryLimiter

from meeting.common import (
    inject_phase_prefix,
    build_opening_message,
    collapse_blank_lines,
    _log,
)


def _is_current_session(token) -> bool:
    """token이 지금 활성 세션 것인지 확인 (web.py 순환 임포트 피하려고 지연 임포트)."""
    try:
        from web import is_current_session
        return is_current_session(token)
    except Exception:
        return True


def _make_summary_method(token):
    """AG2 기본 reflection_with_llm 요약을 그대로 쓰되, stale 세션이면 그 요약용 LLM 호출조차
    생략하고 빈 문자열을 반환한다 — is_termination_msg로 대화는 끊겨도 요약은 여전히 한 번
    더 나가던 걸 마저 막는다."""
    def _summary_method(sender, recipient, summary_args):
        if token is not None and not _is_current_session(token):
            return ""
        return autogen.ConversableAgent._reflection_with_llm_as_summary(sender, recipient, summary_args)
    return _summary_method


def _collapse_hook(*, sender, message, recipient, silent):
    """에이전트 발화의 빈 줄·줄바꿈을 한 문단으로 합친다 (내용 보존)."""
    content = message if isinstance(message, str) else message.get("content", "")
    if not content:
        return message
    cleaned = collapse_blank_lines(content)
    if isinstance(message, str):
        return cleaned
    message["content"] = cleaned
    return message

# 페이즈별 라운드 예산
# 라운드 4 (D-E-PM × 4 = 12 에이전트 발화) + user 차례 (6 발화마다 = 라운드 2 끝)
# 발산·심화: user 2번. opening 1 + 6 agent + user + 6 agent + user = 15
# 수렴: user 1번. opening 1 + 6 agent + user + 6 agent = 14
#
# 주의: 이 숫자 자체는 항상 맞았다. phase 전환 시 실제로 적용되던 값이 이 딕셔너리가
# 아니라 GroupChatManager 생성 시점(divergence)에 얼려진 max_round=15였던 게 진짜
# 버그였다 — register_reply()가 config를 copy.copy()로 얼려서 저장해서 groupchat.max_round
# 재대입이 run_chat엔 반영이 안 됐다(_run_chat_config() 참고, 실측으로 확인). 그 얼려진
# 사본을 직접 갱신하도록 고친 뒤에는 이 딕셔너리 값 그대로가 맞다.
PHASE_MAX_ROUNDS = {
    "divergence": 15,
    "elaboration": 15,
    "convergence": 14,
}


def confirm_convergence(iostream):
    """수렴 페이즈 종료 직전 사용자에게 추가 의견을 묻는다.
    빈 입력이면 None, 비어 있지 않으면 입력 문자열 그대로."""
    iostream.print(
        "[시스템] 마지막으로 추가하실 의견이 있으면 입력해주세요. (빈칸이면 다음으로 진행)"
    )
    raw = iostream.input("최종 의견: ")
    text = (raw or "").strip()
    return text if text else None


def _create_6turn_speaker_selection(agents, user):
    """6발화마다 유저 개입하는 speaker selection.
    A→B→C→A→B→C→User→A→B→C→A→B→C→User→..."""
    state = {"agent_count": 0, "total": 0}

    def select_speaker(last_speaker, groupchat):
        state["total"] += 1
        last_name = last_speaker.name if last_speaker else "None"

        if last_speaker == user:
            state["agent_count"] = 0
            next_agent = agents[0]
            _log("speaker", f"턴{state['total']}: {last_name} → {next_agent.name} (유저 후 리셋)")
            return next_agent

        state["agent_count"] += 1
        if state["agent_count"] >= 6:
            _log("speaker", f"턴{state['total']}: {last_name} → Participant (6발화 도달)")
            return user

        idx = state["agent_count"] % len(agents)
        next_agent = agents[idx]
        _log("speaker", f"턴{state['total']}: {last_name} → {next_agent.name} ({state['agent_count']}/6)")
        return next_agent

    return select_speaker


def _create_phase_groupchat(agents, user, phase, token=None):
    """한 페이즈용 GroupChat 생성. phase별 max_round 적용."""
    speaker_fn = _create_6turn_speaker_selection(agents, user)
    max_round = PHASE_MAX_ROUNDS.get(phase, 15)

    groupchat = autogen.GroupChat(
        agents=[user] + agents,
        messages=[],
        max_round=max_round,
        send_introductions=True,
        speaker_selection_method=speaker_fn,
    )

    def _is_termination_msg(msg):
        if "MEETING_END" in msg.get("content", ""):
            return True
        # 새 세션이 시작돼 이 세션이 버려졌으면(예: "처음으로" 버튼) 매 턴마다 감지해서 즉시 중단 —
        # 안 하면 아무도 안 보는 화면 뒤에서 API 호출이 계속 나가며 비용만 쌓인다.
        if token is not None and not _is_current_session(token):
            return True
        return False

    manager = autogen.GroupChatManager(
        groupchat=groupchat,
        llm_config=agents[0].llm_config,
        is_termination_msg=_is_termination_msg,
    )

    # 세션 전체(3phase 연속)가 대략 42개 항목이라 50이면 리셋 없이도 안 잘리고 다 들어간다.
    transform = TransformMessages(
        transforms=[MessageHistoryLimiter(max_messages=50)]
    )
    for agent in agents:
        transform.add_to_agent(agent)

    return groupchat, manager


def _run_chat_config(manager):
    """GroupChatManager.__init__이 run_chat을 등록할 때 config로 넘긴 groupchat은
    register_reply()가 copy.copy()로 얼려서 저장한 별도 객체다(manager._groupchat과
    다른 인스턴스) — run_chat이 실제로 읽는 max_round는 이 얼려진 사본 쪽이라,
    groupchat.max_round = ... 로 원본을 바꿔도 반영되지 않는다(실측으로 확인된 버그).
    이 함수는 그 얼려진 사본을 찾아 반환한다 — phase 전환 시 max_round를 갱신하려면
    반드시 이 객체에 대입해야 한다."""
    for entry in manager._reply_func_list:
        if entry["reply_func"] == autogen.GroupChatManager.run_chat:
            return entry["config"]
    raise RuntimeError("GroupChatManager.run_chat용 등록된 config를 찾지 못했습니다.")


def run_decentralized_discussion(agents, user, brief, iostream=None, token=None):
    """Decentralized 토론 실행 — 세션 전체를 하나의 GroupChat으로 이어가며 phase만 전환.

    GroupChat/GroupChatManager는 발산 시작 때 딱 한 번만 만든다. 심화·수렴 전환은
    새 대화를 여는 게 아니라, AG2의 GroupChatManager.resume()으로 지금까지의 전체
    대화를 각 에이전트의 실제 기억(_oai_messages)에 다시 채워넣은 뒤 그대로 이어간다
    — phase 요약을 만들어 새로 여는 대신, 진짜 이전 대화를 기억하게 하는 것."""
    phases = ["divergence", "elaboration", "convergence"]

    # 발화의 빈 줄을 한 문단으로 합치는 hook을 에이전트마다 1회 등록
    for agent in agents:
        agent.register_hook("process_message_before_send", _collapse_hook)

    groupchat = None
    manager = None
    result = None
    summary_args = {
        "summary_prompt": (
            "지금까지 논의된 내용을 간결하게 요약하세요:\n"
            "1. 제안된 주요 아이디어\n"
            "2. 합의된 방향\n"
            "3. 미해결 이슈"
        ),
    }

    for i, phase in enumerate(phases):
        if token is not None and not _is_current_session(token):
            _log("decentralized_stale_session_stop", {"token": token, "at": "phase_start"})
            return None

        for agent in agents:
            inject_phase_prefix(agent, phase, brief)

        _log("phase_start", {"phase": phase, "condition": "decentralized"})

        if groupchat is None:
            # 세션 전체에서 GroupChat을 여는 유일한 지점
            opening = build_opening_message(brief, phase)
            groupchat, manager = _create_phase_groupchat(agents, user, phase, token=token)
            result = user.initiate_chat(
                manager,
                message=opening,
                summary_method=_make_summary_method(token),
                summary_args=summary_args,
            )
        else:
            # phase 전환 — 라운드 예산만 갱신하고, 지금까지의 전체 대화를 resume()으로
            # 재생해서 이어간다. resume()이 send_introductions도 자동으로 꺼준다.
            # run_chat이 실제로 읽는 건 GroupChatManager 생성 시 얼려진 config 사본이므로
            # (groupchat이 아니라) 그 사본에 직접 대입해야 한다 — _run_chat_config() 참고.
            groupchat.max_round = PHASE_MAX_ROUNDS.get(phase, 15)
            _run_chat_config(manager).max_round = PHASE_MAX_ROUNDS.get(phase, 15)
            last_speaker, last_message = manager.resume(messages=groupchat.messages)
            # last_message는 직전 phase에서 이미 한 번 로깅된 발화다. 여기서
            # manager와의 대화를 다시 여는 데 필요할 뿐 새 발화가 아니므로
            # silent=True로 넘겨야 한다 — 안 그러면 initiate_chat 내부의
            # self.send(..., silent=False 기본값)가 캡처 hook을 다시 태워
            # 같은 발화가 새 phase 로그에 한 번 더 찍힌다(실측으로 확인된 버그).
            result = last_speaker.initiate_chat(
                manager,
                message=last_message,
                clear_history=False,
                silent=True,
                summary_method=_make_summary_method(token),
                summary_args=summary_args,
            )

    # 세션 마지막에 사용자가 말했고 아무도 안 받았으면 그 발언을 결과에 반영
    trailing_user = ""
    for m in reversed(groupchat.messages):
        nm = m.get("name")
        if nm and nm != user.name:
            break  # 에이전트가 마지막 → 이미 응답됨
        if nm == user.name:
            trailing_user = m.get("content", "") or ""
            break

    return type("Result", (), {
        "summary": result.summary if result and result.summary else "",
        "trailing_user": trailing_user,
    })()

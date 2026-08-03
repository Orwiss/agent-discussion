"""Centralized 조건 — PM이 D, E를 각각 sub-chat으로 호출 + 직접 흐름 제어.

AG2의 a_initiate_chats(병렬 sub-chat)를 라운드 단위로 호출. 단 a_initiate_chat(전체 outer chat)은 우회.
phase별 라운드 4 + user 차례 N번 등간격 (발산·심화 N=2, 수렴 N=1).

흐름 (한 라운드):
  PM LLM 호출 → 라우팅 발화 (화면 push)
  ↓
  asyncio.gather로 D, E sub-chat 병렬 (silent=True, 화면엔 _push_to_ui로 직접)
  ↓
  PM LLM 호출 → 종합 발화 (outer messages·화면 push)
  ↓
  (라운드 2의 배수 끝마다) user 차례 — 사용자 입력 또는 빈 Enter

학술 인용:
  - AutoGen (Wu et al., ICLR 2024) — a_initiate_chats sub-chat 패턴
  - Centralized star topology — Tran et al. (2025) survey
  - PM 행동 베이스 — Mumford et al. (2002) Leading People + PosterMate (Shin et al., UIST 2025)
"""
import asyncio
import json
import traceback
import autogen
from autogen.agentchat.chat import a_initiate_chats
from autogen.io import IOStream

from meeting.common import inject_phase_prefix, build_opening_message, _log, collapse_blank_lines


# phase별 라운드 수와 user 차례 횟수
ROUNDS_PER_PHASE = 4
USER_TURNS = {"divergence": 2, "elaboration": 2, "convergence": 1}

# PM 라우팅·종합 호출에 넘기는 공식 기록(messages)의 슬라이딩 윈도우 크기.
# 세션 전체(3phase 연속)가 대략 42개 항목이라 50이면 리셋 없이도 안 잘리고 다 들어간다.
HISTORY_WINDOW = 50


def _is_current_session(token) -> bool:
    """token이 지금 활성 세션 것인지 확인 (web.py 순환 임포트 피하려고 지연 임포트)."""
    try:
        from web import is_current_session
        return is_current_session(token)
    except Exception:
        return True


def _push_to_ui(sender: str, content: str, recipient: str = "PM", summary: str = "", token=None) -> None:
    """현재 IOStream(웹소켓)에 sender 이름으로 발화 push.
    summary가 있으면 접힘 미리보기용으로 함께 전송.
    token이 지금 활성 세션 것과 다르면(이전 세션이 뒤늦게 쓰는 경우) 조용히 버린다."""
    if not content or not content.strip():
        return
    try:
        from web import is_current_session
        if token is not None and not is_current_session(token):
            return
        iostream = IOStream.get_default()
        payload = {"sender": sender, "recipient": recipient, "content": content}
        if summary:
            payload["summary"] = summary
        if hasattr(iostream, "send_text"):
            iostream.send_text(
                sender,
                content,
                recipient=recipient,
                summary=summary,
            )
            return
        ws = getattr(iostream, "_websocket", None)
        if ws is None:
            _log("push_to_ui_fail", {"sender": sender, "reason": "no_supported_transport"})
            return
        ws.send(json.dumps({"type": "text", "content": payload}))
    except Exception as e:
        _log("push_to_ui_fail", {"sender": sender, "error": str(e), "type": type(e).__name__})


def _log_msg(speaker: str, content: str, token=None) -> None:
    """messages.jsonl + messages.csv 누적 (a_initiate_chat 우회라 hook이 안 잡으니 직접).
    token이 지금 활성 세션 것과 다르면(이전 세션이 뒤늦게 쓰는 경우) 조용히 버린다."""
    if not content or not content.strip():
        return
    try:
        from web import log_message, _append_messages_csv, is_current_session
        if token is not None and not is_current_session(token):
            return
        log_message(speaker, content)
        _append_messages_csv(speaker, content)
    except Exception:
        pass


def _extract_content(reply) -> str:
    if reply is None:
        return ""
    if isinstance(reply, dict):
        return (reply.get("content") or "").strip()
    return str(reply).strip()


async def _pm_reply(pm, msgs, user) -> str:
    """PM 응답 생성 (단일 패스).
    truncation 재시도는 제거됨 — 근본원인(추론 토큰이 본문을 잘라먹던 것)이
    config의 reasoning:{max_tokens:0}으로 이미 차단되어, 재시도는 43세션 중 3회만
    발동했고 그마저 실제 truncation이 아니라 짧은 서두 조각이었음."""
    ok, msg = await pm.a_generate_oai_reply(messages=msgs, sender=user)
    return collapse_blank_lines(_extract_content(msg)) if ok else ""


def _get_summarizer(pm):
    """접힘 미리보기용 요약기.

    PM 인스턴스마다 하나씩 둬서 동시에 실행되는 참가자 세션의 대화 기록과
    토큰 사용량이 섞이지 않게 한다.
    """
    summarizer = getattr(pm, "_experiment_summarizer", None)
    if summarizer is None:
        summarizer = autogen.AssistantAgent(
            name="Summarizer",
            system_message=(
                "당신은 한 줄 요약기입니다. 주어진 발언의 핵심 아이디어를 "
                "구체적인 내용이 드러나게 한 줄(50자 내외, 문장부호 없이)로 요약하세요. "
                "너무 압축해서 무슨 내용인지 안 보이게 만들지 마세요. 다만 한 줄을 넘길 정도로 길게는 쓰지 마세요. 다른 말 금지."
            ),
            llm_config=pm.llm_config,
        )
        # PM 생성 시점에 web.py에서 붙여준 세션 태그를 요약기에도 그대로 물려준다 —
        # 요약기는 PM보다 늦게(첫 호출 때) 생기므로 web.py의 일괄 태깅을 못 받는다.
        summarizer.session = getattr(pm, "session", None)
        pm._experiment_summarizer = summarizer
    return summarizer


def reset_summarizer_usage(pm=None) -> None:
    """해당 세션 PM에 연결된 요약기의 누적 사용량만 초기화."""
    summarizer = getattr(pm, "_experiment_summarizer", None) if pm is not None else None
    if summarizer is not None and getattr(summarizer, "client", None):
        summarizer.client.clear_usage_summary()


def get_extra_usage_agents(pm=None) -> list:
    """토큰 usage 집계에 pm/designer/engineer 말고 추가로 포함해야 할 에이전트
    (요약기처럼 세션 agents 리스트엔 없지만 토큰을 쓰는 것들)."""
    summarizer = getattr(pm, "_experiment_summarizer", None) if pm is not None else None
    return [summarizer] if summarizer is not None else []


async def _summarize(pm, text: str) -> str:
    """발화를 한 줄 요약 (접힘 미리보기용). 이미 짧으면 그대로 반환.
    응답이 15초 안에 안 오면(hang 대비) 한 번 더 시도하고, 그래도 안 되면 원문 앞부분으로 대체."""
    t = (text or "").strip()
    if len(t) < 30:
        return t
    agent = _get_summarizer(pm)
    for attempt in range(2):
        try:
            ok, msg = await asyncio.wait_for(
                agent.a_generate_oai_reply(messages=[{"role": "user", "content": t}], sender=pm),
                timeout=15,
            )
            s = collapse_blank_lines(_extract_content(msg)) if ok else ""
            s = s.strip().strip("\"'.").strip()
            if s:
                return s
        except Exception as e:
            _log("summarize_retry", {"attempt": attempt, "error": str(e), "type": type(e).__name__})
    return t[:20]


def _make_designer_trigger(question: str) -> str:
    return (
        f"PM이 이렇게 물었습니다: {question}\n"
        "질문에 '디자이너'라고 불리는 부분이 있으면 그게 당신입니다. "
        "자기 영역에서 핵심 하나로, 2~3문장으로 답하세요. 확신이 약한 부분은 솔직히 밝히세요."
    )


def _make_engineer_trigger(question: str) -> str:
    return (
        f"PM이 이렇게 물었습니다: {question}\n"
        "질문에 '엔지니어'라고 불리는 부분이 있으면 그게 당신입니다. "
        "자기 영역에서 핵심 하나로, 2~3문장으로 답하세요. 확신이 약한 부분은 솔직히 밝히세요."
    )


def _make_routing_prompt(is_first_round: bool, phase: str) -> str:
    name_call = (
        "어떻게 구현할지보다 어떤 것이 가능할지 질문하세요. 각자 자기 영역에서 답하는 건 "
        "디자이너와 엔지니어가 알아서 할 일입니다. 질문할 때는 디자이너와 엔지니어를 각각 이름으로 "
        "불러 누구에게 무엇을 묻는지 구분되게 하고, 한번에 너무 많은 내용을 질문하지 마세요."
    )
    if is_first_round:
        return (
            "반드시 지금 단계의 목적에 부합하는 업무를 디자이너와 엔지니어에게 질문을 통해 위임하세요. "
            f"{name_call} "
            "'디자이너는 ~라고 했다'처럼 상대가 낸 아이디어를 이름 붙여 전달하지 말고, "
            "지금 논의가 어느 방향으로 가고 있는지 주제만 전하세요."
        )
    if phase == "divergence":
        attitude = "두 사람의 답을 나란히 놓고 봤을 때 아직 언급되지 않은 다른 방향이 있는지 살핀 뒤"
    else:
        attitude = (
            "두 사람의 답변을 종합적으로 검토해서 각자의 생각이 혼자서는 어떤 부분이 부족한지, "
            "두 답이 서로 어디서 충돌하는지 차근차근 따져보세요. 한두 문장으로 부족한 지점과 충돌하는 지점을 충분히 살핀 끝에"
        )
    return (
        f"방금 정리한 내용을 다시 설명하지 말고, {attitude} "
        f"자연스럽게 다음 질문이 따라 나오게 하세요. 이때 {name_call} "
        "반드시 지금 단계의 목적에 부합하는 업무를 디자이너와 엔지니어에게 질문을 "
        "통해 위임하세요. '디자이너는 ~라고 했다'처럼 상대가 낸 아이디어를 이름 붙여 전달하지는 마세요. "
        "전체 3~4문장, 250자 이내로."
    )


def _make_synthesis_prompt(d_reply: str, e_reply: str, phase: str) -> str:
    head = f"[디자이너]\n{d_reply}\n\n[엔지니어]\n{e_reply}\n\n"
    if phase in ("divergence", "elaboration"):
        reaction = (
            "두 의견을 하나로 섞지 마세요. 디자이너나 엔지니어의 의견에 반응할 때는, "
            "부족한 부분이 있다면 이유를 들며 반박하고, 괜찮은 부분에는 당신의 의견을 같이 제시하세요."
        )
        if phase == "divergence":
            reaction += " 완전히 새로운 의견을 제시해도 됩니다."
        return head + f"반드시 지금 단계의 목적에 맞게 정리하세요. {reaction}"
    return (
        head +
        "반드시 지금 단계의 목적에 맞게 정리하세요. 두 사람의 답변을 종합적으로 검토하면서도 각자의 기여가 드러나게 정리하고, "
        "사용자·가치 관점에서 당신 생각도 한 마디 보태세요."
    )


async def _run_one_round(pm, designer, engineer, user, messages, phase, is_first_round=False, token=None):
    """한 라운드 실행 — PM 라우팅 → D/E 병렬 sub-chat → PM 종합.

    messages(세션 전체 공통 공식 기록)에 디자이너·엔지니어 원문 + PM 종합을 그 자리에서
    이어붙인다 — PM 종합문만 남기면 PM 자신도 다음 라운드부턴 원문을 잃는다. 이 기록은
    디자이너·엔지니어에게는 절대 안 보내지므로(둘은 각자 트리거만 받음) 격리는 안 깨진다."""
    try:
        # (1) PM 라우팅
        routing_prompt = _make_routing_prompt(is_first_round, phase)
        last_msg = messages[-1] if messages else {}
        if last_msg.get("name") == "Participant":
            routing_prompt = (
                f"참가자가 방금 이런 의견을 냈습니다: \"{last_msg.get('content', '')}\"\n"
                "이 의견부터 짚고 질문하세요.\n"
            ) + routing_prompt
        routing_msgs = messages[-HISTORY_WINDOW:] + [{
            "role": "user", "name": "system", "content": routing_prompt,
        }]
        routing_text = await _pm_reply(pm, routing_msgs, user)
        # 이후 라운드는 라우팅 프롬프트 자체가 직전 D/E 답을 곱씹는 내용을 포함하므로,
        # 화면에서 숨기면 종합→다음 D/E 답 사이가 근거 없이 점프해 보임 — 항상 노출.
        if routing_text:
            _push_to_ui("PM", routing_text, recipient="Participant", token=token)
            _log_msg("PM_routing", routing_text, token=token)

        # (2) D, E 병렬 sub-chat
        sub_queue = [
            {
                "chat_id": 1,
                "sender": pm,
                "recipient": designer,
                "message": _make_designer_trigger(routing_text),
                "max_turns": 1,
                "summary_method": "last_msg",
                "clear_history": False,
                "silent": True,
            },
            {
                "chat_id": 2,
                "sender": pm,
                "recipient": engineer,
                "message": _make_engineer_trigger(routing_text),
                "max_turns": 1,
                "summary_method": "last_msg",
                "clear_history": False,
                "silent": True,
            },
        ]
        sub_results = await a_initiate_chats(sub_queue)
        d_reply = ""
        e_reply = ""
        for cid, res in sub_results.items():
            summary = getattr(res, "summary", "") or ""
            if cid == 1:
                d_reply = collapse_blank_lines(summary)
            elif cid == 2:
                e_reply = collapse_blank_lines(summary)

        # 접힘 미리보기용 한 줄 요약 (D·E 병렬 생성 → 지연 최소화)
        d_sum, e_sum = await asyncio.gather(_summarize(pm, d_reply), _summarize(pm, e_reply))

        if d_reply:
            _push_to_ui("Designer", d_reply, recipient="PM", summary=d_sum, token=token)
            _log_msg("Designer", d_reply, token=token)
            messages.append({"role": "user", "content": d_reply, "name": "Designer"})
        if e_reply:
            _push_to_ui("Engineer", e_reply, recipient="PM", summary=e_sum, token=token)
            _log_msg("Engineer", e_reply, token=token)
            messages.append({"role": "user", "content": e_reply, "name": "Engineer"})

        # (3) PM 종합
        synth_msgs = messages[-HISTORY_WINDOW:] + [{
            "role": "user", "name": "system",
            "content": _make_synthesis_prompt(d_reply, e_reply, phase),
        }]
        synth_text = await _pm_reply(pm, synth_msgs, user)
        if synth_text:
            _push_to_ui("PM", synth_text, recipient="Participant", token=token)
            _log_msg("PM", synth_text, token=token)
            messages.append({"role": "assistant", "content": synth_text, "name": "PM"})

        return synth_text
    except Exception as e:
        _log("centralized_error", {
            "error": str(e), "type": type(e).__name__, "traceback": traceback.format_exc(),
        })
        return ""


async def run_centralized_discussion(pm, designer, engineer, user, brief, token=None):
    """Centralized 토론 — 세션 전체를 하나의 대화로 이어가며 phase만 전환.

    각 phase: 라운드 4 + user 차례 (등간격, 라운드 2 끝·4 끝)
    - 발산·심화: user 2번
    - 수렴: user 1번 (라운드 4 끝은 양식 자동 전환이라 skip)

    phase 리셋 없음 — messages는 세션 시작부터 끝까지 하나로 이어지고(HISTORY_WINDOW로만
    자름), phase 전환은 발산 시작 때 딱 한 번 오프닝을 띄운 뒤로는 시스템 메시지
    (inject_phase_prefix) 갱신만으로 처리한다 — 화면에 새 메시지가 안 뜬다.

    web.py는 async loop에서 이 함수를 await.
    """
    reset_summarizer_usage(pm)
    phases = ["divergence", "elaboration", "convergence"]
    messages = []

    for i, phase in enumerate(phases):
        if token is not None and not _is_current_session(token):
            _log("centralized_stale_session_stop", {"token": token, "at": "phase_start"})
            return None

        for agent in (pm, designer, engineer):
            inject_phase_prefix(agent, phase, brief)

        _log("phase_start", {"phase": phase, "condition": "centralized"})

        if phase == "divergence":
            # 세션 전체에서 대화를 여는 유일한 지점 — 이후 phase 전환은 시스템
            # 메시지 갱신만으로 처리하고 새 오프닝은 안 띄운다.
            opening_msg = build_opening_message(brief, phase)
            messages.append({
                "role": "user", "content": opening_msg, "name": "Participant",
            })
            _push_to_ui("Participant", opening_msg, recipient="PM", token=token)
            _log_msg("Participant", opening_msg, token=token)

        is_convergence = (phase == "convergence")
        n_rounds = ROUNDS_PER_PHASE

        for round_idx in range(n_rounds):
            if token is not None and not _is_current_session(token):
                _log("centralized_stale_session_stop", {"token": token, "at": "round_start"})
                return None

            await _run_one_round(
                pm, designer, engineer, user, messages, phase,
                is_first_round=(i == 0 and round_idx == 0), token=token,
            )

            # 라운드 2의 배수 끝마다 user 차례. 수렴 phase의 라운드 4 끝은 skip.
            current_round = round_idx + 1
            is_user_turn = (current_round % 2 == 0)
            is_last_round = (current_round == n_rounds)
            if is_user_turn and not (is_last_round and is_convergence):
                user_input = await user.a_get_human_input(
                    "Replying as Participant. Provide feedback to PM. "
                    "Press enter to skip and use auto-reply, or type 'exit' to end the conversation: "
                )
                user_input = (user_input or "").strip()
                if user_input.lower() == "exit":
                    break
                if user_input:
                    messages.append({
                        "role": "user", "content": user_input, "name": "Participant",
                    })
                    _push_to_ui("Participant", user_input, recipient="PM", token=token)
                    _log_msg("Participant", user_input, token=token)

    # 세션 결과 — 마지막 PM 종합 + 그 뒤에 나온 참가자 발언
    last_synth = ""
    trailing_user = ""
    for m in reversed(messages):
        name = m.get("name")
        if name == "PM" and not last_synth:
            last_synth = m.get("content", "")
        if name == "Participant" and not trailing_user:
            trailing_user = m.get("content", "")
        if last_synth and trailing_user:
            break
    return type("Result", (), {
        "summary": last_synth,
        "trailing_user": trailing_user,
    })()

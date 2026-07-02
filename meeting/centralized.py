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
import re
import autogen
from autogen.agentchat.chat import a_initiate_chats
from autogen.io import IOStream

from meeting.common import inject_phase_prefix, build_opening_message, _log, collapse_blank_lines


# phase별 라운드 수와 user 차례 횟수
ROUNDS_PER_PHASE = 4
USER_TURNS = {"generate": 2, "deepen": 2, "converge": 1}


def _push_to_ui(sender: str, content: str, recipient: str = "PM", summary: str = "") -> None:
    """현재 IOStream(웹소켓)에 sender 이름으로 발화 push.
    summary가 있으면 접힘 미리보기용으로 함께 전송."""
    if not content or not content.strip():
        return
    try:
        iostream = IOStream.get_default()
        ws = getattr(iostream, "_websocket", None)
        if ws is None:
            return
        payload = {"sender": sender, "recipient": recipient, "content": content}
        if summary:
            payload["summary"] = summary
        ws.send(json.dumps({"type": "text", "content": payload}))
    except Exception:
        pass


def _log_msg(speaker: str, content: str) -> None:
    """messages.jsonl + messages.csv 누적 (a_initiate_chat 우회라 hook이 안 잡으니 직접)."""
    if not content or not content.strip():
        return
    try:
        from web import log_message, _append_messages_csv
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


def _looks_truncated(text: str) -> bool:
    """발화가 잘렸는지 추정. 완결 문장은 종결 부호로 끝남 —
    중간에 잘리면 부호 없이 단어/조사에서 뚝 끝남. (길이 기준으론 못 잡아서 부호로 판정)"""
    t = (text or "").strip()
    if len(t) < 10:
        return True
    return not re.search(r"[.!?。…?！？\"')\]]\s*$", t)


async def _pm_reply_guarded(pm, msgs, user, tries: int = 3) -> str:
    """PM 응답 생성. 잘린 응답(종결 부호 없음/너무 짧음)이면 재시도.
    정상 응답이면 첫 시도에 바로 반환 — 깨졌을 때만 추가 호출."""
    text = ""
    for _ in range(tries):
        ok, msg = await pm.a_generate_oai_reply(messages=msgs, sender=user)
        text = collapse_blank_lines(_extract_content(msg)) if ok else ""
        if not _looks_truncated(text):
            return text
        _log("pm_reply_retry", {"got": text[:30], "len": len(text)})
    return text


async def _retry_subchat(pm, agent, trigger: str, tries: int = 3) -> str:
    """단일 D/E sub-chat 실행 + 잘린 응답이면 재시도 (업스트림 truncation 대비).
    정상이면 첫 시도에 바로 반환."""
    text = ""
    for _ in range(tries):
        res = await a_initiate_chats([{
            "chat_id": 1, "sender": pm, "recipient": agent,
            "message": trigger, "max_turns": 1,
            "summary_method": "last_msg", "clear_history": False, "silent": True,
        }])
        text = collapse_blank_lines(getattr(res.get(1), "summary", "") or "")
        if not _looks_truncated(text):
            return text
        _log("subagent_reply_retry", {"agent": getattr(agent, "name", "?"), "len": len(text)})
    return text


_summarizer = None


def _get_summarizer(pm):
    """접힘 미리보기용 경량 요약기 (PM과 같은 모델, 1회 생성 후 재사용)."""
    global _summarizer
    if _summarizer is None:
        _summarizer = autogen.AssistantAgent(
            name="Summarizer",
            system_message=(
                "당신은 한 줄 요약기입니다. 주어진 발언의 핵심 아이디어를 "
                "명사구 한 줄(20자 이내, 문장부호 없이)로만 출력하세요. 다른 말 금지."
            ),
            llm_config=pm.llm_config,
        )
    return _summarizer


async def _summarize(pm, text: str) -> str:
    """발화를 한 줄 요약 (접힘 미리보기용). 이미 짧으면 그대로 반환."""
    t = (text or "").strip()
    if len(t) < 30:
        return t
    try:
        agent = _get_summarizer(pm)
        ok, msg = await agent.a_generate_oai_reply(
            messages=[{"role": "user", "content": t}], sender=pm
        )
        s = collapse_blank_lines(_extract_content(msg)) if ok else ""
        s = s.strip().strip("\"'.").strip()
        return s or t[:20]
    except Exception:
        return t[:20]


def _make_designer_trigger(question: str) -> str:
    return (
        f"PM이 이렇게 물었습니다: {question}\n\n"
        "인사나 공감·동의 추임새 없이, 디자인 관점에서 자기 의견부터 바로 답하세요. "
        "2~3문장으로 짧게, 핵심 하나만. PM의 안을 그대로 받지 말고 다듬거나 다르게 풀어 답하세요."
    )


def _make_engineer_trigger(question: str) -> str:
    return (
        f"PM이 이렇게 물었습니다: {question}\n\n"
        "인사나 공감·동의 추임새 없이, 엔지니어로서 자기 의견부터 바로 답하세요. "
        "2~3문장으로 짧게, 핵심 하나만. PM의 안을 그대로 받지 말고 다듬거나 다른 각도로 답하세요."
    )


def _make_routing_prompt() -> str:
    return (
        "당신은 PM입니다. 받아주는 말 없이, 직전에 나온 핵심 포인트를 이어받아 "
        "회의를 한 걸음 더 끌고 가세요.\n\n"
        "지금 다루던 그 포인트를 더 파고들 수 있게, 디자이너와 엔지니어에게 "
        "자연스럽게 나눠 물으세요. 한 명씩 따로 심문하듯 던지지 말고, "
        "하나의 흐름 안에서 각자 어디를 봐주면 좋을지 짚어주는 식으로. "
        "다루는 주제는 같고, 보는 각도만 각자 전문 영역으로 갈립니다 "
        "(디자이너=화면·경험, 엔지니어=기술·데이터가 여는 가능성).\n\n"
        "초반엔 넓게, 회의가 진행될수록 더 구체적인 지점을 파고드세요. "
        "1~2문장으로 짧게, 동료에게 묻듯 자연스럽게.\n\n"
        "참가자가 의견을 냈으면 그 방향을 직접 반영. 바로 본론으로."
    )


def _make_synthesis_prompt(d_reply: str, e_reply: str, is_final: bool = False) -> str:
    head = f"[디자이너]\n{d_reply}\n\n[엔지니어]\n{e_reply}\n\n"
    if is_final:
        # 회의 마지막 발언 — 새 질문 없이 최종 컨셉으로 마무리
        return (
            head +
            "이번이 회의의 마지막 발언입니다. 새 질문이나 다음 단계 제안은 하지 말고, "
            "지금까지 논의를 모아 최종 컨셉을 분명하게 마무리하세요. "
            "핵심 기능과 차별점이 무엇인지 짚고, 회의를 닫는 톤으로. 3~4문장으로."
        )
    return (
        head +
        "받아주는 말 없이 바로, 두 답을 한 줄로 정리하고 다음에 무엇을 볼지 한 마디로 짚어 넘어가세요. "
        "두 답을 깎아내리거나 틀렸다고 하지 말고 살려서 엮으세요. "
        "참가자가 직전에 의견을 냈으면 그 방향을 살리세요. "
        "본론으로 바로 시작. 길게 늘어놓지 말고 2~3문장으로 짧게."
    )


async def _run_one_round(pm, designer, engineer, user, messages, is_final=False):
    """한 라운드 실행 — PM 라우팅 → D/E 병렬 sub-chat → PM 종합. 종합 발화 반환."""
    try:
        # (1) PM 라우팅
        routing_msgs = list(messages) + [{
            "role": "user", "name": "system", "content": _make_routing_prompt(),
        }]
        routing_text = await _pm_reply_guarded(pm, routing_msgs, user)
        # 라우팅 발화 화면 노출은 직전 메시지가 user(opening 또는 참가자 발화)일 때만.
        # 직전이 PM 종합이면 종합 발화가 이미 다음 의제 역할 → 라우팅 중복이라 push X.
        last_msg = messages[-1] if messages else {}
        is_after_user = (
            last_msg.get("name") == "Participant"
            or last_msg.get("role") == "user"
        )
        if routing_text:
            if is_after_user:
                _push_to_ui("PM", routing_text, recipient="Participant")
            _log_msg("PM_routing", routing_text)

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

        # 잘린 응답(종결 부호 없음/너무 짧음)이면 해당 에이전트만 재시도
        if _looks_truncated(d_reply):
            d_reply = await _retry_subchat(pm, designer, _make_designer_trigger(routing_text))
        if _looks_truncated(e_reply):
            e_reply = await _retry_subchat(pm, engineer, _make_engineer_trigger(routing_text))

        # 접힘 미리보기용 한 줄 요약 (D·E 병렬 생성 → 지연 최소화)
        d_sum, e_sum = await asyncio.gather(_summarize(pm, d_reply), _summarize(pm, e_reply))

        if d_reply:
            _push_to_ui("Designer", d_reply, recipient="PM", summary=d_sum)
            _log_msg("Designer", d_reply)
        if e_reply:
            _push_to_ui("Engineer", e_reply, recipient="PM", summary=e_sum)
            _log_msg("Engineer", e_reply)

        # (3) PM 종합
        synth_msgs = list(messages) + [{
            "role": "user", "name": "system",
            "content": _make_synthesis_prompt(d_reply, e_reply, is_final=is_final),
        }]
        synth_text = await _pm_reply_guarded(pm, synth_msgs, user)
        if synth_text:
            _push_to_ui("PM", synth_text, recipient="Participant")
            _log_msg("PM", synth_text)

        return synth_text
    except Exception as e:
        _log("centralized_error", {"error": str(e), "type": type(e).__name__})
        return ""


async def run_centralized_discussion(pm, designer, engineer, user, brief):
    """Centralized 토론 — phase별 직접 흐름 제어.

    각 phase: 라운드 4 + user 차례 (등간격, 라운드 2 끝·4 끝)
    - 발산·심화: user 2번
    - 수렴: user 1번 (라운드 4 끝은 양식 자동 전환이라 skip)

    web.py는 async loop에서 이 함수를 await.
    """
    phases = ["generate", "deepen", "converge"]
    all_results = []

    for i, phase in enumerate(phases):
        carryover = ""
        if all_results:
            prev = all_results[-1]
            prev_summary = getattr(prev, "summary", "") or ""
            prev_user = getattr(prev, "trailing_user", "") or ""
            carryover = (
                f"\n\n=== 이전 페이즈 요약 ===\n{prev_summary}\n==================\n"
            )
            if prev_user:
                carryover += (
                    f"\n참가자가 직전에 이런 의견을 냈습니다: \"{prev_user}\"\n"
                    "이 의견부터 짚고 이번 페이즈를 시작하세요.\n"
                )

        opening_msg = build_opening_message(brief, phase, carryover)

        for agent in (pm, designer, engineer):
            inject_phase_prefix(agent, phase)

        _log("phase_start", {"phase": phase, "condition": "centralized"})

        # opening — outer messages 시작 + 화면·로그
        messages = [{
            "role": "user", "content": opening_msg, "name": "Participant",
        }]
        _push_to_ui("Participant", opening_msg, recipient="PM")
        _log_msg("Participant", opening_msg)

        is_converge = (phase == "converge")
        n_rounds = ROUNDS_PER_PHASE

        for round_idx in range(n_rounds):
            is_final_round = is_converge and (round_idx + 1 == n_rounds)
            synth_text = await _run_one_round(
                pm, designer, engineer, user, messages, is_final=is_final_round
            )
            if synth_text:
                messages.append({
                    "role": "assistant", "content": synth_text, "name": "PM",
                })

            # 라운드 2의 배수 끝마다 user 차례. 수렴 phase의 라운드 4 끝은 skip.
            current_round = round_idx + 1
            is_user_turn = (current_round % 2 == 0)
            is_last_round = (current_round == n_rounds)
            if is_user_turn and not (is_last_round and is_converge):
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
                    _push_to_ui("Participant", user_input, recipient="PM")
                    _log_msg("Participant", user_input)

        # phase 결과 — 마지막 PM 종합 + 그 뒤에 나온 참가자 발언(있으면 다음 페이즈로 넘김)
        last_synth = ""
        trailing_user = ""
        for m in reversed(messages):
            name = m.get("name")
            if name == "PM":
                last_synth = m.get("content", "")
                break
            if name == "Participant" and not trailing_user:
                trailing_user = m.get("content", "")
        all_results.append(type("Result", (), {
            "summary": last_synth,
            "trailing_user": trailing_user,
        })())

    return all_results[-1]

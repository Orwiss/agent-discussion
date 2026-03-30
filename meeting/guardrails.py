import re
from collections import deque

# ── 최근 메시지 fingerprint 버퍼 (cross-agent 복사 감지용) ──
_recent_fingerprints: deque = deque(maxlen=20)

# ── 에이전트별 최근 키워드 기록 (반복 감지용) ──
_agent_recent_keywords: dict = {}  # {agent_name: [set(), set(), set()]} 최근 3턴

# ── 브리프에서 추출된 금지 키워드 (서버단 강제) ──
_banned_keywords: list = []


def set_banned_keywords(keywords):
    """브리프 제약에서 추출한 금지 키워드를 설정"""
    global _banned_keywords
    _banned_keywords = [k.lower() for k in keywords]


def _fingerprint(text: str) -> str:
    normalized = re.sub(r'\s+', '', text)[:120]
    return normalized


def strip_think_tags(text):
    return re.sub(r'<think>[\s\S]*?</think>', '', text).strip()


def _strip_cjk_leaks(text):
    """CJK 혼입 치환: 자주 나오는 패턴을 한국어로 교정"""
    # 1) 한국어 사이에 끼어든 중국어/일본어 단어 치환
    cjk_map = {
        '奮': '분', '興奮': '흥분', '趣味': '재미', '感': '감',
        '信頼': '신뢰', '豊か': '풍부', '色彩': '색채', '驚異': '경이',
        '重要': '중요', '自然': '자연', '亲': '친', '深い': '깊은',
    }
    for cjk, kr in cjk_map.items():
        text = text.replace(cjk, kr)
    # 2) 남은 CJK 문자가 포함된 단어를 통째로 제거
    text = re.sub(r'[^\s]*[\u4e00-\u9fff\u3040-\u309f\u30a0-\u30ff]+[^\s]*', '', text)
    # 3) 태국어, 아랍어 등 비한국어 문자 포함 단어 제거
    text = re.sub(r'[^\s]*[\u0e00-\u0e7f\u0600-\u06ff\u0400-\u04ff]+[^\s]*', '', text)
    # 4) 영어가 아닌 라틴 확장 문자 (독일어 freundlich 등)는 유지하되, 문맥 없이 튀어나온 외국어 단어 제거
    # 5) 연속 공백/줄바꿈 정리
    text = re.sub(r'  +', ' ', text)
    text = re.sub(r'\n\n\n+', '\n\n', text)
    return text.strip()


def _strip_board_tags(text):
    return re.sub(r'\[보드:(추가|연결|분기)\]\s*.+?(?:\n|$)', '', text).strip()


def _strip_phase_advance(text):
    return re.sub(r'PHASE_ADVANCE\s*', '', text).strip()


def _set_message_content(message, content):
    if isinstance(message, str):
        return content
    else:
        message["content"] = content
        return message


def _remove_banned_sentences(text):
    """금지 키워드를 포함한 문장을 통째로 삭제"""
    if not _banned_keywords:
        return text

    # 문장 분리 (마침표, 줄바꿈, 물음표 기준)
    sentences = re.split(r'(?<=[.?!\n])\s*', text)
    kept = []
    for s in sentences:
        s_lower = s.lower()
        if any(kw in s_lower for kw in _banned_keywords):
            continue  # 금지 키워드 포함 문장 삭제
        kept.append(s)

    return '\n'.join(kept).strip()


def _inject_last_speaker_context(recipient):
    """직전 발언자의 요약을 반환 — 에이전트가 반응하도록 유도"""
    if not hasattr(recipient, 'groupchat'):
        return ""

    messages = recipient.groupchat.messages
    if len(messages) < 2:
        return ""

    # 직전 에이전트 발언 찾기 (유저 아닌 것)
    for m in reversed(messages[:-1]):
        name = m.get("name", "")
        content = m.get("content", "")
        if name and name != "Orwiss" and content and len(content) > 10:
            # 첫 100자만 요약으로
            summary = content[:100].replace('\n', ' ')
            return f"\n\n[직전 발언 — {name}]: {summary}...\n위 발언에 대해 당신의 관점에서 반응하세요. 동의/반박/발전 모두 가능합니다.\n"

    return ""


# ── 직전 발언 컨텍스트 저장 (GroupChat 메시지에서 추출) ──
_last_agent_utterance = {"name": "", "summary": ""}


def update_last_utterance(name, content):
    """직전 에이전트 발언을 기록 (clean_message_hook에서 호출)"""
    global _last_agent_utterance
    if name and name != "Orwiss" and content and len(content) > 10:
        _last_agent_utterance = {
            "name": name,
            "summary": content[:120].replace('\n', ' ')
        }


def phase_aware_context_hook(messages):
    """
    process_all_messages_before_reply 훅.
    발산 페이즈: blind_ideation (다른 에이전트 발언 숨김)
    심화/수렴 페이즈: 전체 히스토리 유지

    _idea_board_ref에서 현재 페이즈를 읽어서 판단.
    """
    # 현재 페이즈 확인
    from agents.synthesizer import _idea_board_ref
    phase = "generate"
    if _idea_board_ref is not None:
        phase = _idea_board_ref.get("current_phase", "generate") or "generate"

    if phase == "generate":
        return blind_ideation_hook(messages)
    else:
        return messages  # 심화/수렴에서는 전체 히스토리


def board_exclusion_hook(agent, messages):
    """
    update_agent_state hook.
    매 턴 현재 보드에 있는 아이디어를 "제외 목록"으로 시스템 메시지에 주입.
    에이전트가 이미 나온 아이디어를 반복하지 못하게 강제.
    """
    if not hasattr(agent, 'context_variables') or not agent.context_variables:
        return
    board = agent.context_variables.get("idea_board", "")
    if not board or board == "아직 아이디어 없음":
        return

    # 보드에서 아이디어 추출
    items = [line.strip() for line in board.split('\n') if line.strip()]
    if not items:
        return

    exclusion = "\n".join(f"  - {item}" for item in items[-6:])  # 최근 6개
    warning = (
        f"\n\n⚠️ 이미 나온 아이디어 (이것과 비슷한 제안 금지):\n"
        f"{exclusion}\n"
        f"위 아이디어와 형태/재질/색감이 겹치는 제안을 하지 마세요. 완전히 다른 방향만."
    )

    # 시스템 메시지에 제외목록 추가
    current = agent.system_message or ""
    if "이미 나온 아이디어" not in current:
        agent.update_system_message(current + warning)


def blind_ideation_hook(messages):
    """
    process_all_messages_before_reply 훅.
    발산 페이즈에서 다른 에이전트의 구체적 아이디어를 숨기되,
    대화가 진행 중이라는 맥락은 유지.

    완전히 제거하면 모델이 상황을 못 파악함 (gpt-4o 거부 사례).
    대신 아이디어 내용만 "[다른 참여자가 아이디어를 제시함]"으로 대체.
    """
    if not messages or len(messages) < 2:
        return messages

    filtered = [messages[0]]  # 브리프 유지
    for m in messages[1:]:
        role = m.get("role", "")
        name = m.get("name", "")
        content = m.get("content", "")

        # 시스템, 유저, 경고 → 그대로 유지
        if role == "system" or name == "Orwiss" or "⚠️" in content:
            filtered.append(m)
        # Moderator 발언 → 유지 (비교/정리는 보여줘야 함)
        elif name == "Moderator":
            filtered.append(m)
        # 다른 에이전트 발언 → 존재만 알리고 내용은 숨김
        else:
            filtered.append({
                **m,
                "content": f"[{name}이 아이디어를 제시했습니다. 이와 다른 새로운 방향을 제안하세요.]"
            })

    return filtered


def clean_history_hook(messages):
    """
    process_all_messages_before_reply 훅.
    에이전트가 응답하기 전에 히스토리 전체에서 CJK 잔여물을 제거.
    이미 들어간 깨진 텍스트가 다음 에이전트에게 전파되는 걸 방지.
    """
    if not messages:
        return messages
    cleaned = []
    for m in messages:
        content = m.get("content", "")
        if content:
            new_content = _strip_cjk_leaks(content)
            cleaned.append({**m, "content": new_content})
        else:
            cleaned.append(m)
    return cleaned


def diversity_guard_hook(messages):
    """
    process_all_messages_before_reply 훅.
    시그니처: (messages: list[dict]) -> list[dict]

    최근 에이전트 발언들의 키워드 겹침을 계산.
    겹침이 높으면 마지막 메시지에 "다른 방향" 지시를 주입.
    """
    if not messages or len(messages) < 4:
        return messages

    # 최근 에이전트 발언 3개 수집 (유저 제외)
    recent_agent_msgs = []
    for m in reversed(messages):
        name = m.get("name", "")
        content = m.get("content", "")
        if name and name != "Orwiss" and content and len(content) > 20:
            # 키워드 추출
            words = set(re.findall(r'[가-힣a-zA-Z]{3,}', content.lower()))
            recent_agent_msgs.append(words)
            if len(recent_agent_msgs) >= 3:
                break

    if len(recent_agent_msgs) < 3:
        return messages

    # 3개 발언 간 키워드 겹침 비율 계산
    overlap_01 = len(recent_agent_msgs[0] & recent_agent_msgs[1])
    overlap_02 = len(recent_agent_msgs[0] & recent_agent_msgs[2])
    overlap_12 = len(recent_agent_msgs[1] & recent_agent_msgs[2])
    avg_size = max(1, sum(len(s) for s in recent_agent_msgs) / 3)
    overlap_ratio = (overlap_01 + overlap_02 + overlap_12) / (3 * avg_size)

    # 겹침이 40% 이상이면 다양성 경고 주입
    if overlap_ratio > 0.4:
        shared_words = recent_agent_msgs[0] & recent_agent_msgs[1] & recent_agent_msgs[2]
        avoid_list = ", ".join(list(shared_words)[:5])
        redirect = {
            "role": "system",
            "content": (
                f"⚠️ 최근 발언들이 너무 비슷합니다. "
                f"다음 키워드를 피하고 완전히 다른 방향을 제안하세요: {avoid_list}. "
                f"형태, 질감, 비율, 스타일 중 최소 2가지가 기존 제안과 달라야 합니다."
            ),
        }
        return messages + [redirect]

    # 다음 발언자의 자기 반복 감지 — 마지막 메시지의 수신자가 다음 발언자
    # 마지막 메시지에서 다음 발언자 이름을 알 수 없으므로, 모든 에이전트의 반복 경고를 공통으로 주입
    repeat_warnings = []
    for agent_name, history in _agent_recent_keywords.items():
        if len(history) < 2:
            continue
        # 최근 2턴의 키워드 겹침
        recent_sets = list(history)
        overlap = recent_sets[-1] & recent_sets[-2]
        union = recent_sets[-1] | recent_sets[-2]
        if union and len(overlap) / len(union) > 0.5:
            avoid = ", ".join(list(overlap)[:5])
            repeat_warnings.append(f"{agent_name}: 이전 발언과 너무 비슷합니다. {avoid} 등을 반복하지 마세요.")

    if repeat_warnings:
        warning = {
            "role": "system",
            "content": "⚠️ 반복 경고:\n" + "\n".join(repeat_warnings) + "\n이전에 한 말을 반복하지 말고 새로운 내용만 이야기하세요.",
        }
        return messages + [warning]

    return messages


def reply_context_hook(message):
    """
    process_last_received_message 훅.
    시그니처: (message) -> message
    직전 에이전트 발언 요약을 메시지 끝에 주입.
    """
    if isinstance(message, str):
        content = message
    else:
        content = message.get("content", "") if isinstance(message, dict) else str(message)

    if not content:
        return message

    last = _last_agent_utterance
    if last["name"] and last["summary"]:
        context = f"\n\n[직전: {last['name']}이 '{last['summary']}...'라고 했습니다. 이에 대해 반응하세요.]"
        if isinstance(message, str):
            return content + context
        elif isinstance(message, dict):
            message["content"] = content + context
            return message
        else:
            return content + context

    return message


def clean_message_hook(*, sender, message, recipient, silent):
    """
    모든 에이전트 발언 클리닝 hook (process_message_before_send).

    1. <think> 태그 제거
    2. 금지 키워드 포함 문장 삭제 (서버단 강제)
    3. Moderator 아닌 에이전트의 [보드:] / PHASE_ADVANCE 제거
    4. cross-agent 복사 감지 → 차단
    5. 자기 직전 발언과 동일 → 차단
    """
    if isinstance(message, str):
        content = message
    else:
        content = message.get("content", "")

    if not content or sender.name == "Orwiss":
        return message

    # 1) <think> 태그 제거
    cleaned = strip_think_tags(content)

    # 2) opening brief 에코 차단 — 에이전트가 브리프를 그대로 뱉으면 차단
    if '=== 디자인 회의 시작 ===' in cleaned and sender.name != "Orwiss":
        return _set_message_content(message, "")

    # 3) 내부 에이전트 역할 프롬프트 잔여물 제거
    prompt_leaks = [
        '사용자 시나리오 전문가.', '첫인상 평가자.', '3D 조형 전문가.',
        '시각 레퍼런스 전문가.', 'UE5 도구 전문가.', 'UE5 아트 파이프라인 전문가.',
        '사용자 시나리오 전문가', '첫인상 평가자', '3D 조형 전문가',
    ]
    for leak in prompt_leaks:
        cleaned = cleaned.replace(leak, '')

    # 4) CJK 혼입 치환
    cleaned = _strip_cjk_leaks(cleaned)

    # 5) 금지 키워드 포함 문장 삭제
    cleaned = _remove_banned_sentences(cleaned)

    # 6) Moderator 아닌 에이전트: [보드:] / PHASE_ADVANCE 제거
    if sender.name != "Moderator":
        cleaned = _strip_board_tags(cleaned)
        cleaned = _strip_phase_advance(cleaned)

    if not cleaned.strip():
        return _set_message_content(message, "")

    # 4) cross-agent 복사 감지
    fp = _fingerprint(cleaned)
    if fp and fp in _recent_fingerprints:
        return _set_message_content(message, "")

    # 5) 자기 직전 발언과 동일
    if hasattr(sender, '_last_sent_content') and sender._last_sent_content == cleaned:
        return _set_message_content(message, "")

    # 6) recipient GroupChat 히스토리와 비교
    if hasattr(recipient, 'groupchat'):
        recent = recipient.groupchat.messages[-5:] if recipient.groupchat.messages else []
        for prev in recent:
            prev_content = prev.get("content", "")
            if prev_content and fp == _fingerprint(prev_content):
                return _set_message_content(message, "")

    # 통과
    if fp:
        _recent_fingerprints.append(fp)
    sender._last_sent_content = cleaned

    # 직전 발언 기록
    update_last_utterance(sender.name, cleaned)

    # 에이전트별 키워드 기록 (반복 감지용)
    kw_set = set(re.findall(r'[가-힣a-zA-Z]{3,}', cleaned.lower()))
    if sender.name not in _agent_recent_keywords:
        _agent_recent_keywords[sender.name] = deque(maxlen=3)
    _agent_recent_keywords[sender.name].append(kw_set)

    return _set_message_content(message, cleaned)

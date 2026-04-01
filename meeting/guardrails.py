"""
실험용 가드레일 — 출력 정리 목적만 (양 조건 동일 적용)
커스텀 로직 없음. clean_message_hook + clean_history_hook만 유지.
"""
import re
from collections import deque

# ── 최근 메시지 fingerprint 버퍼 (cross-agent 복사 감지용) ──
_recent_fingerprints: deque = deque(maxlen=20)


def _fingerprint(text: str) -> str:
    normalized = re.sub(r'\s+', '', text)[:120]
    return normalized


def strip_think_tags(text):
    return re.sub(r'<think>[\s\S]*?</think>', '', text).strip()


def _strip_cjk_leaks(text):
    """CJK 혼입 치환: 자주 나오는 패턴을 한국어로 교정"""
    cjk_map = {
        '奮': '분', '興奮': '흥분', '趣味': '재미', '感': '감',
        '信頼': '신뢰', '豊か': '풍부', '色彩': '색채', '驚異': '경이',
        '重要': '중요', '自然': '자연', '亲': '친', '深い': '깊은',
    }
    for cjk, kr in cjk_map.items():
        text = text.replace(cjk, kr)
    text = re.sub(r'[^\s]*[\u4e00-\u9fff\u3040-\u309f\u30a0-\u30ff]+[^\s]*', '', text)
    text = re.sub(r'[^\s]*[\u0e00-\u0e7f\u0600-\u06ff\u0400-\u04ff]+[^\s]*', '', text)
    text = re.sub(r'  +', ' ', text)
    text = re.sub(r'\n\n\n+', '\n\n', text)
    return text.strip()


def _set_message_content(message, content):
    if isinstance(message, str):
        return content
    else:
        message["content"] = content
        return message


def clean_history_hook(messages):
    """
    process_all_messages_before_reply 훅.
    히스토리 전체에서 CJK 잔여물 제거.
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


def clean_message_hook(*, sender, message, recipient, silent):
    """
    모든 에이전트 발언 클리닝 hook (process_message_before_send).
    1. <think> 태그 제거
    2. CJK 혼입 치환
    3. cross-agent 복사 감지 → 차단
    4. 자기 직전 발언과 동일 → 차단
    """
    if isinstance(message, str):
        content = message
    else:
        content = message.get("content", "")

    if not content or sender.name == "Participant":
        return message

    # 1) <think> 태그 제거
    cleaned = strip_think_tags(content)

    # 2) opening brief 에코 차단
    if '=== 디자인 아이디에이션 시작 ===' in cleaned and sender.name != "Participant":
        return _set_message_content(message, "")

    # 3) CJK 혼입 치환
    cleaned = _strip_cjk_leaks(cleaned)

    # 4) 마크다운 형식 제거 (볼드, 이탤릭, 헤더)
    cleaned = re.sub(r'\*\*(.+?)\*\*', r'\1', cleaned)  # **볼드** → 볼드
    cleaned = re.sub(r'\*(.+?)\*', r'\1', cleaned)      # *이탤릭* → 이탤릭
    cleaned = re.sub(r'^#{1,3}\s+', '', cleaned, flags=re.MULTILINE)  # ### 헤더 → 헤더

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

    return _set_message_content(message, cleaned)

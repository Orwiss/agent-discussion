"""
SocietyOfMindAgent response_preparer callable.

문자열 대신 함수를 사용해서:
1. 내부 GroupChat의 첫 메시지(외부에서 온 원본)를 제외하고
2. 내부 토론 결과만 LLM에게 보내서 정제
→ 크로스 에이전트 복사 문제 근본 해결

참고: microsoft/autogen#6123, #6142
"""


def make_response_preparer(prompt_text):
    """
    response_preparer callable을 생성합니다.

    Args:
        prompt_text: 기존에 문자열로 쓰던 response_preparer 프롬프트

    Returns:
        callable(agent, messages) -> str
    """

    def _preparer(agent, messages):
        if not messages:
            return ""

        # 내부 GroupChat 메시지에서 첫 번째(외부 원본)를 제외
        # messages[0] = 외부에서 온 메시지 (이게 복사 원인)
        # messages[1:] = 실제 내부 토론
        inner_messages = messages[1:] if len(messages) > 1 else messages

        # 내부 토론 내용만 추출 (에이전트 이름 제거 — 외부 노출 방지)
        import re
        inner_names = ['시나리오분석가', '인상평가자', '탈선유도자', '조형분석가',
                       '시각레퍼런스전문가', '퍼포먼스분석가', '구현전문가']
        inner_text = []
        for m in inner_messages:
            content = m.get("content", "")
            if content and content.strip():
                # 내부 에이전트 이름 라벨 제거
                for name in inner_names:
                    content = re.sub(rf'{name}[:\s]*', '', content)
                # 영어 메타 텍스트 제거
                content = re.sub(r'Here is the rewritten response[:\s]*', '', content, flags=re.IGNORECASE)
                if content.strip():
                    inner_text.append(content.strip())

        if not inner_text:
            return ""

        # LLM에게 보낼 최종 프롬프트 조합
        combined = "\n\n".join(inner_text)
        return f"{combined}\n\n---\n\n{prompt_text}"

    return _preparer

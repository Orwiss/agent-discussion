# 멀티 에이전트 디자인 회의 시스템 — 코드 레벨 상세 설계

## 의존성

```
pip install ag2
```

AG2 = AutoGen 0.2 포크. `import autogen`으로 사용. Microsoft AutoGen 0.4와 API 완전히 다름.

---

## 1. LLM 설정 — Groq + 모델 이질성

```python
# config.py
import os
from dotenv import load_dotenv

load_dotenv()

GROQ_API_KEY = os.getenv("GROQ_API_KEY")
GROQ_BASE_URL = "https://api.groq.com/openai/v1"

# === 에이전트별 모델 배정 (모델 이질성으로 반복 방지) ===
#
# 같은 모델이면 같은 키워드 어트랙터를 공유한다.
# llama, mixtral, gemma는 학습 데이터와 아키텍처가 달라서
# 같은 주제를 줘도 다른 연상 패턴이 나옴.

# UX 리서처 — llama 3.3 70B: 가장 크고 다양한 어휘/관점
llm_config_ux = {
    "config_list": [{
        "model": "llama-3.3-70b-versatile",
        "api_key": GROQ_API_KEY,
        "base_url": GROQ_BASE_URL,
    }],
    "temperature": 0.7,
    "cache_seed": None,
}

# 3D 디자이너 — mixtral 8x7B: MoE 구조라 activation 패턴이 llama와 다름
llm_config_3d = {
    "config_list": [{
        "model": "mixtral-8x7b-32768",
        "api_key": GROQ_API_KEY,
        "base_url": GROQ_BASE_URL,
    }],
    "temperature": 0.7,
    "cache_seed": None,
}

# 언리얼 개발자 — 본체는 llama-70b (gemma2는 8K 컨텍스트 제한이라 대화 길어지면 터짐)
# 대신 내부 검토 에이전트 하나를 gemma2로 배정해서 모델 이질성 유지
llm_config_unreal = {
    "config_list": [{
        "model": "llama-3.3-70b-versatile",
        "api_key": GROQ_API_KEY,
        "base_url": GROQ_BASE_URL,
    }],
    "temperature": 0.7,
    "cache_seed": None,
}

# 언리얼 내부 검토 전용 — gemma2로 이질성 확보
llm_config_inner_gemma = {
    "config_list": [{
        "model": "gemma2-9b-it",
        "api_key": GROQ_API_KEY,
        "base_url": GROQ_BASE_URL,
    }],
    "temperature": 0.9,
    "cache_seed": None,
}

# Synthesizer, Facilitator — 종합 능력 필요
llm_config_main = {
    "config_list": [{
        "model": "llama-3.3-70b-versatile",
        "api_key": GROQ_API_KEY,
        "base_url": GROQ_BASE_URL,
    }],
    "temperature": 0.7,
    "cache_seed": None,
}

# 내부 검토용 — 빠르고 저렴, 발산용이라 temperature 높게
llm_config_inner = {
    "config_list": [{
        "model": "llama-3.1-8b-instant",
        "api_key": GROQ_API_KEY,
        "base_url": GROQ_BASE_URL,
    }],
    "temperature": 0.9,
    "cache_seed": None,
}

# speaker selection용 — 판단만 하면 되니 작은 모델, 낮은 temperature
llm_config_selector = {
    "config_list": [{
        "model": "llama-3.1-8b-instant",
        "api_key": GROQ_API_KEY,
        "base_url": GROQ_BASE_URL,
    }],
    "temperature": 0.1,
    "cache_seed": None,
}
```

### .env 파일

```
GROQ_API_KEY=여기에_키_입력
```

---

## 2. Stage 1: 사전 정제 — 구현 상세

### Facilitator 에이전트

```python
# agents/facilitator.py
import autogen

facilitator = autogen.AssistantAgent(
    name="Facilitator",
    system_message="""당신은 디자인 회의 퍼실리테이터입니다.

사용자와 1:1로 대화하며 다음을 파악하세요:
1. 디자인 목표: 이 회의에서 달성하려는 것
2. 현재 상태: 이미 있는 디자인이 있는지, 백지인지
3. 제약 조건: 기술적, 시간적, 리소스 제약
4. 피드백 범위: 논의할 것과 논의하지 않을 것
5. 기대: 어떤 종류의 결과를 원하는지

파악이 부족한 항목이 있으면 하나씩 질문하세요.
한번에 여러 질문을 하지 마세요.

모든 항목이 충분히 파악되면, 회의 유형을 판단하세요:
- 발산형: 구체적 디자인 없음, 방향 탐색 필요
- 개선형: 기존 디자인 있음, 개선점 찾기
- 선택형: 후보 2~3개 있음, 비교 선택

마지막에 아래 형식으로 회의 브리프를 출력하세요:

=== 회의 브리프 ===
[회의 유형]: 발산형/개선형/선택형
[디자인 목표]: ...
[현재 상태]: ...
[제약 조건]: ...
[피드백 범위]: ...
[참여자 기대]: ...
==================
TERMINATE""",
    llm_config=llm_config_main,
)
```

### 사용자 프록시

```python
user = autogen.UserProxyAgent(
    name="Orwiss",
    human_input_mode="ALWAYS",
    code_execution_config=False,
    default_auto_reply="",  # Enter 누르면 빈 문자열
    is_termination_msg=lambda msg: "TERMINATE" in msg.get("content", ""),
)
```

### Stage 1 실행 및 브리프 추출

```python
# meeting/stage1.py
def run_stage1(user, facilitator):
    result = user.initiate_chat(
        facilitator,
        message="디자인 회의를 시작하려고 합니다.",
        max_turns=20,
        summary_method="reflection_with_llm",
        summary_args={
            "summary_prompt": (
                "위 대화에서 합의된 내용을 바탕으로 회의 브리프를 정리하세요. "
                "반드시 회의 유형(발산형/개선형/선택형)을 명시하세요. "
                "JSON 형식으로: "
                '{"meeting_type": "...", "goal": "...", "current_state": "...", '
                '"constraints": "...", "scope": "...", "expectations": "..."}'
            )
        },
    )
    # result.summary에 브리프 JSON이 담김
    return result
```

**핵심 AG2 메커니즘:**
- `initiate_chat()`의 `summary_method="reflection_with_llm"` → 대화 끝나면 LLM이 자동 요약
- `summary_args["summary_prompt"]`로 요약 형식 지정
- `result.summary` → 이게 Stage 2의 `carryover`로 전달됨

---

## 3. SocietyOfMind 에이전트 + 내부 검토 — 구현 상세

### UX 리서처 (예시, 나머지도 같은 패턴)

```python
# agents/ux_researcher.py
import autogen
from autogen.agentchat.contrib.society_of_mind_agent import SocietyOfMindAgent

def create_ux_researcher(llm_config_ux, llm_config_inner):
    # === 내부 검토 에이전트 3명 ===

    scenario_analyst = autogen.AssistantAgent(
        name="시나리오분석가",
        system_message="""당신은 사용자 행동 시나리오 전문가입니다.
논의 중인 디자인에 대해:
1. 구체적 사용자 시나리오를 3단계로 작성하세요 (진입 → 사용 → 이탈)
2. 각 단계에서 사용자가 막히거나 혼란스러울 수 있는 지점을 찾으세요
3. "이런 사용자라면 여기서 이렇게 행동할 것이다"를 구체적으로 서술하세요""",
        llm_config=llm_config_inner,
    )

    accessibility_reviewer = autogen.AssistantAgent(
        name="접근성검토자",
        system_message="""당신은 접근성과 사용성 전문가입니다.
시나리오분석가의 시나리오에 대해:
1. 다양한 사용자 맥락(초보자, 전문가, 장애 사용자, 모바일)에서 검토하세요
2. 놓친 엣지 케이스가 있으면 지적하세요
3. 시나리오에 동의하더라도, 다른 사용자 유형의 관점을 추가하세요""",
        llm_config=llm_config_inner,
    )

    # === 탈선 유도자: 반복 방지 핵심 ===
    # 이 에이전트가 내부 검토 마지막에 발언하면서
    # 이미 나온 키워드를 걸러내고 새 방향을 강제함
    derailment_agent = autogen.AssistantAgent(
        name="탈선유도자",
        system_message="""당신은 반복 방지 전문가입니다.
앞선 두 사람의 논의를 보고:

1. 본 회의에서 이미 나온 키워드/주제와 겹치는 부분을 찾아내세요
2. 겹치는 부분은 빼고, 아직 안 나온 UX 관점을 찾으세요
3. "이미 X는 논의됐으니, 대신 Y 관점에서 보면 어떨까?"를 제시하세요
4. 최종적으로 본 회의에 전달할 하나의 정제된 UX 의견을 만드세요
   - 반드시 이전 발언과 다른 키워드/각도여야 합니다
   - 구체적 사용자 시나리오나 행동 근거를 포함하세요
TERMINATE""",
        llm_config=llm_config_inner,
    )

    # 내부 GroupChat
    inner_groupchat = autogen.GroupChat(
        agents=[scenario_analyst, accessibility_reviewer, derailment_agent],
        messages=[],
        max_round=6,  # 내부 검토는 짧게
        speaker_selection_method="round_robin",  # 순서대로 한 바퀴
        send_introductions=True,
    )

    inner_manager = autogen.GroupChatManager(
        groupchat=inner_groupchat,
        llm_config=llm_config_inner,
    )

    # SocietyOfMindAgent로 감싸기
    ux_researcher = SocietyOfMindAgent(
        name="UX리서처",
        chat_manager=inner_manager,
        llm_config=llm_config_ux,  # response_preparer가 이 LLM(llama-3.3-70b)을 사용
        response_preparer=(
            "위 내부 검토를 바탕으로, 본 회의에 전달할 UX 관점의 의견을 "
            "하나로 정제하세요. 내부 검토 과정은 언급하지 마세요. "
            "반드시 구체적 사용자 시나리오나 행동 근거를 포함하세요. "
            "'~는 어떨까?' 형식의 제안으로 마무리하세요."
        ),
        description=(
            "UX 리서처. 사용자 행동 시나리오 기반으로 추론한다. "
            "사용자 경험, 접근성, 사용자 여정에 관한 논의가 필요할 때 지명한다."
        ),
    )

    return ux_researcher
```

**AG2 SocietyOfMindAgent 내부 동작:**
1. 본 회의에서 UX리서처 차례가 되면 `generate_inner_monologue_reply()` 호출
2. 본 회의의 전체 메시지 히스토리가 내부 에이전트들에게 컨텍스트로 전달됨
3. 내부 GroupChat이 `max_round`만큼 돌아감
4. 내부 대화 종료 후 `response_preparer` 프롬프트로 LLM이 최종 응답 생성
5. 이 응답만 본 회의에 전달됨 (내부 대화는 본 회의 messages에 안 쌓임)

### 3D 디자이너

```python
# agents/designer_3d.py — 같은 패턴, 내부 에이전트만 다름

def create_designer_3d(llm_config_3d, llm_config_inner):
    # 내부 검토 에이전트:
    # - 조형분석가: 형태/구조/공간 배치 관점
    # - 시각레퍼런스전문가: 다른 게임/영화/앱의 구체적 사례를 대고 비교
    # - 탈선유도자: 이미 나온 시각적 키워드 걸러내고 새 방향 강제

    # response_preparer — mixtral-8x7b 사용
    response_preparer = (
        "위 내부 검토를 바탕으로, 본 회의에 전달할 시각/공간 디자인 의견을 "
        "하나로 정제하세요. 내부 검토 과정은 언급하지 마세요. "
        "반드시 구체적 레퍼런스(게임, 영화, 앱 등의 실제 사례)를 포함하세요. "
        "'~는 어떨까?' 형식의 제안으로 마무리하세요."
    )

    description = (
        "3D 디자이너. 시각적 레퍼런스를 대고 변형 제안하는 방식으로 추론한다. "
        "시각적 표현, 공간 배치, 아트 방향에 관한 논의가 필요할 때 지명한다."
    )
    # ... (create_ux_researcher와 동일 패턴, llm_config_3d 사용)
```

### 언리얼 개발자

```python
# agents/unreal_dev.py — 같은 패턴

def create_unreal_dev(llm_config_unreal, llm_config_inner, llm_config_inner_gemma):
    # 내부 검토 에이전트:
    # - 퍼포먼스분석가: llm_config_inner (llama-8b)
    # - 구현전문가: llm_config_inner_gemma (gemma2-9b) ← 모델 이질성 확보 지점
    # - 탈선유도자: llm_config_inner (llama-8b)

    # response_preparer — llama-70b 사용 (본체)
    response_preparer = (
        "위 내부 검토를 바탕으로, 본 회의에 전달할 기술 구현 의견을 "
        "하나로 정제하세요. 내부 검토 과정은 언급하지 마세요. "
        "반드시 기술 스펙이나 예상 공수를 포함하세요. "
        "불가능한 것이 있으면 대안과 함께 제시하세요. "
        "'~는 어떨까?' 형식의 제안으로 마무리하세요."
    )

    description = (
        "언리얼 개발자. 기술 스펙과 실현 가능성 기반으로 추론한다. "
        "기술 구현, 성능, 실현 가능성에 관한 논의가 필요할 때 지명한다."
    )
    # ... (create_ux_researcher와 동일 패턴, llm_config_unreal 사용)
```

### Synthesizer (일반 에이전트, SocietyOfMind 아님)

```python
# agents/synthesizer.py

# === 아이디어 보드 업데이트 tool (AG2 내장 함수 호출 기능) ===
def update_idea_board(action: str, content: str, context_variables: ContextVariables) -> str:
    """아이디어 보드를 업데이트합니다.

    Args:
        action: "추가", "연결", "분기" 중 하나
        content: 업데이트할 내용
        context_variables: AG2가 자동 주입 (파라미터 이름이 context_variables이면 자동 주입됨)
    """
    current_board = context_variables.get("idea_board", "")
    updated = f"{current_board}\n[{action}] {content}"
    context_variables.set("idea_board", updated)
    return f"보드 업데이트 완료: [{action}] {content}"


synthesizer = autogen.AssistantAgent(
    name="Synthesizer",
    system_message="""당신은 디자인 회의의 정리자입니다.

역할:
1. 다른 참여자들의 발언을 듣고 update_idea_board 함수를 호출해서 보드를 업데이트하세요
2. 겹치는 아이디어를 연결하고, 빠진 관점을 지적합니다
3. "둘 다 좋다"는 금지. 반드시 차이점과 트레이드오프를 명시하세요

update_idea_board 함수 사용법:
- action="추가": 새 아이디어 추가
- action="연결": 기존 아이디어 간 연결 발견
- action="분기": 기존 아이디어에서 새 방향 갈라짐

정리가 필요한 시점에만 발언하세요. 매번 말할 필요 없습니다.""",
    llm_config=llm_config_main,
    functions=[update_idea_board],  # AG2 내장 tool 등록
    description=(
        "Synthesizer. 아이디어를 종합하고 보드를 관리한다. "
        "논의가 분산되거나, 정리가 필요하거나, 아이디어 간 연결이 보일 때 지명한다."
    ),
)
```

---

## 4. 반복 방지 — 키워드 추적 + 금지어 동적 주입

### 아이디어 보드 + 키워드 추적 초기화

```python
# meeting/idea_board.py
from autogen.agentchat.group.context_variables import ContextVariables

def create_idea_board(brief_summary: str, meeting_type: str):
    return ContextVariables(data={
        "meeting_type": meeting_type,
        "brief": brief_summary,
        "idea_board": "아직 아이디어 없음",
        "round_number": 1,
        "explored_directions": "",
        "unexplored_directions": "",
        # === 반복 방지: 에이전트별 사용 키워드 추적 ===
        "used_keywords_UX리서처": "",
        "used_keywords_3D디자이너": "",
        "used_keywords_언리얼개발자": "",
    })
```

### 매 턴 시스템 메시지에 보드 + 금지 키워드 주입

```python
from autogen.agentchat.conversable_agent import UpdateSystemMessage

def inject_board_and_keywords(agent, messages):
    """매 턴 시스템 메시지 앞에 아이디어 보드 + 금지 키워드를 주입"""
    ctx = agent.context_variables
    base_system_msg = agent._original_system_message  # 별도 저장 필요

    # 이 에이전트가 이전에 쓴 키워드 목록
    used_key = f"used_keywords_{agent.name}"
    used_keywords = ctx.get(used_key, "")

    board_section = f"""
=== 현재 회의 상태 ===
[회의 유형]: {ctx.get("meeting_type", "미정")}
[라운드]: {ctx.get("round_number", 1)}
[아이디어 보드]:
{ctx.get("idea_board", "없음")}
[탐색된 방향]: {ctx.get("explored_directions", "없음")}
[미탐색 방향]: {ctx.get("unexplored_directions", "없음")}
=====================
"""

    keyword_section = ""
    if used_keywords:
        keyword_section = f"""
=== 반복 금지 ===
당신이 이전에 이미 언급한 주제: [{used_keywords}]
이번 발언에서는 위 주제를 반복하지 마세요.
완전히 다른 각도에서 접근하세요.
=================
"""

    return board_section + keyword_section + base_system_msg
```

### context_variables 공유 설정

AG2의 old-style GroupChat에서는 `context_variables`가 **자동 공유 안 됨**. 수동으로 같은 인스턴스를 할당해야 함:

```python
# 모든 에이전트에 같은 context_variables 할당
idea_board = create_idea_board(brief, meeting_type)

for agent in [ux_researcher, designer_3d, unreal_dev, synthesizer, user]:
    agent.context_variables = idea_board
```

---

## 5. 커스텀 Speaker Selection — 아이디어 커버리지 기반

### 템플릿 커스터마이징

```python
# meeting/speaker.py

# speaker selection은 영어로 작성 (llama-8b의 한국어가 불안정하므로)
SPEAKER_SELECTION_MESSAGE = """You are the moderator of a design meeting.
Participants and roles:
{roles}

Choose the next speaker using these criteria:
1. If there are underdeveloped ideas on the board, pick the person
   who can best expand on them.
2. If all ideas are sufficiently explored, pick the person with
   the most different perspective.
3. Do not let the same person speak more than 3 times in a row.
4. Only pick Synthesizer when 3+ ideas have accumulated or
   the discussion is becoming scattered.

Return exactly one name from {agentlist}."""

SPEAKER_SELECTION_PROMPT = (
    "Read the conversation above. Pick the next speaker from {agentlist}. "
    "Return only the name."
)
```

### GroupChat에 적용

```python
groupchat = autogen.GroupChat(
    agents=[user, ux_researcher, designer_3d, unreal_dev, synthesizer],
    messages=[],
    max_round=30,  # 상한선만 넉넉하게, 실제론 자연 종료됨
    send_introductions=True,
    speaker_selection_method="auto",
    select_speaker_message_template=SPEAKER_SELECTION_MESSAGE,
    select_speaker_prompt_template=SPEAKER_SELECTION_PROMPT,
    select_speaker_auto_llm_config=llm_config_selector,
    allow_repeat_speaker=True,  # 필요하면 연속 발언 허용 (템플릿에서 3회 제한)
)
```

**`{roles}` 플레이스홀더**: 각 에이전트의 `name`과 `description`을 자동 삽입.
**`{agentlist}` 플레이스홀더**: `[UX리서처, 3D디자이너, 언리얼개발자, Synthesizer, Orwiss]` 형태로 삽입.
**`select_speaker_auto_llm_config`**: speaker selection에만 별도 LLM 사용 가능. 본 회의 에이전트와 다른 모델/temperature 가능.

---

## 6. 발언 품질 가드레일 — 최후의 안전망

모델 이질성(1단계)과 내부 검토 탈선 유도자(2단계)로 대부분 걸러지지만,
최후의 안전망으로 `process_message_before_send` 훅을 건다.

```python
# meeting/guardrails.py
import re

def extract_keywords(text, top_n=5):
    """발언에서 핵심 키워드 추출 (간단한 규칙 기반)"""
    # 실제 구현에서는 LLM 호출로 더 정확하게 할 수 있음
    # Groq는 빨라서 추가 호출 레이턴시 부담 적음
    words = re.findall(r'[가-힣a-zA-Z]{2,}', text)
    # 빈도 기반 상위 N개 (불용어 제외)
    stopwords = {"그리고", "하지만", "그래서", "때문에", "이런", "저런", "있는", "하는",
                 "것이", "수도", "대해", "경우", "통해", "위해", "이를"}
    freq = {}
    for w in words:
        if w not in stopwords:
            freq[w] = freq.get(w, 0) + 1
    sorted_words = sorted(freq, key=freq.get, reverse=True)
    return sorted_words[:top_n]


def novelty_guard(*, sender, message, recipient, silent):
    """발언 직전에 키워드를 추출하고, 사용 키워드 목록을 업데이트"""
    content = message if isinstance(message, str) else message.get("content", "")

    # 빈 메시지나 사용자 메시지는 통과
    if not content or sender.name == "Orwiss":
        return message

    # 키워드 추출
    new_keywords = extract_keywords(content)

    # context_variables에 사용 키워드 누적
    if hasattr(sender, 'context_variables') and sender.context_variables:
        ctx = sender.context_variables
        used_key = f"used_keywords_{sender.name}"
        existing = ctx.get(used_key, "")
        existing_list = [k.strip() for k in existing.split(",") if k.strip()]
        updated = list(set(existing_list + new_keywords))
        ctx.set(used_key, ", ".join(updated))

    return message


# 에이전트에 등록
# for agent in [ux_researcher, designer_3d, unreal_dev, synthesizer]:
#     agent.register_hook("process_message_before_send", novelty_guard)
```

**훅 동작 방식:**
- `register_hook("process_message_before_send", func)` → 에이전트가 메시지를 보내기 직전에 호출
- 시그니처: `func(*, sender, message, recipient, silent) -> message`
- 반환된 message가 실제로 전송됨. 수정해서 반환하면 수정된 버전이 전달됨
- 여러 훅 등록 시 순서대로 체인됨

**주의:** SocietyOfMindAgent의 경우, 이 훅은 내부 검토가 끝나고 `response_preparer`가 만든 최종 응답에 대해 작동함. 내부 검토 개별 발언에는 적용 안 됨.

**반복 방지 전체 흐름 정리:**
```
1단계 (모델 이질성): 에이전트마다 다른 모델 → 근본적으로 다른 연상 패턴
    ↓
2단계 (탈선 유도자): 내부 검토에서 "이미 나온 거 말고 다른 거" 강제
    ↓
3단계 (키워드 추적): novelty_guard 훅이 매 발언의 키워드를 추출/누적
    ↓
4단계 (금지어 주입): inject_board_and_keywords가 매 턴 시스템 메시지에 금지 목록 주입
    ↓
결과: 에이전트가 같은 키워드에 갇혀 반복하는 것을 다층적으로 방지
```

---

## 7. Stage 2 라운드 실행 — initiate_chats로 발산-수렴 사이클

### 라운드별 GroupChat 생성

```python
# meeting/stage2.py

def create_round_groupchat(
    agents,
    user,
    round_number,
    max_round=15,
    llm_config_selector=None,
):
    """라운드별 GroupChat + Manager 생성"""
    groupchat = autogen.GroupChat(
        agents=[user] + agents,
        messages=[],
        max_round=max_round,
        send_introductions=(round_number == 1),  # 첫 라운드만 소개
        speaker_selection_method="auto",
        select_speaker_message_template=SPEAKER_SELECTION_MESSAGE,
        select_speaker_prompt_template=SPEAKER_SELECTION_PROMPT,
        select_speaker_auto_llm_config=llm_config_selector,
        allow_repeat_speaker=True,
    )

    manager = autogen.GroupChatManager(
        groupchat=groupchat,
        llm_config=llm_config_selector,
        is_termination_msg=lambda msg: "ROUND_END" in msg.get("content", ""),
    )

    return groupchat, manager
```

### 회의 유형별 라운드 구성

```python
def build_chat_queue(meeting_type, brief, agents, user, llm_config_selector):
    """회의 유형에 따라 sequential chat queue 구성"""

    if meeting_type == "발산형":
        return build_divergent_queue(brief, agents, user, llm_config_selector)
    elif meeting_type == "개선형":
        return build_improvement_queue(brief, agents, user, llm_config_selector)
    elif meeting_type == "선택형":
        return build_selection_queue(brief, agents, user, llm_config_selector)


def build_divergent_queue(brief, agents, user, llm_config_selector):
    """발산형: 넓게 → 좁혀서 → 마무리"""

    gc1, mgr1 = create_round_groupchat(agents, user, round_number=1, max_round=30)
    gc2, mgr2 = create_round_groupchat(agents, user, round_number=2, max_round=30)
    gc3, mgr3 = create_round_groupchat(agents, user, round_number=3, max_round=10)

    return [
        {
            "sender": user,
            "recipient": mgr1,
            "message": f"=== 디자인 회의 Round 1: 넓게 탐색 ===\n\n{brief}\n\n"
                       "자유롭게 아이디어를 내주세요. 어떤 방향이든 환영합니다.",
            "max_turns": 30,
            "summary_method": "reflection_with_llm",
            "summary_args": {
                "summary_prompt": (
                    "이 라운드에서 나온 아이디어를 정리하세요. "
                    "1. 가장 발전 가능성이 큰 아이디어 2개 (이유와 함께) "
                    "2. 아직 탐색되지 않은 방향 1개 "
                    "3. 해소되지 않은 긴장이나 트레이드오프"
                ),
            },
        },
        {
            "sender": user,
            "recipient": mgr2,
            "message": "=== 디자인 회의 Round 2: 좁혀서 깊이 ===\n\n"
                       "이전 라운드에서 유망했던 아이디어를 발전시키고, "
                       "미탐색 방향도 탐색하세요.",
            # carryover는 자동으로 Round 1의 summary가 들어감
            "max_turns": 30,
            "summary_method": "reflection_with_llm",
            "summary_args": {
                "summary_prompt": (
                    "이 라운드에서 발전된 내용을 정리하세요. "
                    "1. 가장 구체화된 방향 2개 (장단점 포함) "
                    "2. 남은 미해결 질문 "
                    "3. 다음 라운드에서 결정해야 할 것"
                ),
            },
        },
        {
            "sender": user,
            "recipient": mgr3,
            "message": "=== 디자인 회의 Round 3: 마무리 ===\n\n"
                       "가장 유망한 방향을 구체화하고 최종 정리하세요. "
                       "Synthesizer가 선택지 A/B + 각각 얻는 것/잃는 것 + "
                       "미해결 이슈를 정리해주세요.",
            "max_turns": 30,
            "summary_method": "reflection_with_llm",
            "summary_args": {
                "summary_prompt": (
                    "최종 회의록을 작성하세요: "
                    "1. 논의된 주요 아이디어 (발전 과정 포함) "
                    "2. 최종 선택지 A/B (각각 얻는 것, 잃는 것) "
                    "3. 권고 (있다면) "
                    "4. 미해결 이슈와 다음 단계"
                ),
            },
        },
    ]


def build_improvement_queue(brief, agents, user, llm_config_selector):
    """개선형: 문제 발견 → 해결책 → 구체화"""

    gc1, mgr1 = create_round_groupchat(agents, user, round_number=1, max_round=30)
    gc2, mgr2 = create_round_groupchat(agents, user, round_number=2, max_round=30)
    gc3, mgr3 = create_round_groupchat(agents, user, round_number=3, max_round=10)

    return [
        {
            "sender": user,
            "recipient": mgr1,
            "message": f"=== 디자인 회의 Round 1: 문제 발견 ===\n\n{brief}\n\n"
                       "현재 디자인의 문제점만 찾아주세요. "
                       "해결책은 아직 내지 마세요. "
                       "'목표 X에 비추어 Y가 문제다. 왜냐하면 Z' 형식으로.",
            "max_turns": 30,
            "summary_method": "reflection_with_llm",
            "summary_args": {
                "summary_prompt": (
                    "발견된 문제점을 정리하세요. "
                    "1. 합의된 핵심 문제 (우선순위 순) "
                    "2. 의견이 갈리는 문제 "
                    "3. 아직 충분히 검토 안 된 영역"
                ),
            },
        },
        {
            "sender": user,
            "recipient": mgr2,
            "message": "=== 디자인 회의 Round 2: 해결책 탐색 ===\n\n"
                       "이전 라운드에서 합의된 문제에 대해 해결책을 제안하세요. "
                       "'만약 ~라면?' 형식의 탐색적 제안을 해주세요.",
            "max_turns": 30,
            "summary_method": "reflection_with_llm",
            "summary_args": {
                "summary_prompt": (
                    "제안된 해결책을 정리하세요. "
                    "1. 문제별 해결책 후보 "
                    "2. 각 해결책의 장단점 "
                    "3. 가장 유망한 방향"
                ),
            },
        },
        {
            "sender": user,
            "recipient": mgr3,
            "message": "=== 디자인 회의 Round 3: 구체화 ===\n\n"
                       "가장 유망한 해결책을 구체화하세요.",
            "max_turns": 30,
            "summary_method": "reflection_with_llm",
            "summary_args": {
                "summary_prompt": (
                    "최종 회의록을 작성하세요: "
                    "1. 원래 문제 → 선택된 해결책 (매핑) "
                    "2. 해결책 실행 시 얻는 것/잃는 것 "
                    "3. 미해결 이슈와 다음 단계"
                ),
            },
        },
    ]


def build_selection_queue(brief, agents, user, llm_config_selector):
    """선택형: 각 후보 탐색 → 비교"""

    gc1, mgr1 = create_round_groupchat(agents, user, round_number=1, max_round=30)
    gc2, mgr2 = create_round_groupchat(agents, user, round_number=2, max_round=10)

    return [
        {
            "sender": user,
            "recipient": mgr1,
            "message": f"=== 디자인 회의 Round 1: 후보별 탐색 ===\n\n{brief}\n\n"
                       "각 후보에 대해 각자의 관점에서 평가해주세요. "
                       "장점과 단점을 모두 찾으세요.",
            "max_turns": 30,
            "summary_method": "reflection_with_llm",
            "summary_args": {
                "summary_prompt": (
                    "후보별 평가를 정리하세요. "
                    "1. 후보별 장단점 (관점별) "
                    "2. 의견이 갈리는 지점 "
                    "3. 아직 검토 안 된 측면"
                ),
            },
        },
        {
            "sender": user,
            "recipient": mgr2,
            "message": "=== 디자인 회의 Round 2: 비교 + 결정 ===\n\n"
                       "후보를 직접 비교하세요. "
                       "새로운 조합이나 하이브리드도 가능합니다.",
            "max_turns": 30,
            "summary_method": "reflection_with_llm",
            "summary_args": {
                "summary_prompt": (
                    "최종 비교 결과를 정리하세요: "
                    "1. 후보별 총평 "
                    "2. 추천 (있다면, 근거와 함께) "
                    "3. 하이브리드 가능성 "
                    "4. 결정 전 확인해야 할 것"
                ),
            },
        },
    ]
```

### 실행

```python
# meeting/stage2.py

def run_stage2(chat_queue, idea_board):
    """Stage 2 실행"""
    results = autogen.initiate_chats(chat_queue)
    # results: list[ChatResult]
    # results[-1].summary가 최종 회의록
    return results
```

**initiate_chats의 carryover 동작:**
- 첫 번째 chat: `carryover` 없음 (직접 message에 brief 포함)
- 두 번째 chat: 자동으로 `results[0].summary`가 carryover로 붙음
- 세 번째 chat: `results[0].summary` + `results[1].summary`가 carryover
- carryover는 message 뒤에 `"\nContext: \n" + summary` 형태로 붙음

---

## 8. main.py — 전체 흐름

```python
# main.py
import json
import autogen
from config import (
    llm_config_main, llm_config_ux, llm_config_3d,
    llm_config_unreal, llm_config_inner, llm_config_inner_gemma,
    llm_config_selector,
)
from agents.facilitator import facilitator
from agents.ux_researcher import create_ux_researcher
from agents.designer_3d import create_designer_3d
from agents.unreal_dev import create_unreal_dev
from agents.synthesizer import synthesizer
from meeting.stage1 import run_stage1
from meeting.stage2 import run_stage2, build_chat_queue
from meeting.idea_board import create_idea_board
from meeting.guardrails import novelty_guard

# === 에이전트 생성 (모델 이질성 적용) ===
user = autogen.UserProxyAgent(
    name="Orwiss",
    human_input_mode="ALWAYS",
    code_execution_config=False,
    is_termination_msg=lambda msg: "TERMINATE" in msg.get("content", ""),
)

ux_researcher = create_ux_researcher(llm_config_ux, llm_config_inner)
designer_3d = create_designer_3d(llm_config_3d, llm_config_inner)
unreal_dev = create_unreal_dev(llm_config_unreal, llm_config_inner, llm_config_inner_gemma)

agents = [ux_researcher, designer_3d, unreal_dev, synthesizer]

# 가드레일 등록 (반복 방지 3단계: 키워드 추적)
for agent in agents:
    agent.register_hook("process_message_before_send", novelty_guard)

# === Stage 1: 사전 정제 ===
stage1_result = run_stage1(user, facilitator)
brief_data = json.loads(stage1_result.summary)
meeting_type = brief_data["meeting_type"]

# === 아이디어 보드 초기화 (반복 방지 4단계: 금지어 주입) ===
idea_board = create_idea_board(stage1_result.summary, meeting_type)
for agent in agents + [user]:
    agent.context_variables = idea_board

# === Stage 2: 디자인 회의 ===
chat_queue = build_chat_queue(
    meeting_type=meeting_type,
    brief=stage1_result.summary,
    agents=agents,
    user=user,
    llm_config_selector=llm_config_selector,
)

results = run_stage2(chat_queue, idea_board)

# === 최종 회의록 출력 ===
print("\n" + "=" * 60)
print("최종 회의록")
print("=" * 60)
print(results[-1].summary)
```

---

## 9. 알려진 제약 / 주의사항

### SocietyOfMindAgent 제약
- `update_agent_state_before_reply` 파라미터를 직접 지원하지 않음. 아이디어 보드를 내부 검토에 주입하려면, 내부 에이전트의 system_message를 수동으로 업데이트하거나, `register_hook("process_all_messages_before_reply", ...)`을 사용해야 함
- 매 턴마다 내부 GroupChat을 리셋하고 처음부터 돌림. 내부 검토 간 상태 유지 안 됨
- 본 회의 히스토리가 내부 에이전트에게 전달되므로, 본 회의가 길어지면 내부 검토의 토큰 소모 급증

### context_variables 공유 제약
- old-style GroupChat에서는 자동 공유 안 됨. 수동으로 같은 인스턴스 할당 필요
- SocietyOfMindAgent의 내부 에이전트들은 외부 context_variables에 접근 불가. 외부 에이전트(SocietyOfMindAgent 자체)만 접근 가능
- 탈선 유도자가 "이미 나온 키워드"를 알려면, response_preparer에서 context_variables의 키워드 목록을 본 회의 히스토리와 함께 전달하는 방법이 필요함

### Groq 관련
- Groq는 rate limit이 있음 (모델별 다름). SocietyOfMind 내부 검토가 동시에 여러 요청을 보내면 걸릴 수 있음. rate limit 터지면 max_round를 줄이거나 retry 로직 추가
- mixtral-8x7b-32768은 컨텍스트 윈도우가 32K로 넉넉. gemma2-9b-it은 8K로 짧아서 내부 검토 에이전트에만 배정 (본체로 쓰면 대화 길어질 때 터짐)
- llama-3.1-8b-instant (내부 검토, speaker selection)는 가장 빠르지만 한국어 성능이 약함 → speaker selection 프롬프트는 영어로 작성. 내부 검토는 한국어로 시도 후 문제 생기면 영어 전환

### 토큰 관리
- 3개 SocietyOfMindAgent × 내부 3명 × 본 회의 히스토리 = 토큰 소모 큼
- `MessageHistoryLimiter`를 내부 검토에는 적용하기 어려움 (매 턴 리셋되므로)
- 본 회의에 `MessageTokenLimiter`를 적용하거나, `max_round`를 보수적으로 설정하는 것이 현실적

### Synthesizer의 아이디어 보드 업데이트
- AG2 내장 functions 파라미터로 `update_idea_board` tool 등록 (섹션 3 참조)
- `context_variables` 파라미터 이름을 가진 함수 인자는 AG2가 자동 주입하고 LLM 스키마에서 제외함
- tool 실행은 별도 UserProxyAgent가 필요할 수 있음 (AG2의 tool execution 흐름 확인 필요)

### human_input_mode="ALWAYS" in GroupChat
- Orwiss가 speaker로 선택될 때만 인풋 프롬프트가 뜸
- speaker_selection이 Orwiss를 안 고르면 개입 기회 없음
- 대안: GroupChatManager에 human_input_mode="ALWAYS" → 매 턴 개입 가능하지만 너무 잦을 수 있음
- 현실적 대안: 커스텀 speaker_selection에서 N턴마다 Orwiss를 강제 선택

---

## 10. 점진적 구현 순서

```
Step 1: 기본 동작 확인
- AG2 설치, Groq 연동 확인
- 간단한 3인 GroupChat 돌려보기 (모델 이질성 확인)
- SocietyOfMindAgent 1개 만들어서 내부 검토 동작 확인

Step 2: Stage 1 구현
- Facilitator 1:1 대화
- summary_method로 브리프 생성 확인
- 회의 유형 분류 테스트

Step 3: Stage 2 기본 구현
- 3개 SocietyOfMindAgent(탈선 유도자 포함) + Synthesizer로 단일 GroupChat
- speaker selection 템플릿 커스터마이징
- human_input_mode 동작 확인

Step 4: 반복 방지 확인
- 모델 이질성으로 에이전트별 발언 다양성 확인
- 탈선 유도자가 실제로 새 방향을 유도하는지 확인
- novelty_guard 키워드 추적 동작 확인
- 금지어 주입으로 반복이 줄어드는지 확인

Step 5: 라운드 시스템
- initiate_chats로 라운드 연결
- carryover로 라운드 간 컨텍스트 전달 확인
- 회의 유형별 라운드 구성

Step 6: 아이디어 보드
- context_variables 공유 설정
- 보드 업데이트 메커니즘 (tool 또는 훅)
- 시스템 메시지 주입

Step 7: 품질 개선
- 프롬프트 반복 수정
- 토큰 관리 최적화
- Groq rate limit 대응
```

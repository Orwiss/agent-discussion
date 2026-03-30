import autogen
from autogen.agentchat.contrib.society_of_mind_agent import SocietyOfMindAgent
from agents.preparer import make_response_preparer


def create_ux_researcher(llm_config_ux, llm_config_inner, brief=""):
    ctx = f"\n\n=== 회의 맥락 ===\n{brief}\n==================\n" if brief else ""

    scenario_analyst = autogen.AssistantAgent(
        name="시나리오분석가",
        system_message=f"""사용자 시나리오 전문가.{ctx}
구체적인 상황을 묘사하세요. "친근하다" "신뢰감" 같은 추상어 금지.
대신: "키 150cm의 둥근 로봇이 사용자 앞에 나타나면, 10대는 장난감처럼 만져보려 하고 40대는 안내 키오스크처럼 거리를 둘 것이다"
이런 식으로 구체적 행동을 묘사. 3문장 이내.""",
        llm_config=llm_config_inner,
    )

    impression_reviewer = autogen.AssistantAgent(
        name="인상평가자",
        system_message=f"""첫인상 평가자.{ctx}
시나리오분석가가 안 다룬 시각적 측면만 다루세요.
"이 캐릭터는 실리콘 같은 매끈한 흰색 표면에 LED 눈이 있어서 애플 제품 같은 깔끔함이 느껴진다"
이런 식으로 외형의 시각적 특징 → 사용자 인상 연결. 추상어 금지. 2문장.""",
        llm_config=llm_config_inner,
    )

    derailment_agent = autogen.AssistantAgent(
        name="탈선유도자",
        system_message=f"""앞사람들이 전혀 언급 안 한 사용자 유형 하나를 골라서, 그 사용자가 이 디자인을 보면 어떻게 행동할지 묘사하세요.{ctx}
예: "시각장애 보조견과 함께 온 사용자는 이 에이전트의 소리와 촉감에 의존할 것이다"
앞사람 내용 반복 금지. 2문장. TERMINATE""",
        llm_config=llm_config_inner,
    )

    inner_groupchat = autogen.GroupChat(
        agents=[scenario_analyst, impression_reviewer, derailment_agent],
        messages=[],
        max_round=6,
        speaker_selection_method="round_robin",
        send_introductions=True,
    )

    inner_manager = autogen.GroupChatManager(
        groupchat=inner_groupchat,
        llm_config=llm_config_inner,
    )

    ux_researcher = SocietyOfMindAgent(
        name="UXResearcher",
        chat_manager=inner_manager,
        llm_config=llm_config_ux,
        response_preparer=make_response_preparer(
            "위 내부 검토를 정제. [보드:], PHASE_ADVANCE, 내부 에이전트 이름 사용 금지.\n"
            "사용자 피드백/브리프 제약 반드시 따를 것.\n\n"
            "## 나쁜 답변:\n"
            "'사용자에게 친근하고 신뢰감을 주는 디자인이 필요합니다.'\n\n"
            "## 좋은 답변:\n"
            "'이걸 처음 본 아이는 인형처럼 안아보려 할 것 같고, "
            "직장인은 에어팟 케이스처럼 주머니에 넣으려 할 것 같다. "
            "표면이 차갑고 딱딱하면 아이가 금방 놓을 테니 쿠션 같은 촉감은 어떨까?'\n\n"
            "수치 금지. 행동+감각으로 묘사. 3문장 이내."
        ),
        description=(
            "UX 리서처. 사용자 행동 시나리오 기반으로 추론한다. "
            "사용자 경험, 첫인상, 사용자 여정에 관한 논의가 필요할 때 지명한다."
        ),
    )

    return ux_researcher

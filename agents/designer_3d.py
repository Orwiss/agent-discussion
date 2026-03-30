import autogen
from autogen.agentchat.contrib.society_of_mind_agent import SocietyOfMindAgent
from agents.preparer import make_response_preparer


def create_designer_3d(llm_config_3d, llm_config_inner, brief=""):
    ctx = f"\n\n=== 회의 맥락 ===\n{brief}\n==================\n" if brief else ""

    form_analyst = autogen.AssistantAgent(
        name="조형분석가",
        system_message=f"""3D 조형 전문가.{ctx}
"친근한 디자인" 같은 추상어 금지. 구체적 형태를 묘사하세요.
예: "머리가 몸통의 1/3인 SD 비율, 팔다리는 원통형, 전체 높이 1m, 표면은 무광 화이트"
형태·비율·실루엣을 수치나 비유로 묘사. 3문장.""",
        llm_config=llm_config_inner,
    )

    reference_expert = autogen.AssistantAgent(
        name="시각레퍼런스전문가",
        system_message=f"""시각 레퍼런스 전문가.{ctx}
조형분석가의 형태에 가장 가까운 실존 작품 1개를 대고, 거기서 뭘 가져오고 뭘 바꿔야 하는지 구체적으로.
월-E, 베이맥스 같은 뻔한 레퍼런스는 피하세요. 덜 알려진 작품을 우선.
2문장.""",
        llm_config=llm_config_inner,
    )

    derailment_agent = autogen.AssistantAgent(
        name="탈선유도자",
        system_message=f"""앞사람들과 완전히 다른 형태 하나만 제안.{ctx}
앞에서 인간형이 나왔으면 비인간형을, 유기적이면 기하학적을, 큰 것이면 작은 것을.
"바닥에서 솟아오르는 20cm 크리스탈 기둥" 같은 구체적 묘사. 2문장. TERMINATE""",
        llm_config=llm_config_inner,
    )

    inner_groupchat = autogen.GroupChat(
        agents=[form_analyst, reference_expert, derailment_agent],
        messages=[],
        max_round=6,
        speaker_selection_method="round_robin",
        send_introductions=True,
    )

    inner_manager = autogen.GroupChatManager(
        groupchat=inner_groupchat,
        llm_config=llm_config_inner,
    )

    designer_3d = SocietyOfMindAgent(
        name="Designer3D",
        chat_manager=inner_manager,
        llm_config=llm_config_3d,
        response_preparer=make_response_preparer(
            "위 내부 검토를 정제. [보드:], PHASE_ADVANCE, 내부 에이전트 이름 사용 금지.\n"
            "사용자 피드백/브리프 제약 반드시 따를 것.\n\n"
            "## 나쁜 답변:\n"
            "'높이 150cm, 머리비율 1/5, 은색 메탈릭, 원통형 팔다리'\n\n"
            "## 좋은 답변:\n"
            "'Journey의 여행자처럼 바람에 펄럭이는 천 망토를 두르고, "
            "얼굴 없이 Ori처럼 가슴에서 따뜻한 빛만 새어나오는 존재는 어떨까? "
            "만지면 구름 같은 촉감일 것 같다.'\n\n"
            "수치(cm, 비율, %) 금지. 비유와 감각으로 묘사. 3문장 이내."
        ),
        description=(
            "3D 디자이너. 시각적 레퍼런스를 대고 변형 제안하는 방식으로 추론한다. "
            "시각적 표현, 공간 배치, 아트 방향에 관한 논의가 필요할 때 지명한다."
        ),
    )

    return designer_3d

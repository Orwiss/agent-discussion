import autogen
from autogen.agentchat.contrib.society_of_mind_agent import SocietyOfMindAgent
from agents.preparer import make_response_preparer


def create_unreal_dev(llm_config_unreal, llm_config_inner, llm_config_inner_alt, brief=""):
    ctx = f"\n\n=== 회의 맥락 ===\n{brief}\n==================\n" if brief else ""

    performance_analyst = autogen.AssistantAgent(
        name="퍼포먼스분석가",
        system_message=f"""UE5 도구 전문가.{ctx}
"메타휴먼 에디터에서 Body 탭의 Proportion 슬라이더로 머리 비율을 1.3배로 키운다"
이런 식으로 구체적 도구 이름 + 어떤 설정을 만지는지. 추상적 설명 금지.
성능 이야기 금지. 3문장.""",
        llm_config=llm_config_inner,
    )

    # gemma2로 모델 이질성 확보
    implementation_expert = autogen.AssistantAgent(
        name="구현전문가",
        system_message=f"""UE5 아트 파이프라인 전문가.{ctx}
퍼포먼스분석가가 말한 것과 다른 도구/방법만 다루세요.
"Substance Painter에서 메탈릭 마스크를 그려서 UE5로 임포트" 같은 구체적 워크플로우.
반복 금지. 3문장.""",
        llm_config=llm_config_inner_alt,
    )

    derailment_agent = autogen.AssistantAgent(
        name="탈선유도자",
        system_message=f"""앞사람들이 언급 안 한 UE5 도구나 에셋 하나를 소개.{ctx}
예: "Quixel Bridge에서 바위 텍스처를 가져와 에이전트 표면에 입히면 골렘 느낌"
앞사람 반복 금지. 2문장. TERMINATE""",
        llm_config=llm_config_inner,
    )

    inner_groupchat = autogen.GroupChat(
        agents=[performance_analyst, implementation_expert, derailment_agent],
        messages=[],
        max_round=6,
        speaker_selection_method="round_robin",
        send_introductions=True,
    )

    inner_manager = autogen.GroupChatManager(
        groupchat=inner_groupchat,
        llm_config=llm_config_inner,
    )

    unreal_dev = SocietyOfMindAgent(
        name="UnrealDev",
        chat_manager=inner_manager,
        llm_config=llm_config_unreal,
        response_preparer=make_response_preparer(
            "위 내부 검토를 정제. [보드:], PHASE_ADVANCE, 내부 에이전트 이름 사용 금지.\n"
            "사용자 피드백/브리프 제약 반드시 따를 것. 성능 이야기 금지.\n\n"
            "## 나쁜 답변:\n"
            "'메타휴먼 에디터와 머티리얼 레이어를 활용할 수 있습니다.'\n\n"
            "## 좋은 답변:\n"
            "'구름 같은 촉감을 내려면 MetaHuman Skin 탭에서 Subsurface를 올려서 "
            "빛이 살짝 통과하는 느낌을 주고, 안에서 빛이 나오는 건 Emissive Decal로 "
            "가슴 부분에만 씌우면 된다. 한 3시간이면 충분할 것 같다.'\n\n"
            "수치 나열 금지. 도구+방법+예상 시간만. 3문장 이내."
        ),
        description=(
            "언리얼 개발자. 기술 스펙과 실현 가능성 기반으로 추론한다. "
            "기술 구현, 성능, 실현 가능성에 관한 논의가 필요할 때 지명한다."
        ),
    )

    return unreal_dev

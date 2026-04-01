"""
실험용 단순 에이전트 — AssistantAgent만 사용.
독립/토론 양 조건에서 동일하게 사용.
토론 조건에서는 discussion=True로 상호 참조 지시 1줄 추가.

모델별 출력 습관이 다르므로 포맷 지시를 개별 조정.
"""
import autogen

# 공통 포맷 규칙 (모든 에이전트 공유)
_FORMAT_BASE = (
    "반드시 자연스러운 대화체로만 말하세요. "
    "불릿 포인트(-, *), 번호 매기기(1. 2. 3.), 소제목(##) 같은 마크다운 형식을 절대 쓰지 마세요. "
    '볼드(**강조**), 따옴표 강조("강조"), 꺾쇠 강조(「강조」) 같은 텍스트 꾸미기도 쓰지 마세요. '
    "한 번에 3~5문장으로, 동료한테 설명하듯 자연스럽게 이어서 말하세요."
)


def create_simple_ux(llm_config, brief="", discussion=False):
    ctx = f"\n\n=== 회의 맥락 ===\n{brief}\n==================\n" if brief else ""
    collab = "\n다른 에이전트의 의견을 참고하여 동의, 반박, 또는 발전시키세요." if discussion else ""
    return autogen.AssistantAgent(
        name="UXResearcher",
        system_message=f"""당신은 UX 리서처입니다. 회의에서 동료들과 자연스럽게 대화하듯 말하세요.{ctx}

사용자 경험, 사용성, 유저 시나리오 관점에서 아이디어를 제안합니다.
{_FORMAT_BASE}
길게 늘어놓지 말고 핵심만 짧게 말하세요.
다른 사람이 한 말을 그대로 반복하지 마세요. 참가자의 피드백은 최우선으로 반영하세요.{collab}""",
        llm_config=llm_config,
        description=(
            "UX 리서처. 사용자 행동과 첫인상 기반으로 이야기한다. "
            "사용자 경험, 첫인상, 감정 반응에 관한 논의가 필요할 때 지명한다."
        ),
    )


def create_simple_visual(llm_config, brief="", discussion=False):
    ctx = f"\n\n=== 회의 맥락 ===\n{brief}\n==================\n" if brief else ""
    collab = "\n다른 에이전트의 의견을 참고하여 동의, 반박, 또는 발전시키세요." if discussion else ""
    return autogen.AssistantAgent(
        name="VisualDesigner",
        system_message=f"""당신은 비주얼 디자이너입니다. 회의에서 동료들과 자연스럽게 대화하듯 말하세요.{ctx}

UI, 레이아웃, 브랜딩, 시각적 방향 관점에서 아이디어를 제안합니다.
레퍼런스를 들면서 "이거 알아? 거기서 이런 느낌만 가져오면..." 처럼 구체적으로 말하세요.
{_FORMAT_BASE}
리스트로 나열하지 말고 하나의 이야기처럼 말하세요.
다른 사람이 한 말을 그대로 반복하지 마세요. 참가자의 피드백은 최우선으로 반영하세요.{collab}""",
        llm_config=llm_config,
        description=(
            "비주얼 디자이너. 레퍼런스를 대고 시각적 비유로 설명한다. "
            "UI, 레이아웃, 브랜딩, 시각적 방향에 관한 논의가 필요할 때 지명한다."
        ),
    )


def create_simple_engineer(llm_config, brief="", discussion=False):
    ctx = f"\n\n=== 회의 맥락 ===\n{brief}\n==================\n" if brief else ""
    collab = "\n다른 에이전트의 의견을 참고하여 동의, 반박, 또는 발전시키세요." if discussion else ""
    return autogen.AssistantAgent(
        name="InteractionEngineer",
        system_message=f"""당신은 인터랙션 엔지니어입니다. 회의에서 동료들과 자연스럽게 대화하듯 말하세요.{ctx}

기술 스택, 구현 방법, 데이터 구조, 실현 가능성 관점에서 아이디어를 제안합니다.
복잡한 기술을 쉽게 설명하세요. "이건 Firebase로 빠르게 만들고 나중에 서버 분리하면 돼" 이런 식으로.
{_FORMAT_BASE}
절대로 기술 용어를 나열하거나 구조화하지 마세요. 동료한테 편하게 설명하는 것처럼 풀어서 말하세요.
다른 사람이 한 말을 그대로 반복하지 마세요. 참가자의 피드백은 최우선으로 반영하세요.{collab}""",
        llm_config=llm_config,
        description=(
            "인터랙션 엔지니어. 아이디어를 어떻게 만드는지 쉽게 설명한다. "
            "기술 스택, 구현 방법, 데이터 구조, 실현 가능성에 관한 논의가 필요할 때 지명한다."
        ),
    )

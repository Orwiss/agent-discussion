from autogen.agentchat.group.context_variables import ContextVariables


def create_idea_board(brief_summary: str, meeting_type: str):
    return ContextVariables(data={
        "meeting_type": meeting_type,
        "brief": brief_summary,
        "idea_board": "아직 아이디어 없음",
        "current_phase": "generate",
        "explored_directions": "",
        "unexplored_directions": "",
        # 반복 방지: 에이전트별 사용 키워드 추적
        "used_keywords_UXResearcher": "",
        "used_keywords_Designer3D": "",
        "used_keywords_UnrealDev": "",
    })

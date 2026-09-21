# performance_packet.py
from dataclasses import dataclass, field
from typing import List

import os

# 에이전트 이름은 PM·Designer·Engineer로 바뀌었지만, 캐릭터 ID는 UE5 쪽에 이미
# 박혀 있는 이름을 그대로 둔다 — 언리얼 블루프린트를 안 고쳐도 되게 하려는 것이다.
# UE5에서 캐릭터 이름을 새로 지으면 CHARACTER_PM 같은 변수로 덮어쓰면 된다.
AGENT_CHARACTER_MAP: dict[str, str] = {
    "PM":       os.getenv("CHARACTER_PM", "MH_UXResearcher"),
    "Designer": os.getenv("CHARACTER_DESIGNER", "MH_VisualDesigner"),
    "Engineer": os.getenv("CHARACTER_ENGINEER", "MH_SoftwareEngineer"),
}


@dataclass
class PerformancePacket:
    agent_name:      str
    character_id:    str
    text:            str
    audio_bytes:     bytes = field(default=b"")
    blendshape_fps:  int = 60
    weight_count:    int = 0
    blendshape_frames: List[List[float]] = field(default_factory=list)


def build_packet(agent_name: str, text: str) -> PerformancePacket | None:
    character_id = AGENT_CHARACTER_MAP.get(agent_name)
    if not character_id:
        return None
    return PerformancePacket(
        agent_name=agent_name,
        character_id=character_id,
        text=text,
    )

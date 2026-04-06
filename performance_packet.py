# performance_packet.py
from dataclasses import dataclass, field

AGENT_CHARACTER_MAP: dict[str, str] = {
    "UXResearcher":     "MH_UXResearcher",
    "VisualDesigner":   "MH_VisualDesigner",
    "SoftwareEngineer": "MH_SoftwareEngineer",
}


@dataclass
class PerformancePacket:
    agent_name:      str
    character_id:    str
    text:            str
    audio_bytes:     bytes = field(default=b"")


def build_packet(agent_name: str, text: str) -> PerformancePacket | None:
    character_id = AGENT_CHARACTER_MAP.get(agent_name)
    if not character_id:
        return None
    return PerformancePacket(
        agent_name=agent_name,
        character_id=character_id,
        text=text,
    )

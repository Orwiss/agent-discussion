# tts_pipeline.py
import os
import base64
import threading
import logging
from dotenv import load_dotenv
from pythonosc import udp_client
from elevenlabs.client import ElevenLabs
from elevenlabs import VoiceSettings
from performance_packet import PerformancePacket, build_packet

load_dotenv()
logger = logging.getLogger(__name__)

AGENT_VOICE_MAP: dict[str, str] = {
    "UXResearcher":     os.getenv("VOICE_UX_RESEARCHER", ""),
    "VisualDesigner":   os.getenv("VOICE_VISUAL_DESIGNER", ""),
    "SoftwareEngineer": os.getenv("VOICE_SOFTWARE_ENGINEER", ""),
}

_eleven_client: ElevenLabs | None = None
_osc_client: udp_client.SimpleUDPClient | None = None
_osc_lock = threading.Lock()


def _eleven() -> ElevenLabs:
    global _eleven_client
    if _eleven_client is None:
        key = os.getenv("ELEVENLABS_API_KEY")
        if not key:
            raise EnvironmentError("ELEVENLABS_API_KEY 없음")
        _eleven_client = ElevenLabs(api_key=key)
    return _eleven_client


def _osc() -> udp_client.SimpleUDPClient:
    global _osc_client
    if _osc_client is None:
        host = os.getenv("UE5_OSC_HOST", "127.0.0.1")
        port = int(os.getenv("UE5_OSC_PORT", "7400"))
        _osc_client = udp_client.SimpleUDPClient(host, port)
        logger.info(f"OSC 클라이언트 초기화: {host}:{port}")
    return _osc_client


def _synthesize(packet: PerformancePacket) -> bytes:
    voice_id = AGENT_VOICE_MAP.get(packet.agent_name, "")
    if not voice_id:
        raise ValueError(f"Voice ID 없음: {packet.agent_name}")
    gen = _eleven().text_to_speech.convert(
        voice_id=voice_id,
        text=packet.text,
        model_id="eleven_flash_v2_5",
        voice_settings=VoiceSettings(
            stability=0.45,
            similarity_boost=0.75,
            style=0.0,
            use_speaker_boost=True,
        ),
        output_format="pcm_16000",
    )
    return b"".join(gen)


_CHUNK_B64_SIZE = 60000  # Base64 String 청크 크기 (OSC String 안전 범위)

def _send_via_osc(packet: PerformancePacket) -> None:
    osc = _osc()
    audio = packet.audio_bytes
    # PCM → Base64 인코딩 → String 청크로 분할
    b64 = base64.b64encode(audio).decode("ascii")
    chunks = [b64[i:i + _CHUNK_B64_SIZE] for i in range(0, len(b64), _CHUNK_B64_SIZE)]
    with _osc_lock:
        osc.send_message("/mh/start", [
            packet.character_id,
            len(chunks),
        ])
        for idx, chunk in enumerate(chunks):
            osc.send_message("/mh/chunk", [packet.character_id, idx, chunk])
        osc.send_message("/mh/end", [packet.character_id])
    logger.info(
        f"[OSC] {packet.agent_name} | {len(audio)}bytes → Base64 {len(b64)}chars → {len(chunks)}chunks"
    )


def trigger(agent_name: str, text: str) -> None:
    def _run():
        try:
            packet = build_packet(agent_name, text)
            if packet is None:
                return
            packet.audio_bytes = _synthesize(packet)
            _send_via_osc(packet)
        except Exception as e:
            logger.error(f"[TTS] {agent_name} 실패: {e}", exc_info=True)
    threading.Thread(target=_run, daemon=True, name=f"tts-{agent_name}").start()

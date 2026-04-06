# tts_pipeline.py
# ElevenLabs TTS → Audio2Face blendshapes → OSC to UE5
import os
import json
import base64
import re
import struct
import subprocess
import tempfile
import threading
import logging
from dotenv import load_dotenv
from pythonosc import udp_client
from elevenlabs.client import ElevenLabs
from elevenlabs import VoiceSettings
from performance_packet import PerformancePacket, build_packet

load_dotenv()
logger = logging.getLogger(__name__)

# ── Voice mapping ──
AGENT_VOICE_MAP: dict[str, str] = {
    "UXResearcher":     os.getenv("VOICE_UX_RESEARCHER", ""),
    "VisualDesigner":   os.getenv("VOICE_VISUAL_DESIGNER", ""),
    "SoftwareEngineer": os.getenv("VOICE_SOFTWARE_ENGINEER", ""),
}

# ── Audio2Face bridge ──
A2F_BRIDGE_PATH = os.getenv("A2F_BRIDGE_PATH", "")
A2F_MODEL_PATH = os.getenv("A2F_MODEL_PATH", "")

# ── Singletons ──
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


# ── Step 1: ElevenLabs TTS → PCM bytes ──
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


# ── Step 2: PCM → WAV 임시 파일 (16kHz, 16bit, mono) ──
def _pcm_to_wav(pcm_bytes: bytes, sample_rate: int = 16000) -> str:
    """PCM 바이트를 WAV 파일로 저장하고 경로 반환."""
    tmp = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
    num_channels = 1
    bits_per_sample = 16
    byte_rate = sample_rate * num_channels * bits_per_sample // 8
    block_align = num_channels * bits_per_sample // 8
    data_size = len(pcm_bytes)

    # WAV header
    tmp.write(b"RIFF")
    tmp.write(struct.pack("<I", 36 + data_size))
    tmp.write(b"WAVE")
    tmp.write(b"fmt ")
    tmp.write(struct.pack("<I", 16))               # chunk size
    tmp.write(struct.pack("<H", 1))                # PCM format
    tmp.write(struct.pack("<H", num_channels))
    tmp.write(struct.pack("<I", sample_rate))
    tmp.write(struct.pack("<I", byte_rate))
    tmp.write(struct.pack("<H", block_align))
    tmp.write(struct.pack("<H", bits_per_sample))
    tmp.write(b"data")
    tmp.write(struct.pack("<I", data_size))
    tmp.write(pcm_bytes)
    tmp.close()
    return tmp.name


# ── Step 3: Audio2Face bridge → blendshape weights ──
def _generate_blendshapes(wav_path: str) -> dict:
    """a2f-bridge.exe를 subprocess로 호출해서 블렌드셰이프 JSON 반환."""
    if not A2F_BRIDGE_PATH or not A2F_MODEL_PATH:
        logger.warning("[A2F] A2F_BRIDGE_PATH 또는 A2F_MODEL_PATH 미설정 → 블렌드셰이프 생략")
        return None

    try:
        result = subprocess.run(
            [A2F_BRIDGE_PATH, "--model", A2F_MODEL_PATH, "--audio", wav_path],
            capture_output=True, text=True, timeout=60,
            env={**os.environ,
                 "PATH": os.environ.get("PATH", "") + ";" +
                         os.getenv("TENSORRT_BIN", "") + ";" +
                         os.getenv("A2F_SDK_BIN", "")},
        )
        if result.returncode != 0:
            logger.error(f"[A2F] 실행 실패: {result.stderr[:500]}")
            return None

        data = json.loads(result.stdout)
        logger.info(
            f"[A2F] 블렌드셰이프 생성: {data['weight_count']}weights × {data['num_frames']}frames"
        )
        return data
    except Exception as e:
        logger.error(f"[A2F] 에러: {e}", exc_info=True)
        return None


# ── Step 4: OSC 전송 — 블렌드셰이프 ──
def _send_blendshapes_via_osc(packet: PerformancePacket) -> None:
    """블렌드셰이프 프레임을 OSC로 UE5에 전송."""
    if not packet.blendshape_frames:
        return

    osc = _osc()
    char_id = packet.character_id
    num_frames = len(packet.blendshape_frames)

    with _osc_lock:
        # 시작 신호: [캐릭터ID, 총 프레임 수, weight 개수, fps]
        osc.send_message("/mh/bs_start", [
            char_id, num_frames, packet.weight_count, packet.blendshape_fps
        ])

        # 프레임별 weights 전송
        for i, frame in enumerate(packet.blendshape_frames):
            osc.send_message("/mh/bs", [char_id, i] + frame)

        # 종료 신호
        osc.send_message("/mh/bs_end", [char_id])

    logger.info(
        f"[OSC/BS] {packet.agent_name} | {num_frames}frames × {packet.weight_count}weights"
    )


# ── Step 5: OSC 전송 — 오디오 (Base64 청크) ──
_CHUNK_B64_SIZE = 60000

def _send_audio_via_osc(packet: PerformancePacket) -> None:
    """오디오를 Base64 청크로 OSC 전송."""
    osc = _osc()
    audio = packet.audio_bytes
    b64 = base64.b64encode(audio).decode("ascii")
    chunks = [b64[i:i + _CHUNK_B64_SIZE] for i in range(0, len(b64), _CHUNK_B64_SIZE)]

    with _osc_lock:
        osc.send_message("/mh/audio_start", [packet.character_id, len(chunks)])
        for idx, chunk in enumerate(chunks):
            osc.send_message("/mh/audio_chunk", [packet.character_id, idx, chunk])
        osc.send_message("/mh/audio_end", [packet.character_id])

    logger.info(
        f"[OSC/Audio] {packet.agent_name} | {len(audio)}bytes → {len(chunks)}chunks"
    )


# ── 문장 분리 ──
_SENTENCE_RE = re.compile(r'(?<=[.?!。!?])\s+')

def _split_sentences(text: str) -> list[str]:
    """마침표/물음표/느낌표 기준 문장 분리."""
    parts = _SENTENCE_RE.split(text.strip())
    return [s.strip() for s in parts if s.strip()]


# ── Entry point ──
def trigger(agent_name: str, text: str) -> None:
    """guardrails.py에서 호출. daemon thread에서 문장 단위 스트리밍 실행."""
    def _run():
        try:
            sentences = _split_sentences(text)
            if not sentences:
                return

            for i, sentence in enumerate(sentences):
                wav_path = None
                try:
                    # 1. 문장별 패킷 생성
                    packet = build_packet(agent_name, sentence)
                    if packet is None:
                        continue

                    # 2. ElevenLabs TTS → PCM
                    packet.audio_bytes = _synthesize(packet)

                    # 3. PCM → WAV 임시 파일
                    wav_path = _pcm_to_wav(packet.audio_bytes)

                    # 4. Audio2Face → 블렌드셰이프
                    bs_data = _generate_blendshapes(wav_path)
                    if bs_data:
                        packet.blendshape_fps = bs_data["fps"]
                        packet.weight_count = bs_data["weight_count"]
                        packet.blendshape_frames = bs_data["frames"]

                    # 5. OSC 전송 — 준비되는 즉시 전송
                    _send_blendshapes_via_osc(packet)
                    _send_audio_via_osc(packet)

                    logger.info(
                        f"[Stream] {agent_name} 문장 {i+1}/{len(sentences)}: "
                        f"{len(sentence)}자 → {len(packet.blendshape_frames)}frames"
                    )

                except Exception as e:
                    logger.error(f"[TTS] {agent_name} 문장 {i+1} 실패: {e}", exc_info=True)
                finally:
                    if wav_path and os.path.exists(wav_path):
                        try:
                            os.unlink(wav_path)
                        except OSError:
                            pass

        except Exception as e:
            logger.error(f"[TTS] {agent_name} 실패: {e}", exc_info=True)

    threading.Thread(target=_run, daemon=True, name=f"tts-{agent_name}").start()

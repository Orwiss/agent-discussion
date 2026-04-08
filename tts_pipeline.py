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
import queue
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
A2E_MODEL_PATH = os.getenv("A2E_MODEL_PATH", "")

# ── Singletons ──
_eleven_client: ElevenLabs | None = None
_osc_client: udp_client.SimpleUDPClient | None = None
_osc_lock = threading.Lock()
_speech_queue = queue.Queue(maxsize=2)  # 최대 "현재 재생" + "다음 1개 준비"
_trigger_lock = threading.Lock()  # 한 에이전트의 전체 발화가 끝나야 다음 에이전트 시작


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


# ── Step 2.5: 텍스트 감성분석 → A2E 감정 벡터 ──
# A2E emotion indices: 0=amazement 1=anger 2=cheekiness 3=disgust 4=fear 5=grief 6=joy 7=outofbreath 8=pain 9=sadness
_EMOTION_KEYWORDS = {
    "joy":        ["기쁘", "좋", "신나", "행복", "웃", "환영", "즐거", "멋지", "훌륭", "감사", "대박", "최고"],
    "sadness":    ["슬프", "아쉽", "힘들", "외로", "그립", "안타깝", "우울"],
    "anger":      ["화나", "짜증", "분노", "열받", "답답", "못마땅"],
    "fear":       ["무서", "걱정", "불안", "두렵", "위험"],
    "amazement":  ["놀라", "대단", "믿기", "어마어마", "충격", "깜짝"],
    "disgust":    ["역겹", "싫", "혐오", "불쾌"],
}
_EMOTION_INDEX = {"amazement": 0, "anger": 1, "cheekiness": 2, "disgust": 3,
                  "fear": 4, "grief": 5, "joy": 6, "outofbreath": 7, "pain": 8, "sadness": 9}

def _analyze_sentiment(text: str) -> list[float]:
    """키워드 기반 간단 감성분석. [0]*10 벡터 반환."""
    emotions = [0.0] * 10
    for emo_name, keywords in _EMOTION_KEYWORDS.items():
        for kw in keywords:
            if kw in text:
                idx = _EMOTION_INDEX[emo_name]
                emotions[idx] = min(emotions[idx] + 0.4, 1.0)
    # 아무 감정도 없으면 joy 0.2 (기본 약간 밝은 톤)
    if sum(emotions) == 0:
        emotions[_EMOTION_INDEX["joy"]] = 0.2
    return emotions

def _set_a2e_emotion(emotions: list[float]) -> None:
    """A2E config의 preferred_emotion을 동적으로 수정."""
    if not A2E_MODEL_PATH:
        return
    config_path = os.path.join(os.path.dirname(A2E_MODEL_PATH), "model_config.json")
    try:
        with open(config_path, "r") as f:
            config = json.load(f)
        config["post_processing_config"]["enable_preferred_emotion"] = True
        config["post_processing_config"]["preferred_emotion"] = emotions
        config["post_processing_config"]["preferred_emotion_strength"] = 0.7
        config["post_processing_config"]["emotion_strength"] = 1.0
        config["post_processing_config"]["emotion_contrast"] = 2.0
        with open(config_path, "w") as f:
            json.dump(config, f, indent=4)
    except Exception as e:
        logger.warning(f"[A2E] config 수정 실패: {e}")


# ── Step 3: Audio2Face bridge → blendshape weights ──
def _generate_blendshapes(wav_path: str, text: str = "") -> dict:
    """a2f-bridge.exe를 subprocess로 호출해서 블렌드셰이프 JSON 반환."""
    if not A2F_BRIDGE_PATH or not A2F_MODEL_PATH:
        logger.warning("[A2F] A2F_BRIDGE_PATH 또는 A2F_MODEL_PATH 미설정 → 블렌드셰이프 생략")
        return None

    # 텍스트 감성분석 → A2E config 주입
    if text and A2E_MODEL_PATH:
        emotions = _analyze_sentiment(text)
        _set_a2e_emotion(emotions)
        logger.info(f"[A2E] 감정: {dict(zip(_EMOTION_KEYWORDS.keys(), [emotions[_EMOTION_INDEX[k]] for k in _EMOTION_KEYWORDS]))}")

    try:
        # 모든 DLL(TensorRT, audio2x, CUDA)은 exe 폴더에 복사됨. PATH 조작 불필요.
        cmd = [A2F_BRIDGE_PATH, "--model", A2F_MODEL_PATH, "--audio", wav_path]
        if A2E_MODEL_PATH:
            cmd += ["--emotion-model", A2E_MODEL_PATH]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
        if result.returncode != 0:
            logger.error(f"[A2F] 실행 실패 (code {result.returncode})")
            logger.error(f"[A2F] stderr: {result.stderr[:500]}")
            logger.error(f"[A2F] stdout: {result.stdout[:500]}")
            logger.error(f"[A2F] cmd: {[A2F_BRIDGE_PATH, '--model', A2F_MODEL_PATH, '--audio', wav_path]}")
            return None

        data = json.loads(result.stdout)
        logger.info(
            f"[A2F] 블렌드셰이프 생성: {data['weight_count']}weights × {data['num_frames']}frames"
        )
        return data
    except Exception as e:
        logger.error(f"[A2F] 에러: {e}", exc_info=True)
        return None


# ── Step 3.5: 노트북 스피커로 오디오 재생 ──
def _play_audio_local(pcm_bytes: bytes, sample_rate: int = 16000) -> None:
    """PCM 16bit mono 오디오를 노트북 스피커로 재생."""
    try:
        import sounddevice as sd
        import numpy as np
        audio = np.frombuffer(pcm_bytes, dtype=np.int16).astype(np.float32) / 32768.0
        sd.play(audio, samplerate=sample_rate)
        sd.wait()  # 재생 완료까지 대기 (다음 발화와 겹치지 않게)
    except Exception as e:
        logger.warning(f"[Audio] 재생 실패: {e}")


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

        # 프레임별 weights 전송 (0을 0.0으로 변환 — python-osc가 int/float 구분함)
        for i, frame in enumerate(packet.blendshape_frames):
            osc.send_message("/mh/bs", [char_id, i] + [float(w) for w in frame])

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


# ── 발화 처리 워커 (1개만 실행) ──
def _process_one(agent_name: str, sentence: str, idx: int, total: int) -> dict | None:
    """TTS + A2F 처리. GPU 사용하므로 동시 1개만."""
    wav_path = None
    try:
        packet = build_packet(agent_name, sentence)
        if packet is None:
            return None
        packet.audio_bytes = _synthesize(packet)
        wav_path = _pcm_to_wav(packet.audio_bytes)
        bs_data = _generate_blendshapes(wav_path, text=sentence)
        if bs_data:
            packet.blendshape_fps = bs_data["fps"]
            packet.weight_count = bs_data["weight_count"]
            packet.blendshape_frames = bs_data["frames"]
        logger.info(f"[Process] {agent_name} 문장 {idx+1}/{total} 준비 완료")
        return packet
    except Exception as e:
        logger.error(f"[TTS] {agent_name} 문장 {idx+1} 처리 실패: {e}", exc_info=True)
        return None
    finally:
        if wav_path and os.path.exists(wav_path):
            try:
                os.unlink(wav_path)
            except OSError:
                pass


def _playback_worker():
    """큐에서 준비된 패킷을 꺼내 순서대로 재생. 항상 1개 스레드만 실행."""
    while True:
        packet = _speech_queue.get()
        if packet is None:
            _speech_queue.task_done()
            continue
        try:
            bs_thread = threading.Thread(
                target=_send_blendshapes_via_osc, args=(packet,), daemon=True)
            bs_thread.start()
            _play_audio_local(packet.audio_bytes)
            bs_thread.join()
        except Exception as e:
            logger.error(f"[Playback] 재생 실패: {e}", exc_info=True)
        finally:
            _speech_queue.task_done()

# 재생 워커 시작 (1개)
threading.Thread(target=_playback_worker, daemon=True, name="playback-worker").start()


# ── Entry point ──
def trigger(agent_name: str, text: str) -> None:
    """guardrails.py에서 호출. 처리→큐→재생 파이프라인."""
    def _run():
        with _trigger_lock:  # 한 에이전트의 모든 문장이 큐에 들어간 후 다음 에이전트
            try:
                sentences = _split_sentences(text)
                if not sentences:
                    return
                for i, sentence in enumerate(sentences):
                    packet = _process_one(agent_name, sentence, i, len(sentences))
                    if packet:
                        _speech_queue.put(packet)
            except Exception as e:
                logger.error(f"[TTS] {agent_name} 실패: {e}", exc_info=True)

    threading.Thread(target=_run, daemon=True, name=f"tts-{agent_name}").start()

# tts_pipeline.py
# ElevenLabs TTS → Audio2Face gRPC → OSC to UE5
import os
import asyncio
import base64
import json
import re
import subprocess
import time
import threading
import queue
import logging
from dotenv import load_dotenv
from pythonosc import udp_client
from elevenlabs.client import ElevenLabs
from elevenlabs import VoiceSettings
from performance_packet import AGENT_CHARACTER_MAP, PerformancePacket, build_packet

import grpc
import numpy as np
from nvidia_ace.services.a2f_controller.v1_pb2_grpc import A2FControllerServiceStub
from nvidia_ace.controller.v1_pb2 import AudioStream, AudioStreamHeader
from nvidia_ace.a2f.v1_pb2 import (
    AudioWithEmotion, EmotionPostProcessingParameters,
    FaceParameters, BlendShapeParameters, EmotionParameters,
)
from nvidia_ace.audio.v1_pb2 import AudioHeader

load_dotenv()
logger = logging.getLogger(__name__)

# -- Voice mapping --
# 에이전트 이름이 centralized/decentralized 구조로 바뀌면서 PM·Designer·Engineer가
# 됐다. VOICE_PM 같은 새 변수를 먼저 보고, 없으면 예전 변수를 그대로 읽는다.
# PM에는 예전 UX 리서처 목소리가 기본으로 붙는다 — 바꾸려면 VOICE_PM을 지정한다.
AGENT_VOICE_MAP: dict[str, str] = {
    "PM":       os.getenv("VOICE_PM") or os.getenv("VOICE_UX_RESEARCHER", ""),
    "Designer": os.getenv("VOICE_DESIGNER") or os.getenv("VOICE_VISUAL_DESIGNER", ""),
    "Engineer": os.getenv("VOICE_ENGINEER") or os.getenv("VOICE_SOFTWARE_ENGINEER", ""),
}

# -- Audio2Face gRPC --
A2F_GRPC_HOST = os.getenv("A2F_GRPC_HOST", "localhost")
A2F_GRPC_PORT = os.getenv("A2F_GRPC_PORT", "52000")
NGC_API_KEY = os.getenv("NGC_API_KEY", "")
A2F_CONTAINER_NAME = "audio2face-3d"
A2F_DOCKER_IMAGE = "nvcr.io/nim/nvidia/audio2face-3d:2.0"
A2F_CACHE_DIR = os.path.expanduser("~/.cache/audio2face-3d")
# A2F가 미리 잡아두는 동시 스트림 수. 기본 10이면 8GB 노트북 GPU에서 UE와 같이 돌 때
# VRAM이 넘쳐 추론이 초당 1~2프레임까지 떨어진다. 발화는 한 번에 한 문장씩만 보낸다.
A2F_MAX_STREAM = os.getenv("A2F_MAX_STREAM", "2")

_a2f_ready = False
_a2f_ready_lock = threading.Lock()


def _ensure_a2f_running() -> None:
    """A2F Docker 컨테이너가 실행 중인지 확인하고, 없으면 자동 시작."""
    global _a2f_ready
    if _a2f_ready:
        return

    with _a2f_ready_lock:
        if _a2f_ready:
            return

        # 1. 이미 실행 중인지 확인
        try:
            result = subprocess.run(
                ["docker", "inspect", "-f", "{{.State.Running}}", A2F_CONTAINER_NAME],
                capture_output=True, text=True, timeout=10,
            )
            if result.returncode == 0 and "true" in result.stdout.strip():
                logger.info(f"[A2F/Docker] 컨테이너 '{A2F_CONTAINER_NAME}' 이미 실행 중")
                _a2f_ready = True
                return
        except FileNotFoundError:
            logger.error("[A2F/Docker] docker 명령어를 찾을 수 없음. Docker Desktop 설치 필요.")
            return
        except Exception:
            pass

        # 2. NGC API 키 확인
        if not NGC_API_KEY or NGC_API_KEY.startswith("nvapi-여기"):
            logger.error("[A2F/Docker] NGC_API_KEY가 .env에 설정되지 않음")
            return

        # 3. 캐시 디렉토리 생성
        os.makedirs(A2F_CACHE_DIR, exist_ok=True)

        # 4. 기존 중지된 컨테이너 제거
        subprocess.run(
            ["docker", "rm", "-f", A2F_CONTAINER_NAME],
            capture_output=True, timeout=10,
        )

        # 5. 컨테이너 시작
        logger.info(f"[A2F/Docker] 컨테이너 시작 중... (최초 실행 시 모델 다운로드로 수 분 소요)")
        # Windows에서 --network=host 미지원 → 포트 매핑 사용
        cache_dir = A2F_CACHE_DIR.replace("\\", "/")
        if cache_dir[1] == ":":
            cache_dir = "//" + cache_dir[0].lower() + cache_dir[2:]
        cmd = [
            "docker", "run", "-d",
            "--name", A2F_CONTAINER_NAME,
            "--gpus", "all",
            "-p", f"{A2F_GRPC_PORT}:52000",
            "-p", "8000:8000",
            "-e", f"NGC_API_KEY={NGC_API_KEY}",
            "-e", f"PERF_MAX_STREAM={A2F_MAX_STREAM}",
            "-v", f"{cache_dir}:/tmp/a2x",
            A2F_DOCKER_IMAGE,
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
        if result.returncode != 0:
            logger.error(f"[A2F/Docker] 시작 실패: {result.stderr.strip()}")
            return

        # 6. health check 대기 (최대 5분)
        logger.info("[A2F/Docker] health check 대기 중...")
        for i in range(60):
            time.sleep(5)
            try:
                import urllib.request
                req = urllib.request.urlopen("http://localhost:8000/v1/health/ready", timeout=3)
                if req.status == 200:
                    logger.info(f"[A2F/Docker] 서비스 준비 완료 ({(i+1)*5}초)")
                    _a2f_ready = True
                    return
            except Exception:
                if i % 6 == 0:
                    logger.info(f"[A2F/Docker] 대기 중... ({(i+1)*5}초)")
                continue

        logger.error("[A2F/Docker] 5분 내 서비스 준비 안 됨. docker logs audio2face-3d 확인.")


# -- Singletons --
_eleven_client: ElevenLabs | None = None
_osc_client: "_OscFanout | None" = None
_osc_lock = threading.Lock()
# 재생 대기열. trigger()가 앞서 만들어 둘 수 있는 문장 수 = maxsize (+ 재생 워커가 들고 있는 1개)
_speech_queue: "queue.Queue[tuple[int, PerformancePacket]]" = queue.Queue(maxsize=int(os.getenv("VR_PREFETCH", "3")))
# 대기열에 넣었지만 UE에서 재생이 아직 안 끝난 문장 수. 0이면 idle.
_unplayed = 0
_idle_cv = threading.Condition()
# reset() 때마다 1씩 오른다. 대기열의 문장은 넣을 때의 값을 달고 있고, 다르면 버린다.
_epoch = 0

# 재생 타이밍 (초). 같은 사람이 문장을 이어 말할 때 / 말하는 사람이 바뀔 때의 공백.
SENTENCE_GAP = float(os.getenv("VR_SENTENCE_GAP", "0.3"))
SPEAKER_GAP = float(os.getenv("VR_SPEAKER_GAP", "0.5"))
# 1이면 앞 문장 재생 중에 다음 문장 데이터를 미리 보낸다. UE의 BP_AgentBlendshapes가
# bs_end를 보관만 하는 버전(bLegacyBSStart=false)이어야 한다 — 옛 버전이면 0으로 둔다.
PRESEND = os.getenv("VR_PRESEND", "1").strip().lower() in {"1", "true", "yes", "on"}

# gRPC async event loop (dedicated thread)
_grpc_loop: asyncio.AbstractEventLoop | None = None
_grpc_loop_lock = threading.Lock()


def _get_grpc_loop() -> asyncio.AbstractEventLoop:
    """gRPC async 호출용 전용 이벤트 루프. 한 번만 생성."""
    global _grpc_loop
    if _grpc_loop is None:
        with _grpc_loop_lock:
            if _grpc_loop is None:
                _grpc_loop = asyncio.new_event_loop()
                t = threading.Thread(
                    target=_grpc_loop.run_forever, daemon=True, name="grpc-loop"
                )
                t.start()
    return _grpc_loop


def _eleven() -> ElevenLabs:
    global _eleven_client
    if _eleven_client is None:
        key = os.getenv("ELEVENLABS_API_KEY")
        if not key:
            raise EnvironmentError("ELEVENLABS_API_KEY 없음")
        _eleven_client = ElevenLabs(api_key=key)
    return _eleven_client


class _OscFanout:
    """UE5_OSC_HOST에 적힌 모든 주소로 같은 메시지를 보낸다.
    노트북 PIE와 ZeroTier 너머 PC 언리얼에 동시에 보낼 때 쓴다 (쉼표로 구분)."""

    def __init__(self, hosts: list[str], port: int) -> None:
        self.clients = [udp_client.SimpleUDPClient(h, port) for h in hosts]

    def send_message(self, address: str, value) -> None:
        for c in self.clients:
            c.send_message(address, value)


_osc_init_lock = threading.Lock()


def _osc() -> _OscFanout:
    global _osc_client
    with _osc_init_lock:
        if _osc_client is None:
            hosts = [h.strip() for h in os.getenv("UE5_OSC_HOST", "127.0.0.1").split(",") if h.strip()]
            port = int(os.getenv("UE5_OSC_PORT", "7400"))
            _osc_client = _OscFanout(hosts, port)
            logger.info(f"OSC 클라이언트 초기화: {', '.join(hosts)} :{port}")
    return _osc_client


# -- Step 1: ElevenLabs TTS -> PCM bytes --
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
    return _apply_voice_gain(packet.agent_name, b"".join(gen))


# 목소리마다 음량이 달라서 맞춘다 (dB). 2026-10-04 같은 문장 3개로 잰 말소리 평균:
# PM -21.8 / Designer -15.3 / Engineer -15.4 dBFS. PM은 Engineer에 맞추고,
# Designer는 높은 목소리라 같은 음량이어도 크게 들려서 조금 내린다. VOICE_GAIN_PM 등으로 덮어쓴다.
_VOICE_GAIN_DB = {"PM": 5.9, "Designer": -1.5, "Engineer": 0.0}


def _voice_gain_db(agent_name: str) -> float:
    env = os.getenv(f"VOICE_GAIN_{agent_name.upper()}")
    if env:
        try:
            return float(env)
        except ValueError:
            logger.warning(f"VOICE_GAIN_{agent_name.upper()} 값이 숫자가 아님: {env}")
    return _VOICE_GAIN_DB.get(agent_name, 0.0)


def _apply_voice_gain(agent_name: str, pcm: bytes) -> bytes:
    """A2F에 넣기 전에 음량을 맞춘다. 키운 뒤 최대치가 넘치면 깨지지 않게 전체를 줄인다."""
    db = _voice_gain_db(agent_name)
    if not db or not pcm:
        return pcm
    x = np.frombuffer(pcm, dtype=np.int16).astype(np.float32) * (10 ** (db / 20))
    peak = float(np.abs(x).max())
    limit = 0.98 * 32767
    if peak > limit:
        x *= limit / peak
    return x.astype(np.int16).tobytes()


# -- Step 2: Audio2Face gRPC -> blendshape weights --
SAMPLE_RATE = 16000

# UE5 MetaHuman이 기대하는 블렌드셰이프 순서 (bs_names.csv 기준)
_UE5_BS_ORDER = [
    "neutral", "eyeBlinkLeft", "eyeLookDownLeft", "eyeLookInLeft", "eyeLookOutLeft",
    "eyeLookUpLeft", "eyeSquintLeft", "eyeWideLeft", "eyeBlinkRight", "eyeLookDownRight",
    "eyeLookInRight", "eyeLookOutRight", "eyeLookUpRight", "eyeSquintRight", "eyeWideRight",
    "jawForward", "jawLeft", "jawRight", "jawOpen", "mouthClose", "mouthFunnel", "mouthPucker",
    "mouthLeft", "mouthRight", "mouthSmileLeft", "mouthSmileRight", "mouthFrownLeft",
    "mouthFrownRight", "mouthDimpleLeft", "mouthDimpleRight", "mouthStretchLeft",
    "mouthStretchRight", "mouthRollLower", "mouthRollUpper", "mouthShrugLower",
    "mouthShrugUpper", "mouthPressLeft", "mouthPressRight", "mouthLowerDownLeft",
    "mouthLowerDownRight", "mouthUpperUpLeft", "mouthUpperUpRight", "browDownLeft",
    "browDownRight", "browInnerUp", "browOuterUpLeft", "browOuterUpRight", "cheekPuff",
    "cheekSquintLeft", "cheekSquintRight", "noseSneerLeft", "noseSneerRight", "tongueOut",
    "tongueTipUp", "tongueTipDown", "tongueTipLeft", "tongueTipRight", "tongueRollUp",
    "tongueRollDown", "tongueRollLeft", "tongueRollRight", "tongueUp", "tongueDown",
    "tongueLeft", "tongueRight", "tongueIn", "tongueStretch", "tongueWide",
]


def _build_remap_table(grpc_names: list[str]) -> list[int]:
    """Microservice 블렌드셰이프 순서 → UE5 순서 매핑 테이블 생성.
    반환값[i] = UE5 인덱스 i에 대응하는 gRPC 인덱스. 없으면 -1 (0.0 채움).
    """
    # Microservice는 PascalCase, UE5는 camelCase → 소문자로 비교
    grpc_lower = {name.lower(): idx for idx, name in enumerate(grpc_names)}
    remap = []
    for ue5_name in _UE5_BS_ORDER:
        grpc_idx = grpc_lower.get(ue5_name.lower(), -1)
        remap.append(grpc_idx)
    return remap


def _remap_frame(frame: list[float], remap_table: list[int]) -> list[float]:
    """gRPC 프레임을 UE5 순서로 재배열."""
    return [frame[idx] if idx >= 0 else 0.0 for idx in remap_table]

async def _a2f_grpc_call(pcm_bytes: bytes) -> dict | None:
    """gRPC 양방향 스트리밍으로 Audio2Face 블렌드셰이프 생성."""
    url = f"{A2F_GRPC_HOST}:{A2F_GRPC_PORT}"
    try:
        async with grpc.aio.insecure_channel(url) as channel:
            stub = A2FControllerServiceStub(channel)
            stream = stub.ProcessAudioStream()

            # --- Write task ---
            # 1. AudioStreamHeader (첫 메시지, 필수)
            await stream.write(AudioStream(
                audio_stream_header=AudioStreamHeader(
                    audio_header=AudioHeader(
                        audio_format=AudioHeader.AUDIO_FORMAT_PCM,
                        channel_count=1,
                        samples_per_second=SAMPLE_RATE,
                        bits_per_sample=16,
                    ),
                    emotion_post_processing_params=EmotionPostProcessingParameters(
                        emotion_contrast=1.0,
                        live_blend_coef=0.7,
                        enable_preferred_emotion=False,
                        emotion_strength=0.6,
                        max_emotions=3,
                    ),
                    face_params=FaceParameters(float_params={
                        "upperFaceStrength": 1.0,
                        "lowerFaceStrength": 1.25,
                        "upperFaceSmoothing": 0.001,
                        "lowerFaceSmoothing": 0.006,
                        "faceMaskLevel": 0.6,
                        "faceMaskSoftness": 0.0085,
                        "tongueStrength": 1.3,
                    }),
                    blendshape_params=BlendShapeParameters(
                        enable_clamping_bs_weight=True,
                        bs_weight_multipliers={
                            # NVIDIA config_claire.yml 기반 multiplier
                            "EyeBlinkLeft": 1.0, "EyeBlinkRight": 1.0,
                            "EyeSquintLeft": 1.0, "EyeSquintRight": 1.0,
                            "EyeWideLeft": 1.0, "EyeWideRight": 1.0,
                            # 시선은 A2F가 제어 안 함 → 0
                            "EyeLookDownLeft": 0.0, "EyeLookDownRight": 0.0,
                            "EyeLookInLeft": 0.0, "EyeLookInRight": 0.0,
                            "EyeLookOutLeft": 0.0, "EyeLookOutRight": 0.0,
                            "EyeLookUpLeft": 0.0, "EyeLookUpRight": 0.0,
                            # 턱: 좌우 줄이고 열기는 유지
                            "JawForward": 0.7,
                            "JawLeft": 0.2, "JawRight": 0.2,
                            "JawOpen": 1.0,
                            # 입: 핵심 입술 모양 강화
                            "MouthClose": 1.0,
                            "MouthFunnel": 1.2, "MouthPucker": 1.2,
                            "MouthLeft": 0.2, "MouthRight": 0.2,
                            "MouthSmileLeft": 0.8, "MouthSmileRight": 0.8,
                            "MouthFrownLeft": 0.4, "MouthFrownRight": 0.4,
                            "MouthDimpleLeft": 0.7, "MouthDimpleRight": 0.7,
                            "MouthStretchLeft": 0.1, "MouthStretchRight": 0.1,
                            "MouthRollLower": 0.9, "MouthRollUpper": 0.5,
                            "MouthShrugLower": 0.9, "MouthShrugUpper": 0.4,
                            "MouthPressLeft": 0.8, "MouthPressRight": 0.8,
                            "MouthLowerDownLeft": 0.8, "MouthLowerDownRight": 0.8,
                            "MouthUpperUpLeft": 0.8, "MouthUpperUpRight": 0.8,
                            # 눈썹
                            "BrowDownLeft": 1.0, "BrowDownRight": 1.0,
                            "BrowInnerUp": 1.0,
                            "BrowOuterUpLeft": 1.0, "BrowOuterUpRight": 1.0,
                            # 볼/코
                            "CheekPuff": 0.2,
                            "CheekSquintLeft": 1.0, "CheekSquintRight": 1.0,
                            "NoseSneerLeft": 0.8, "NoseSneerRight": 0.8,
                            # 혀
                            "TongueOut": 0.0,
                        },
                    ),
                    emotion_params=EmotionParameters(
                        live_transition_time=0.0001,
                    ),
                )
            ))

            # 2. AudioWithEmotion (PCM 청크 전송, 감정은 A2E 자동 분석에 위임)
            audio_array = np.frombuffer(pcm_bytes, dtype=np.int16)
            chunk_size = SAMPLE_RATE  # 1초 단위 청크
            for i in range(0, len(audio_array), chunk_size):
                chunk = audio_array[i:i + chunk_size]
                await stream.write(AudioStream(
                    audio_with_emotion=AudioWithEmotion(
                        audio_buffer=chunk.astype(np.int16).tobytes()
                    )
                ))

            # 3. EndOfAudio (필수 — 이걸 보내야 서버가 최종 Status 반환)
            await stream.write(AudioStream(
                end_of_audio=AudioStream.EndOfAudio()
            ))

            # --- Read task ---
            bs_names = []
            all_frames = []

            while True:
                message = await stream.read()
                if message == grpc.aio.EOF:
                    break

                if message.HasField("animation_data_stream_header"):
                    hdr = message.animation_data_stream_header
                    bs_names = list(hdr.skel_animation_header.blend_shapes)
                    logger.info(f"[A2F/gRPC] 블렌드셰이프 {len(bs_names)}개 수신 시작")

                elif message.HasField("animation_data"):
                    for frame in message.animation_data.skel_animation.blend_shape_weights:
                        all_frames.append(list(frame.values))

                elif message.HasField("status"):
                    status = message.status
                    if status.code != 0:
                        logger.warning(f"[A2F/gRPC] Status {status.code}: {status.message}")

            if not all_frames:
                logger.error("[A2F/gRPC] 블렌드셰이프 프레임 없음")
                return None

            # Microservice → UE5 블렌드셰이프 순서 리매핑
            remap_table = _build_remap_table(bs_names)
            remapped_frames = [_remap_frame(f, remap_table) for f in all_frames]

            num_frames = len(remapped_frames)
            audio_duration = len(audio_array) / SAMPLE_RATE
            fps = int(round(num_frames / audio_duration)) if audio_duration > 0 else 60

            logger.info(
                f"[A2F/gRPC] 완료: {len(_UE5_BS_ORDER)}weights x {num_frames}frames @ {fps}fps"
            )
            return {
                "fps": fps,
                "weight_count": len(_UE5_BS_ORDER),
                "num_frames": num_frames,
                "frames": remapped_frames,
                "bs_names": _UE5_BS_ORDER,
            }

    except grpc.aio.AioRpcError as e:
        logger.error(f"[A2F/gRPC] RPC 에러: {e.code()} - {e.details()}")
        return None
    except Exception as e:
        logger.error(f"[A2F/gRPC] 에러: {e}", exc_info=True)
        return None


def _generate_blendshapes(pcm_bytes: bytes) -> dict | None:
    """동기 래퍼: Docker 확인 후 gRPC async 호출을 전용 이벤트 루프에서 실행."""
    _ensure_a2f_running()
    loop = _get_grpc_loop()
    future = asyncio.run_coroutine_threadsafe(_a2f_grpc_call(pcm_bytes), loop)
    return future.result(timeout=300)


# -- Step 3: 노트북 스피커로 오디오 재생 --
def _play_audio_local(pcm_bytes: bytes, sample_rate: int = 16000) -> None:
    """PCM 16bit mono 오디오를 노트북 스피커로 재생."""
    try:
        import sounddevice as sd
        audio = np.frombuffer(pcm_bytes, dtype=np.int16).astype(np.float32) / 32768.0
        sd.play(audio, samplerate=sample_rate)
        sd.wait()
    except Exception as e:
        logger.warning(f"[Audio] 재생 실패: {e}")


# -- Step 4: OSC 전송 - 블렌드셰이프 --
# UDP는 흐름 제어가 없어서 쉬지 않고 쏘면 받는 쪽 소켓 버퍼가 넘치고 뒷부분이 조용히 버려진다.
# 실측(루프백, 기본 수신 버퍼 65,536B): 52 weights 기준 600프레임(20초)을 페이싱 없이 보내면
# 439/600만 도착했다. 프레임 사이에 1ms만 주면 600/600 도착한다. 150·300프레임은 페이싱
# 없이도 통과하므로, 짧은 발화만 테스트하면 이 문제가 안 보인다.
_BS_PACE_SEC = 0.001

def _send_blendshapes_via_osc(packet: PerformancePacket) -> None:
    """블렌드셰이프 프레임을 OSC로 UE5에 전송."""
    if not packet.blendshape_frames:
        return

    osc = _osc()
    char_id = packet.character_id
    num_frames = len(packet.blendshape_frames)

    with _osc_lock:
        osc.send_message("/mh/bs_start", [
            char_id, num_frames, packet.weight_count, packet.blendshape_fps
        ])

        for i, frame in enumerate(packet.blendshape_frames):
            osc.send_message("/mh/bs", [char_id, i] + [float(w) for w in frame])
            time.sleep(_BS_PACE_SEC)

        osc.send_message("/mh/bs_end", [char_id])

    logger.info(
        f"[OSC/BS] {packet.agent_name} | {num_frames}frames x {packet.weight_count}weights"
    )


# -- 시선용 OSC: 누가 누구에게 말하는지, 참가자 차례인지 --
def _send_speaker_via_osc(packet: PerformancePacket) -> None:
    """/mh/speaker [말하는 캐릭터, 받는 캐릭터 또는 "Participant", 마지막 문장 1/0].
    UE의 BP_AgentGaze가 이걸 보고 말하는 사람은 끝에서 받는 사람을, 나머지는 말하는 사람을 본다."""
    recipient = AGENT_CHARACTER_MAP.get(packet.recipient, "Participant")
    if recipient == packet.character_id:
        recipient = "Participant"
    with _osc_lock:
        _osc().send_message("/mh/speaker", [packet.character_id, recipient, 1 if packet.is_last else 0])


def send_turn(state: str) -> None:
    """/mh/turn ["participant" | "agents"]"""
    with _osc_lock:
        _osc().send_message("/mh/turn", [state])
    logger.info(f"[OSC/Turn] {state}")


# -- 참가자가 지금 말하고 있다 (STT가 받아 적는 글자가 늘어날 때). 메타휴먼이 이때만 맞장구로 끄덕인다 --
def send_pt_speaking() -> None:
    """/mh/pt_speaking [1] — UE의 BP_OSCManager가 받은 시각을 PTSpeakAt에 적는다."""
    with _osc_lock:
        _osc().send_message("/mh/pt_speaking", [1])


# -- 참가자 눈앞 안내 문구 (BP_OSCManager가 HUDText에 담고 BP_HUDMessage가 띄운다) --
def send_hud(text: str) -> None:
    """/mh/hud [영문 신호] — 빈 문자열이면 숨긴다. 한글은 UE OSC에서 깨지므로 문구는 UE 위젯에 둔다.
    UDP라 한 번 잃어도 되게 세 번 보낸다 (같은 신호라 중복은 무해)."""
    with _osc_lock:
        for _ in range(3):
            _osc().send_message("/mh/hud", [text])
            time.sleep(0.05)
    logger.info(f"[OSC/HUD] {text!r}")


# -- 참가자 노트북 화면: 실험 조건 / 타이핑 / 자막 --
def _send_repeated(address: str, args: list) -> None:
    """상태 신호는 UDP라 한 번 잃어도 되게 세 번 보낸다 (send_hud와 같은 이유, 같은 값이라 중복은 무해)."""
    with _osc_lock:
        for _ in range(3):
            _osc().send_message(address, args)
            time.sleep(0.05)


# 마지막으로 보낸 조건. UE가 세션 도중에 다시 켜져도 몇 초 안에 알도록 resend_condition()이
# 타이핑 시작·발화 첫 자막 직전과 vr_output의 주기 신호 때 이 값을 한 번씩 다시 보낸다.
_condition: str | None = None


def send_condition(condition: str) -> None:
    """/mh/condition ["centralized" | "decentralized"] — UE는 이걸 못 받으면 decentralized로 동작한다."""
    global _condition
    _condition = condition
    _send_repeated("/mh/condition", [condition])
    logger.info(f"[OSC/Condition] {condition}")


def resend_condition(condition: str | None = None) -> None:
    """조건을 한 번 다시 보낸다 — 안 주면 마지막으로 보낸 조건 (아직 없으면 아무것도 안 한다)."""
    condition = condition or _condition
    if condition is None:
        return
    with _osc_lock:
        _osc().send_message("/mh/condition", [condition])


def resend_typing(agent_name: str) -> None:
    """주기 신호용 — 타이핑 중인 사람의 /mh/typing [캐릭터, 1]을 한 번 다시 보낸다 (UE가 도중에 다시 켜졌을 때)."""
    character_id = AGENT_CHARACTER_MAP.get(agent_name)
    if not character_id:
        return
    with _osc_lock:
        _osc().send_message("/mh/typing", [character_id, 1])


def send_typing(agent_name: str, typing: bool) -> None:
    """/mh/typing [캐릭터, 1=타이핑 시작 / 0=끝]. centralized에서 디자이너·엔지니어가 PM에게 답을
    말하는 대신 노트북으로 치는 동안. 시작 직전에 조건을 한 번 다시 보낸다."""
    character_id = AGENT_CHARACTER_MAP.get(agent_name)
    if not character_id:
        return
    if typing:
        resend_condition()
    _send_repeated("/mh/typing", [character_id, 1 if typing else 0])
    logger.info(f"[OSC/Typing] {agent_name} {'start' if typing else 'end'}")


_utterance_seq = 0
_utterance_lock = threading.Lock()


def _next_utterance_id() -> int:
    global _utterance_seq
    with _utterance_lock:
        _utterance_seq += 1
        return _utterance_seq


def _send_subtitle(character_id: str, utterance_id: int, index: int, is_last: bool, kind: str, text: str,
                   full_text: str) -> None:
    """/mh/subtitle [캐릭터, 발화 번호, 문장 번호, 마지막 문장 1/0, "speech" | "summary", 이 문장(blob),
    발화 전문(blob)]. 전문은 웹 말풍선과 같은 텍스트라 노트북 채팅창이 발화 시작 때 한 번에 띄울 수 있다.
    텍스트는 UTF-8 바이트를 blob으로 보낸다 — UE OSC는 문자열을 바이트 단위(ANSI)로 읽어서 한글이 깨진다."""
    with _osc_lock:
        _osc().send_message("/mh/subtitle", [
            character_id, utterance_id, index, 1 if is_last else 0, kind,
            text.encode("utf-8"), full_text.encode("utf-8"),
        ])


def send_summary_subtitle(agent_name: str, text: str) -> None:
    """타이핑이 끝난 디자이너·엔지니어의 답 요약을 자막으로 띄운다 (한 줄짜리 발화 하나로 보낸다)."""
    character_id = AGENT_CHARACTER_MAP.get(agent_name)
    if not character_id or not text:
        return
    resend_condition()  # 발화(요약) 첫 자막 직전
    _send_subtitle(character_id, _next_utterance_id(), 0, True, "summary", text, text)
    logger.info(f"[OSC/Subtitle] {agent_name} 요약 {len(text)}자")


# -- 재생 멈춤: centralized에서 디자이너·엔지니어가 타이핑하는 동안 PM 종합 발화를 붙잡아 둔다 --
# TTS·A2F는 그동안 미리 만들어 두고, 재생(audio_end)만 막는다 — 생성까지 막으면 타이핑이 끝난 뒤
# 몇 초씩 공백이 생긴다. vr_output이 PM 라우팅 발화가 다 끝난 뒤(= 재생 중인 문장이 없을 때)에만 건다.
_play_gate = threading.Event()
_play_gate.set()
PLAY_HOLD_TIMEOUT = float(os.getenv("VR_DRAIN_TIMEOUT", "180"))


def hold_playback() -> None:
    _play_gate.clear()


def release_playback() -> None:
    _play_gate.set()


# -- Step 5: OSC 전송 - 오디오 (Base64 청크) --
# 크기: ZeroTier 어댑터 MTU가 2,800B다. 40,000B 청크는 실제 경로에서 IP 단편 15개로 쪼개지고
# 그중 하나만 잃어도 datagram 전체가 버려진다. 2,048B면 OSC 오버헤드를 얹어도 단일 패킷에 들어간다.
# base64 '문자열'을 자르는 구조이므로 이 값은 반드시 4의 배수여야 한다(아니면 조각이 유효한
# base64가 아니게 되어 수신측 FBase64::Decode가 실패한다).
_CHUNK_B64_SIZE = 2048

# 페이싱: 크기를 줄이는 것만으로는 안 고쳐진다. 실측(루프백, 기본 수신 버퍼 65,536B)에서
# 40,000B는 2/4, 2,600B는 41/53, 1,024B는 109/134만 도착했다. 청크 사이에 2ms를 주면
# 네 크기 모두 전량 도착한다. 원인은 크기가 아니라 흐름 제어 없이 연달아 보내는 것이다.
# 특히 audio_end가 잘 유실되는데, 수신측 MHAudioPlayerComponent는 AudioEnd()에서만
# 디코딩·재생 준비를 하므로 이것 하나를 잃으면 소리가 아예 안 난다.
_AUDIO_PACE_SEC = 0.002

def _send_audio_body(packet: PerformancePacket) -> None:
    """audio_start + 청크까지만 보낸다. UE는 audio_end를 받아야 디코딩·재생하므로
    앞 문장이 재생되는 동안 미리 보내 둘 수 있다 (재생 중인 소리는 SoundWave에 복사돼 있어 안 끊긴다)."""
    osc = _osc()
    audio = packet.audio_bytes
    b64 = base64.b64encode(audio).decode("ascii")
    chunks = [b64[i:i + _CHUNK_B64_SIZE] for i in range(0, len(b64), _CHUNK_B64_SIZE)]

    with _osc_lock:
        osc.send_message("/mh/audio_start", [packet.character_id, len(chunks)])
        for idx, chunk in enumerate(chunks):
            osc.send_message("/mh/audio_chunk", [packet.character_id, idx, chunk])
            time.sleep(_AUDIO_PACE_SEC)

    logger.info(
        f"[OSC/Audio] {packet.agent_name} | {len(audio)}bytes -> {len(chunks)}chunks"
    )


def _send_audio_end(packet: PerformancePacket) -> None:
    """UE가 이걸 받는 순간 소리와 입 모양을 같이 재생한다 (BP_AgentBlendshapes.StartBlendshapes)."""
    with _osc_lock:
        _osc().send_message("/mh/audio_end", [packet.character_id])


def _send_audio_via_osc(packet: PerformancePacket) -> None:
    """오디오를 Base64 청크로 OSC 전송 (바로 재생). vr_replay·테스트용."""
    _send_audio_body(packet)
    _send_audio_end(packet)


# -- 문장 분리 --
_SENTENCE_RE = re.compile(r'(?<=[.?!。!?])\s+')

def _split_sentences(text: str) -> list[str]:
    """마침표/물음표/느낌표 기준 문장 분리."""
    parts = _SENTENCE_RE.split(text.strip())
    return [s.strip() for s in parts if s.strip()]


# -- 녹화: 실제 A2F 결과를 파일로 남겨 나중에 UE로 다시 보낸다 (vr_replay.py) --
# 노트북 한 대에서는 UE PIE와 A2F가 VRAM을 같이 못 쓴다. 그래서 UE를 끄고 A2F만 켠 채
# 세션을 돌려 녹화하고, 반대로 A2F를 끄고 UE PIE를 켠 채 재생한다.
# VR_RECORD_DIR이 비어 있으면 아무것도 안 한다.
_record_dir: str | None = None
_record_seq = 0
_record_lock = threading.Lock()


def _record_packet(packet: PerformancePacket) -> None:
    global _record_dir, _record_seq
    root = os.getenv("VR_RECORD_DIR", "").strip()
    if not root or not packet.audio_bytes:
        return
    try:
        with _record_lock:
            if _record_dir is None:
                _record_dir = os.path.join(root, time.strftime("%Y%m%d_%H%M%S"))
                os.makedirs(_record_dir, exist_ok=True)
                logger.info(f"[Record] 녹화 폴더: {_record_dir}")
            _record_seq += 1
            path = os.path.join(_record_dir, f"{_record_seq:04d}_{packet.agent_name}.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump({
                "time": time.time(),
                "agent_name": packet.agent_name,
                "character_id": packet.character_id,
                "text": packet.text,
                "blendshape_fps": packet.blendshape_fps,
                "weight_count": packet.weight_count,
                "blendshape_frames": packet.blendshape_frames,
                "audio_b64": base64.b64encode(packet.audio_bytes).decode("ascii"),
            }, f, ensure_ascii=False)
        if not packet.blendshape_frames:
            logger.warning(f"[Record] {path}: 블렌드셰이프 없이 녹화됨 (A2F 실패)")
    except Exception:
        logger.exception("[Record] 녹화 실패")


# -- 발화 처리 워커 --
def _process_one(agent_name: str, sentence: str, idx: int, total: int) -> PerformancePacket | None:
    """TTS + A2F gRPC 처리."""
    try:
        packet = build_packet(agent_name, sentence)
        if packet is None:
            return None
        packet.audio_bytes = _synthesize(packet)
        bs_data = _generate_blendshapes(packet.audio_bytes)
        if bs_data:
            packet.blendshape_fps = bs_data["fps"]
            packet.weight_count = bs_data["weight_count"]
            packet.blendshape_frames = bs_data["frames"]
        _record_packet(packet)
        logger.info(f"[Process] {agent_name} 문장 {idx+1}/{total} 준비 완료")
        return packet
    except Exception as e:
        logger.error(f"[TTS] {agent_name} 문장 {idx+1} 처리 실패: {e}", exc_info=True)
        return None


def _packet_seconds(packet: PerformancePacket) -> float:
    return len(packet.audio_bytes) / (SAMPLE_RATE * 2)


def _preload(packet: PerformancePacket) -> None:
    """표정 + 소리 데이터를 UE에 미리 보낸다. 재생은 _start_playback()의 audio_end에서 시작된다.
    UE는 bs_end를 받으면 보관 칸(Pending)에 넣기만 하므로 앞 문장 재생을 건드리지 않는다."""
    _send_blendshapes_via_osc(packet)
    _send_audio_body(packet)


def _start_playback(packet: PerformancePacket, send_speaker: bool = True) -> float:
    """(시선용 /mh/speaker 다음) audio_end를 보내 재생을 시작하고, 재생이 끝날 시각을 돌려준다."""
    if send_speaker:
        _send_speaker_via_osc(packet)
    _send_audio_end(packet)
    if packet.sentence_index == 0:  # 발화 첫 자막 직전에 조건을 한 번 다시 보낸다
        resend_condition()
    _send_subtitle(packet.character_id, packet.utterance_id, packet.sentence_index,
                   packet.is_last, "speech", packet.text, packet.full_text or packet.text)
    return time.monotonic() + _packet_seconds(packet)


def _finish(packet: PerformancePacket | None) -> None:
    global _unplayed
    with _idle_cv:
        _unplayed -= 1
        _idle_cv.notify_all()


def _playback_worker():
    """큐에서 준비된 패킷을 꺼내 순서대로 재생. 항상 1개 스레드만 실행.
    앞 문장이 재생되는 동안 다음 문장을 미리 보내 두고(_preload), 앞 문장이 끝나면
    VR_SENTENCE_GAP(같은 사람) / VR_SPEAKER_GAP(사람이 바뀜)만큼 쉰 뒤 audio_end만 보낸다.
    전에는 앞 문장이 끝난 뒤에 전송을 시작해서 전송 시간(0.8~2초)이 그대로 공백이 됐다."""
    current: PerformancePacket | None = None
    current_end = 0.0
    while True:
        try:
            wait = None if current is None else max(0.0, current_end - time.monotonic())
            epoch, packet = _speech_queue.get(timeout=wait)
        except queue.Empty:
            # 다음 문장 없이 앞 문장이 끝났다 → 재생 완료로 표시 (wait_until_idle이 풀린다)
            _finish(current)
            current = None
            continue
        if epoch != _epoch:  # reset() 전에 들어온 문장 — 재생하지 않는다
            _finish(packet)
            continue
        try:
            if PRESEND:
                _preload(packet)
            speaker_sent = False
            if current is not None:
                gap = SENTENCE_GAP if current.character_id == packet.character_id else SPEAKER_GAP
                delay = current_end - time.monotonic()
                if delay > 0:
                    time.sleep(delay)
                if epoch != _epoch:  # 기다리는 동안 취소됐다 — 미리 보낸 데이터는 다음 문장이 덮어쓴다
                    _finish(current)
                    current = None
                    _finish(packet)
                    continue
                # /mh/speaker는 앞 문장이 끝나는 순간 보낸다 — 공백 동안 UE의 SpeakerPending이
                # 시선을 붙잡아 둬서, 사람이 바뀔 때 0.5초짜리 "아무도 안 말함"(state 4)이 끼지 않는다.
                # 재생이 막혀 있으면(타이핑 중) 시선 신호는 막힘이 풀린 뒤 _start_playback이 보낸다.
                if _play_gate.is_set():
                    _send_speaker_via_osc(packet)
                    speaker_sent = True
                    time.sleep(gap)
                _finish(current)
                current = None
            if not _play_gate.is_set():
                if not _play_gate.wait(PLAY_HOLD_TIMEOUT):
                    logger.warning(f"[Playback] 재생 멈춤이 {PLAY_HOLD_TIMEOUT:.0f}초 동안 안 풀려서 그냥 재생")
            if epoch != _epoch:  # 미리 보내거나 재생 멈춤을 기다리는 동안 취소됐다
                _finish(packet)
                continue
            if not PRESEND:
                _preload(packet)
            current_end = _start_playback(packet, send_speaker=not speaker_sent)
            current = packet
        except Exception as e:
            logger.error(f"[Playback] 재생 실패: {e}", exc_info=True)
            # 이 문장은 재생 못 한 채로 끝낸다. 아직 재생 중인 앞 문장은 위의 timeout에서 끝난다.
            _finish(packet)

# 재생 워커 시작 (1개)
threading.Thread(target=_playback_worker, daemon=True, name="playback-worker").start()


# -- Entry point --
def wait_until_idle(timeout: float | None = None) -> bool:
    """대기열에 넣은 문장이 전부 재생될 때까지 기다린다 (UE 재생 시간 기준).
    vr_output.wait_until_idle()이 trigger() 호출이 다 끝난 뒤에 부르므로,
    돌아오면 넘긴 발화가 전부 재생된 상태다."""
    with _idle_cv:
        return _idle_cv.wait_for(lambda: _unplayed == 0, timeout)


_ENGINEER_RE = re.compile(r"엔지니어|engineer", re.IGNORECASE)
_DESIGNER_RE = re.compile(r"디자이너|designer", re.IGNORECASE)


def _routing_recipients(sentences: list[str]) -> list[str]:
    """centralized PM 라우팅 발화의 문장별 받는 사람 — PM이 디자이너, 엔지니어 순으로 보며 묻게 한다.
    엔지니어를 부르는 첫 문장 앞까지는 Designer, 그 문장부터는 Engineer. 디자이너도 같이 부르는 문장
    ("디자이너와 엔지니어 두 분께")은 갈림점으로 안 친다. 그런 문장이 없으면 문장 수 절반에서 나눈다."""
    n = len(sentences)
    split = next(
        (i for i, s in enumerate(sentences) if _ENGINEER_RE.search(s) and not _DESIGNER_RE.search(s)),
        (n + 1) // 2,
    )
    return ["Designer" if i < split else "Engineer" for i in range(n)]


def trigger(agent_name: str, text: str, recipient: str = "", routing: bool = False) -> None:
    """vr_output의 워커 스레드에서 발화 순서대로 호출된다. 문장마다 TTS + A2F를 만들어
    재생 대기열에 넣고, 다 넣으면 돌아온다. 앞 발화가 재생되는 동안 다음 발화를 미리 만들어서
    에이전트가 바뀔 때 생기던 공백(5.6~9.8초 실측)을 없앤다.
    대기열(VR_PREFETCH 문장)이 차면 put()에서 기다리므로 너무 멀리 앞서 만들지 않는다.
    routing=True(centralized PM 라우팅)면 /mh/speaker의 받는 사람을 문장마다 디자이너→엔지니어로 둔다."""
    global _unplayed
    epoch = _epoch
    utterance_id = _next_utterance_id()
    sentences = _split_sentences(text)
    recipients = _routing_recipients(sentences) if routing else [recipient] * len(sentences)
    for i, sentence in enumerate(sentences):
        if epoch != _epoch:  # 만드는 도중 세션이 취소됐다
            return
        packet = _process_one(agent_name, sentence, i, len(sentences))
        if packet is None:
            # 한 번 더 시도한다 — 그래도 실패하면 이 문장은 화면에만 남고 VR에서는 건너뛴다
            logger.warning(f"[TTS] {agent_name} 문장 {i+1} 재시도")
            packet = _process_one(agent_name, sentence, i, len(sentences))
        if packet is None:
            logger.error(f"[TTS] {agent_name} 문장 {i+1} 건너뜀 (VR에서 안 들림): {sentence[:40]}")
            continue
        packet.recipient = recipients[i]
        packet.is_last = (i == len(sentences) - 1)
        packet.utterance_id = utterance_id
        packet.sentence_index = i
        packet.full_text = text
        with _idle_cv:
            _unplayed += 1
        _speech_queue.put((epoch, packet))


def reset() -> None:
    """세션 취소 시 vr_output.reset()이 부른다. 아직 재생 안 한 문장을 전부 버린다.
    지금 재생 중인 문장은 끝까지 간다 (UE에 멈춤 신호가 없다)."""
    global _epoch
    with _idle_cv:
        _epoch += 1
    _play_gate.set()  # 타이핑 때문에 막아 둔 재생을 풀어 줘야 붙잡혀 있던 문장이 버려진다
    while True:
        try:
            _speech_queue.get_nowait()
        except queue.Empty:
            break
        _finish(None)

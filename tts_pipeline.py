# tts_pipeline.py
# ElevenLabs TTS → Audio2Face gRPC → OSC to UE5
import os
import asyncio
import base64
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
from performance_packet import PerformancePacket, build_packet

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
AGENT_VOICE_MAP: dict[str, str] = {
    "UXResearcher":     os.getenv("VOICE_UX_RESEARCHER", ""),
    "VisualDesigner":   os.getenv("VOICE_VISUAL_DESIGNER", ""),
    "SoftwareEngineer": os.getenv("VOICE_SOFTWARE_ENGINEER", ""),
}

# -- Audio2Face gRPC --
A2F_GRPC_HOST = os.getenv("A2F_GRPC_HOST", "localhost")
A2F_GRPC_PORT = os.getenv("A2F_GRPC_PORT", "52000")
NGC_API_KEY = os.getenv("NGC_API_KEY", "")
A2F_CONTAINER_NAME = "audio2face-3d"
A2F_DOCKER_IMAGE = "nvcr.io/nim/nvidia/audio2face-3d:2.0"
A2F_CACHE_DIR = os.path.expanduser("~/.cache/audio2face-3d")

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
_osc_client: udp_client.SimpleUDPClient | None = None
_osc_lock = threading.Lock()
_speech_queue = queue.Queue(maxsize=2)
_trigger_lock = threading.Lock()
_prev_done = threading.Event()
_prev_done.set()  # 처음엔 idle

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


def _osc() -> udp_client.SimpleUDPClient:
    global _osc_client
    if _osc_client is None:
        host = os.getenv("UE5_OSC_HOST", "127.0.0.1")
        port = int(os.getenv("UE5_OSC_PORT", "7400"))
        _osc_client = udp_client.SimpleUDPClient(host, port)
        logger.info(f"OSC 클라이언트 초기화: {host}:{port}")
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
    return b"".join(gen)


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

        osc.send_message("/mh/bs_end", [char_id])

    logger.info(
        f"[OSC/BS] {packet.agent_name} | {num_frames}frames x {packet.weight_count}weights"
    )


# -- Step 5: OSC 전송 - 오디오 (Base64 청크) --
_CHUNK_B64_SIZE = 40000  # ZeroTier UDP 안전 마진 (60KB → 40KB)

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
        f"[OSC/Audio] {packet.agent_name} | {len(audio)}bytes -> {len(chunks)}chunks"
    )


# -- 문장 분리 --
_SENTENCE_RE = re.compile(r'(?<=[.?!。!?])\s+')

def _split_sentences(text: str) -> list[str]:
    """마침표/물음표/느낌표 기준 문장 분리."""
    parts = _SENTENCE_RE.split(text.strip())
    return [s.strip() for s in parts if s.strip()]


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
        logger.info(f"[Process] {agent_name} 문장 {idx+1}/{total} 준비 완료")
        return packet
    except Exception as e:
        logger.error(f"[TTS] {agent_name} 문장 {idx+1} 처리 실패: {e}", exc_info=True)
        return None


def _playback_worker():
    """큐에서 준비된 패킷을 꺼내 순서대로 재생. 항상 1개 스레드만 실행."""
    while True:
        packet = _speech_queue.get()
        if packet is None:
            _speech_queue.task_done()
            continue
        try:
            # 블렌드셰이프 + 오디오를 병렬로 OSC 전송
            # UE5의 TryStartPlayback에서 둘 다 도착하면 동시 재생
            bs_thread = threading.Thread(
                target=_send_blendshapes_via_osc, args=(packet,), daemon=True)
            audio_thread = threading.Thread(
                target=_send_audio_via_osc, args=(packet,), daemon=True)
            bs_thread.start()
            audio_thread.start()
            bs_thread.join()
            audio_thread.join()
            # 다음 발화와 겹치지 않게 오디오 길이만큼 대기
            audio_duration = len(packet.audio_bytes) / (SAMPLE_RATE * 2)
            time.sleep(audio_duration)
        except Exception as e:
            logger.error(f"[Playback] 재생 실패: {e}", exc_info=True)
        finally:
            _speech_queue.task_done()

# 재생 워커 시작 (1개)
threading.Thread(target=_playback_worker, daemon=True, name="playback-worker").start()


# -- Entry point --
def trigger(agent_name: str, text: str) -> None:
    """guardrails.py에서 호출. 이전 발화 재생이 끝날 때까지 대기 후,
    현재 발화의 TTS/재생을 백그라운드로 시작하고 즉시 리턴.
    → AutoGen은 항상 한 발화만 미리 생성 가능 (텍스트 선행, 오디오는 순차)."""
    _prev_done.wait()
    _prev_done.clear()

    def _run():
        try:
            with _trigger_lock:
                sentences = _split_sentences(text)
                if not sentences:
                    return
                for i, sentence in enumerate(sentences):
                    packet = _process_one(agent_name, sentence, i, len(sentences))
                    if packet:
                        _speech_queue.put(packet)
                _speech_queue.join()
        except Exception as e:
            logger.error(f"[TTS] {agent_name} 실패: {e}", exc_info=True)
        finally:
            _prev_done.set()

    threading.Thread(target=_run, daemon=True, name=f"tts-{agent_name}").start()

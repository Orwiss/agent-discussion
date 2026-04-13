# MetaHuman 실시간 발화 파이프라인 — 전체 구현 가이드

> 기반 문서: `multi_agent_metahuman_realtime_pipeline_plain.md`
> 대상 레포: `agent-discussion`
> 엔진: UE 5.7 / MetaHuman Creator 엔진 통합 버전

---

## 0. 실현 가능성 분석

### 코드에서 확인한 사실들

**`clean_message_hook` 실제 시그니처** (`guardrails.py:63`):
```python
def clean_message_hook(*, sender, message, recipient, silent):
```
keyword-only 인자. `sender.name`으로 에이전트 이름 직접 접근 가능.

**통과 경로는 단 하나** (`guardrails.py:114~119`):
```python
    # 통과
    if fp:
        _recent_fingerprints.append(fp)
    sender._last_sent_content = cleaned
    return _set_message_content(message, cleaned)
```
차단 경로 4곳은 전부 `_set_message_content(message, "")` 반환. **TTS 삽입 위치는 정확히 `line 117`과 `line 119` 사이다.** 차단된 메시지에는 TTS가 걸리지 않는 구조가 자연스럽게 보장된다.

**AG2 스레딩 구조** (`web.py:461`):
```python
with IOWebsockets.run_server_in_thread(host="127.0.0.1", port=8765, on_connect=on_connect) as ws_uri:
```
`on_connect`는 별도 스레드에서 동기 실행. AG2 GroupChat 루프도 동기. `threading.Thread(daemon=True)`로 TTS를 백그라운드 실행하면 AG2 루프 블로킹 없음. **안전하다.**

**에이전트 구분**: `sender.name` 고정값 3개 — `UXResearcher`, `VisualDesigner`, `SoftwareEngineer`. 매 발화마다 포함되어 있어 voice ID / MetaHuman 매핑 즉시 가능.

### 구조적 한계 2가지

**한계 1 — 발화 딜레이 누적 (배치 구조)**
```
LLM 생성: 2~8초 (DeepSeek-R1 추론 포함 최대 15초)
+ ElevenLabs Flash v2.5: ~300~700ms
+ OSC 전송 (PCM 청킹): ~50~100ms
= 총 2.5초 ~ 16초
```
채팅창 텍스트 노출과 MetaHuman 발화 시작 사이에 이 딜레이가 있다. Phase 1 데모 용도로는 허용 범위. 문장 단위 스트리밍은 Phase 2에서 도입.

**한계 2 — `guardrails.py`는 `iostream` 접근 불가**
`clean_message_hook`은 `iostream`(WebSocket) 참조가 없다. 그런데 TTS 결과(오디오)를 WebSocket이 아닌 OSC로 UE5에 직접 보내는 설계이므로 **이건 문제가 아니다.** 오히려 설계상 맞다.

---

## Phase 1 구체 구현 계획

### Step 1. 패키지 & 환경 설정

**`requirements.txt`에 추가:**
```
python-osc>=1.8.3
elevenlabs>=2.0.0
```

**`.env`에 추가:**
```env
ELEVENLABS_API_KEY=sk_xxxxxxxxxxxxxxxx

VOICE_UX_RESEARCHER=<ElevenLabs Voice ID>
VOICE_VISUAL_DESIGNER=<ElevenLabs Voice ID>
VOICE_SOFTWARE_ENGINEER=<ElevenLabs Voice ID>

UE5_OSC_HOST=127.0.0.1
UE5_OSC_PORT=7400
```

---

### Step 2. `performance_packet.py` 신규 생성 (루트)

Performance Packet 데이터 구조 전담 모듈. Phase 1에서는 mood 없이 NEUTRAL 고정 — 립싱크만 동작시킨다.

> **mood를 Phase 1에서 빼는 이유:** mood가 의미 있으려면 발화 도중 표정이 바뀌어야 하는데, Phase 1은 발화 전체를 한번에 TTS하는 배치 구조라 발화 내내 표정이 고정된다. 어색한 고정 표정보다 NEUTRAL이 낫다. mood는 Phase 2에서 문장 단위 스트리밍과 함께 도입한다.

```python
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
```

---

### Step 3. `tts_pipeline.py` 신규 생성 (루트)

```python
# tts_pipeline.py
import os
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


_CHUNK_SIZE = 8192

def _send_via_osc(packet: PerformancePacket) -> None:
    osc = _osc()
    audio = packet.audio_bytes
    chunks = [audio[i:i + _CHUNK_SIZE] for i in range(0, len(audio), _CHUNK_SIZE)]
    with _osc_lock:
        osc.send_message("/mh/start", [
            packet.character_id,
            len(chunks),
        ])
        for idx, chunk in enumerate(chunks):
            osc.send_message("/mh/chunk", [packet.character_id, idx, chunk])
        osc.send_message("/mh/end", [packet.character_id])
    logger.info(
        f"[OSC] {packet.agent_name} | {len(audio)}bytes → {len(chunks)}chunks"
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
```

---

### Step 4. `guardrails.py` 수정 — 정확한 삽입 위치

**현재 코드 (line 114~119, 유일한 통과 경로):**
```python
    # 통과
    if fp:
        _recent_fingerprints.append(fp)
    sender._last_sent_content = cleaned

    return _set_message_content(message, cleaned)
```

**변경 후:**
```python
    # guardrails.py 상단 (line 5 근처) import 추가
    from tts_pipeline import trigger

    # ...기존 코드 전부 유지...

    # 통과
    if fp:
        _recent_fingerprints.append(fp)
    sender._last_sent_content = cleaned

    trigger(sender.name, cleaned)           # ← 이 1줄만 추가

    return _set_message_content(message, cleaned)
```

**수정 범위: import 1줄 + trigger 호출 1줄. 기존 로직 0줄 변경.**

---

### Step 5. UE 5.7 세팅

#### 5-1. 프로젝트 생성 & 플러그인

1. UE 5.7 → New Project → Games → Blank → Blueprint → 이름: `AgentMH`
2. Edit → Plugins 검색 후 활성화:
   - `OSC` — 체크 (기본 비활성)
   - `MetaHuman` — 체크 (5.6+부터 엔진 내장)
   - `Audio Driven Animation` — 체크
3. Restart Now

#### 5-2. MetaHuman 에셋 (UE 5.7 엔진 통합 방식)

```
Window → MetaHuman → MetaHuman Creator
```
에디터 내부에서 캐릭터 3개 디자인 → Export to Unreal → `Content/MetaHumans/[이름]/BP_[이름].uasset` 생성.

#### 5-3. RuntimeAudioImporter 플러그인

PCM 바이트를 런타임에 SoundWave로 변환하는 데 필요.

```
Edit → Plugins → "RuntimeAudioImporter" 검색
```
없으면 Fab에서 검색 → Free → Install to Engine.

#### 5-4. `BP_OSCManager` Blueprint 생성

Content Browser → Blueprint Class → Actor → 이름 `BP_OSCManager`

**Components:**
- `+ Add` → `OSC Server` → 이름 `OscServer`

**Details (OscServer 선택):**
- Listen Address: `0.0.0.0`
- Listen Port: `7400`
- Start Listening on Begin Play: ✅

**Event Graph:**

```
[Event BeginPlay]
  └→ [OscServer] Start Listening
  └→ [OscServer] Bind Event to On Osc Message Received
        └→ [Custom Event] OnOscMessage(Address, Message)
```

`OnOscMessage` 내부:

```
[Switch on String] Address
  │
  ├─ "/mh/start"
  │     Get OSC Message String Value(Message, 0) → Set CurrentCharID
  │     Get OSC Message Int32 Value(Message, 1)  → Set ExpectedChunks
  │     Clear Array: ChunkBuffer
  │
  ├─ "/mh/chunk"
  │     Get OSC Message Blob Value(Message, 2) → ChunkData
  │     Set Array Elem: ChunkBuffer[ChunkIndex] = ChunkData
  │
  └─ "/mh/end"
        Flatten ChunkBuffer → FullPCMBytes
        [Call] PlayOnCharacter(CurrentCharID, FullPCMBytes)
```

**`PlayOnCharacter` 함수:**

```
Switch on String (CharID):
  "MH_UXResearcher"     → TargetMH = MH_UXResearcher_Ref
  "MH_VisualDesigner"   → TargetMH = MH_VisualDesigner_Ref
  "MH_SoftwareEngineer" → TargetMH = MH_SoftwareEngineer_Ref
  └→ TargetMH → PlayVoice(PCMBytes)
```

#### 5-5. MetaHuman 자식 Blueprint

`BP_[이름]` 우클릭 → Create Child Blueprint Class → `BP_MH_UXResearcher` 등 3개

각 자식 BP에 `PlayVoice` 함수 추가:

```
Function PlayVoice(PCMBytes)

  [RuntimeAudioImporterLibrary] Import Audio From Buffer
    → PCMBytes, 16-bit, 16000Hz, 1ch
    → Callback: OnImportComplete(ImportedSoundWave)

  [OnImportComplete]
    [AudioComponent] Set Sound → ImportedSoundWave
    [AudioComponent] Play
```

> Phase 1에서는 표정 없이 립싱크만 동작. mood 기반 표정은 Phase 2에서 문장 단위 스트리밍과 함께 도입.

**Audio Driven Animation 연결:**
MetaHuman Face Component → Details → Audio Driven Animation → Audio Component 슬롯에 AudioComponent 드래그.

#### 5-6. 레벨 세팅

1. `BP_MH_UXResearcher`, `BP_MH_VisualDesigner`, `BP_MH_SoftwareEngineer` 배치
2. `BP_OSCManager` 배치
3. `BP_OSCManager` Details에서 각 `_Ref` 변수에 레벨의 MetaHuman 지정

---

### Phase 1 파일 변경 요약

```
agent-discussion/
├── performance_packet.py      ← 신규 (mood 없음, 캐릭터 매핑만)
├── tts_pipeline.py            ← 신규
├── meeting/
│   └── guardrails.py          ← 2줄 추가
├── requirements.txt           ← 2개 패키지 추가
└── .env                       ← 5줄 추가

AgentMH/  (별도 UE 프로젝트)
├── BP_OSCManager              (OSC 수신 + PCM 조립)
├── BP_MH_UXResearcher         (립싱크만, 표정 없음)
├── BP_MH_VisualDesigner       (립싱크만, 표정 없음)
└── BP_MH_SoftwareEngineer     (립싱크만, 표정 없음)
```

---

## Phase 2 로드맵 — 살아있어 보인다: 문장 단위 스트리밍 + mood + gaze

### 핵심 변경: 배치 → 문장 단위 파이프라인

Phase 1의 "발화 전체를 한번에 TTS" 구조를 문장 단위로 쪼갠다. 이래야 발화 도중 표정이 바뀌는 게 가능해진다.

### mood 판정 방식: LLM 태그 방식

키워드 매칭이 아닌, 에이전트 system prompt에 **문장마다** mood 태그를 직접 붙이도록 지시한다. LLM이 자기 발화의 감정을 제일 잘 아므로 정확도가 가장 높고, 추가 API 호출 없이 지연 0.

**system prompt 지시 예시:**
```
모든 발화에서 각 문장 앞에 [MOOD:타입:강도] 태그를 붙여라.
사용 가능한 타입: NEUTRAL, CONFIDENCE, EXCITEMENT, CONFUSION, PLAYFULNESS, SADNESS, ANGER
강도는 0.0~1.0 사이 소수. 태그가 없는 문장은 NEUTRAL:0.4로 처리된다.
```

**LLM 출력 예시:**
```
[MOOD:EXCITEMENT:0.7] 이 방향 진짜 괜찮은 것 같아요.
[MOOD:CONFIDENCE:0.8] 사용자 테스트 결과도 이걸 뒷받침하고 있고요.
[MOOD:CONFUSION:0.5] 다만 모바일 쪽은 좀 더 봐야 할 것 같습니다.
```

- `clean_message_hook`에서 태그 파싱 → mood 추출 → 태그 제거 후 텍스트 전달
- 문장 단위로 mood가 바뀌므로 스트리밍 파이프라인과 자연스럽게 맞물림

### Python 쪽

**`tts_pipeline.py` — 문장 단위 큐 시스템으로 교체:**

Phase 1의 `trigger`를 대체한다. 발화-per-thread 방식에서 문장-per-큐 방식으로 변경.

```python
import queue
import re

_MOOD_TAG_RE = re.compile(r"^\[MOOD:(\w+):([\d.]+)\]\s*")

def parse_mood_tag(text: str) -> tuple[str, float, str]:
    """문장에서 [MOOD:TYPE:INTENSITY] 태그를 파싱하고 제거."""
    m = _MOOD_TAG_RE.match(text)
    if m:
        return m.group(1), float(m.group(2)), text[m.end():]
    return "NEUTRAL", 0.4, text

def split_sentences(text: str) -> list[str]:
    """마침표/물음표/느낌표 기준 문장 분리. 빈 문장 제거."""
    parts = re.split(r'(?<=[.?!。])\s+', text)
    return [s.strip() for s in parts if s.strip()]

_packet_queue: queue.Queue[PerformancePacket] = queue.Queue()

def _queue_worker():
    while True:
        packet = _packet_queue.get()
        try:
            packet.audio_bytes = _synthesize(packet)
            _send_via_osc(packet)
        except Exception as e:
            logger.error(f"[Queue] 처리 실패: {e}")
        finally:
            _packet_queue.task_done()

threading.Thread(target=_queue_worker, daemon=True, name="tts-worker").start()

def trigger(agent_name: str, text: str, gaze_override: str = "FORWARD") -> None:
    sentences = split_sentences(text)
    for sentence in sentences:
        mood, intensity, clean_text = parse_mood_tag(sentence)
        packet = build_packet(agent_name, clean_text, mood, intensity)
        if packet:
            packet.gaze_target = gaze_override
            _packet_queue.put(packet)
```

**`performance_packet.py` — mood + gaze 필드 추가, `build_packet` 시그니처 확장:**

Phase 1의 `build_packet(agent_name, text)`에서 `build_packet(agent_name, text, mood, intensity)`로 변경.

```python
from typing import Literal

MoodType = Literal["NEUTRAL", "CONFIDENCE", "EXCITEMENT", "CONFUSION", "PLAYFULNESS", "SADNESS", "ANGER"]

@dataclass
class PerformancePacket:
    agent_name:      str
    character_id:    str
    text:            str
    mood:            MoodType = "NEUTRAL"
    mood_intensity:  float = 0.4
    gaze_target:     str = "FORWARD"
    audio_bytes:     bytes = field(default=b"")

def build_packet(agent_name: str, text: str,
                 mood: MoodType = "NEUTRAL", intensity: float = 0.4) -> PerformancePacket | None:
    character_id = AGENT_CHARACTER_MAP.get(agent_name)
    if not character_id:
        return None
    return PerformancePacket(
        agent_name=agent_name,
        character_id=character_id,
        text=text,
        mood=mood,
        mood_intensity=intensity,
    )
```

**`guardrails.py`에 마지막 화자 추적 (gaze용):**
```python
import threading  # 상단에 import 추가

_last_speaker: str = ""
_speaker_lock = threading.Lock()

# 통과 경로에서:
with _speaker_lock:
    gaze = _last_speaker if _last_speaker and _last_speaker != sender.name else "FORWARD"
    _last_speaker = sender.name
trigger(sender.name, cleaned, gaze_override=gaze)
```

### UE5 쪽

#### OSC 프로토콜 확장

`/mh/start` 메시지에 mood, gaze 필드 추가:
```
/mh/start [character_id, mood, mood_intensity, gaze_target, chunk_count]
```

**`_send_via_osc` 변경 (Phase 1 → Phase 2):**
```python
def _send_via_osc(packet: PerformancePacket) -> None:
    osc = _osc()
    audio = packet.audio_bytes
    chunks = [audio[i:i + _CHUNK_SIZE] for i in range(0, len(audio), _CHUNK_SIZE)]
    with _osc_lock:
        osc.send_message("/mh/start", [
            packet.character_id,
            packet.mood,
            packet.mood_intensity,
            packet.gaze_target,
            len(chunks),
        ])
        for idx, chunk in enumerate(chunks):
            osc.send_message("/mh/chunk", [packet.character_id, idx, chunk])
        osc.send_message("/mh/end", [packet.character_id])
```

#### `SetMood` 함수 (각 MetaHuman BP에 추가)

mood별 morph target 매핑. intensity를 곱해서 강약 조절.

```
Switch on String (Mood):
  "NEUTRAL"    → Morph targets 전체 reset to 0
  "CONFIDENCE" → browRaiseIn * 0.3, mouthSmile * 0.4
  "EXCITEMENT" → browRaiseIn * 0.6, eyeWide * 0.3, mouthSmile * 0.5
  "CONFUSION"  → browDown * 0.4, eyeSquint * 0.2
  "PLAYFULNESS"→ browRaiseIn * 0.3, mouthSmile * 0.6, headTilt * 0.2
  "SADNESS"    → browDown * 0.3, mouthFrown * 0.4, eyeSquint * 0.1
  "ANGER"      → browDown * 0.6, eyeSquint * 0.4, jawClench * 0.3

모든 값에 intensity를 곱함. 예: browDown * 0.4 * intensity
```

> morph target 이름은 MetaHuman Creator 기본 rig 기준. 실제 에셋에 따라 이름이 다를 수 있으므로 UE5에서 Face Component의 Morph Target 리스트를 확인해야 함.

#### Look At (gaze) 설정

각 MetaHuman BP에 Look At Component 추가:

```
[On /mh/start received]
  Switch on String (gaze_target):
    "MH_UXResearcher"     → LookAt Target = MH_UXResearcher_Ref → Head Location
    "MH_VisualDesigner"   → LookAt Target = MH_VisualDesigner_Ref → Head Location
    "MH_SoftwareEngineer" → LookAt Target = MH_SoftwareEngineer_Ref → Head Location
    "FORWARD"             → LookAt Target = Default Forward Vector

  Look At 파라미터:
    Interpolation Speed: 3.0 (급격한 시선 이동 방지)
    Clamp Angle: 60° (고개가 부자연스럽게 꺾이는 거 방지)
```

#### 상/하안면 애니메이션 레이어 분리

립싱크(하안면)와 mood 표정(상안면)이 동시 동작해야 한다. Animation Blueprint에서 Layered Blend per Bone 사용:

```
Output Pose → Layered Blend per Bone
  Base: IDLE
  Blend 0 (jaw 이하, Branch Filter: "jaw_root"): Audio Driven Animation
  Blend 1 (brow 이상, Branch Filter: "brow_root"): Mood morph targets

  Blend Weight: 각각 1.0
  Blend Depth: 해당 본 하위 전체
```

> 핵심 리스크: MetaHuman rig의 본 계층 구조에서 jaw_root와 brow_root가 깔끔하게 나뉘는지 확인 필요. 안 나뉘면 morph target 기반으로 수동 분리해야 하는데 작업량이 크게 늘어남.

#### 발화 상태 관리

```
[On /mh/start] → Set IsSpeaking = true
[On /mh/end + AudioComponent OnAudioFinished] → Set IsSpeaking = false

IsSpeaking 활용:
- true: Audio Driven Animation 활성, mood 표정 활성
- false: 립싱크 중단, 표정 서서히 NEUTRAL로 복귀 (Interpolation 1.5초)
```

---

## Phase 3 로드맵 — 듣고 있어 보인다: Listener Reaction

Phase 2까지는 발화자만 움직인다. 나머지 2명은 가만히 서 있음. Phase 3에서는 **듣는 쪽도 반응**하게 만든다.

### 핵심 아이디어

발화자 A가 말하는 동안, 듣는 쪽 B/C에게 "지금 A가 이런 내용을 말하고 있다"를 전달해서 적절한 비언어 반응(고개 끄덕임, 갸우뚱, 미소 등)을 재생.

### Python 쪽

**`listener_reaction.py` 신규 생성:**

```python
# listener_reaction.py
import os
import logging
from typing import Literal
import google.generativeai as genai

logger = logging.getLogger(__name__)

ReactionType = Literal["NOD", "TILT", "SMILE", "CONFUSED", "NEUTRAL"]

genai.configure(api_key=os.getenv("GEMINI_API_KEY"))
_model = genai.GenerativeModel("gemini-2.5-flash")

_CLASSIFY_PROMPT = """다음 발화를 듣는 사람의 자연스러운 비언어 반응을 하나만 골라라.
선택지: NOD(동의/이해), TILT(흥미/호기심), SMILE(재미/공감), CONFUSED(혼란/의문), NEUTRAL(무반응)

발화: {text}

반응(한 단어만):"""


def classify(text: str) -> ReactionType:
    """발화 내용 기반 listener reaction 분류. ~200ms."""
    try:
        resp = _model.generate_content(
            _CLASSIFY_PROMPT.format(text=text[:300]),  # 토큰 절약
            generation_config={"max_output_tokens": 5, "temperature": 0.0},
        )
        result = resp.text.strip().upper()
        if result in ("NOD", "TILT", "SMILE", "CONFUSED", "NEUTRAL"):
            return result
        return "NEUTRAL"
    except Exception as e:
        logger.warning(f"[Listener] 분류 실패, NEUTRAL fallback: {e}")
        return "NEUTRAL"
```

**`tts_pipeline.py`에 listener 알림 추가:**

문장 단위로 TTS를 보낼 때, 동시에 듣는 쪽 MetaHuman에게 reaction OSC를 보낸다.

```python
from listener_reaction import classify
from performance_packet import AGENT_CHARACTER_MAP

def _notify_listeners(speaker: str, text: str) -> None:
    """발화자가 아닌 MetaHuman들에게 listener reaction 전송."""
    reaction = classify(text)
    if reaction == "NEUTRAL":
        return  # 무반응이면 안 보냄
    osc = _osc()
    for agent, char_id in AGENT_CHARACTER_MAP.items():
        if agent == speaker:
            continue
        osc.send_message("/mh/listener", [char_id, reaction])
    logger.info(f"[Listener] {speaker} 발화 → 나머지에게 {reaction}")
```

큐 워커에서 `_send_via_osc` 직후 `_notify_listeners` 호출:

```python
def _queue_worker():
    while True:
        packet = _packet_queue.get()
        try:
            packet.audio_bytes = _synthesize(packet)
            _send_via_osc(packet)
            _notify_listeners(packet.agent_name, packet.text)
        except Exception as e:
            logger.error(f"[Queue] 처리 실패: {e}")
        finally:
            _packet_queue.task_done()
```

**`.env`에 추가:**
```env
GEMINI_API_KEY=AIza...
```

**`requirements.txt`에 추가:**
```
google-generativeai>=0.8.0
```

### UE5 쪽

#### `/mh/listener` OSC 수신 처리

`BP_OSCManager`의 `OnOscMessage`에 분기 추가:

```
├─ "/mh/listener"
│     Get OSC Message String Value(Message, 0) → TargetCharID
│     Get OSC Message String Value(Message, 1) → ReactionType
│     [Call] PlayListenerReaction(TargetCharID, ReactionType)
```

#### Listener State Machine (각 MetaHuman Animation Blueprint)

```
                    ┌──────────────┐
                    │     IDLE     │◄─── 모든 state에서 타이머 만료 시
                    └──────┬───────┘
                           │ /mh/listener 수신
              ┌────────────┼────────────┬──────────────┐
              ▼            ▼            ▼              ▼
        ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌───────────┐
        │   NOD    │ │   TILT   │ │  SMILE   │ │ CONFUSED  │
        │ 1.5~2초  │ │ 1.5~2초  │ │ 1.5~2초  │ │ 1.5~2초   │
        └──────────┘ └──────────┘ └──────────┘ └───────────┘
```

각 state의 애니메이션:

```
NOD:
  Head pitch: 0 → -8° → 0 (0.4초 주기, 2~3회 반복)
  Morph: mouthSmile * 0.15 (미세한 동의 미소)

TILT:
  Head roll: 0 → 12° (0.6초)
  Morph: browRaiseIn * 0.3

SMILE:
  Morph: mouthSmile * 0.5, eyeSquint * 0.15 (눈웃음)
  Duration: 1.5초 → 서서히 NEUTRAL

CONFUSED:
  Head tilt: 0 → -8° roll (0.5초)
  Morph: browDown * 0.3, browRaiseOut * 0.2 (한쪽 눈썹만 올림)
```

> **타이밍 주의:** listener reaction은 발화자의 문장 TTS 완료 후 전송되므로, 실제 발화와 반응 사이에 TTS 지연만큼 갭이 있다. 자연스럽게 보이려면 UE5 쪽에서 reaction 시작 시 0.2~0.5초 랜덤 딜레이를 넣는 게 좋다 — 사람도 즉각 반응하지 않으니까.

#### 동시 발화 + 리스닝 충돌 처리

MetaHuman이 말하면서(IsSpeaking=true) 동시에 listener reaction을 받을 수 있다. 이 경우:

```
if IsSpeaking:
    /mh/listener 무시  # 말하는 중에 고개 끄덕이면 어색함
else:
    PlayListenerReaction 실행
```

---

## Phase별 난이도

| Phase | 목표 | Python | UE5 | 주요 리스크 | 추가 비용 |
|-------|------|--------|-----|------------|----------|
| Phase 1 | 소리가 나온다 | ★★☆☆☆ | ★★★☆☆ | RuntimeAudioImporter PCM 포맷 | ElevenLabs Creator $22/월 |
| Phase 2 | 살아있어 보인다 | ★★★☆☆ | ★★★★☆ | 문장 단위 스트리밍 타이밍, 상하안면 분리, LLM mood 태그 안정성 | 동일 |
| Phase 3 | 듣고 있어 보인다 | ★★★☆☆ | ★★★★★ | Listener State Machine 자연스러움, reaction 타이밍 | Gemini Flash ~무료 |

---

## 비용 추정

### ElevenLabs

| 플랜 | 월 문자 수 | 가격 | 커스텀 Voice |
|------|-----------|------|-------------|
| Free | 10,000자 | $0 | 불가 |
| Starter | 30,000자 | $5/월 | 최대 10개 |
| Creator | 100,000자 | $22/월 | 최대 30개 |
| Pro | 500,000자 | $99/월 | 최대 160개 |

에이전트 3명 × 평균 발화 200자 × 토론 1회 약 30턴 = **~18,000자/세션**.

- **Free 티어:** 커스텀 voice 불가 + 1세션도 빠듯. 개발/테스트 불가능.
- **Starter ($5):** 커스텀 voice 가능, 약 1.5세션/월. 개발용으로는 가능하지만 빠듯.
- **Creator ($22):** 약 5세션/월. 개발 + 데모 용도로 적정.

> 에이전트별 다른 목소리가 필수이므로 최소 Starter 이상 필요. 개발 단계에서는 Creator 추천.

### Gemini Flash (Phase 3)

- Gemini 2.5 Flash: 입력 $0.15/1M tokens, 출력 $0.60/1M tokens
- listener reaction 분류는 입력 ~100토큰, 출력 ~5토큰
- 세션당 ~90회 호출 (30턴 × 3문장) = **~$0.002/세션**. 사실상 무료.

---

## 전송 방식 검토: OSC의 한계와 대안

### Phase 1~2: OSC (현행 유지)

OSC는 제어 신호용 프로토콜이지 오디오 스트리밍용이 아니다. 하지만:
- 로컬 환경(127.0.0.1)에서는 UDP 패킷 유실이 거의 없음
- UE5 OSC 플러그인이 기본 제공되어 설정이 간단
- Phase 1~2에서는 발화 단위/문장 단위 배치이므로 실시간 스트리밍 수준의 안정성이 필요 없음

**최소한의 안전장치 (Phase 2에서 추가):**
```
[UE5] /mh/end 수신 시:
  if len(ChunkBuffer) < ExpectedChunks:
      Log Warning: "청크 누락 {received}/{expected}"
      OSC → Python에 /mh/retry [character_id] 전송
```

### 원격 환경 또는 안정성 요구 시: TCP 소켓 대안

같은 머신이 아닌 경우(UE5가 별도 PC)에는 OSC/UDP 대신 TCP가 낫다:

```
Python (TCP Server :7400)
  ↕ persistent connection
UE5 (TCP Client → FTcpSocketBuilder)
```

UE5 쪽에서 `FSocket` + `FTcpSocketBuilder`로 TCP 클라이언트를 만들 수 있고, Python 쪽은 `asyncio.start_server`로 대응. 단, UE5 TCP 소켓은 Game Thread에서 직접 읽으면 프레임 드랍이 생기므로 별도 `FRunnable` 스레드가 필요.

> Phase 1~2에서는 로컬 OSC로 충분. TCP 전환은 원격 환경이 필요해지는 시점에 검토.

---

## 테스트 순서

### Phase 1 테스트

```bash
# 1-1. ElevenLabs 단독 — TTS가 작동하는가
python test_tts.py
# → PCM 파일 생성 확인, 로컬 재생으로 음질/목소리 검증

# 1-2. OSC 전송 — UE5가 수신하는가
# (UE5 PIE 실행 상태에서)
python test_osc.py
# → UE5 Output Log에 /mh/start, /mh/chunk, /mh/end 수신 로그 확인

# 1-3. UE5 단독 — RuntimeAudioImporter가 PCM을 먹는가
# UE5 에디터에서 테스트 버튼으로 하드코딩된 PCM 바이트 재생
# → 립싱크가 동작하는지 확인 (Audio Driven Animation)

# 1-4. 전체 통합
python web.py
# → 브라우저 세션 시작 → 에이전트 발화 → MetaHuman 립싱크 확인
```

### Phase 2 테스트

```bash
# 2-1. mood 태그 파싱 단위 테스트
python -m pytest test_mood_parser.py
# → parse_mood_tag, split_sentences 정확성 확인

# 2-2. LLM mood 태그 생성 확인
# 에이전트 system prompt에 mood 태그 지시 추가 후 단독 실행
# → 발화에 [MOOD:TYPE:INTENSITY] 태그가 문장마다 붙는지 확인

# 2-3. 문장 단위 스트리밍 통합
python web.py
# → 한 발화 내에서 MetaHuman 표정이 문장마다 바뀌는지 확인
# → gaze: 발화자가 이전 화자를 쳐다보는지 확인
```

### Phase 3 테스트

```bash
# 3-1. listener reaction 분류 정확도
python test_listener.py
# → 다양한 발화 샘플에 대해 NOD/TILT/SMILE/CONFUSED/NEUTRAL 분류 확인
# → 응답 시간 200ms 이내 확인

# 3-2. 통합 테스트
python web.py
# → 발화자 A가 말하는 동안 B, C가 자연스럽게 반응하는지 확인
# → IsSpeaking 충돌: 말하는 중 listener reaction이 무시되는지 확인
```

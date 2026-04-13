# 파이프라인 설명서

> 지금 돌아가고 있는 Audio2Face 파이프라인이 정확히 뭘 하는지 처음부터 끝까지 설명.
> 블랙박스 풀기용.

---

## 0. 한 줄 요약

**에이전트가 말한 텍스트 → ElevenLabs로 음성 생성 → NVIDIA A2F가 음성에서 얼굴 블렌드셰이프 뽑음 → OSC로 UE5에 전송 → MetaHuman 얼굴 움직임.**

---

## 1. 전체 그림

```
[노트북 (Python)]                         [데스크탑 (UE5)]
┌─────────────────────────────┐           ┌──────────────────┐
│ 1. 에이전트 LLM 출력          │           │                  │
│    "안녕하세요"              │           │                  │
│           │                 │           │                  │
│           ▼                 │           │                  │
│ 2. ElevenLabs TTS           │           │                  │
│    → PCM 오디오 바이트        │           │                  │
│           │                 │           │                  │
│           ▼                 │           │                  │
│ 3. Docker: A2F Microservice │           │                  │
│    (gRPC localhost:52000)   │           │                  │
│    → 68개 블렌드셰이프 프레임  │           │                  │
│           │                 │           │                  │
│           ▼                 │           │                  │
│ 4. 블렌드셰이프 리매핑         │           │                  │
│    (PascalCase → camelCase) │           │                  │
│           │                 │           │                  │
│           ▼                 │           │                  │
│ 5. OSC 전송 (UDP)           │──────────▶│ 6. BP_OSCManager │
│    - /mh/bs_* (블렌드셰이프)  │ ZeroTier  │    수신 + 라우팅  │
│    - /mh/audio_* (오디오)   │           │           │      │
└─────────────────────────────┘           │           ▼      │
                                          │ 7. MetaHuman     │
                                          │    - 얼굴 블렌드셰이프│
                                          │    - 오디오 재생  │
                                          └──────────────────┘
```

---

## 2. 각 단계 상세

### 1단계: 에이전트 발화

- **파일**: `agents/simple_agents.py`, `web.py`, `meeting/guardrails.py`
- **일어나는 일**: LLM(Groq, OpenRouter 등)이 에이전트 페르소나로 응답 생성
- **끝나면**: `meeting/guardrails.py`의 `clean_message_hook`이 텍스트 정리 (think 태그 제거, 마크다운 제거 등)
- **그 다음**: `trigger(agent_name, cleaned_text)` 호출 → `tts_pipeline.py`로 넘어감

### 2단계: ElevenLabs TTS

- **파일**: `tts_pipeline.py` → `_synthesize()` 함수
- **입력**: 한국어 텍스트 + 에이전트별 voice_id
- **출력**: PCM 바이트 (**16kHz, 16-bit, mono**)
- **중요**: A2F가 요구하는 정확한 포맷 (다른 샘플레이트면 안 됨)
- **에이전트별 voice_id**: `.env`에 저장
  ```
  VOICE_UX_RESEARCHER=...
  VOICE_VISUAL_DESIGNER=...
  VOICE_SOFTWARE_ENGINEER=...
  ```

### 3단계: Docker A2F Microservice

이게 제일 블랙박스 같은 부분이야. 자세히 설명.

#### 3-1. Docker 컨테이너란?

NVIDIA가 만든 **프로그램 하나가 컨테이너로 포장되어 있음**. 컨테이너를 실행하면 그 안에 TensorRT + Audio2Face 모델 + gRPC 서버가 다 들어있음.

```bash
# 이 명령어 한 줄로 A2F 서비스가 떠 있음
docker run -d --name audio2face-3d --gpus all \
  -p 52000:52000 -p 8000:8000 \
  -e NGC_API_KEY=nvapi-... \
  -v ~/.cache/audio2face-3d:/tmp/a2x \
  nvcr.io/nim/nvidia/audio2face-3d:2.0
```

- `-p 52000:52000`: 컨테이너 내부의 52000 포트를 호스트 52000으로 연결 (gRPC)
- `-p 8000:8000`: health check용 HTTP 포트
- `--gpus all`: GPU 사용 허용
- `-v ~/.cache/...`: TensorRT 엔진 캐시 (최초 빌드 후 재사용)
- `NGC_API_KEY`: NVIDIA 무료 API 키 (이미지 다운로드 인증용)

**자동 시작**: `tts_pipeline.py`의 `_ensure_a2f_running()` 함수가 파이썬 실행 시 컨테이너가 떠 있는지 확인하고, 없으면 자동으로 띄움.

#### 3-2. 컨테이너 안에서 일어나는 일

1. 컨테이너 시작 시 **James 모델** (기본값)의 TensorRT 엔진을 GPU에 로드
2. `localhost:52000`에서 **gRPC 서버** 대기
3. `localhost:8000/v1/health/ready`로 상태 확인 가능

#### 3-3. gRPC 통신 (핵심)

**왜 gRPC?** NVIDIA 공식 API가 gRPC 양방향 스트리밍이야. REST나 CLI 아님.

**통신 단계** (`tts_pipeline.py` → `_a2f_grpc_call()`):

```
Python ────────────────────▶ A2F 컨테이너
    1. AudioStreamHeader 전송
       (오디오 포맷, 얼굴 파라미터, 블렌드셰이프 multiplier 등 설정)
       
    2. AudioWithEmotion 전송 (여러 번)
       (PCM 오디오를 1초 단위 청크로 쪼개서 전송)
       
    3. EndOfAudio 전송
       (이제 끝났다는 신호 — 이거 안 보내면 서버가 응답 안 함)

A2F 컨테이너 ──────────────▶ Python
    1. AnimationDataStreamHeader 수신
       (블렌드셰이프 이름 리스트, 샘플레이트 등)
       
    2. AnimationData 수신 (여러 번)
       (한 프레임 = 68개 블렌드셰이프 weight)
       
    3. Status 수신 (성공/실패)
```

**gRPC 프로토콜 정의**: `proto/protobuf_files/*.proto` 파일 (NVIDIA 공식)
**Python 스텁**: `nvidia_ace/` 디렉토리 (proto 컴파일 결과)

#### 3-4. 얼굴 파라미터 (gRPC 헤더에서 전송)

**face_params** — A2F가 입/눈 움직임을 만들 때 쓰는 설정:
- `upperFaceStrength`: 윗얼굴 움직임 강도
- `lowerFaceStrength`: 아래얼굴 움직임 강도
- `lowerFaceSmoothing`: 아래얼굴 부드러움 (작을수록 선명)
- `tongueStrength`: 혀 움직임

**bs_weight_multipliers** — 각 블렌드셰이프별 가중치 (내 튜닝):
- `JawLeft/Right: 0.2` ← 턱 좌우 흔들림 방지
- `MouthFunnel: 1.2` ← 입술 오므리기 강조
- `EyeLook*: 0.0` ← A2F는 시선 제어 안 함 → 0으로 꺼버림
- (자세한 값은 `tts_pipeline.py` 270~310 line)

**emotion**: 자동. A2F 안에 **A2E(Audio2Emotion) 모델**이 내장돼 있어서 음성 파형에서 감정을 자동 분석함. 우리는 수동 감정 주입 안 함 (`enable_preferred_emotion=False`).

### 4단계: 블렌드셰이프 리매핑

A2F Microservice는 블렌드셰이프를 **PascalCase**로 보내:
```
0: EyeBlinkLeft
1: EyeLookDownLeft
...
```

근데 우리 UE5 Blueprint는 `bs_names.csv`의 **camelCase + neutral 포함** 순서를 기대해:
```
0: neutral
1: eyeBlinkLeft
2: eyeLookDownLeft
...
```

**순서가 1칸씩 밀려있어.** 그래서 `tts_pipeline.py`의 `_build_remap_table()` + `_remap_frame()`이:
1. Microservice가 보낸 블렌드셰이프 이름을 UE5 순서로 다시 배치
2. `neutral`은 0.0으로 채움 (Microservice가 안 보냄)
3. 이름 매칭은 소문자 비교로 함 (PascalCase/camelCase 무시)

**이걸 안 하면** 턱 weight가 눈에 가는 식으로 **완전 어긋나.** 초반에 그래서 턱만 막 움직였던 거.

### 5단계: OSC 전송 (UDP)

블렌드셰이프와 오디오를 **별도 OSC 메시지**로 UE5에 보냄. 데스크탑 IP는 `.env`의 `UE5_OSC_HOST`, 포트 7400.

#### 블렌드셰이프 메시지
```
/mh/bs_start [char_id, num_frames, weight_count, fps]
  → "이제부터 블렌드셰이프 프레임 전송 시작"

/mh/bs [char_id, frame_idx, w0, w1, ..., w67]    (N번 반복)
  → 한 프레임 = 68개 float weight

/mh/bs_end [char_id]
  → "다 보냈다" → UE5가 재생 시작
```

#### 오디오 메시지
```
/mh/audio_start [char_id, num_chunks]
  → "오디오 청크 N개 보낼 거야"

/mh/audio_chunk [char_id, chunk_idx, base64_data]  (N번 반복)
  → Base64 인코딩된 PCM 조각 (40KB씩 쪼갬, ZeroTier UDP 제한 때문)

/mh/audio_end [char_id]
  → "끝" → UE5가 Base64 디코딩 + 재생
```

**char_id**: `MH_UXResearcher`, `MH_VisualDesigner`, `MH_SoftwareEngineer` — 어느 캐릭터 꺼인지 구분용.

### 6단계: UE5가 수신

**BP_OSCManager** (Blueprint Actor): 포트 7400에서 OSC 대기

#### 블렌드셰이프 처리 경로

```
OSC 수신 → BP_OSCManager
  → char_id 기반으로 어느 MetaHuman인지 판별
  → 그 MetaHuman의 BP_MH_BlendshapePlayer 컴포넌트에 전달
    → 프레임 배열 저장
    → Tick 이벤트에서 매 프레임 Set Morph Target (68개)
    → MetaHuman 얼굴 움직임
```

#### 오디오 처리 경로 (우리가 직접 만든 C++ 플러그인)

**왜 플러그인이 필요했나?** UE5 Blueprint만으로는 런타임에 PCM 바이트를 받아서 재생할 수가 없어. C++이 필요.

**플러그인 위치**: `Plugins/MHAudioPlayer/`

```
OSC 수신 → BP_OSCManager
  → char_id 기반으로 MetaHuman 판별
  → 그 MetaHuman의 MHAudioPlayerComponent (C++ 컴포넌트) 호출
    → AudioStart / AudioChunk / AudioEnd 순서로 받음
    → AudioEnd에서 Base64 디코딩 → PCM 바이트 조립
    → USoundWaveProcedural 객체 생성 + QueueAudio로 PCM 넣음
    → UAudioComponent로 재생 (MetaHuman 위치에서 공간 오디오)
```

**싱크**: `/mh/audio_end`에서 바로 `PlayAudio()` 호출. 블렌드셰이프는 `/mh/bs_end`에서 자동 재생. 두 메시지가 거의 동시에 도착하니까 싱크 맞음.

---

## 3. 주요 파일 역할

### Python 쪽

| 파일 | 역할 |
|---|---|
| `web.py` | 웹서버 + AG2 에이전트 오케스트레이션 |
| `agents/simple_agents.py` | 에이전트 3명 정의 (페르소나, system_message) |
| `meeting/guardrails.py` | 에이전트 출력 클리닝 + `trigger()` 호출 |
| `tts_pipeline.py` | **핵심**: TTS + A2F gRPC + OSC 전송 |
| `performance_packet.py` | 발화 패킷 데이터 구조 |
| `nvidia_ace/` | gRPC 프로토 Python 스텁 (자동 생성) |
| `proto/protobuf_files/` | NVIDIA 공식 proto 파일 원본 |
| `bs_names.csv` | 68개 블렌드셰이프 이름 (UE5 순서) |
| `.env` | API 키, 호스트, 포트 설정 |

### UE5 쪽

| 파일/블루프린트 | 역할 |
|---|---|
| `BP_OSCManager` | OSC 수신 + 캐릭터 라우팅 |
| `BP_MH_BlendshapePlayer` | 블렌드셰이프 재생 컴포넌트 |
| `MHAudioPlayerComponent` (C++) | PCM 오디오 수신/재생 컴포넌트 |
| 각 MetaHuman BP | 두 컴포넌트를 붙여놓음 |

---

## 4. 데이터 흐름 실제 예시

"안녕하세요" 한 마디를 말하면:

```
1. LLM: "안녕하세요"
   ↓
2. ElevenLabs TTS: 1.2초짜리 PCM 바이트 (~38KB)
   ↓
3. A2F gRPC 호출:
   - Header 전송 (포맷 + 파라미터)
   - Audio 청크 전송 (1초씩 2개)
   - EndOfAudio 전송
   → 약 36프레임 × 68 weight (1.2초 × 30fps)
   ↓
4. 리매핑 (Microservice 순서 → UE5 순서)
   ↓
5. OSC 전송:
   - /mh/bs_start [MH_UXResearcher, 36, 68, 30]
   - /mh/bs ×36 (프레임별 68개 weight)
   - /mh/bs_end
   - /mh/audio_start [MH_UXResearcher, 1]
   - /mh/audio_chunk [MH_UXResearcher, 0, "..."]
   - /mh/audio_end
   ↓
6. UE5:
   - BP_OSCManager가 수신
   - MH_UXResearcher 캐릭터에 라우팅
   - BP_MH_BlendshapePlayer: Tick마다 Set Morph Target
   - MHAudioPlayerComponent: PCM 재생
   ↓
7. 화면: MetaHuman 얼굴이 움직이며 "안녕하세요" 발화
```

---

## 5. 디버깅 체크리스트

### "말이 안 들림 / 얼굴이 안 움직임"

1. **Docker 컨테이너 떠있나?**
   ```bash
   docker ps | grep audio2face
   curl http://localhost:8000/v1/health/ready
   ```

2. **Python 로그 확인** — `[A2F/gRPC]` 로그 있어야 함

3. **UE5 Output Log에 `[MHAudio] PCM 디코딩 완료` 뜨나?** — OSC가 UE5에 도달하고 있는지 확인

4. **방화벽** — 데스크탑 UDP 7400 허용됐나?

5. **ZeroTier** — 두 기기가 같은 네트워크에 있나?

### "립싱크가 안 맞음"

- 블렌드셰이프 순서 리매핑이 잘 되는지 확인
- FPS 계산 (현재 30fps — A2F 기본값)
- 오디오와 블렌드셰이프 시작 시점 (`/mh/bs_end`와 `/mh/audio_end`)

### "엉뚱한 부위가 움직임 (턱이 눈에)"

- `_build_remap_table` 로직 확인 — 이게 고장나면 순서가 밀림

---

## 6. 왜 이렇게 복잡한가?

짧은 답: **공짜로 NVIDIA 상용 기술 쓰려다 보니.**

- A2F가 원래 Omniverse 전용이었는데 Microservice로 분리됨 → gRPC API 써야 함
- gRPC는 HTTP REST보다 설정이 복잡함 (proto 파일, 스텁 생성 등)
- UE5는 기본 OSC 플러그인이 있지만 바이너리 오디오 직접 못 받음 → 우리가 C++ 플러그인 만들어야 했음
- Microservice가 보내는 블렌드셰이프 이름/순서가 MetaHuman 기본이랑 달라서 리매핑 필요
- ZeroTier로 기기가 분리되어 있어서 UDP 청크 크기 제한 고려해야 함

**한 번 세팅하면 그 다음은 `python web.py` 한 줄로 다 돌아감.** 세팅 자체가 복잡한 거지 운영은 간단해.

---

## 7. "이거 말고 다른 방법은 없었나?"

- **Omniverse 데스크탑 앱 사용** → NVIDIA가 이 방향 버림, 재현성 낮음
- **Audio2Face SDK를 C++로 직접 임베드** → C++ 코드 직접 작성 필요, 유지보수 부담 큼
- **유료 플러그인 (RuntimeAudioImporter 등)** → 무료 요구사항에 맞지 않음
- **다른 립싱크 솔루션 (OVR LipSync 등)** → 한국어 퀄리티 미검증, 커뮤니티 작음

**결국 현재 방식이 무료 + 재현성 + 품질의 균형점.**

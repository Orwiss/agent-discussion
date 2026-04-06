# UE5.6 Blueprint 세팅 가이드

> 노트북에서 OSC로 전송되는 블렌드셰이프 + 오디오를 MetaHuman에 적용

---

## 전체 흐름

```
노트북 (Python)
  → OSC /mh/bs_start [캐릭터ID, 프레임수, weight수, fps]
  → OSC /mh/bs [캐릭터ID, 프레임인덱스, w0, w1, ..., w67]  ×N
  → OSC /mh/bs_end [캐릭터ID]
  → OSC /mh/audio_start [캐릭터ID, 청크수]
  → OSC /mh/audio_chunk [캐릭터ID, 청크인덱스, base64데이터]  ×N
  → OSC /mh/audio_end [캐릭터ID]

데스크탑 (UE5.6)
  → BP_OSCManager가 수신
  → 블렌드셰이프: 프레임 버퍼에 저장 → Tick에서 Set Morph Target
  → 오디오: Base64 디코딩 → MediaPlayer로 재생
```

---

## STEP 0. 프로젝트 세팅

1. Epic Games Launcher → UE **5.6** 설치
2. New Project → Games → Blank → Blueprint → 이름: `AgentMH`
3. Edit → Plugins → `OSC` 검색 → 체크 ✅ → 재시작
4. MetaHuman Creator에서 캐릭터 3개 Export (이 프로젝트로)

> 필요한 플러그인은 **OSC 하나**뿐입니다.

---

## STEP 1. MetaHuman 자식 BP (3개)

### 1-1. 생성

각 MetaHuman BP 우클릭 → Create Child Blueprint Class:
- `BP_MH_UXResearcher`
- `BP_MH_VisualDesigner`
- `BP_MH_SoftwareEngineer`

### 1-2. 변수 추가 (각 자식 BP에)

| 변수명 | 타입 | 설명 |
|--------|------|------|
| `BSFrames` | Array of Array of Float | 블렌드셰이프 프레임 버퍼 (※ 아래 대안 참고) |
| `BSFrameIndex` | Integer | 현재 재생 중인 프레임 인덱스 |
| `BSWeightCount` | Integer | weight 개수 (68) |
| `BSPlaying` | Boolean | 블렌드셰이프 재생 중 여부 |
| `BSFPS` | Integer | 기본값 60 |
| `BSTimer` | Float | 프레임 타이밍 누적 |

> **Array of Array 대안:** UE5에서 2D 배열이 안 될 수 있음. 그 경우 `BSRawData` (Array of Float)로 1D 배열로 펼쳐서 저장하고, `BSFrameIndex × BSWeightCount` 로 인덱싱.

### 1-3. 블렌드셰이프 재생 (Event Tick)

```
[Event Tick] (DeltaTime)
  │
  └→ [Branch] (BSPlaying)
       │
       ├─ True:
       │    BSTimer += DeltaTime
       │    │
       │    └→ [While] BSTimer >= 1.0 / BSFPS:
       │          BSTimer -= 1.0 / BSFPS
       │          │
       │          └→ [Branch] BSFrameIndex < 총 프레임 수:
       │               │
       │               ├─ True:
       │               │    현재 프레임의 weights 꺼내기
       │               │    For Each weight:
       │               │      [Face → Set Morph Target] (이름: 인덱스 매핑, 값: weight)
       │               │    BSFrameIndex++
       │               │
       │               └─ False:
       │                    BSPlaying = false
       │                    BSFrameIndex = 0
       │                    모든 morph target → 0.0 (리셋)
       │
       └─ False: (아무것도 안 함)
```

### 1-4. StartBlendshapes 함수

새 함수: `StartBlendshapes`
- 입력 없음 (BSFrames가 이미 채워진 상태에서 호출)

```
[StartBlendshapes]
  → BSFrameIndex = 0
  → BSTimer = 0.0
  → BSPlaying = true
```

### 1-5. Morph Target 이름 매핑

Audio2Face SDK가 출력하는 68개 weight의 인덱스를 MetaHuman의 morph target 이름에 매핑해야 합니다.

정확한 매핑은 SDK 모델의 config에 정의되어 있습니다:
`Audio2Face-3D-SDK\_data\audio2face-models\audio2face-3d-v2.3-mark\bs_skin_config.json`

이 파일에서 블렌드셰이프 이름 목록을 확인한 후, UE5에서 같은 이름의 morph target에 매핑합니다.

---

## STEP 2. BP_OSCManager

### 2-1. 생성

Content Browser → Blueprint Class → Actor → `BP_OSCManager`

### 2-2. 변수

| 변수명 | 타입 | 설정 |
|--------|------|------|
| `CurrentCharID` | String | — |
| `CurrentBSFrames` | Array of Float | 1D로 펼친 블렌드셰이프 데이터 |
| `ExpectedFrames` | Integer | — |
| `CurrentWeightCount` | Integer | — |
| `CurrentFPS` | Integer | — |
| `AudioChunks` | Array of String | Base64 청크 |
| `ExpectedAudioChunks` | Integer | — |
| `MH_UXResearcher` | Actor (Object Reference) | Instance Editable ✅ |
| `MH_VisualDesigner` | Actor (Object Reference) | Instance Editable ✅ |
| `MH_SoftwareEngineer` | Actor (Object Reference) | Instance Editable ✅ |

### 2-3. OSC Server 컴포넌트

1. `+ Add Component` → `OSC Server` → 이름: `OscServer`
2. Details:
   - Server IP Address: `0.0.0.0`
   - Server Port: `7400`
   - Start Listening On Begin Play: ✅

### 2-4. Event Graph — BeginPlay

```
[Event BeginPlay]
  └→ [Bind Event to On OSC Message Received]
        Target: OscServer
        Event → [OnOscMessage] (Message, IP, Port)
```

### 2-5. OnOscMessage — 주소 분기

```
[OnOscMessage] (Message)
  └→ [Get OSC Message Address] → [Get Full Path] → AddressString
     └→ [Switch on String]
           ├─ "/mh/bs_start"    → Handle_BSStart
           ├─ "/mh/bs"          → Handle_BSFrame
           ├─ "/mh/bs_end"      → Handle_BSEnd
           ├─ "/mh/audio_start" → Handle_AudioStart
           ├─ "/mh/audio_chunk" → Handle_AudioChunk
           └─ "/mh/audio_end"   → Handle_AudioEnd
```

### 2-6. Handle_BSStart

```
[Handle_BSStart] (Message)
  → Get String at Index 0 → Set CurrentCharID
  → Get Int32 at Index 1 → Set ExpectedFrames
  → Get Int32 at Index 2 → Set CurrentWeightCount
  → Get Int32 at Index 3 → Set CurrentFPS
  → Clear CurrentBSFrames
```

### 2-7. Handle_BSFrame

```
[Handle_BSFrame] (Message)
  → Get String at Index 0 → CharID (확인용)
  → Get Int32 at Index 1 → FrameIndex
  → For i = 0 to CurrentWeightCount - 1:
      Get Float at Index (i + 2) → weight값
      CurrentBSFrames.Add(weight값)
```

> 1D 배열에 순서대로 쌓임. 프레임N의 weight M = CurrentBSFrames[N * WeightCount + M]

### 2-8. Handle_BSEnd

```
[Handle_BSEnd] (Message)
  → CurrentCharID로 해당 MetaHuman 찾기 (Switch on String)
  → Cast to BP_MH_UXResearcher (또는 해당 클래스)
  → MetaHuman의 BSRawData = CurrentBSFrames (복사)
  → MetaHuman의 BSWeightCount = CurrentWeightCount
  → MetaHuman의 BSFPS = CurrentFPS
  → MetaHuman.StartBlendshapes() 호출
```

### 2-9. Handle_AudioStart / Chunk / End

**AudioStart:**
```
Get String at Index 0 → CharID
Get Int32 at Index 1 → Set ExpectedAudioChunks
Clear AudioChunks
```

**AudioChunk:**
```
Get String at Index 2 → Base64 String
AudioChunks.Add(Base64String)
```

**AudioEnd:**
```
AudioChunks → Join String Array → FullBase64
Base64 디코드 → PCM bytes
→ 오디오 재생 (아래 참고)
```

### 2-10. 오디오 재생 방법

UE5.6 내장 기능으로 PCM을 런타임 재생하려면 플러그인이 필요한데,
**간단한 대안: Python 노트북에서 직접 스피커로 재생.**

`.env`에 추가:
```
PLAY_AUDIO_LOCAL=true
```

tts_pipeline.py에서 sounddevice로 재생하면 UE5에서 오디오 처리할 필요 없음.
MetaHuman은 립싱크(블렌드셰이프)만 담당.

> 이 경우 /mh/audio_* OSC 메시지는 아예 안 보내도 됨.
> VR에서 공간 오디오가 필요하면 그때 UE5 오디오 재생을 추가.

---

## STEP 3. 레벨 배치

1. `BP_MH_UXResearcher`, `BP_MH_VisualDesigner`, `BP_MH_SoftwareEngineer` 배치
2. `BP_OSCManager` 배치
3. BP_OSCManager Details에서 각 MH 변수에 레벨의 MetaHuman 지정
4. MetaHuman 3개 삼각형 배치

---

## STEP 4. 네트워크 테스트

1. 데스크탑 IP 확인: cmd에서 `ipconfig` → IPv4 주소 (예: 192.168.0.10)
2. 노트북의 `.env`에서 `UE5_OSC_HOST=192.168.0.10` 설정
3. 데스크탑: UE5 Play (PIE)
4. 노트북: `python test_tts.py` → OSC 전송 확인
5. MetaHuman 입이 움직이면 성공!

---

## 트러블슈팅

### "OSC 수신이 안 된다"
- 데스크탑 방화벽에서 UDP 7400 포트 열기
- Windows 방화벽 → 고급 설정 → 인바운드 규칙 → 새 규칙 → UDP → 포트 7400 → 허용

### "블렌드셰이프가 적용 안 된다"
- Morph Target 이름 매핑 확인
- bs_skin_config.json의 이름과 MetaHuman의 morph target 이름이 일치해야 함

### "Base64 Decode 노드가 없다"
- 오디오는 노트북에서 직접 재생하는 방식으로 우회 (STEP 2-10 참고)

# UE5 Blueprint 세팅 가이드 (초보자용)

> agent-discussion → ElevenLabs TTS → OSC → UE5 MetaHuman 발화 + 립싱크

---

## 전체 흐름 요약

```
Python (agent-discussion)
  → ElevenLabs TTS로 음성 생성 (PCM)
  → Base64로 인코딩
  → OSC로 UE5에 전송

UE5 (AgentMH)
  → BP_OSCManager가 OSC 수신
  → Base64 디코딩 → PCM 바이트 복원
  → RuntimeAudioImporter로 SoundWave 변환
  → MetaHuman AudioComponent에서 재생
  → 오디오 볼륨 기반 립싱크 (jaw 열기/닫기)
```

---

## STEP 0. 플러그인 설치

플러그인 **2개**만 설치하면 됩니다.

### 0-1. OSC 플러그인 (엔진 내장)

1. UE5 에디터 열기
2. 상단 메뉴 `Edit` → `Plugins`
3. 검색창에 `OSC` 입력
4. `OSC` 플러그인 찾아서 체크박스 ✅ 클릭
5. 에디터 재시작 (Restart Now)

### 0-2. RuntimeAudioImporter (GitHub 무료)

PCM 바이트를 런타임에 재생 가능한 SoundWave로 바꿔주는 플러그인.

1. 브라우저에서 열기: https://github.com/gtreshchev/RuntimeAudioImporter
2. 녹색 `Code` 버튼 클릭 → `Download ZIP` 클릭
3. 다운받은 ZIP 압축 풀기
4. 안에 `RuntimeAudioImporter-master` 같은 폴더가 있을 텐데,
   그 안의 실제 플러그인 폴더를 UE5 프로젝트에 복사:
   ```
   AgentMH/
   └── Plugins/               ← 이 폴더가 없으면 새로 만들기
       └── RuntimeAudioImporter/
           ├── Source/
           ├── RuntimeAudioImporter.uplugin
           └── ...
   ```
5. UE5 에디터 재시작
6. `Edit` → `Plugins` → `RuntimeAudioImporter` 검색 → 활성화 확인

> ⚠️ Fab(마켓플레이스)에서 16만원짜리가 이거임. GitHub에서 받으면 무료.

---

## STEP 1. MetaHuman 자식 Blueprint 만들기 (3개)

### 1-1. 자식 BP 생성

MetaHuman Creator로 이미 3개 캐릭터를 만들어놨으므로,
Content Browser에 `BP_[캐릭터이름]` 같은 Blueprint가 있을 거예요.

각 MetaHuman BP마다:

1. Content Browser에서 해당 MetaHuman BP 찾기
   - 보통 `Content/MetaHumans/[이름]/BP_[이름]` 경로
2. **우클릭** → `Create Child Blueprint Class`
3. 이름 지정:
   - 첫 번째 → `BP_MH_UXResearcher`
   - 두 번째 → `BP_MH_VisualDesigner`
   - 세 번째 → `BP_MH_SoftwareEngineer`
4. 저장 위치: `Content/Blueprints/` (폴더 없으면 우클릭 → New Folder)

### 1-2. 컴포넌트 추가

`BP_MH_UXResearcher`를 **더블클릭**해서 Blueprint 에디터 열기.

1. 좌측 상단 `Components` 패널에서 `+ Add` 버튼 클릭
2. 검색: `Audio`
3. `Audio Component` 선택
4. 추가된 컴포넌트 이름을 `VoiceAudio`로 변경
   - 컴포넌트 클릭 → F2 누르면 이름 변경 가능
5. `Compile` (상단 좌측) → `Save` (상단 좌측)

> `BP_MH_VisualDesigner`, `BP_MH_SoftwareEngineer`에도 **똑같이** 반복

### 1-3. PlayVoice 함수 만들기

`BP_MH_UXResearcher`가 열린 상태에서:

**함수 생성:**
1. 좌측 `My Blueprint` 패널 → `Functions` 섹션 옆의 `+` 버튼 클릭
2. 함수 이름: `PlayVoice` 입력 → Enter

**입력 파라미터 추가:**
3. 함수가 열리면, 우측 `Details` 패널에서:
   - `Inputs` 옆의 `+` 버튼 클릭
   - 이름: `PCMBytes`
   - 타입: 돋보기 아이콘 클릭 → `byte` 검색 → `Byte` 선택
   - 그 다음 타입 옆의 아이콘(네모 모양)을 클릭해서 `Array`(배열)로 전환
   - 결과적으로 `Array of Byte` 타입이 되어야 함

**노드 배치:**
4. 그래프(검은 바탕)에서 **우클릭** → 검색창에 `Import Audio From Buffer` 입력 → 선택
   - 이 노드는 RuntimeAudioImporter 플러그인에서 제공하는 노드

5. `Import Audio From Buffer` 노드 설정:
   - 좌측의 `Buffer` 핀 ← 함수 입력의 `PCMBytes` 핀에서 **선을 드래그**해서 연결
   - `Format` 드롭다운 클릭 → `RAW (PCM)` 선택
   - `Sample Rate`: `16000` 입력
   - `Bit Depth`: `Int16` 선택
   - `Num of Channels`: `1` 입력

6. 이 노드의 `On Result` (또는 `On Complete`) 핀에서:
   - 핀에서 드래그 → `Add Custom Event` 선택 → 이름: `OnAudioReady`
   - 자동으로 `Imported Sound Wave` 파라미터가 붙음

7. `OnAudioReady` 이벤트 **뒤에** 노드 2개 추가:
   - 그래프 우클릭 → `VoiceAudio` 입력 → `Set Sound` 선택
     - `New Sound` 핀 ← `Imported Sound Wave` 연결
   - `Set Sound` 실행 핀에서 드래그 → `VoiceAudio` → `Play` 선택

**최종 연결 흐름:**
```
[PlayVoice 시작] (PCMBytes 입력)
  │
  └→ [Import Audio From Buffer]
        Buffer: PCMBytes
        Format: RAW (PCM)
        Sample Rate: 16000
        Bit Depth: Int16
        Channels: 1
        │
        └→ [OnAudioReady] (ImportedSoundWave)
              │
              └→ [VoiceAudio → Set Sound] (ImportedSoundWave)
                    │
                    └→ [VoiceAudio → Play]
```

8. `Compile` → `Save`

> `BP_MH_VisualDesigner`, `BP_MH_SoftwareEngineer`에도 **똑같이** PlayVoice 함수 만들기

### 1-4. 립싱크 (오디오 볼륨 기반 Jaw 움직임)

OVRLipSync은 UE5.7과 호환이 안 되므로,
오디오 볼륨(음량)을 실시간으로 읽어서 MetaHuman의 턱(Jaw)을 열고 닫는 방식으로 립싱크합니다.
완벽한 비짐은 아니지만 **말할 때 입이 움직이는 수준**은 충분히 됩니다.

#### Event Graph에 Tick 기반 립싱크 추가

`BP_MH_UXResearcher` 열기 → `Event Graph` 탭:

1. **변수 추가** (좌측 My Blueprint → Variables → `+`):
   - `IsSpeaking` / Boolean / 기본값 false

2. **Event Tick 노드 찾기** (없으면 우클릭 → `Event Tick` 검색):

3. `Event Tick` 뒤에 아래 노드들 연결:

```
[Event Tick]
  │
  └→ [Branch] (Condition: IsSpeaking)
        │
        ├─ True:
        │    → [VoiceAudio → Get Playback Percentage]
        │       (또는 [VoiceAudio → Is Playing] 으로 체크)
        │
        │    → [VoiceAudio → Get Envelope Value]  ← 핵심! 현재 볼륨값 반환 (0.0~1.0)
        │       └→ EnvelopeValue (float)
        │
        │    → [Face (SkeletalMeshComponent) → Set Morph Target]
        │         - Target Name: "jaw_open_01"  (또는 "CTRL_expressions_jawOpen")
        │         - Value: EnvelopeValue * 0.7  (Multiply 노드 사용)
        │
        └─ False:
             → [Face → Set Morph Target]
                  - Target Name: "jaw_open_01"
                  - Value: 0.0  (입 다물기)
```

**노드 하나씩 만드는 법:**

a) `Event Tick` 실행 핀에서 드래그 → `Branch` 검색 → 선택
   - `Condition` 핀 ← `IsSpeaking` 변수 Get으로 연결

b) `True` 핀에서 드래그 → 우클릭 → `Get Envelope Value` 검색 → 선택
   - 이 노드 없으면: `VoiceAudio`를 그래프로 드래그 → 핀에서 `Envelope` 검색
   - 반환값은 float (0.0 = 무음, 1.0 = 최대 볼륨)

c) `Envelope Value` 출력에서 드래그 → `Multiply (float)` → 다른 핀에 `0.7` 입력
   - 0.7은 입이 너무 크게 벌어지는 거 방지 (나중에 조정 가능)

d) 우클릭 → `Set Morph Target` 검색 → 선택
   - `Target`: Face 컴포넌트 (Components에서 드래그)
   - `Morph Target Name`: `jaw_open_01` 입력
     (안 되면 `CTRL_expressions_jawOpen` 시도)
   - `Value`: 위의 Multiply 결과 연결

e) `False` 핀에서도 `Set Morph Target` 하나 더:
   - 같은 Target Name
   - Value: `0.0` (입 닫기)

> **Morph Target 이름 찾는 법:**
> MetaHuman의 Face 컴포넌트 클릭 → Details → Morph Target 리스트에서
> jaw 관련 항목 확인. 보통 `jaw_open_01` 또는 `CTRL_expressions_jawOpen`

#### PlayVoice에서 IsSpeaking 제어

PlayVoice 함수의 `VoiceAudio → Play` **바로 뒤에** 추가:

```
[VoiceAudio: Play]
  └→ [Set IsSpeaking = true]
```

그리고 Event Graph에 오디오 재생 종료 감지 추가:

```
[Event BeginPlay]
  └→ [VoiceAudio → Bind Event to On Audio Finished]
        └→ [Custom Event: OnVoiceDone]
              └→ [Set IsSpeaking = false]
              └→ [Face → Set Morph Target: "jaw_open_01", Value: 0.0]
```

**만드는 법:**
1. `Event BeginPlay` 실행 핀에서 드래그
2. `Bind Event to On Audio Finished` 검색 → 선택
   - Target: VoiceAudio (Components에서 드래그)
3. Event 핀(빨간색)에서 드래그 → `Add Custom Event` → 이름: `OnVoiceDone`
4. `OnVoiceDone` 뒤에:
   - `Set IsSpeaking` → false
   - `Set Morph Target` → jaw_open_01, Value: 0.0

5. `Compile` → `Save`

> 나머지 2개 BP에도 **똑같이** 반복

---

## STEP 2. BP_OSCManager 만들기

### 2-1. Blueprint 생성

1. Content Browser → `Content/Blueprints/` 폴더
2. 빈 공간 **우클릭** → `Blueprint Class`
3. 부모 클래스: `Actor` 선택
4. 이름: `BP_OSCManager`
5. **더블클릭**해서 열기

### 2-2. 변수 만들기

좌측 `My Blueprint` 패널 → `Variables` 옆의 `+` 버튼으로 **6개** 추가:

| # | 변수명 | 타입 | 설정 |
|---|--------|------|------|
| 1 | `CurrentCharID` | String | — |
| 2 | `ExpectedChunks` | Integer | — |
| 3 | `ReceivedChunks` | Array of String | 타입을 String으로 선택 → 배열 아이콘 클릭 |
| 4 | `MH_UXResearcher` | Actor (Object Reference) | 눈 아이콘 👁 클릭 (Instance Editable) |
| 5 | `MH_VisualDesigner` | Actor (Object Reference) | 눈 아이콘 👁 클릭 |
| 6 | `MH_SoftwareEngineer` | Actor (Object Reference) | 눈 아이콘 👁 클릭 |

**Instance Editable 설정법:**
변수 옆에 닫힌 눈 아이콘(👁)이 있어요. 클릭하면 눈이 떠짐 = 레벨에서 편집 가능해짐.
이걸 해야 나중에 레벨에서 MetaHuman을 드롭다운으로 지정할 수 있어요.

### 2-3. OSC Server 컴포넌트 추가

1. 상단 `Components` 패널 → `+ Add` 클릭
2. 검색: `OSC Server` → 선택
3. 추가된 컴포넌트 이름을 `OscServer`로 변경 (F2)
4. `OscServer` 선택한 상태에서 우측 `Details` 패널:
   - `Server IP Address`: `0.0.0.0`
   - `Server Port`: `7400`
   - `Start Listening On Begin Play`: ✅ 체크

### 2-4. Event Graph — BeginPlay (OSC 수신 시작)

`Event Graph` 탭 열기. `Event BeginPlay` 노드가 이미 있을 거예요.

1. `Event BeginPlay` 실행 핀에서 **드래그**
2. 검색: `Bind Event to On OSC Message Received` → 선택
3. 이 노드의 `Target` 핀 ← Components에서 `OscServer`를 그래프로 드래그해서 연결
4. `Event` 핀 (빨간색 네모)에서 **드래그** → `Add Custom Event` → 이름: `OnOscMessage`
5. 자동으로 파라미터 붙음: Message, IP Address, Port

```
[Event BeginPlay]
  └→ [Bind Event to On OSC Message Received]
        Target: OscServer
        Event → [OnOscMessage] (Message, IP, Port)
```

### 2-5. Event Graph — OnOscMessage (주소별 분기)

`OnOscMessage` 이벤트 뒤에:

1. `Message` 핀에서 드래그 → `Get OSC Message Address` 검색 → 선택
2. 결과 핀에서 드래그 → `Get Full Path` 검색 → 선택 (String 반환)
3. 그 String에서 드래그 → `Switch on String` 검색 → 선택
4. `Switch on String` 노드에서 `+ Add Pin` **3번** 클릭:
   - 핀 1에 입력: `/mh/start`
   - 핀 2에 입력: `/mh/chunk`
   - 핀 3에 입력: `/mh/end`

```
[OnOscMessage] (Message)
  └→ [Get OSC Message Address] (Message)
     └→ [Get Full Path] → AddressString
        └→ [Switch on String]
              ├─ "/mh/start"  → Handle_Start
              ├─ "/mh/chunk"  → Handle_Chunk
              └─ "/mh/end"    → Handle_End
```

### 2-6. Handle_Start 함수

**함수 생성:**
`My Blueprint` → `Functions` → `+` → 이름: `Handle_Start`
- `Details`에서 Input 추가: 이름 `Message`, 타입 `OSC Message`

**함수 내부 노드 (순서대로 연결):**

1. 우클릭 → `Get OSC Message String at Index` 검색 → 배치
   - `Message` 핀 ← 함수 입력의 Message 연결
   - `Index`: `0`
   - 출력 → `CurrentCharID` 변수를 그래프로 드래그 → `Set CurrentCharID` 선택 → 연결

2. 우클릭 → `Get OSC Message Int32 at Index` 검색 → 배치
   - `Message` 핀 ← 함수 입력의 Message
   - `Index`: `1`
   - 출력 → `Set ExpectedChunks` 연결

3. `ReceivedChunks` 변수를 그래프로 드래그 → `Get` 선택
   - 핀에서 드래그 → `Clear` 검색 → 선택 (배열 비우기)

4. 실행 핀 전부 순서대로 연결

```
[Handle_Start] (Message)
  └→ [Get String at Index 0] → [Set CurrentCharID]
     └→ [Get Int32 at Index 1] → [Set ExpectedChunks]
        └→ [ReceivedChunks → Clear]
```

5. 이벤트 그래프의 `Switch on String` → `/mh/start` 핀에서 → `Handle_Start` 호출
   - Message 핀도 OnOscMessage의 Message에서 연결

### 2-7. Handle_Chunk 함수

**함수 생성:**
`Functions` → `+` → 이름: `Handle_Chunk`
- Input: `Message` / `OSC Message`

**함수 내부:**

1. `Get OSC Message String at Index` → `Index`: `2` → 출력: Base64 청크 String
2. `ReceivedChunks` → Get → 핀에서 드래그 → `Add` 선택
   - Add의 입력에 위 String 연결

```
[Handle_Chunk] (Message)
  └→ [Get String at Index 2] → ChunkString
     └→ [ReceivedChunks → Add(ChunkString)]
```

3. `Switch on String` → `/mh/chunk` 핀에서 → `Handle_Chunk` 연결

### 2-8. Handle_End 함수

**함수 생성:**
`Functions` → `+` → 이름: `Handle_End`
- Input: `Message` / `OSC Message`

**함수 내부:**

1. `ReceivedChunks` → Get → 드래그 → `Join String Array` 검색 → 선택
   - `Separator`: 비워두기 (빈 문자열)
   - 출력: FullBase64String

2. FullBase64String에서 드래그 → `Base64 Decode to Bytes` 검색 → 선택
   - 출력: PCMBytes (Array of Byte)

   > **이 노드가 없는 경우:**
   > 우클릭 → `Base 64` 또는 `Decode` 등으로 검색해보기
   > 정 없으면 STEP 2-8 대안 (파일 저장 방식) 참고

3. `PlayOnCharacter` 함수 호출 (다음 단계에서 만듦):
   - `CurrentCharID` → Get → 연결
   - `PCMBytes` → 연결

```
[Handle_End] (Message)
  └→ [ReceivedChunks → Join String Array] (Separator: "")
     └→ FullBase64String
        └→ [Base64 Decode to Bytes] → PCMBytes
           └→ [PlayOnCharacter(CurrentCharID, PCMBytes)]
```

4. `Switch on String` → `/mh/end` 핀에서 → `Handle_End` 연결

#### STEP 2-8 대안: Base64 Decode 노드가 없는 경우

Blueprint에서 Base64 디코드가 안 되면, **Python에서 PCM 파일을 디스크에 저장**하고
UE5에서 파일 경로로 읽는 방식으로 우회할 수 있습니다. 이건 막히면 말해주세요.

### 2-9. PlayOnCharacter 함수

**함수 생성:**
`Functions` → `+` → 이름: `PlayOnCharacter`
- Input 1: `CharID` / String
- Input 2: `PCMBytes` / Array of Byte

**함수 내부:**

1. `CharID`에서 드래그 → `Switch on String` → `+ Add Pin` 3개:
   - `MH_UXResearcher`
   - `MH_VisualDesigner`
   - `MH_SoftwareEngineer`

2. 각 핀 뒤에 (3개 다 같은 패턴):
   - 해당 변수 → Get → 드래그 → `Cast to BP_MH_UXResearcher` (또는 해당 클래스)
   - Cast 성공(✓) 핀 → `As BP_MH_...` 핀에서 드래그 → `PlayVoice` 검색 → 선택
   - `PlayVoice`의 `PCMBytes` 핀 ← 함수 입력의 `PCMBytes` 연결

```
[PlayOnCharacter] (CharID, PCMBytes)
  └→ [Switch on String: CharID]
       │
       ├─ "MH_UXResearcher"
       │    └→ [Get MH_UXResearcher 변수]
       │       └→ [Cast to BP_MH_UXResearcher]
       │          └→ [PlayVoice(PCMBytes)]
       │
       ├─ "MH_VisualDesigner"
       │    └→ [Get MH_VisualDesigner 변수]
       │       └→ [Cast to BP_MH_VisualDesigner]
       │          └→ [PlayVoice(PCMBytes)]
       │
       └─ "MH_SoftwareEngineer"
            └→ [Get MH_SoftwareEngineer 변수]
               └→ [Cast to BP_MH_SoftwareEngineer]
                  └→ [PlayVoice(PCMBytes)]
```

3. `Compile` → `Save`

---

## STEP 3. 레벨에 배치

1. 레벨 에디터로 돌아가기 (Blueprint 에디터 닫거나 상단 탭에서 레벨 선택)

2. Content Browser에서 **4개** 액터를 레벨에 **드래그 앤 드롭**:
   - `BP_MH_UXResearcher`
   - `BP_MH_VisualDesigner`
   - `BP_MH_SoftwareEngineer`
   - `BP_OSCManager`

3. 레벨에서 `BP_OSCManager` 클릭

4. 우측 `Details` 패널에서 (Instance Editable로 만든 변수들이 보임):
   - `MH UX Researcher` 드롭다운 → 레벨의 UXResearcher 선택
   - `MH Visual Designer` 드롭다운 → 레벨의 VisualDesigner 선택
   - `MH Software Engineer` 드롭다운 → 레벨의 SoftwareEngineer 선택

5. MetaHuman 3개 위치를 적당히 조정
   - 예: 삼각형으로 서로 마주보게 배치
   - 카메라가 3명 다 보이도록

---

## STEP 4. 테스트

### 테스트 1: OSC 수신 확인

1. UE5에서 `Play` (▶) 버튼 클릭 (PIE 시작)
2. VS Code 터미널에서:
   ```
   py -3.13 test_osc.py
   ```
3. UE5 하단 `Output Log` 탭 확인:
   - `/mh/start`, `/mh/chunk`, `/mh/end` 관련 로그가 나오면 **OSC 수신 성공**

### 테스트 2: 전체 통합

1. UE5에서 `Play` 상태 유지
2. VS Code 터미널에서:
   ```
   py -3.13 web.py
   ```
3. 브라우저에서 세션 시작
4. 에이전트가 발화하면 MetaHuman에서 소리 + 입 움직임 확인

---

## 트러블슈팅

### "Import Audio From Buffer 노드가 안 보인다"
→ RuntimeAudioImporter 플러그인 미설치.
→ STEP 0-2 다시 확인. Plugins 폴더에 제대로 복사했는지 확인.

### "OSC Server 컴포넌트가 안 보인다"
→ OSC 플러그인 미활성화.
→ Edit → Plugins → `OSC` 검색 → 체크 → 에디터 재시작.

### "Cast 실패 (Cast to BP_MH_... 에서 실패)"
→ 레벨에 배치한 액터가 **자식 BP**인지 확인.
→ 원본 MetaHuman BP가 아니라 `BP_MH_UXResearcher` 등을 배치해야 함.

### "소리는 나는데 입이 안 움직인다"
→ Morph Target 이름이 다를 수 있음.
→ Face 컴포넌트 클릭 → Details → Morph Targets에서 jaw 관련 이름 확인.
→ `jaw_open_01`, `CTRL_expressions_jawOpen`, `jawOpen` 등 시도.

### "Get Envelope Value 노드가 없다"
→ VoiceAudio 컴포넌트를 그래프로 드래그 → 핀에서 `Envelope` 검색.
→ 또는 Details에서 `Envelope Following` 설정을 먼저 활성화해야 할 수 있음:
   VoiceAudio 선택 → Details → `Envelope Follower` 섹션 → `Enable Envelope Following`: ✅

### "Base64 Decode 노드가 없다"
→ 이 경우 Python에서 PCM을 파일로 저장 → UE5에서 파일 경로로 읽는 방식으로 우회 가능.
→ 말해주시면 Python 코드 수정 + UE5 BP 수정 안내 드립니다.

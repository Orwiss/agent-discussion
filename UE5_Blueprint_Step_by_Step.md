# UE5 Blueprint 만들기 - 완전 초보자 가이드

> Python에서 OSC로 보내는 블렌드셰이프 데이터를 MetaHuman 얼굴에 실시간 적용.
> **문장 단위 스트리밍** — 첫 문장 준비되면 바로 재생 시작!

---

## 사전 지식: Blueprint 기본 조작법

모든 Blueprint 작업은 이 패턴의 반복입니다:

```
1. 그래프 빈 공간에서 우클릭
2. 검색창에 노드 이름 입력
3. 노드 선택 → 그래프에 배치됨
4. 핀(동그라미)을 드래그해서 다른 노드의 핀에 연결
```

**핀의 종류:**
- **흰색 삼각형 (실행 핀):** 실행 순서. 왼쪽→오른쪽으로 연결
- **초록색 동그라미:** Boolean (true/false)
- **파란색 동그라미:** Integer (정수)
- **연두색 동그라미:** Float (소수)
- **분홍색 동그라미:** String (문자열)
- **노란색 동그라미:** Object Reference

> **핀 연결 방법:** 출력 핀(오른쪽)에서 마우스 드래그 → 입력 핀(왼쪽)에 놓기.
> 같은 색끼리만 연결됩니다.

---

## STEP 1. MetaHuman 자식 Blueprint 만들기

### 1-1. 생성하기

```
Content Browser에서:
  Content/MetaHumans/ 폴더로 이동
  → 배치할 MetaHuman의 BP 찾기 (예: BP_metahuman_ux)
  → 우클릭 → "Create Child Blueprint Class"
  → 이름: BP_MH_UXResearcher
```

3개 생성: `BP_MH_UXResearcher`, `BP_MH_VisualDesigner`, `BP_MH_SoftwareEngineer`

### 1-2. 변수 추가하기

BP_MH_UXResearcher 더블클릭 → Blueprint 에디터.

**변수 하나 추가하는 방법:**
1. 왼쪽 **My Blueprint** 패널 → Variables 옆 **+** 클릭
2. 새 변수 이름 입력
3. 오른쪽 **Details** 패널 → **Variable Type** 클릭 → 타입 검색
4. 배열로 만들려면: 타입 옆 아이콘(단일 격자) 클릭 → **Array** 선택
5. 상단 **Compile** (초록 체크) 클릭
6. Details에서 **Default Value** 설정

아래 변수들을 전부 추가:

| 변수명 | 타입 | 기본값 | 설명 |
|--------|------|--------|------|
| BSRawData | Float (Array) | - | 블렌드셰이프 데이터 |
| BSFrameIndex | Integer | 0 | 현재 프레임 |
| BSWeightCount | Integer | 68 | weight 개수 |
| BSPlaying | Boolean | false | 재생 중? |
| BSFPS | Integer | 60 | FPS |
| BSTimer | Float | 0.0 | 타이머 |
| QueuedRawData | Float (Array) | - | 큐: 다음 문장 |
| QueuedWeightCount | Integer | 0 | 큐: weight 수 |
| QueuedFPS | Integer | 0 | 큐: FPS |
| HasQueuedData | Boolean | false | 큐에 데이터? |
| BSNames | String (Array) | 아래 참고 | morph 이름 |

### 1-3. BSNames 배열 채우기

BSNames를 선택 → Compile → Default Value 섹션에서 **+** 를 68번 클릭해서 항목 추가.

아래 순서대로 정확히 입력:

```
[0]  neutral             [1]  eyeBlinkLeft        [2]  eyeLookDownLeft
[3]  eyeLookInLeft       [4]  eyeLookOutLeft      [5]  eyeLookUpLeft
[6]  eyeSquintLeft       [7]  eyeWideLeft         [8]  eyeBlinkRight
[9]  eyeLookDownRight    [10] eyeLookInRight      [11] eyeLookOutRight
[12] eyeLookUpRight      [13] eyeSquintRight      [14] eyeWideRight
[15] jawForward          [16] jawLeft             [17] jawRight
[18] jawOpen             [19] mouthClose          [20] mouthFunnel
[21] mouthPucker         [22] mouthLeft           [23] mouthRight
[24] mouthSmileLeft      [25] mouthSmileRight     [26] mouthFrownLeft
[27] mouthFrownRight     [28] mouthDimpleLeft     [29] mouthDimpleRight
[30] mouthStretchLeft    [31] mouthStretchRight   [32] mouthRollLower
[33] mouthRollUpper      [34] mouthShrugLower     [35] mouthShrugUpper
[36] mouthPressLeft      [37] mouthPressRight     [38] mouthLowerDownLeft
[39] mouthLowerDownRight [40] mouthUpperUpLeft     [41] mouthUpperUpRight
[42] browDownLeft        [43] browDownRight       [44] browInnerUp
[45] browOuterUpLeft     [46] browOuterUpRight    [47] cheekPuff
[48] cheekSquintLeft     [49] cheekSquintRight    [50] noseSneerLeft
[51] noseSneerRight      [52] tongueOut           [53] tongueTipUp
[54] tongueTipDown       [55] tongueTipLeft       [56] tongueTipRight
[57] tongueRollUp        [58] tongueRollDown      [59] tongueRollLeft
[60] tongueRollRight     [61] tongueUp            [62] tongueDown
[63] tongueLeft          [64] tongueRight         [65] tongueIn
[66] tongueStretch       [67] tongueWide
```

### 1-4. Face 컴포넌트 찾기

왼쪽 **Components** 패널에서:
1. **(Inherited)** 를 펼침 (▶ 클릭)
2. **Face** (SkeletalMeshComponent) 찾기
3. 나중에 이걸 그래프에 드래그해서 쓸 거임

> 이름이 Face가 아닐 수 있음. "Face", "FaceMesh", "Head" 등으로 찾아보세요.

### 1-5. EnqueueBlendshapes 함수

**만들기:**
1. My Blueprint → Functions 옆 **+** 클릭
2. 이름: `EnqueueBlendshapes`
3. 함수 노드 선택 → Details → **Inputs** 에서 **+** 를 3번 클릭:
   - `InRawData` → Float Array
   - `InWeightCount` → Integer
   - `InFPS` → Integer

**노드 연결:**

```
① [EnqueueBlendshapes] 노드의 실행 핀(▶)에서 드래그
   → 빈 공간에 놓기 → "Branch" 검색 → 선택

② Branch의 Condition 핀에 BSPlaying 연결:
   → 빈 공간 우클릭 → "Get BSPlaying" 검색 → 선택
   → BSPlaying의 출력 핀 → Branch의 Condition 핀에 연결

③ Branch의 True 핀 (재생 중 → 큐에 저장):
   → True 핀에서 드래그 → "Set QueuedRawData" 검색
   → InRawData 핀에서 드래그 → Set QueuedRawData의 입력 핀에 연결
   
   → Set QueuedRawData 실행 핀에서 드래그 → "Set QueuedWeightCount" 검색
   → InWeightCount 핀에서 드래그 → Set QueuedWeightCount 입력에 연결
   
   → 이어서 "Set QueuedFPS" → InFPS 연결
   → 이어서 "Set HasQueuedData" → 체크박스 ✅ (true)

④ Branch의 False 핀 (재생 안 함 → 바로 시작):
   → False 핀에서 드래그 → "Set BSRawData" 검색
   → InRawData 핀 → Set BSRawData 입력에 연결
   
   → 이어서 "Set BSWeightCount" → InWeightCount 연결
   → 이어서 "Set BSFPS" → InFPS 연결
   → 이어서 "StartBlendshapes" 검색 → 연결
```

완성된 모습:
```
[EnqueueBlendshapes]
  |
  ▶──[Branch]──┬─ True ──▶ Set QueuedRawData ──▶ Set QueuedWeightCount ──▶ Set QueuedFPS ──▶ Set HasQueuedData(✅)
               |
               └─ False ──▶ Set BSRawData ──▶ Set BSWeightCount ──▶ Set BSFPS ──▶ StartBlendshapes
```

### 1-6. StartBlendshapes 함수

**만들기:**
1. My Blueprint → Functions 옆 **+** → 이름: `StartBlendshapes`

**노드 연결:**

```
① [StartBlendshapes] 실행 핀에서 드래그
   → "Set BSFrameIndex" 검색 → 값에 0 입력

② Set BSFrameIndex 실행 핀에서 드래그
   → "Set BSTimer" 검색 → 값에 0.0 입력

③ Set BSTimer 실행 핀에서 드래그
   → "Set BSPlaying" 검색 → 체크박스 ✅ (true)
```

완성:
```
[StartBlendshapes] ──▶ Set BSFrameIndex(0) ──▶ Set BSTimer(0.0) ──▶ Set BSPlaying(✅)
```

### 1-7. Event Tick — 메인 로직

**Event Graph** 탭 클릭 (상단).

이게 제일 큰 로직입니다. Part A~D로 나눠서 만듭니다.

---

#### Part A: BSPlaying 체크

```
① Event Tick 노드가 이미 있을 수 있음. 없으면:
   우클릭 → "Event Tick" 검색 → 선택

② Event Tick 실행 핀에서 드래그
   → "Branch" 검색 → 선택

③ Condition에 BSPlaying 연결:
   우클릭 → "Get BSPlaying" 검색 → 선택
   BSPlaying 출력 → Branch의 Condition에 연결
```

> False 핀은 비워둡니다 (아무것도 안 함).
> True 핀에서 Part B를 이어갑니다.

완성:
```
[Event Tick] ──▶ [Branch] ──┬─ True ──▶ (Part B로)
                   |         |
              Get BSPlaying  └─ False ──▶ (없음)
```

---

#### Part B: 타이머 + 프레임 타이밍

```
④ True 핀에서 드래그 → "Set BSTimer" 검색 → 선택

⑤ Set BSTimer에 넣을 값 = 현재 BSTimer + DeltaTime:
   우클릭 → "Get BSTimer" 검색 → 선택
   우클릭 → "Float + Float" 검색 → 선택 (또는 Add 검색)
   
   연결:
   - Get BSTimer 출력 → Float+Float의 A 핀
   - Event Tick의 "Delta Seconds" 핀 → Float+Float의 B 핀
   - Float+Float 출력 → Set BSTimer의 값 핀

⑥ Set BSTimer 실행 핀에서 드래그 → "Branch" 검색 (두 번째 Branch)

⑦ 두 번째 Branch의 Condition = BSTimer >= (1.0 / BSFPS):
   우클릭 → "Get BSTimer" 검색
   우클릭 → "Float >= Float" 검색
   우클릭 → "Get BSFPS" 검색
   우클릭 → "Float / Float" 검색 (또는 Divide)
   
   연결:
   - 1.0 (Float 리터럴, 우클릭 → "Make Literal Float" → 1.0) → Divide의 A 핀
   - Get BSFPS → "To Float" (Integer to Float 변환) → Divide의 B 핀
   - Get BSTimer → Float>=Float의 A 핀
   - Divide 출력 → Float>=Float의 B 핀
   - Float>=Float 출력 → Branch의 Condition
```

> **False 핀은 비워둡니다** (다음 Tick까지 대기).

```
⑧ True 핀에서 → BSTimer에서 프레임 시간 빼기:
   (BSTimer가 프레임 시간보다 크면 → 한 프레임 처리할 시간!)
   드래그 → "Set BSTimer" 검색
   
   값 = Get BSTimer - (1.0 / BSFPS):
   - "Get BSTimer" + "Float - Float" + 위에서 만든 Divide 결과 사용
   - Float-Float 출력 → Set BSTimer 값 핀
```

완성:
```
(Part A에서)
  │
  ▶──Set BSTimer(+DeltaTime) ──▶ [Branch] ──┬─ True ──▶ Set BSTimer(-1/FPS) ──▶ (Part C로)
                                    |         |
                           BSTimer >= 1/FPS   └─ False ──▶ (없음, 다음 Tick 대기)
```

---

#### Part C: 프레임 처리 (For Loop)

```
⑨ Set BSTimer 실행 핀에서 드래그 → "Branch" 검색 (세 번째 Branch)

⑩ Condition = BSFrameIndex < TotalFrames:
   TotalFrames를 먼저 계산:
   우클릭 → "Get BSRawData" → 드래그 → "Length" 검색
   우클릭 → "Get BSWeightCount"
   우클릭 → "Integer / Integer" 검색 (Divide)
   
   연결:
   - Length 출력 → Divide A 핀
   - Get BSWeightCount → Divide B 핀
   = 이게 TotalFrames
   
   우클릭 → "Get BSFrameIndex"
   우클릭 → "Integer < Integer" 검색 (Less)
   
   연결:
   - Get BSFrameIndex → Less A 핀
   - Divide 출력 → Less B 핀
   - Less 출력 → Branch Condition

⑪ True 핀 → For Loop:
   드래그 → "For Loop" 검색
   - First Index: 0 (기본값)
   - Last Index: Get BSWeightCount - 1
     → "Get BSWeightCount" → "Integer - Integer" → B에 1 → 결과를 Last Index에

⑫ Loop Body 핀 (매 반복마다 실행) → 데이터 인덱스 계산:
   
   DataIndex = BSFrameIndex × BSWeightCount + LoopIndex:
   우클릭 → "Get BSFrameIndex"
   우클릭 → "Get BSWeightCount"  
   우클릭 → "Integer × Integer" (Multiply)
   우클릭 → "Integer + Integer" (Add)
   
   연결:
   - Get BSFrameIndex → Multiply A
   - Get BSWeightCount → Multiply B
   - Multiply 결과 → Add A
   - For Loop의 "Index" 핀 → Add B
   = 이게 DataIndex

⑬ Weight 값 가져오기:
   우클릭 → "Get BSRawData"
   BSRawData 핀에서 드래그 → "Get (a copy)" 검색
   DataIndex (Add 결과) → Get의 Index 핀
   = 이게 Weight 값

⑭ Morph Target 이름 가져오기:
   우클릭 → "Get BSNames"
   BSNames 핀에서 드래그 → "Get (a copy)" 검색
   For Loop의 "Index" 핀 → Get의 Index 핀
   = 이게 MorphName

⑮ Set Morph Target:
   Loop Body 실행 핀에서 드래그 → "Set Morph Target" 검색
   
   연결:
   - Target: Components 패널에서 Face를 그래프로 드래그 → Target에 연결
   - Morph Target Name: ⑭의 MorphName 연결
   - Value: ⑬의 Weight 연결

⑯ ★중요★ For Loop의 "Completed" 핀에서 (Loop Body가 아님!):
   드래그 → "Set BSFrameIndex" 검색
   
   값 = BSFrameIndex + 1:
   "Get BSFrameIndex" → "Integer + Integer" → B에 1 → 결과를 Set에 연결
```

> **Completed vs Loop Body:**
> - **Loop Body:** 68번 반복할 때마다 실행 (morph target 설정)
> - **Completed:** 68번 다 끝난 후 1번만 실행 (프레임 인덱스 +1)
> - BSFrameIndex++를 Loop Body에 넣으면 68번 증가해서 깨집니다!

완성:
```
(Part B에서)
  │
  ▶──[Branch] ──┬─ True ──▶ [For Loop 0~67] ──┬─ Loop Body ──▶ Get BSRawData[FrameIdx*68+i]
        |        |                              |                     │
  FrameIdx <     └─ False ──▶ (Part D로)        |                     ▶──Set Morph Target(Face, BSNames[i], Weight)
  TotalFrames                                   |
                                                └─ Completed ──▶ Set BSFrameIndex(+1)
```

---

#### Part D: 재생 끝 + 큐 처리

step ⑨의 세 번째 Branch의 **False** 핀에 연결 (BSFrameIndex >= TotalFrames):

```
⑰ False 핀에서 드래그 → "Set BSPlaying" → 체크 해제 (false)
   이어서 → "Set BSFrameIndex" → 값 0

⑱ 큐 확인:
   이어서 → "Branch" 검색
   Condition: 우클릭 → "Get HasQueuedData" → Branch Condition에 연결

⑲ True 핀 (큐에 다음 문장 있음 → 바로 재생!):
   드래그 → "Set BSRawData"
   우클릭 → "Get QueuedRawData" → Set BSRawData 값에 연결
   
   이어서 → "Set BSWeightCount"
   우클릭 → "Get QueuedWeightCount" → 값에 연결
   
   이어서 → "Set BSFPS"
   우클릭 → "Get QueuedFPS" → 값에 연결
   
   이어서 → "Set HasQueuedData" → 체크 해제 (false)
   이어서 → "Set QueuedWeightCount" → 값 0
   이어서 → "Set QueuedFPS" → 값 0
   
   이어서 → "Get QueuedRawData" → 드래그 → "Clear" 검색 → 선택
   
   이어서 → "StartBlendshapes" 검색 → 연결
   (끊김 없이 다음 문장 재생!)

⑳ False 핀 (큐 비어있음 → 표정 리셋):
   드래그 → "For Loop" 검색
   - First Index: 0
   - Last Index: "Get BSNames" → "Length" → "Integer - Integer" → B에 1

   Loop Body에서:
   → "Set Morph Target" 검색
   - Target: Face 컴포넌트 (위에서와 동일)
   - Morph Target Name: "Get BSNames" → "Get (a copy)" at Loop Index
   - Value: 0.0 (Make Literal Float → 0.0)
```

완성:
```
(Part C의 Branch False에서)
  │
  ▶──Set BSPlaying(false) ──▶ Set BSFrameIndex(0) ──▶ [Branch] ──┬─ True (큐 있음)
                                                          |        |
                                                   HasQueuedData   ├──▶ Set BSRawData(=Queued)
                                                                   ├──▶ Set BSWeightCount(=Queued)
                                                                   ├──▶ Set BSFPS(=Queued)
                                                                   ├──▶ Set HasQueuedData(false)
                                                                   ├──▶ Clear QueuedRawData
                                                                   └──▶ StartBlendshapes (다음 문장!)
                                                                   
                                                          └─ False (큐 없음)
                                                                   │
                                                                   ▶──[For Loop 0~67] ──▶ Set Morph Target(Face, BSNames[i], 0.0)
                                                                   (표정 리셋)
```

### 1-8. 나머지 2개도 동일하게

BP_MH_VisualDesigner, BP_MH_SoftwareEngineer도 동일하게 만듭니다.

> 팁: BP_MH_UXResearcher의 Event Graph에서
> Ctrl+A (전체 선택) → Ctrl+C (복사)
> → 다른 BP 열어서 Ctrl+V (붙여넣기)

---

## STEP 2. BP_OSCManager 만들기

### 2-1. 생성

```
Content Browser 빈 공간 우클릭 → Blueprint Class → Actor → 이름: BP_OSCManager
```

### 2-2. OSC Server 컴포넌트 추가

```
Components 패널 → + Add → "OSC Server" 검색 → 선택
이름을 "OscServer"로 변경 (클릭해서 F2)
```

OscServer 선택 → Details:
- Server IP Address: `0.0.0.0`
- Server Port: `7400`
- Start Listening On Begin Play: ✅

### 2-3. 변수 추가

| 변수명 | 타입 | Instance Editable |
|--------|------|-------------------|
| CurrentCharID | String | |
| CurrentBSFrames | Float (Array) | |
| ExpectedFrames | Integer | |
| CurrentWeightCount | Integer | |
| CurrentFPS | Integer | |
| MH_UXResearcher | BP_MH_UXResearcher (Object Ref) | ✅ 눈 아이콘 |
| MH_VisualDesigner | BP_MH_VisualDesigner (Object Ref) | ✅ 눈 아이콘 |
| MH_SoftwareEngineer | BP_MH_SoftwareEngineer (Object Ref) | ✅ 눈 아이콘 |

> **MH_* 변수 타입 설정법:**
> Variable Type 클릭 → "BP_MH_UXResearcher" 검색 → **Object Reference** 선택
> (Class Reference 아님!)

> **Instance Editable:** 변수 이름 옆의 **눈 아이콘** 클릭 → 레벨에서 값 지정 가능

### 2-4. BeginPlay — OSC 이벤트 바인딩

```
① Event BeginPlay (이미 있을 수 있음, 없으면 우클릭 → 검색)

② Components 패널에서 OscServer를 그래프로 드래그

③ OscServer 핀에서 드래그
   → "Bind Event to On Osc Message Received" 검색 → 선택

④ Bind 노드의 빨간 Event 핀에서 드래그
   → "Add Custom Event" 선택 → 이름: OnOscMessage

⑤ 실행 핀 연결:
   Event BeginPlay ──▶ Bind Event to On Osc Message Received
```

### 2-5. OnOscMessage — 주소 분기

```
① OnOscMessage의 Message 핀에서 드래그
   → "Get OSC Message Address" 검색 → 선택

② Get OSC Message Address 출력 핀에서 드래그
   → "Get Full Path" 검색 → 선택
```

> **Get Full Path가 필요한 이유:**
> Get OSC Message Address는 FOSCAddress 구조체입니다.
> Get Full Path로 "/mh/bs_start" 같은 문자열로 변환합니다.

```
③ Get Full Path의 Return Value (String) 핀에서 드래그
   → "Switch on String" 검색 → 선택

④ Switch 노드 선택 → Details 패널에서 핀 추가:
   + 클릭 → /mh/bs_start
   + 클릭 → /mh/bs
   + 클릭 → /mh/bs_end

⑤ OnOscMessage 실행 핀에서 드래그 → Switch on String에 연결
```

### 2-6. /mh/bs_start 처리

Switch의 `/mh/bs_start` 핀에서 시작:

> **중요: OSC Get 노드에는 출력 핀이 2개 있습니다!**
> - **Return Value (bool):** 값을 찾았는지 여부 (true/false). 이걸 쓰면 안 됨!
> - **Value:** 실제 데이터. **이걸 써야 합니다!**

```
① /mh/bs_start 핀에서 드래그
   → "Get OSC Message String at Index" 검색

② Get 노드에서:
   - Message 핀: OnOscMessage의 Message 연결
   - Index: 0
   - ★ Value 핀 (String) ★ 에서 드래그 → "Set CurrentCharID" 검색 → 연결
   (Return Value가 아님!)

③ Set CurrentCharID 실행 핀에서 드래그
   → "Get OSC Message Int32 at Index" 검색
   - Message: OnOscMessage의 Message
   - Index: 1
   - ★ Value 핀 (Integer) ★ → "Set ExpectedFrames" 연결

④ 이어서 → "Get OSC Message Int32 at Index"
   - Index: 2
   - ★ Value ★ → "Set CurrentWeightCount"

⑤ 이어서 → "Get OSC Message Int32 at Index"
   - Index: 3
   - ★ Value ★ → "Set CurrentFPS"

⑥ 이어서 → "Get CurrentBSFrames" → 드래그 → "Clear" 검색 → 선택
```

완성:
```
/mh/bs_start ──▶ Get String[0]→Set CharID ──▶ Get Int[1]→Set Frames ──▶ Get Int[2]→Set WeightCount ──▶ Get Int[3]→Set FPS ──▶ Clear BSFrames
```

### 2-7. /mh/bs 처리

Switch의 `/mh/bs` 핀에서 시작:

```
① /mh/bs 핀에서 드래그 → "For Loop" 검색
   - First Index: 0
   - Last Index: "Get CurrentWeightCount" → "Integer - Integer" → B에 1

② Loop Body에서:
   → "Get OSC Message Float at Index" 검색
   - Message: OnOscMessage의 Message
   - Index: For Loop의 "Index" 핀 + 2
     ("Integer + Integer" → A에 Loop Index, B에 2)
   - ★ Value 핀 (Float) ★ 에서 드래그

③ "Get CurrentBSFrames" → 드래그 → "Add" 검색
   → ② 의 Float Value → Add의 Element 핀에 연결
```

완성:
```
/mh/bs ──▶ For Loop (0 ~ WeightCount-1)
             │
             Loop Body: Get Float[LoopIndex+2] → CurrentBSFrames.Add
```

### 2-8. /mh/bs_end 처리

Switch의 `/mh/bs_end` 핀에서 시작:

```
① /mh/bs_end 핀에서 드래그
   → "Get OSC Message String at Index" 검색
   - Index: 0
   - ★ Value ★ 가져옴 = CharID

② Value (String) 핀에서 드래그
   → "Switch on String" 검색
   + 클릭 → MH_UXResearcher
   + 클릭 → MH_VisualDesigner
   + 클릭 → MH_SoftwareEngineer

③ MH_UXResearcher 핀에서:
   우클릭 → "Get MH_UXResearcher" 검색
   핀에서 드래그 → "EnqueueBlendshapes" 검색
   
   연결:
   - InRawData: 우클릭 → "Get CurrentBSFrames" → InRawData에 연결
   - InWeightCount: 우클릭 → "Get CurrentWeightCount" → 연결
   - InFPS: 우클릭 → "Get CurrentFPS" → 연결

④ MH_VisualDesigner, MH_SoftwareEngineer도 동일하게
   (각각 Get MH_VisualDesigner, Get MH_SoftwareEngineer 사용)
```

완성:
```
/mh/bs_end ──▶ Get String[0] ──▶ Switch on String
                                   ├─ MH_UXResearcher ──▶ Get MH_UXR → EnqueueBlendshapes(BSFrames, WeightCount, FPS)
                                   ├─ MH_VisualDesigner ──▶ Get MH_VD → EnqueueBlendshapes(...)
                                   └─ MH_SoftwareEngineer ──▶ Get MH_SE → EnqueueBlendshapes(...)
```

---

## STEP 3. 레벨 배치 + 연결

### 3-1. 배치

Content Browser에서 레벨로 드래그:
1. `BP_MH_UXResearcher` (원본 MetaHuman이 아닌 **자식 BP!**)
2. `BP_MH_VisualDesigner`
3. `BP_MH_SoftwareEngineer`
4. `BP_OSCManager`

### 3-2. OSCManager에 MetaHuman 연결

1. 레벨에서 `BP_OSCManager` 클릭
2. Details 패널 스크롤 → **Default** 섹션
3. `MH_UXResearcher` 드롭다운 → 레벨의 `BP_MH_UXResearcher` 선택
4. `MH_VisualDesigner` → 레벨의 `BP_MH_VisualDesigner` 선택
5. `MH_SoftwareEngineer` → 레벨의 `BP_MH_SoftwareEngineer` 선택

---

## STEP 4. 테스트

1. UE5 에디터 상단 **▶ Play** (또는 Alt+P)
2. 노트북 터미널에서:
```bash
python test_osc.py
```
3. MetaHuman 입이 움직이면 **성공!**

---

## 트러블슈팅

| 증상 | 원인 | 해결 |
|------|------|------|
| OSC 수신 안 됨 | 방화벽 | Windows 방화벽 → 인바운드 규칙 → UDP 7400 허용 |
| 입이 안 움직임 | Value 핀 잘못 연결 | OSC Get 노드에서 **Value** 핀 사용 (Return Value 아님) |
| 입이 안 움직임 | Face 컴포넌트 미연결 | Set Morph Target의 Target에 Face 연결 확인 |
| 입이 안 움직임 | BSNames 비어있음 | 68개 이름이 전부 입력됐는지 확인 |
| Cast 실패 | 원본 BP 배치 | 자식 BP (BP_MH_*) 를 배치해야 함 |
| 마지막 표정 얼어있음 | 리셋 누락 | Part D step ⑳ 확인 |
| OSC 노드 안 보임 | 플러그인 꺼져있음 | Edit → Plugins → OSC 활성화 → 재시작 |

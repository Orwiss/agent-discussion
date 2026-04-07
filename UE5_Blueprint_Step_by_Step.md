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

## 전체 흐름

```
노트북 (Python)
  → OSC /mh/bs_start [캐릭터ID, 프레임수, weight수, fps]
  → OSC /mh/bs [캐릭터ID, 프레임인덱스, w0, w1, ..., w67]  ×N
  → OSC /mh/bs_end [캐릭터ID]

데스크탑 (UE5.6)
  → BP_OSCManager가 OSC 수신
  → 블렌드셰이프: 프레임 버퍼에 저장 → Tick에서 Set Morph Target
  → 오디오: 노트북에서 직접 스피커로 재생 (UE5에서 처리 안 함)
```

---

## 만들 것 요약

```
[BP_MH_BlendshapePlayer] ← ActorComponent (★ 1번만 만듦 ★)
  - 변수, 함수, Event Tick 로직 전부 여기

BP_MH_UXResearcher      → + Add Component → BP_MH_BlendshapePlayer
BP_MH_VisualDesigner    → + Add Component → BP_MH_BlendshapePlayer
BP_MH_SoftwareEngineer  → + Add Component → BP_MH_BlendshapePlayer

[BP_OSCManager] ← Actor
  - OSC 수신 → 캐릭터별 분기 → 컴포넌트의 EnqueueBlendshapes 호출
```

---

## STEP 0. 프로젝트 세팅

1. Epic Games Launcher → UE **5.6** 설치
2. New Project → Games → Blank → Blueprint → 이름: `AgentMH`
3. Edit → Plugins → `OSC` 검색 → 체크 ✅ → 재시작
4. MetaHuman Creator에서 캐릭터 3개 Export (이 프로젝트로)

> 필요한 플러그인은 **OSC 하나**뿐입니다.

---

## STEP 1. BP_MH_BlendshapePlayer (ActorComponent)

> 변수/함수/로직을 **1번만** 만들면 3개 MetaHuman에서 공유됩니다.

### 1-1. 생성

```
Content Browser 빈 공간 우클릭
  → Blueprint Class
  → "Actor Component" 검색 → 선택
  → 이름: BP_MH_BlendshapePlayer
```

### 1-2. Tick 활성화 (중요!)

ActorComponent는 기본적으로 Tick이 꺼져있습니다.

```
BP_MH_BlendshapePlayer 더블클릭 → 에디터 열림
  → 상단 툴바에서 "Class Defaults" 클릭
  → Details 패널 → Component Tick 섹션:
     ✅ Can Ever Tick (체크)
     ✅ Start With Tick Enabled (체크)
```

> 이거 안 하면 Event Tick이 절대 실행 안 됩니다! 에러도 안 나서 찾기 어렵습니다.

### 1-3. 변수 추가

| 변수명 | 타입 | 기본값 | 설명 |
|--------|------|--------|------|
| FaceMesh | Skeletal Mesh Component (Object Ref) | - | BeginPlay에서 자동 설정 |
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
| BSNames | String (Array) | 아래 참고 | morph 이름 68개 |

**변수 추가 방법:**
1. 왼쪽 My Blueprint → Variables 옆 **+** 클릭
2. 이름 입력
3. Details → Variable Type 클릭 → 타입 검색
4. 배열: 타입 옆 아이콘 클릭 → **Array** 선택
5. Compile → Default Value 설정

### 1-4. BSNames 배열 채우기 (Data Table로 간편하게)

68개를 하나하나 타이핑하면 힘드니까 **CSV 파일**을 Import해서 씁니다.

#### 방법 1: Data Table Import (추천)

**1단계: 구조체 만들기**

```
Content Browser 우클릭
  → Blueprint → Structure → 이름: S_BSName
  → 더블클릭으로 열기
  → + Add Variable:
     - Name: MorphName, 타입: String
  → Save
```

> 필드가 **MorphName** 하나뿐입니다. CSV 컬럼명과 정확히 일치해야 합니다.

**2단계: CSV Import**

레포에 `bs_names.csv` 파일이 있습니다 (68개 이름 전부 들어있음).

CSV 형식:
```
---,MorphName        ← 첫 컬럼 "---"은 Row Name (인덱스), 두 번째가 구조체 필드
0,neutral
1,eyeBlinkLeft
...
67,tongueWide
```

```
Content Browser 빈 공간 우클릭 → Import
  → bs_names.csv 선택
  → "DataTable" 옵션 선택
  → Row Type: S_BSName
  → Import → 이름: DT_BSNames
```

> Import 후 DT_BSNames를 더블클릭하면 68개 Row가 보여야 합니다.
> Row 0 = neutral, Row 1 = eyeBlinkLeft, ... Row 67 = tongueWide

**3단계: BeginPlay에서 Data Table → BSNames 배열로 변환**

1-5의 BeginPlay 로직 **앞에** 추가:

```
① 우클릭 → "Get Data Table Row Names" 검색
   - Data Table: DT_BSNames (Content Browser에서 드래그)
   - 출력: Row Names 배열 (FName Array) → "0", "1", ... "67"

② Row Names로 순회하면서 각 Row의 MorphName 가져오기:
   → "For Each Loop" 검색
   → Loop Body에서:

     "Get Data Table Row" 검색
     - Data Table: DT_BSNames
     - Row Name: Array Element (현재 Row Name)
     - Out Row 핀에서 드래그 → "Break S_BSName" 검색
     - MorphName 핀에서 드래그 → "Get BSNames" → "Add" 연결
```

완성:
```
[BeginPlay] ──▶ Get Data Table Row Names(DT_BSNames)
                  │
                  ▶──For Each Loop
                       │
                       Loop Body: Get Data Table Row → Break S_BSName → MorphName → BSNames.Add
                  │
                  ▶──Completed ──▶ (1-5의 Face 찾기 로직으로 이어짐)
```

> 이렇게 하면 BSNames 배열이 자동으로 68개 이름으로 채워집니다.
> 수동 입력 없이 CSV에서 한 방에!

#### 방법 2: 수동 입력 (Data Table 안 쓸 경우)

BSNames 선택 → Compile → Default Value에서 **+** 를 68번 클릭.
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

### 1-5. BeginPlay — Face 메시 자동 찾기

컴포넌트가 붙은 MetaHuman에서 Face 메시를 자동으로 찾습니다.

```
① 우클릭 → "Event BeginPlay" 검색 → 선택

② 실행 핀에서 드래그 → "Get Owner" 검색 → 선택
   (이 컴포넌트가 붙어있는 Actor를 가져옴)

③ Get Owner 출력 핀에서 드래그
   → "Get Components by Class" 검색 → 선택
   → Component Class: Skeletal Mesh Component

④ 리턴 배열에서 Face 찾기:
   Get Components by Class 출력(Array)에서 드래그
   → "For Each Loop" 검색

⑤ Loop Body에서:
   Array Element 핀에서 **드래그해서 빈 공간에 놓기**
   → 검색창에 "Display Name" 입력 → "Get Display Name" 선택
   (핀에서 드래그하면 SkeletalMeshComponent 함수만 필터링돼서 나옴!)
   (빈 공간 우클릭으로 검색하면 동명 노드가 많아서 헷갈림)

⑥ 이름에 "Face" 포함되는지 확인:
   Get Display Name 출력에서 드래그
   → "Contains" 검색
   → Substring: "face" (소문자)
   → ✅ Search Case: Ignore Case (Use Search Case를 false)

⑦ Contains 결과 → Branch
   → True 핀에서 드래그 → "Set FaceMesh" 검색
   → Array Element (SkeletalMeshComponent) → FaceMesh에 연결
```

완성:
```
[BeginPlay] ──▶ Get Owner ──▶ Get Components by Class(SkeletalMesh)
                                │
                                ▶──For Each Loop
                                     │
                                     Loop Body: Get Display Name → Contains("face")
                                                              │
                                                         [Branch] ── True ──▶ Set FaceMesh
```

> 이렇게 하면 어떤 MetaHuman에 붙여도 Face 메시를 자동으로 찾습니다.
> MetaHuman마다 Face 컴포넌트 이름이 다를 수 있어서 "Contains" 로 찾는 겁니다.

### 1-6. EnqueueBlendshapes 함수

```
My Blueprint → Functions 옆 + → 이름: EnqueueBlendshapes
함수 노드 선택 → Details → Inputs에서 + 3번:
  - InRawData : Float Array
  - InWeightCount : Integer
  - InFPS : Integer
```

```
① [EnqueueBlendshapes] 실행 핀 → "Branch" 검색
   Condition: "Get BSPlaying" 연결

② True 핀 (재생 중 → 큐에 저장):
   → "Set QueuedRawData" → InRawData 연결
   → "Set QueuedWeightCount" → InWeightCount 연결
   → "Set QueuedFPS" → InFPS 연결
   → "Set HasQueuedData" → ✅ true

③ False 핀 (재생 안 함 → 바로 시작):
   → "Set BSRawData" → InRawData 연결
   → "Set BSWeightCount" → InWeightCount 연결
   → "Set BSFPS" → InFPS 연결
   → "StartBlendshapes" 연결
```

완성:
```
[EnqueueBlendshapes]
  │
  ▶──[Branch]──┬─ True ──▶ Set QueuedRawData ──▶ Set QueuedWeightCount ──▶ Set QueuedFPS ──▶ Set HasQueuedData(✅)
               │
               └─ False ──▶ Set BSRawData ──▶ Set BSWeightCount ──▶ Set BSFPS ──▶ StartBlendshapes
```

### 1-7. StartBlendshapes 함수

```
My Blueprint → Functions 옆 + → 이름: StartBlendshapes
```

```
① [StartBlendshapes] ──▶ "Set BSFrameIndex" (값: 0)
                      ──▶ "Set BSTimer" (값: 0.0)
                      ──▶ "Set BSPlaying" (✅ true)
```

완성:
```
[StartBlendshapes] ──▶ Set BSFrameIndex(0) ──▶ Set BSTimer(0.0) ──▶ Set BSPlaying(✅)
```

### 1-8. Event Tick — Part A: BSPlaying 체크

```
① 우클릭 → "Event Tick" 검색 → 선택

② 실행 핀 → "Branch" 검색
   Condition: "Get BSPlaying" 연결

   False 핀은 비워둡니다.
```

완성:
```
[Event Tick] ──▶ [Branch] ──┬─ True ──▶ (Part B로)
                   │         │
              Get BSPlaying  └─ False ──▶ (없음)
```

### 1-9. Event Tick — Part B: 타이머 누적

> **목표:** 매 Tick마다 BSTimer에 경과 시간(Delta Seconds)을 더한다.
> 그래서 BSTimer가 일정 값 이상이 되면 다음 블렌드셰이프 프레임으로 넘긴다.

**③ BSTimer에 경과 시간 더하기 (BSTimer = BSTimer + DeltaSeconds)**

```
③-1. 우클릭 → "Float + Float" 검색 → 선택 (Add 노드)

③-2. Add 노드의 왼쪽 위 입력 핀:
     My Blueprint에서 "BSTimer" 변수를 그래프로 드래그 (Get BSTimer)
     → Get BSTimer 출력 핀을 Add 노드 왼쪽 위 입력에 연결

③-3. Add 노드의 왼쪽 아래 입력 핀:
     Event Tick 노드를 보면 "Delta Seconds" 핀이 있음 (연두색, Float)
     → Delta Seconds 핀을 Add 노드 왼쪽 아래 입력에 연결

③-4. Part A의 Branch True 핀에서 드래그 → "Set BSTimer" 검색
     → Set BSTimer의 값 핀에 Add 노드의 출력(오른쪽) 연결
```

연결 모습:
```
[Get BSTimer] ──────┐
                    ├──▶ [Float + Float] ──▶ [Set BSTimer] 의 값 핀
[Delta Seconds] ────┘
                         (BSTimer + DeltaSeconds 결과가 BSTimer에 저장됨)
```

**④ BSTimer가 "1프레임 분량 시간" 이상인지 비교**

> 1프레임 분량 시간 = 1.0 ÷ FPS. 예: 60fps면 1/60 = 0.0167초.
> BSTimer가 이 값 이상이면 → 다음 프레임으로 넘긴다.

```
④-1. 우클릭 → "Make Literal Float" 검색 → 선택
     → Value에 1.0 입력

④-2. BSFPS는 Integer라서 Float로 변환해야 함:
     My Blueprint에서 "BSFPS" 변수를 그래프로 드래그 (Get BSFPS)
     → Get BSFPS 출력 핀에서 드래그 → "To Float (Integer)" 검색 → 선택

④-3. 우클릭 → "Float / Float" 검색 → 선택 (나누기 노드)
     → 왼쪽 위 입력: Make Literal Float(1.0)의 출력 연결
     → 왼쪽 아래 입력: To Float의 출력 연결
     (이제 이 노드의 출력 = 1.0 / FPS)

④-4. 우클릭 → "Float >= Float" 검색 → 선택 (비교 노드)
     → 왼쪽 위 입력: Get BSTimer 출력 연결 (③에서 만든 것 재사용 또는 새로 드래그)
     → 왼쪽 아래 입력: Float / Float(1.0/FPS) 출력 연결

④-5. Set BSTimer 실행 핀에서 드래그 → "Branch" 검색
     → Condition 핀: Float >= Float의 출력 (초록색) 연결

     False 핀은 비워둡니다 (다음 Tick 대기).
```

**⑤ True면 BSTimer에서 1프레임 분량 시간을 빼기**

> 왜 0으로 리셋이 아니라 빼기? 남은 시간을 보존해서 정확한 타이밍을 유지하기 위함.

```
⑤-1. 우클릭 → "Float - Float" 검색 → 선택 (빼기 노드)
     → 왼쪽 위 입력: Get BSTimer 출력 연결
     → 왼쪽 아래 입력: ④-3에서 만든 Float / Float(1.0/FPS) 출력 연결

⑤-2. Branch의 True 핀에서 드래그 → "Set BSTimer" 검색
     → 값 핀에 Float - Float 출력 연결
```

완성:
```
(Part A True에서)
  │
  ▶──[Set BSTimer = BSTimer + DeltaSeconds]
       │
       ▶──[Branch: BSTimer >= 1.0/FPS ?]
            │
            ├─ True ──▶ [Set BSTimer = BSTimer - 1.0/FPS] ──▶ (Part C로)
            │
            └─ False ──▶ (이번 Tick 끝. 다음 Tick 대기)
```

### 1-10. Event Tick — Part C: 프레임 처리

> **목표:** 현재 프레임의 68개 블렌드셰이프 값을 MetaHuman Face에 적용하고, 다음 프레임으로 넘기기.

**⑥ 아직 재생할 프레임이 남았는지 확인**

> BSRawData는 모든 프레임의 값이 1차원 배열로 쭉 들어있음.
> 예: 10프레임 × 68개 weight = 680개 float.
> 총 프레임 수 = BSRawData 길이 ÷ BSWeightCount. (이건 변수가 아니라 그 자리에서 계산하는 값!)

```
⑥-1. My Blueprint에서 "BSRawData"를 그래프로 드래그 (Get BSRawData)
     → 출력 핀에서 드래그 → "Length" 검색 → 선택
     (BSRawData 배열의 전체 길이를 반환함)

⑥-2. 우클릭 → "Integer / Integer" 검색 → 선택 (나누기 노드)
     → 왼쪽 위 입력: Length 출력 연결
     → 왼쪽 아래 입력: Get BSWeightCount (My Blueprint에서 드래그) 연결
     (이 노드의 출력 = 총 프레임 수)

⑥-3. 우클릭 → "Integer < Integer" 검색 → 선택 (비교 노드)
     → 왼쪽 위 입력: Get BSFrameIndex (My Blueprint에서 드래그) 연결
     → 왼쪽 아래 입력: ⑥-2의 Integer / Integer 출력 연결
     (BSFrameIndex < 총 프레임 수 인가?)

⑥-4. ⑤-2의 Set BSTimer 실행 핀에서 드래그 → "Branch" 검색
     → Condition 핀: Integer < Integer 출력 연결
```

**⑦ True 핀 → 68개 weight를 순회 (For Loop)**

```
⑦. Branch True 핀에서 드래그 → "For Loop" 검색 → 선택
   - First Index: 0
   - Last Index 핀에:
     Get BSWeightCount 드래그 → 출력 핀에서 드래그
     → "Integer - Integer" 검색 → 선택
     → 왼쪽 위: Get BSWeightCount, 왼쪽 아래: 직접 1 입력
     → Integer - Integer 출력을 Last Index에 연결
     (0부터 67까지 = 68번 반복)
```

**⑧ Loop Body에서: 데이터 꺼내서 Morph Target 적용**

> BSRawData에서 현재 프레임의 weight를 꺼내려면 인덱스 계산이 필요:
> 데이터 인덱스 = BSFrameIndex × BSWeightCount + Loop의 현재 Index
> 예: 3번째 프레임(인덱스 2)의 5번째 weight = 2 × 68 + 4 = 140번째

```
⑧-1. 데이터 인덱스 계산:
     우클릭 → "Integer × Integer" 검색 → 선택 (곱하기 노드)
     → 왼쪽 위 입력: Get BSFrameIndex (My Blueprint에서 드래그)
     → 왼쪽 아래 입력: Get BSWeightCount (My Blueprint에서 드래그)

     우클릭 → "Integer + Integer" 검색 → 선택 (더하기 노드)
     → 왼쪽 위 입력: Integer × Integer 출력 연결
     → 왼쪽 아래 입력: For Loop의 "Index" 핀 (파란색) 연결
     (이 더하기 노드의 출력 = 데이터 인덱스)

⑧-2. BSRawData에서 weight 값 꺼내기:
     Get BSRawData 핀에서 드래그 → "Get (a copy)" 검색 → 선택
     → Index 핀: ⑧-1의 Integer + Integer 출력 연결
     (이 노드의 출력 = 현재 weight 값, Float)

⑧-3. BSNames에서 morph target 이름 꺼내기:
     My Blueprint에서 "BSNames" 드래그 (Get BSNames)
     → 출력 핀에서 드래그 → "Get (a copy)" 검색 → 선택
     → Index 핀: For Loop의 "Index" 핀 연결
     (이 노드의 출력 = morph target 이름, String)

⑧-4. Morph Target 적용:
     우클릭 → "Set Morph Target" 검색 → 선택
     → Target 핀: Get FaceMesh (My Blueprint에서 드래그) 연결
     → Morph Target Name 핀: ⑧-3의 Get (a copy) 출력 연결
     → Value 핀: ⑧-2의 Get (a copy) 출력 연결

     For Loop의 Loop Body 실행 핀 → Set Morph Target 실행 핀 연결
```

**⑨ Completed 핀에서: 프레임 인덱스 +1**

> **★ 중요: Completed 핀은 Loop Body가 아닙니다! ★**
> - **Loop Body:** 68번 반복됨 (매번 morph target 하나 설정)
> - **Completed:** 68번 전부 끝난 후 **1번만** 실행됨
> - Completed가 아닌 Loop Body에 연결하면 프레임 인덱스가 68번 증가해서 깨집니다!

```
⑨-1. 우클릭 → "Integer + Integer" 검색 → 선택
     → 왼쪽 위 입력: Get BSFrameIndex 연결
     → 왼쪽 아래 입력: 직접 1 입력

⑨-2. For Loop의 "Completed" 핀 (Loop Body 아래에 있음!)에서 드래그
     → "Set BSFrameIndex" 검색 → 선택
     → 값 핀에 Integer + Integer 출력 연결
```

완성:
```
(Part B에서)
  │
  ▶──[Branch: FrameIndex < RawData길이÷WeightCount ?]
        │
        ├─ True ──▶ [For Loop: 0 ~ WeightCount-1]
        │              │
        │              ├─ Loop Body (68번 반복):
        │              │     데이터인덱스 = FrameIndex × WeightCount + i
        │              │     weight = BSRawData[데이터인덱스]
        │              │     이름 = BSNames[i]
        │              │     → Set Morph Target(FaceMesh, 이름, weight)
        │              │
        │              └─ Completed (1번):
        │                    → Set BSFrameIndex = FrameIndex + 1
        │
        └─ False ──▶ (Part D로: 재생 끝 처리)
```

### 1-11. Event Tick — Part D: 재생 끝 + 큐 확인

> ⑥의 Branch **False** = 모든 프레임 재생 완료.
> 큐에 다음 문장이 있으면 바로 이어서 재생, 없으면 표정 리셋.

**⑩ 재생 중지 + 인덱스 초기화**

```
⑩-1. ⑥의 Branch False 핀에서 드래그 → "Set BSPlaying" 검색
     → 값: false (체크 해제)

⑩-2. Set BSPlaying 실행 핀에서 드래그 → "Set BSFrameIndex" 검색
     → 값: 0
```

**⑪ 큐에 다음 문장이 있는지 확인**

```
⑪. Set BSFrameIndex 실행 핀에서 드래그 → "Branch" 검색
   → Condition 핀: Get HasQueuedData (My Blueprint에서 드래그) 연결
```

**⑫ True 핀: 큐에 다음 문장 있음 → 바로 이어서 재생!**

```
⑫-1. Branch True 핀에서 드래그 → "Set BSRawData" 검색
     → 값 핀: Get QueuedRawData 연결

⑫-2. → "Set BSWeightCount"
     → 값 핀: Get QueuedWeightCount 연결

⑫-3. → "Set BSFPS"
     → 값 핀: Get QueuedFPS 연결

⑫-4. → "Set HasQueuedData" → false (체크 해제)

⑫-5. → "Set QueuedWeightCount" → 값: 0

⑫-6. → "Set QueuedFPS" → 값: 0

⑫-7. → Get QueuedRawData → 출력 핀에서 드래그 → "Clear" 검색 → 선택
     (큐 배열 비우기)

⑫-8. → "StartBlendshapes" 연결 (다음 문장 재생 시작!)
```

**⑬ False 핀: 큐 비어있음 → 표정을 기본값(0)으로 리셋**

```
⑬-1. Branch False 핀에서 드래그 → "For Loop" 검색
     - First Index: 0
     - Last Index 핀에:
       Get BSNames → 출력 핀에서 드래그 → "Length" 검색 → 선택
       → Length 출력에서 드래그 → "Integer - Integer" → 왼쪽 아래에 1 입력
       → Integer - Integer 출력을 Last Index에 연결
       (0부터 67까지 순회)

⑬-2. Loop Body:
     Get BSNames → 출력 핀에서 드래그 → "Get (a copy)" 검색
     → Index 핀: For Loop의 Index 핀 연결

     우클릭 → "Set Morph Target" 검색
     → Target: Get FaceMesh 연결
     → Morph Target Name: Get (a copy) 출력 연결
     → Value: 0.0 (직접 입력)

     For Loop의 Loop Body 실행 핀 → Set Morph Target 실행 핀 연결
```

완성:
```
(Part C의 Branch False에서)
  │
  ▶──Set BSPlaying(false) ──▶ Set BSFrameIndex(0)
       │
       ▶──[Branch: HasQueuedData?]
            │
            ├─ True (큐 있음):
            │    Set BSRawData = QueuedRawData
            │    Set BSWeightCount = QueuedWeightCount
            │    Set BSFPS = QueuedFPS
            │    Set HasQueuedData = false
            │    Clear QueuedRawData
            │    → StartBlendshapes (다음 문장!)
            │
            └─ False (큐 없음):
                 [For Loop 0 ~ BSNames길이-1]
                   → Set Morph Target(FaceMesh, BSNames[i], 0.0)
                 (모든 표정을 기본값으로 리셋)
```

### 1-12. 컴포넌트 완성! Compile + Save

상단 **Compile** (초록 체크) → **Save** 클릭.
BP_MH_BlendshapePlayer 완성!

---

## STEP 2. MetaHuman 자식 BP에 컴포넌트 붙이기

### 2-1. 자식 BP 생성 (3개)

```
Content/MetaHumans/ 에서:
  → 첫 번째 MetaHuman BP 우클릭 → "Create Child Blueprint Class"
  → 이름: BP_MH_UXResearcher

  → 두 번째 MetaHuman BP 우클릭 → "Create Child Blueprint Class"
  → 이름: BP_MH_VisualDesigner

  → 세 번째 MetaHuman BP 우클릭 → "Create Child Blueprint Class"
  → 이름: BP_MH_SoftwareEngineer
```

### 2-2. 컴포넌트 추가 (각각에)

각 자식 BP를 더블클릭해서 열고:

```
Components 패널 → + Add 클릭
  → "BP_MH_BlendshapePlayer" 검색
  → 선택 → 추가됨!
```

**이게 끝입니다.** 변수/함수/로직은 컴포넌트에 다 있으므로 여기서 할 건 없습니다.

3개 전부 동일하게: + Add → BP_MH_BlendshapePlayer

> 각 MetaHuman은 외모가 다르지만, 블렌드셰이프 재생 로직은 동일합니다.

---

## STEP 3. BP_OSCManager 만들기

### 3-1. 생성

```
Content Browser 빈 공간 우클릭 → Blueprint Class → Actor → 이름: BP_OSCManager
```

### 3-2. OSC Server 컴포넌트 추가

```
Components 패널 → + Add → "OSC Server" 검색 → 선택
이름을 "OscServer"로 변경 (F2)
```

OscServer 선택 → Details:
- Server IP Address: `0.0.0.0`
- Server Port: `7400`
- Start Listening On Begin Play: ✅

### 3-3. 변수 추가

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

> **MH_* 변수 타입:** "BP_MH_UXResearcher" 검색 → **Object Reference** 선택
> **Instance Editable:** 변수 옆 **눈 아이콘** 클릭

### 3-4. BeginPlay — OSC 이벤트 바인딩

```
① Event BeginPlay

② Components 패널에서 OscServer를 그래프로 드래그

③ OscServer 핀에서 드래그
   → "Bind Event to On Osc Message Received" 검색

④ 빨간 Event 핀에서 드래그
   → "Add Custom Event" → 이름: OnOscMessage

⑤ 실행 핀 연결:
   Event BeginPlay ──▶ Bind Event to On Osc Message Received
```

### 3-5. OnOscMessage — 주소 분기

```
① OnOscMessage의 Message 핀에서 드래그
   → "Get OSC Message Address" 검색

② Get OSC Message Address 출력 핀에서 드래그
   → "Get Full Path" 검색
   (FOSCAddress 구조체를 "/mh/bs_start" 같은 문자열로 변환)

③ Get Full Path의 Return Value (String) 핀에서 드래그
   → "Switch on String" 검색
   + 클릭 → /mh/bs_start
   + 클릭 → /mh/bs
   + 클릭 → /mh/bs_end

④ OnOscMessage 실행 핀 → Switch on String 연결
```

### 3-6. /mh/bs_start 처리

> **중요: OSC Get 노드에는 출력 핀이 2개!**
> - **Return Value (bool):** 성공 여부. 이걸 쓰면 안 됨!
> - **Value:** 실제 데이터. **이걸 써야 합니다!**

```
① /mh/bs_start 핀에서 드래그
   → "Get OSC Message String at Index" (Index: 0)
   → ★ Value ★ → "Set CurrentCharID"

② → "Get OSC Message Integer at Index" (Index: 1)
   → ★ Value ★ → "Set ExpectedFrames"

③ → "Get OSC Message Integer at Index" (Index: 2)
   → ★ Value ★ → "Set CurrentWeightCount"

④ → "Get OSC Message Integer at Index" (Index: 3)
   → ★ Value ★ → "Set CurrentFPS"

⑤ → "Get CurrentBSFrames" → "Clear"
```

완성:
```
/mh/bs_start ──▶ Get String[0]→CharID ──▶ Get Int[1]→Frames ──▶ Get Int[2]→WeightCount ──▶ Get Int[3]→FPS ──▶ Clear BSFrames
```

### 3-7. /mh/bs 처리

```
① /mh/bs 핀 → "For Loop"
   - First Index: 0
   - Last Index: "Get CurrentWeightCount" - 1

② Loop Body:
   → "Get OSC Message Float at Index"
   - Index: Loop Index + 2 ("Integer + Integer")
   - ★ Value ★ 에서 드래그

③ → "Get CurrentBSFrames" → "Add" → Float Value 연결
```

완성:
```
/mh/bs ──▶ For Loop (0 ~ WeightCount-1)
             │
             Loop Body: Get Float[LoopIndex+2] → CurrentBSFrames.Add
```

### 3-8. /mh/bs_end 처리

여기서 MetaHuman의 **컴포넌트**에 있는 EnqueueBlendshapes를 호출합니다.

```
① /mh/bs_end 핀 → "Get OSC Message String at Index" (Index: 0)
   → ★ Value ★ = CharID

② CharID 핀에서 드래그 → "Switch on String"
   + MH_UXResearcher
   + MH_VisualDesigner
   + MH_SoftwareEngineer

③ MH_UXResearcher 핀에서:
   우클릭 → "Get MH_UXResearcher" → 핀에서 드래그
   → "Get Component by Class" 검색
   → Component Class: BP_MH_BlendshapePlayer

④ Get Component by Class 출력에서 드래그
   → "EnqueueBlendshapes" 검색
   - InRawData: "Get CurrentBSFrames"
   - InWeightCount: "Get CurrentWeightCount"
   - InFPS: "Get CurrentFPS"

⑤ MH_VisualDesigner, MH_SoftwareEngineer도 동일하게
```

완성:
```
/mh/bs_end ──▶ Get String[0] ──▶ Switch on String
                ├─ MH_UXResearcher ──▶ Get MH_UXR → Get Component(BlendshapePlayer) → EnqueueBlendshapes(BSFrames, WeightCount, FPS)
                ├─ MH_VisualDesigner ──▶ Get MH_VD → Get Component(BlendshapePlayer) → EnqueueBlendshapes(...)
                └─ MH_SoftwareEngineer ──▶ Get MH_SE → Get Component(BlendshapePlayer) → EnqueueBlendshapes(...)
```

---

## STEP 4. 레벨 배치 + 연결

### 4-1. 배치

Content Browser에서 레벨로 드래그:
1. `BP_MH_UXResearcher` (원본이 아닌 **자식 BP!**)
2. `BP_MH_VisualDesigner`
3. `BP_MH_SoftwareEngineer`
4. `BP_OSCManager`

### 4-2. OSCManager에 MetaHuman 연결

```
1. 레벨에서 BP_OSCManager 클릭
2. Details 패널 스크롤
3. MH_UXResearcher → 드롭다운 → 레벨의 BP_MH_UXResearcher 선택
4. MH_VisualDesigner → BP_MH_VisualDesigner 선택
5. MH_SoftwareEngineer → BP_MH_SoftwareEngineer 선택
```

---

## STEP 5. 테스트

1. UE5 에디터 **▶ Play** (Alt+P)
2. 노트북 터미널:
```bash
python test_osc.py
```
3. MetaHuman 입이 움직이면 **성공!**

---

## 오디오 재생

UE5 안에서 런타임 PCM 재생은 복잡하므로, **노트북에서 직접 스피커로 재생**합니다.

`.env`에 추가:
```
PLAY_AUDIO_LOCAL=true
```

tts_pipeline.py에서 sounddevice로 재생하면 UE5에서 오디오 처리할 필요 없음.
MetaHuman은 립싱크(블렌드셰이프)만 담당.

> VR에서 공간 오디오가 필요해지면 그때 UE5 오디오 재생을 추가합니다.

---

## 트러블슈팅

| 증상 | 원인 | 해결 |
|------|------|------|
| OSC 수신 안 됨 | 방화벽 | Windows 방화벽 → 인바운드 → UDP 7400 허용 |
| 입이 안 움직임 | Tick 꺼져있음 | BP_MH_BlendshapePlayer의 Class Defaults → Can Ever Tick ✅ |
| 입이 안 움직임 | Value 핀 잘못 연결 | OSC Get 노드에서 **Value** 핀 사용 (Return Value 아님) |
| 입이 안 움직임 | FaceMesh 못 찾음 | BeginPlay에서 "face" Contains 체크 확인. Print String으로 Get Display Name 출력 |
| 입이 안 움직임 | BSNames 비어있음 | 68개 이름 전부 입력됐는지 확인 |
| Cast 실패 | 원본 BP 배치 | 자식 BP (BP_MH_*) 를 배치해야 함 |
| 마지막 표정 얼어있음 | 리셋 누락 | Part D step ⑬ 확인 |
| OSC 노드 안 보임 | 플러그인 꺼짐 | Edit → Plugins → OSC 활성화 → 재시작 |
| 컴포넌트 검색 안 됨 | Compile 안 함 | BP_MH_BlendshapePlayer Compile 먼저 |

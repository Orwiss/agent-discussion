# MetaHuman 자연스러운 움직임 가이드

> UE5 5.7 + 무료 리소스만 사용
> 앉아서 대화하는 MetaHuman에 자연스러운 idle/눈깜빡임/시선 추가

---

## 우선순위 체크리스트

| 작업 | 공수 | 효과 | 난이도 |
|---|---|---|---|
| 1. 눈 깜빡임 | 15-30분 | 높음 | 쉬움 |
| 2. 고개 Look At | 1시간 | 높음 | 쉬움 |
| 3. 앉아있는 idle 몸 | 1-2시간 | 높음 | 중간 |
| 4. 눈동자 Look At | +1-2시간 | 중간 | 어려움 |
| 5. 호흡 레이어 | 30-60분 | 낮음 | 중간 |

---

## 1. 눈 깜빡임 (Procedural Blink)

### 방법

`Face_PostProcess_AnimBP`의 **EventGraph**에 블루프린트 추가 (AnimGraph 아님 — Control Rig/Live Link와 충돌 안 함)

### 로직

```
BeginPlay
  → Loop:
    → Delay (2~4초 랜덤 대기)
    → Set EyesClosed = true
    → Delay (0.1~0.15초 랜덤)
    → Set EyesClosed = false
```

### 자료

- **복붙 가능한 블루프린트**: https://blueprintue.com/blueprint/dd2t15hj/
- **Epic 공식 튜토리얼**: https://dev.epicgames.com/community/learning/tutorials/nzBE/unreal-engine-metahuman-blink-techniques-not-just-the-how-but-the-why
- **Lyra Echo 오토 블링크 가이드**: https://dev.epicgames.com/community/learning/tutorials/7Jjn/unreal-engine-ue5-lyra-setup-auto-eye-blinks-and-facial-expressions-with-epic-s-echo-or-your-own-character-mesh

### 주의

말하는 중에도 자동 깜빡임이 발동되면 A2F 블렌드셰이프와 충돌할 수 있음. 말하는 동안에는 비활성화하는 플래그 추가 고려.

---

## 2. 고개 Look At (시선 추적)

### 방법

UE5 내장 **Look At** 노드를 Animation Blueprint AnimGraph에 추가.

### 설정

- 대상 bone: `head` (또는 `neck_01`)
- 타겟: 발화자 MetaHuman 액터 (OSC의 `char_id`로 판별)
- 블렌드 weight: 0.5~0.8 (너무 빠르게 돌아가면 부자연스러움)

### 자료

- **공식 문서**: https://dev.epicgames.com/documentation/en-us/unreal-engine/animation-blueprint-head-look-at-in-unreal-engine
- **커뮤니티 튜토리얼**: https://dev.epicgames.com/community/learning/tutorials/mJGj/unreal-engine-ue5-techanimtip-1-dynamic-eyes-look-at-target

### 활용 아이디어

- 듣는 캐릭터가 말하는 캐릭터 바라보기
- 침묵 상태에서는 테이블 위/서로 번갈아 보기 (랜덤)
- OSC에서 현재 발화자 정보 활용 → Blueprint에서 타겟 업데이트

---

## 3. 앉아있는 Idle 몸 움직임

### 방법 A: Mixamo (추천)

**장점**: 무료, Adobe 계정만 있으면 됨, 애니메이션 다양함

**단계**:
1. https://www.mixamo.com 접속
2. 검색: "Sitting Idle", "Sitting Talking", "Breathing Idle"
3. FBX 다운로드 (Without Skin 옵션)
4. UE5에 임포트
5. UE5 내장 **IK Retargeter**로 MetaHuman에 리타겟
   - Mixamo skeleton용 IK Rig 생성
   - MetaHuman skeleton용 IK Rig 생성
   - 두 IK Rig 연결
   - T-pose vs A-pose 차이 보정

**공수**: 최초 리타겟 셋업 1-2시간, 이후 새 애니메이션은 수 초

**튜토리얼**:
- https://dev.epicgames.com/community/learning/tutorials/qz8V/unreal-engine-retarget-mixamo-animations-to-metahuman
- https://yelzkizi.org/retarget-mixamo-animations-to-metahuman/

### 방법 B: Game Animation Sample Project (GASP)

**장점**: Epic 공식, 500+ AAA 모션캡처, 44개 idle 포함, MetaHuman 적용 가이드 있음

**Fab 링크**: https://www.fab.com/listings/880e319a-a59e-4ed2-b268-b32dac7fa016

**MetaHuman 적용 가이드**: https://dev.epicgames.com/documentation/en-us/unreal-engine/adding-a-metahuman-to-the-game-animation-sample-project-in-unreal-engine

**공수**: 1-2시간

---

## 4. 눈동자 Look At (고급)

### 주의

MetaHuman 눈 방향은 **bone rotation이 아니라 blendshape curve**로 제어됨. Look At 노드만으론 부족.

### 방법

Face Control Rig의 `CTRL_C_eyesAim` 컨트롤을 블루프린트로 직접 설정.

### 공수

추가 1-2시간

---

## 5. 호흡 레이어 (Additive)

### 방법

MetaHuman Animator로 호흡 애니메이션을 녹화하거나, 기존 idle에 sine wave로 가슴 bone 미세 조정.

### 튜토리얼

https://dev.epicgames.com/community/learning/tutorials/DBM0/unreal-engine-ue-5-4-5-6-using-idle-breathing-animations-with-metahuman-animator-for-natural-looking-instructional-videos

---

## 참고 프로젝트

### NVIDIA ACE Gaming Avatar Sample

**중요**: 현재 우리 프로젝트와 같은 파이프라인 (Audio2Face + MetaHuman)의 공식 UE5 샘플

- **GitHub**: https://github.com/NVIDIA/ACE
- **문서**: https://docs.nvidia.com/ace/gaming-avatar/latest/gaming-avatar-unreal-sample-project.html
- **블로그**: https://developer.nvidia.com/blog/simplify-and-scale-ai-powered-metahuman-deployment-with-nvidia-ace-and-unreal-engine-5/

대화 중 idle 처리, Look At, 눈 깜빡임 등 참고 가능.

### MetaHumans Feature Samples (Epic 공식)

Fab 무료. MetaHuman 쇼케이스 씬, idle/AI/Control Rig 예시 포함.

- https://www.fab.com/listings/0281d63e-71f7-4e07-a344-5fa721ac4d35

### Animation Starter Pack (Epic 무료)

62개 Mannequin 애니메이션, MetaHuman 리타겟 가능.

- https://www.fab.com/listings/98ff449d-79db-4f54-9303-75486c4fb9d9

---

## 추천 실행 순서

1. **눈 깜빡임 블루프린트 복붙** → 즉시 효과
2. **Mixamo "Sitting Idle" 1개 다운 + 리타겟** → 몸 움직임
3. **Look At 노드로 고개 시선** → 상호작용 느낌
4. 여유 있으면 **눈동자 Look At**, **호흡 레이어**

---

## 주의사항

- **A2F 립싱크와 충돌 주의**: 말하는 중 자동 깜빡임/호흡이 입 블렌드셰이프와 겹치지 않도록 주의
- **MetaHuman 라이선스**: Epic 생태계 외 사용 금지
- **IK Retargeter T-pose 보정**: Mixamo는 T-pose, MetaHuman은 약간 다름 → 리타겟 포즈 조정 필수

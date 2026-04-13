# 멀티 에이전트 디자인 회의 시스템 — 파이프라인 도식

## 1. 전체 파이프라인

```mermaid
flowchart TB
    subgraph INPUT["사용자 입력"]
        USER["👤 Orwiss (UserProxyAgent)"]
        WEB["🌐 Web UI (port 8000)<br/>IOWebsockets"]
    end

    subgraph STAGE1["Stage 1: 사전 정제 (1:1 대화)"]
        MOD_S1["Moderator<br/><i>google/gemini-2.5-pro</i>"]
        BRIEF["회의 브리프 생성<br/>· 회의 유형 분류<br/>· 디자인 목표<br/>· 제약 조건 추출"]
    end

    subgraph STAGE2["Stage 2: 디자인 회의 (GroupChat)"]
        direction TB
        GC["DedupGroupChat<br/>max_round=40"]

        subgraph AGENTS["전문 에이전트 (AssistantAgent)"]
            UX["UX 리서처<br/><i>qwen/qwen3-235b</i><br/>사용자 행동·첫인상"]
            D3["3D 디자이너<br/><i>openai/gpt-4o</i><br/>레퍼런스·시각 비유"]
            UE["언리얼 개발자<br/><i>deepseek/deepseek-r1</i><br/>기술 실현·대안"]
        end

        MOD_S2["Moderator (동일 인스턴스)<br/><i>google/gemini-2.5-pro</i><br/>진행·정리·비평"]

        BOARD["공유 아이디어 보드<br/>· [보드:추가/발전/통합]<br/>· 매 턴 시스템 메시지 주입"]
    end

    subgraph PHASES["페이즈 전환 (StateFlow)"]
        P1["🟢 발산 (generate)"]
        P2["🟡 심화 (deepen)"]
        P3["🔴 수렴 (converge)"]
    end

    subgraph OUTPUT["출력"]
        LOG["대화 로그<br/>logs/session_*.log"]
        RESULT["최종 정리<br/>선택지 + 트레이드오프"]
    end

    USER -->|"디자인 회의 시작"| WEB
    WEB -->|WebSocket| MOD_S1
    MOD_S1 -->|"1:1 질문·응답"| USER
    MOD_S1 --> BRIEF
    BRIEF -->|"브리프 + 회의 유형"| GC
    BRIEF -->|"제약 조건 → 금지 키워드"| BOARD

    GC --> UX & D3 & UE
    GC --> MOD_S2
    MOD_S2 -->|"[보드:] 태그 파싱"| BOARD
    MOD_S2 -->|"PHASE_ADVANCE"| PHASES
    P1 --> P2 --> P3

    USER -->|"실시간 개입<br/>(방향 전환/종료)"| GC

    GC --> LOG
    MOD_S2 -->|"수렴 완료 → MEETING_END"| RESULT
```

## 2. 가드레일 시스템

```mermaid
flowchart LR
    subgraph HOOKS["메시지 처리 파이프라인"]
        direction TB
        RAW["에이전트 원본 발언"]
        H1["① clean_message_hook<br/>프롬프트 노출 strip<br/>내부 에이전트명 제거"]
        H2["② _strip_cjk_leaks<br/>CJK 혼입 문자 교정<br/>비한국어 문자 제거"]
        H3["③ strip_think_tags<br/>&lt;think&gt; 태그 제거<br/>(deepseek-r1 등)"]
        H4["④ _strip_board_tags<br/>비-Moderator의<br/>[보드:] 태그 차단"]
        H5["⑤ board_exclusion_hook<br/>금지 키워드 포함<br/>문장 삭제"]
        H6["⑥ phase_aware_context_hook<br/>현재 페이즈 정보<br/>시스템 메시지 주입"]
        CLEAN["정제된 발언"]

        RAW --> H1 --> H2 --> H3 --> H4 --> H5 --> H6 --> CLEAN
    end
```

## 3. Speaker Selection 로직

```mermaid
flowchart TD
    START["다음 발언자 결정"]
    CHK_USER{"마지막 발언자가<br/>Orwiss?"}
    CHK_MOD{"creative 에이전트<br/>3명 연속 발언?"}
    CHK_CONSEC{"같은 에이전트<br/>2회 연속?"}
    LEAST["가장 적게 발언한<br/>에이전트 지명"]
    MOD["Moderator 지명<br/>(정리 타임)"]
    AUTO["auto 모드<br/>(LLM 판단)"]

    START --> CHK_USER
    CHK_USER -->|"Yes"| AUTO
    CHK_USER -->|"No"| CHK_MOD
    CHK_MOD -->|"Yes"| MOD
    CHK_MOD -->|"No"| CHK_CONSEC
    CHK_CONSEC -->|"Yes"| LEAST
    CHK_CONSEC -->|"No"| AUTO
```

## 4. 모델 구성 (B안 프리미엄)

```mermaid
graph LR
    subgraph OPENROUTER["OpenRouter API"]
        direction TB
        QW["qwen/qwen3-235b-a22b-2507<br/>temp=0.6, freq_pen=0.4"]
        GPT["openai/gpt-4o<br/>temp=0.9, freq_pen=0.5"]
        DS["deepseek/deepseek-r1-0528<br/>temp=0.7, freq_pen=0.3"]
        GEM["google/gemini-2.5-pro<br/>temp=0.5, freq_pen=0.3"]
    end

    subgraph FILTER["품질 필터"]
        FP["양자화 필터<br/>fp16 / bf16 / fp8 only"]
    end

    UX["UX 리서처"] --> QW
    D3["3D 디자이너"] --> GPT
    UE["언리얼 개발자"] --> DS
    MOD["Moderator"] --> GEM

    QW & DS & GEM --> FP
    GPT -.->|"자체 서빙<br/>필터 불필요"| GPT
```

## 5. 회의 유형별 페이즈 흐름

```mermaid
flowchart LR
    subgraph DIV["발산형"]
        D_G["발산<br/>넓게 탐색"] --> D_D["심화<br/>유망 2개 + 미탐색 1개"] --> D_C["수렴<br/>선택지 + 트레이드오프"]
    end

    subgraph IMP["개선형"]
        I_G["발산<br/>문제 발견"] --> I_D["심화<br/>해결책 탐색"] --> I_C["수렴<br/>구체화"]
    end

    subgraph SEL["선택형"]
        S_G["발산<br/>후보별 탐색"] --> S_C["수렴<br/>비교 + 하이브리드 + 추천"]
    end
```

## 6. 반복 방지 메커니즘

```mermaid
flowchart TB
    subgraph L1["Layer 1: 모델 이질성"]
        M1["Qwen 235B"] ~~~ M2["GPT-4o"] ~~~ M3["DeepSeek-R1"]
        NOTE1["서로 다른 학습 데이터 + 아키텍처<br/>→ 같은 주제에도 다른 연상 패턴"]
    end

    subgraph L2["Layer 2: 서버단 가드레일"]
        FP["frequency_penalty 0.3~0.5<br/>→ 토큰 레벨 반복 억제"]
        BAN["금지 키워드 문장 삭제<br/>→ 브리프 제약 위반 원천 차단"]
        DEDUP["DedupGroupChat<br/>→ 앞 100자 동일시 무시"]
    end

    subgraph L3["Layer 3: 프롬프트 레벨"]
        SYS["시스템 프롬프트<br/>'다른 사람 말 반복 금지'"]
        BOARD2["아이디어 보드 주입<br/>→ 이미 나온 것 파악"]
        PHASE["페이즈 전환<br/>→ 논의 방향 강제 이동"]
    end

    L1 --> L2 --> L3
```

## 7. 파일 구조

```
agent-discussion/
├── web.py                    # 웹 서버 진입점 (IOWebsockets + HTTP)
├── main.py                   # CLI 진입점 (SocietyOfMind 버전, 구버전)
├── config.py                 # A안 모델 설정 (저가)
├── config_premium.py         # B안 모델 설정 (프리미엄, OpenRouter)
│
├── agents/
│   ├── moderator.py          # Moderator (Facilitator+Synthesizer 통합)
│   ├── simple_agents.py      # UX/3D/UE AssistantAgent (SocietyOfMind 없음)
│   └── preparer.py           # response_preparer callable (크로스복사 방지)
│
├── meeting/
│   ├── stage1.py             # Stage 1: 브리프 수집 (Moderator 1:1)
│   ├── stage2.py             # Stage 2: GroupChat + 페이즈 전환
│   ├── idea_board.py         # 공유 아이디어 보드
│   ├── speaker.py            # 페이즈별 speaker selection
│   └── guardrails.py         # 가드레일 (clean, CJK, 금지어, 페이즈 주입)
│
├── prompts/                  # 시스템 프롬프트 모음
├── logs/                     # 세션 로그
└── logs/clean/               # 정제된 대화 로그
```

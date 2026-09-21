"""실험용 웹 기반 디자인 아이디에이션 시스템 (Centralized vs Decentralized).

세션 흐름:
1. 실험자가 참가자 번호 / 조건(centralized/decentralized) / 태스크(A/B) 선택
2. 토론 진행 (3 phase)
3. 토론 종료 후 양식 입력 단계 (좌우 분할 UI)
4. 양식 제출 후 세션 종료

데이터 산출:
  {LOG_DIR}/P{id}_{cond}_task{T}_{ts}_log.jsonl     # 전체 이벤트
  {LOG_DIR}/P{id}_{cond}_task{T}_{ts}_messages.jsonl  # 대화만
  {LOG_DIR}/P{id}_{cond}_task{T}_{ts}_idea.json     # 양식 결과
  {LOG_DIR}/P{id}_{cond}_task{T}_{ts}_summary.json  # 통계
  {LOG_DIR}/messages.csv / ideas.csv / sessions.csv # 누적 (분석 직행)

모델: config_uniform (Gemini 2.5 Flash 단일, 1차 후보).
"""
import asyncio
import csv
import json
import os
import sys
import http.server
import datetime
import threading
import time
import traceback
import urllib.parse
import mimetypes
import autogen
import autogen.runtime_logging as runtime_logging
from autogen.io import IOStream

from experiment_runtime import (
    ExperimentSession,
    ExperimentSessionRegistry,
    SessionCancelled,
    SessionIOStream,
    current_session,
    session_scope,
)
from study_store import PersistedExperimentSession, StoreError, StudyStore
from runtime_llm_logger import ContextRuntimeLogger

from config_uniform import (
    llm_config_pm, llm_config_designer, llm_config_engineer,
)
from agents.simple_agents import create_pm, create_designer, create_engineer
from meeting.centralized import (
    run_centralized_discussion, reset_summarizer_usage, get_extra_usage_agents,
)
from meeting.decentralized import run_decentralized_discussion
from meeting.guardrails import clean_message_hook, clean_history_hook
from autogen.agentchat.contrib.capabilities.transform_messages import TransformMessages
from autogen.agentchat.contrib.capabilities.transforms import MessageHistoryLimiter

LOG_DIR = os.path.join(os.path.dirname(__file__), "logs", "experiment")
os.makedirs(LOG_DIR, exist_ok=True)
SURVEY_DIST_DIR = os.getenv(
    "SURVEY_DIST_DIR",
    os.path.abspath(
        os.path.join(os.path.dirname(__file__), "..", "agent-web-survey", "dist")
    ),
)

BRIEFS = {
    "A": "대학 신입생이 학교생활에 적응하도록 돕는 모바일 앱의 핵심 컨셉과 주요 기능을 제안하라.",
    "B": "자취를 시작하는 청년이 동네 생활에 정착하도록 돕는 모바일 앱의 핵심 컨셉과 주요 기능을 제안하라.",
}

# 양식 단계 진입 알림 마커 (백엔드 → 프론트)
FORM_REQUEST_MARKER = "[FORM_REQUEST]"
# 주제(brief)를 헤더로 보내는 마커 (채팅에 시스템 메시지로 안 띄움)
TOPIC_MARKER = "[TOPIC]"

# === 누적 CSV ===
MESSAGES_CSV = os.path.join(LOG_DIR, "messages.csv")
IDEAS_CSV = os.path.join(LOG_DIR, "ideas.csv")
SESSIONS_CSV = os.path.join(LOG_DIR, "sessions.csv")
_CSV_LOCK = threading.RLock()
SESSION_REGISTRY = ExperimentSessionRegistry(
    log_dir=LOG_DIR,
    max_active_sessions=int(os.getenv("MAX_ACTIVE_SESSIONS", "3")),
)
STUDY_STORE = StudyStore()

# 이 브랜치는 VR 장비가 붙은 로컬 머신에서만 돌아간다. 공개 배포용 연구자 로그인은
# 걷어냈고, 세션 기록에 들어가던 식별자 자리는 이 고정값으로 채운다 — 이 칸은
# Supabase 행과 세션 인가 조회에 그대로 쓰이므로 없애면 데이터 모양이 바뀐다.
LOCAL_RESEARCHER = "local"

# === 간단한 IP당 레이트리밋 (봇/스크립트 방어용) ===
_RATE_LIMIT_WINDOW_SECONDS = float(os.getenv("RATE_LIMIT_WINDOW_SECONDS", "60"))
_RATE_LIMIT_MAX_REQUESTS = int(os.getenv("RATE_LIMIT_MAX_REQUESTS", "120"))
_RATE_LIMIT_LOCK = threading.Lock()
_RATE_LIMIT_BUCKETS: dict[str, tuple[float, int]] = {}


def is_current_session(token):
    """해당 세션만 아직 실행 중인지 확인한다."""
    session = SESSION_REGISTRY.get(str(token))
    return session is not None and session.is_current


def log_event(event_type, data):
    """현재 작업 스레드의 세션에 이벤트를 기록한다."""
    print(f"  [{event_type}] {data}")
    session = current_session()
    if session is not None:
        session.record_event(event_type, data)
        STUDY_STORE.record_event(session, event_type, data)


def log_message(speaker, content):
    """현재 작업 스레드의 세션에 대화를 기록한다."""
    session = current_session()
    if session is not None:
        session.record_message(speaker, content)
        STUDY_STORE.record_message(session, speaker, content)


def _append_messages_csv(speaker, content):
    """누적 messages.csv에 한 줄 추가."""
    session = current_session()
    if session is None:
        return
    with _CSV_LOCK:
        new_file = not os.path.exists(MESSAGES_CSV)
        with open(MESSAGES_CSV, "a", encoding="utf-8", newline="") as f:
            w = csv.writer(f)
            if new_file:
                w.writerow(["session_id", "participant_id", "condition", "task", "phase", "speaker", "time", "content"])
            w.writerow([
                session.id,
                session.participant_id,
                session.condition,
                session.task,
                session.phase or "",
                speaker,
                datetime.datetime.now().isoformat(),
                content,
            ])


def _append_ideas_csv(form_data, session=None):
    session = session or current_session()
    if session is None or not form_data:
        return
    with _CSV_LOCK:
        new_file = not os.path.exists(IDEAS_CSV)
        with open(IDEAS_CSV, "a", encoding="utf-8", newline="") as f:
            w = csv.writer(f)
            if new_file:
                w.writerow(["session_id", "participant_id", "condition", "task", "concept",
                            "feature_1", "feature_2", "feature_3", "differentiator", "filled_at"])
            features = (form_data.get("features") or []) + ["", "", ""]
            features = features[:3]
            w.writerow([
                session.id,
                session.participant_id,
                session.condition,
                session.task,
                form_data.get("concept", ""),
                features[0], features[1], features[2],
                form_data.get("differentiator", ""),
                form_data.get("filled_at", ""),
            ])


def _summarize_token_usage(usage):
    """autogen.gather_usage_summary() 결과를 세션 요약용으로 정리.
    비용(cost)은 OpenRouter 모델명이 AG2 내장 가격표에 없어 항상 0으로 나오므로
    제외하고 토큰 수만 집계. cache_seed=None(캐시 미사용)이라 실제 사용량과
    캐시 포함 사용량이 같아 usage_excluding_cached_inference만 쓴다."""
    by_model = {}
    total_prompt = total_completion = total_tokens = 0
    for model, data in usage.get("usage_excluding_cached_inference", {}).items():
        if model == "total_cost":
            continue
        by_model[model] = {
            "prompt_tokens": data.get("prompt_tokens", 0),
            "completion_tokens": data.get("completion_tokens", 0),
            "total_tokens": data.get("total_tokens", 0),
        }
        total_prompt += data.get("prompt_tokens", 0)
        total_completion += data.get("completion_tokens", 0)
        total_tokens += data.get("total_tokens", 0)
    return {
        "total_prompt_tokens": total_prompt,
        "total_completion_tokens": total_completion,
        "total_tokens": total_tokens,
        "by_model": by_model,
    }


def _append_sessions_csv(summary):
    with _CSV_LOCK:
        new_file = not os.path.exists(SESSIONS_CSV)
        with open(SESSIONS_CSV, "a", encoding="utf-8", newline="") as f:
            w = csv.writer(f)
            if new_file:
                w.writerow(["session_id", "participant_id", "condition", "task", "started_at", "ended_at",
                            "status", "utterances_total", "interventions", "PM", "Designer", "Engineer",
                            "prompt_tokens", "completion_tokens", "total_tokens"])
            m = summary["meta"]
            c = summary["counts"]
            by = c.get("by_speaker", {})
            tu = summary.get("token_usage") or {}
            w.writerow([
                m.get("session_id", ""), m["participant_id"], m["condition"], m["task"],
                m["started_at"], m["ended_at"], m.get("status", ""),
                c["utterances_total"], c["interventions"],
                by.get("PM", 0), by.get("Designer", 0), by.get("Engineer", 0),
                tu.get("total_prompt_tokens", 0), tu.get("total_completion_tokens", 0),
                tu.get("total_tokens", 0),
            ])


# === 메시지 캡처 hook — 모든 발화를 messages 로그에 기록 ===
def _make_capture_msg_hook(token):
    """세션 ID를 클로저로 잡아 해당 참가자의 발화만 기록한다."""
    call_count = {"n": 0}

    def _hook(sender, message, recipient, silent):
        call_count["n"] += 1
        idx = call_count["n"]
        name = getattr(sender, "name", str(sender))
        content = ""
        if isinstance(message, str):
            content = message
        elif isinstance(message, dict):
            content = message.get("content", "") or ""
        preview = content.strip()[:40].replace("\n", " ")

        # 진단 로깅 — decentralized 세션에서 phase 전환 시 이전 phase 전체가 DB에
        # 통째로 재기록되던 버그(44행, P01 세션에서 확인)의 원인을 로컬/프로덕션
        # 재현으로도 못 찾아서, hook이 실제로 몇 번 호출되고 매번 silent 값이
        # 뭔지 계속 지켜보는 중이다. 방어적 dedup 필터는 시도했다가 진짜 참가자
        # 발화(우연히 같은 문장을 두 번 말한 경우)를 잘못 삭제하는 게 실측으로
        # 확인돼서 뺐다 — 데이터를 지우느니 가끔 중복이 남는 게 낫다는 판단.
        log_event("hook_diag", {
            "idx": idx, "sender": name, "silent": bool(silent),
            "recipient": getattr(recipient, "name", str(recipient)),
            "len": len(content), "preview": preview,
        })

        if silent:
            # GroupChatManager.resume()이 phase 전환마다 이전 대화 전체를 각 에이전트에게
            # agent.send(..., silent=True)로 재생하는데, silent=True는 AG2가 콘솔에
            # "출력"만 안 할 뿐 이 hook은 그대로 호출한다 — 걸러주지 않으면 이전 phase
            # 전체가 로그에 통째로 다시 기록된다(확인된 버그).
            return message
        if not is_current_session(token):
            return message
        stripped = content.strip() if content else ""
        if stripped:
            log_message(name, stripped)
            _append_messages_csv(name, stripped)
        return message
    return _hook


def _session_worker(session: ExperimentSession) -> None:
    """한 참가자의 AG2 실행을 독립 스레드와 독립 IO 큐에서 수행한다."""
    iostream = SessionIOStream(session)
    form_data = None
    final_status = "completed"
    error_message = None

    with session_scope(session), IOStream.set_default(iostream):
        session.set_status("running")
        log_event("session_start", {
            "session_id": session.id,
            "participant_id": session.participant_id,
            "condition": session.condition,
            "task": session.task,
            "base": session.base,
        })
        iostream.print(f"{TOPIC_MARKER}{session.brief}")
        try:
            _run_session(
                iostream,
                session.condition,
                session.brief,
                session.id,
            )
            session.record_discussion_end()
            form_data = _collect_form(iostream)
        except SessionCancelled:
            final_status = "cancelled"
            log_event("session_cancelled", {})
        except Exception as error:
            final_status = "error"
            error_message = str(error)
            iostream.print(f"[오류] {error_message}")
            log_event("error", {
                "message": error_message,
                "traceback": traceback.format_exc(),
            })
        finally:
            if final_status == "completed":
                iostream.print("\n[시스템] 세션이 종료되었습니다.")
            log_event("session_end", {"status": final_status})
            if form_data:
                _append_ideas_csv(form_data, session=session)
                STUDY_STORE.record_idea(session, form_data)
            session.finish(
                final_status,
                idea=form_data,
                token_usage=session.token_usage,
                error=error_message,
            )
            _append_sessions_csv(session.summary())
            STUDY_STORE.record_session_end(session)


def start_session(
    participant_id: str,
    condition: str,
    task: str,
    researcher_email: str,
) -> ExperimentSession:
    participant_id = (participant_id or "").strip().upper() or "P99"
    if len(participant_id) > 64:
        raise ValueError("참가자 번호를 1~64자로 입력해 주세요.")
    if condition not in ("centralized", "decentralized"):
        raise ValueError("올바르지 않은 실험 조건입니다.")
    if task not in BRIEFS:
        raise ValueError("올바르지 않은 태스크입니다.")

    session = SESSION_REGISTRY.create(
        participant_id=participant_id,
        condition=condition,
        task=task,
        brief=BRIEFS[task],
        researcher_email=researcher_email,
    )
    try:
        STUDY_STORE.record_session_start(session)
    except Exception:
        session.cancel()
        raise
    session.thread = threading.Thread(
        target=_session_worker,
        args=(session,),
        name=f"experiment-{session.id[:8]}",
        daemon=True,
    )
    session.thread.start()
    return session


def _run_session(iostream, condition, brief, token):
    """토론 단계 — Centralized면 async, Decentralized면 sync."""
    user = autogen.UserProxyAgent(
        name="Participant",
        human_input_mode="ALWAYS",
        max_consecutive_auto_reply=0,  # 실험 통제: 매번 사용자 입력 강제
        code_execution_config=False,
        is_termination_msg=lambda m: "TERMINATE" in m.get("content", ""),
    )

    pm = create_pm(llm_config_pm, brief=brief, condition=condition)
    designer = create_designer(llm_config_designer, brief=brief, condition=condition)
    engineer = create_engineer(llm_config_engineer, brief=brief, condition=condition)
    agents = [pm, designer, engineer]

    # 세션을 에이전트 객체에 직접 태깅 — LLM 로거가 여기서 읽는다. contextvars와
    # 달리 스레드 경계(centralized의 run_in_executor)를 넘어가도 안 사라진다.
    session = current_session()
    for agent in agents:
        agent.session = session

    # 가드레일 (양 조건 공통)
    for agent in agents:
        agent.register_hook("process_message_before_send", clean_message_hook)
        agent.register_hook("process_all_messages_before_reply", clean_history_hook)

    if condition == "centralized":
        # 디자이너·엔지니어 개인 sub-chat 히스토리(PM과의 1:1 대화, clear_history=False라
        # 라운드마다 계속 누적됨)에 슬라이딩 윈도우를 건다 — 예전엔 이 제한이 아예 없어서
        # 3phase 내내 무제한으로 쌓였다. meeting.centralized.HISTORY_WINDOW와 동일하게 맞춤.
        TransformMessages(transforms=[MessageHistoryLimiter(max_messages=50)]).add_to_agent(designer)
        TransformMessages(transforms=[MessageHistoryLimiter(max_messages=50)]).add_to_agent(engineer)
        # Centralized: _log_msg로 직접 로깅 (캡처 hook 미사용 — 내부 트리거 발화 오염 방지)
        asyncio.run(run_centralized_discussion(pm, designer, engineer, user, brief, token=token))
        usage_agents = agents + get_extra_usage_agents(pm)  # 이 세션의 접힘 요약기만 포함
    else:
        # Decentralized: groupchat이라 발화 캡처를 hook으로 처리
        capture_hook = _make_capture_msg_hook(token)
        for agent in agents:
            agent.register_hook("process_message_before_send", capture_hook)
        user.register_hook("process_message_before_send", capture_hook)
        run_decentralized_discussion(agents, user, brief, iostream=iostream, token=token)
        usage_agents = agents

    session = current_session()
    if session is not None:
        usage = autogen.gather_usage_summary(usage_agents)
        session.token_usage = _summarize_token_usage(usage)


def _collect_form(iostream):
    """토론 종료 후 양식 단계.
    프론트에 FORM_REQUEST_MARKER 보내면 UI 분할 → 양식 표시.
    사용자가 제출하면 JSON 문자열로 한 줄 응답."""
    iostream.print(FORM_REQUEST_MARKER)
    session = current_session()
    if session is None:
        return None
    session.set_status("awaiting_form")
    try:
        form = session.next_form()
        form["filled_at"] = datetime.datetime.now().isoformat()
        session.record_form_submitted()
        log_event("final_form", form)
        return form
    except Exception as e:
        log_event("form_error", {"error": str(e)})
        return None


# === HTML 프론트엔드 ===
HTML_PAGE = r"""<!DOCTYPE html>
<html lang="ko">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>서비스 아이디에이션 세션</title>
<link rel="icon" type="image/svg+xml" href="data:image/svg+xml;base64,PHN2ZyB3aWR0aD0iMzIiIGhlaWdodD0iMzIiIHZpZXdCb3g9IjAgMCAzMiAzMiIgeG1sbnM9Imh0dHA6Ly93d3cudzMub3JnLzIwMDAvc3ZnIj48cmVjdCB3aWR0aD0iMzIiIGhlaWdodD0iMzIiIHJ4PSI3IiBmaWxsPSIjMEIwQjBDIi8+PGcgdHJhbnNmb3JtPSJ0cmFuc2xhdGUoMi41LDIuNSkgc2NhbGUoMS4xNDU4KSI+PHBhdGggZmlsbD0iI2ZmZiIgZD0iTTEyIDJhMiAyIDAgMCAxIDEgMy43M1Y2aDNhNCA0IDAgMCAxIDQgNHYuMDVhMi41MDEgMi41MDEgMCAwIDEgMCA0LjlWMTZhNCA0IDAgMCAxLTQgNEg4YTQgNCAwIDAgMS00LTR2LTEuMDVhMi41IDIuNSAwIDAgMSAwLTQuOVYxMGE0IDQgMCAwIDEgNC00aDN2LS4yN0EyIDIgMCAwIDEgMTIgMm0tMyA5YTEgMSAwIDAgMC0xIDF2MmExIDEgMCAxIDAgMiAwdi0yYTEgMSAwIDAgMC0xLTFtNiAwYTEgMSAwIDAgMC0xIDF2MmExIDEgMCAxIDAgMiAwdi0yYTEgMSAwIDAgMC0xLTEiLz48L2c+PC9zdmc+">
<style>
  @font-face { font-family: 'LineSeed'; src: url('https://cdn.jsdelivr.net/gh/projectnoonnu/noonfonts_11-01@1.0/LINESeedKR-Th.woff2') format('woff2'); font-weight: 100; font-display: swap; }
  @font-face { font-family: 'LineSeed'; src: url('https://cdn.jsdelivr.net/gh/projectnoonnu/noonfonts_11-01@1.0/LINESeedKR-Rg.woff2') format('woff2'); font-weight: 400; font-display: swap; }
  @font-face { font-family: 'LineSeed'; src: url('https://cdn.jsdelivr.net/gh/projectnoonnu/noonfonts_11-01@1.0/LINESeedKR-Bd.woff2') format('woff2'); font-weight: 700; font-display: swap; }
  :root {
    --bg: #e7ebf1;
    --bg-2: #eef1f6;
    --panel: #ffffff;
    --border: #ccd4de;
    --border-soft: #dde3ea;
    --text: #1b2330;
    --muted: #56627a;
    --muted-2: #828c9c;
    --accent: #4f46e5;
    --accent-hover: #4338ca;
    --pm: #5a51d6;
    --designer: #c2620f;
    --engineer: #1f9d57;
    --radius: 16px;
    --radius-sm: 10px;
    --shadow: 0 1px 3px rgba(16,24,40,0.10);
    --shadow-bar: 0 2px 8px rgba(16,24,40,0.07);
  }
  * { margin: 0; padding: 0; box-sizing: border-box; }
  html, body { height: 100%; }
  body {
    font-family: 'LineSeed', 'Segoe UI', 'Apple SD Gothic Neo', 'Malgun Gothic', sans-serif;
    background: var(--bg);
    color: var(--text);
    font-size: 17px;
    height: 100vh; display: flex; flex-direction: column;
    -webkit-font-smoothing: antialiased; text-rendering: optimizeLegibility;
  }
  ::-webkit-scrollbar { width: 11px; height: 11px; }
  ::-webkit-scrollbar-thumb {
    background: #c5ccd6; border-radius: 8px;
    border: 3px solid transparent; background-clip: padding-box;
  }
  ::-webkit-scrollbar-thumb:hover { background: #aab3c0; background-clip: padding-box; }
  ::-webkit-scrollbar-track { background: transparent; }

  /* ===== 시작 화면 ===== */
  #start-page {
    flex: 1; display: flex; flex-direction: column;
    align-items: center; justify-content: center; gap: 22px; padding: 24px;
  }
  #start-page h1 { font-size: 34px; font-weight: 700; letter-spacing: -0.02em; }
  #start-page > p {
    color: var(--muted); font-size: 16px; max-width: 480px;
    text-align: center; line-height: 1.7; margin-bottom: 8px;
  }
  .form-group { display: flex; flex-direction: column; gap: 9px; align-items: center; }
  .form-group label { font-size: 15px; color: var(--muted); font-weight: 500; }
  .form-group input, .form-group select {
    padding: 13px 18px; background: var(--panel); border: 1px solid var(--border);
    border-radius: var(--radius-sm); color: var(--text); font-size: 17px;
    width: 280px; text-align: center; transition: border-color .15s, box-shadow .15s;
    box-shadow: var(--shadow);
  }
  .form-group input:focus, .form-group select:focus {
    outline: none; border-color: var(--accent);
    box-shadow: 0 0 0 3px rgba(79,70,229,0.15);
  }
  #start-page button {
    padding: 15px 48px; background: var(--accent); color: #fff; border: none;
    border-radius: var(--radius-sm); cursor: pointer; font-size: 17px; font-weight: 600;
    margin-top: 12px; transition: background .15s, transform .05s;
  }
  #start-page button:hover { background: var(--accent-hover); }
  #start-page button:active { transform: translateY(1px); }
  #start-page .secondary-link-button {
    display: inline-block; padding: 13px 40px; margin-top: 4px;
    background: transparent; color: var(--accent); border: 1px solid var(--accent);
    border-radius: var(--radius-sm); cursor: pointer; font-size: 15px; font-weight: 600;
    text-decoration: none; transition: background .15s, transform .05s;
  }
  #start-page .secondary-link-button:hover { background: rgba(79,70,229,0.08); }
  #start-page .secondary-link-button:active { transform: translateY(1px); }

  /* ===== 안내 모달 ===== */
  .modal-overlay {
    display: none; position: fixed; inset: 0; background: rgba(27,35,48,0.55);
    align-items: center; justify-content: center; z-index: 50; padding: 20px;
  }
  .modal-overlay.show { display: flex; }
  .modal-box {
    background: var(--panel); border-radius: var(--radius); padding: 32px;
    max-width: 600px; width: 100%; box-shadow: var(--shadow);
  }
  .modal-box h2 { font-size: 20px; font-weight: 700; margin-bottom: 18px; }
  .modal-box .modal-topic-block {
    background: var(--bg-2); border-radius: var(--radius-sm); padding: 18px 20px; margin-bottom: 22px;
  }
  .modal-box .modal-topic-label {
    display: block; font-size: 12px; font-weight: 700; color: var(--accent);
    letter-spacing: 0.04em; margin-bottom: 6px;
  }
  .modal-box .modal-topic-text { font-size: 19px; font-weight: 600; color: var(--text); line-height: 1.5; }
  .modal-box .modal-steps p { color: var(--muted); font-size: 14.5px; line-height: 1.7; margin-bottom: 10px; }
  .modal-box .modal-steps p:last-child { margin-bottom: 0; }
  .modal-box button {
    display: block; margin: 26px auto 0; padding: 13px 40px; background: var(--accent);
    color: #fff; border: none; border-radius: var(--radius-sm); cursor: pointer;
    font-size: 16px; font-weight: 600; transition: background .15s, transform .05s;
  }
  .modal-box button:hover { background: var(--accent-hover); }
  .modal-box button:active { transform: translateY(1px); }

  /* ===== 실험 화면 ===== */
  #experiment-page { flex: 1; display: none; flex-direction: column; overflow: hidden; }
  header {
    padding: 16px 28px; background: var(--panel);
    border-bottom: 1px solid var(--border); box-shadow: var(--shadow-bar); z-index: 2;
    display: flex; justify-content: space-between; align-items: center; flex-shrink: 0;
  }
  header .title h1 { font-size: 19px; font-weight: 650; letter-spacing: -0.01em; }
  header .title #topic {
    display: flex; align-items: center; gap: 9px; margin-top: 7px;
    font-size: 16px; color: var(--text); font-weight: 500; line-height: 1.45; max-width: 78ch;
  }
  header .title .topic-label {
    flex-shrink: 0; font-size: 12px; font-weight: 700; color: #fff;
    background: var(--accent); padding: 3px 11px; border-radius: 999px; letter-spacing: 0.02em;
  }
  header .info {
    font-size: 14px; color: var(--muted); background: var(--panel);
    padding: 7px 14px; border-radius: 999px; border: 1px solid var(--border);
  }
  .header-right { display: flex; align-items: center; gap: 10px; }
  #reset-btn {
    font-size: 13px; color: var(--muted); background: var(--panel);
    padding: 7px 14px; border-radius: 999px; border: 1px solid var(--border);
    cursor: pointer; transition: background .15s, color .15s;
  }
  #reset-btn:hover { background: var(--bg-2); color: var(--text); }

  #discussion-area { flex: 1; display: flex; flex-direction: column; overflow: hidden; }
  #chat-wrap { flex: 1; min-height: 0; position: relative; }
  #chat {
    position: absolute; inset: 0; overflow-y: auto; padding: 28px;
    display: flex; flex-direction: column; gap: 14px;
  }
  #scroll-down-btn {
    display: none; position: absolute; bottom: 18px; left: 50%; transform: translateX(-50%);
    z-index: 5; align-items: center; gap: 6px; padding: 9px 16px; border-radius: 999px;
    border: 1px solid var(--border); background: var(--accent); color: #fff;
    font-size: 13px; font-weight: 600; cursor: pointer; box-shadow: var(--shadow-bar);
    transition: background .15s;
  }
  #scroll-down-btn:hover { background: var(--accent-hover); }
  #scroll-down-btn.visible { display: flex; }
  @keyframes msgIn { from { opacity: 0; } to { opacity: 1; } }
  .msg {
    max-width: min(78%, 960px); padding: 16px 20px; border-radius: var(--radius);
    font-size: 17px; line-height: 1.85; white-space: pre-wrap;
    word-break: keep-all; overflow-wrap: anywhere; letter-spacing: -0.005em;
    animation: msgIn .2s ease both;
  }
  .msg.system {
    align-self: center; background: var(--panel); color: var(--muted);
    font-size: 14px; border: 1px solid var(--border-soft); border-radius: 999px;
    padding: 7px 16px; text-align: center; max-width: 90%;
  }
  .msg.user {
    align-self: flex-end; background: linear-gradient(135deg, #5b7ff7, #3b82f6);
    color: #fff; border-bottom-right-radius: 5px;
  }
  .msg.agent {
    align-self: flex-start; background: var(--panel); border: 1px solid var(--border);
    border-top-left-radius: 5px; box-shadow: var(--shadow); max-width: 100%;
  }
  /* 발화자별 아바타 + 이름을 말풍선 밖에 배치 (이름·본문 뭉개지지 않게, 정체성 신호 강화) */
  .msg-row {
    display: flex; align-items: flex-start; gap: 12px;
    max-width: min(78%, 960px); align-self: flex-start;
  }
  .msg-row .avatar {
    width: 38px; height: 38px; border-radius: 50%; flex-shrink: 0;
    display: flex; align-items: center; justify-content: center;
    font-size: 17px; color: #fff; margin-top: 2px;
  }
  .avatar.pm { background: var(--pm); }
  .avatar.designer { background: var(--designer); }
  .avatar.engineer { background: var(--engineer); }
  .msg-col { display: flex; flex-direction: column; gap: 5px; min-width: 0; flex: 1; }
  .msg-name {
    font-weight: 700; font-size: 14px; letter-spacing: 0.01em;
  }
  .msg-name.pm { color: var(--pm); }
  .msg-name.designer { color: var(--designer); }
  .msg-name.engineer { color: var(--engineer); }
  /* Centralized: 디자이너·엔지니어 발화는 PM 아래 작은 아바타+이름(박스 밖) + 박스 안엔
     PM 질문 인용 + 요약. 클릭 인터랙션 없이 항상 이 형태로 고정. */
  .sub-row {
    display: flex; gap: 9px; margin-left: 50px; max-width: min(64%, 860px);
  }
  .sub-row .avatar {
    width: 26px; height: 26px; border-radius: 50%; flex-shrink: 0;
    display: flex; align-items: center; justify-content: center;
    font-size: 14px; color: #fff; margin-top: 2px;
  }
  .sub-col { display: flex; flex-direction: column; gap: 4px; min-width: 0; flex: 1; }
  .sub-name { font-weight: 700; font-size: 15px; }
  .sub-name.designer { color: var(--designer); }
  .sub-name.engineer { color: var(--engineer); }
  .sub-box {
    background: var(--bg-2); border: 1px solid var(--border-soft); border-radius: 10px;
    padding: 12px 16px;
  }
  .sub-quote {
    display: flex; gap: 5px; min-width: 0;
    border-left: 2px solid #a49bee; padding-left: 7px; margin-bottom: 8px;
  }
  .sub-quote .sub-quote-name { font-weight: 700; font-size: 13px; color: var(--pm); flex-shrink: 0; }
  .sub-quote .sub-quote-text {
    display: block; min-width: 0; max-width: 50%; overflow: hidden;
    text-overflow: ellipsis; white-space: nowrap;
    font-size: 13px; color: var(--muted-2);
  }
  .sub-gist { font-size: 16px; color: var(--muted); line-height: 1.6; }
  .msg.waiting {
    align-self: center; background: rgba(79,70,229,0.10);
    border: 1px solid rgba(79,70,229,0.35); color: var(--accent-hover);
    font-size: 14px; border-radius: 999px; padding: 8px 18px; font-weight: 500;
  }
  #input-area {
    padding: 16px 28px; background: var(--panel);
    border-top: 1px solid var(--border); box-shadow: 0 -2px 8px rgba(16,24,40,0.07); z-index: 2;
    display: flex; gap: 12px; flex-shrink: 0;
  }
  #input-area textarea {
    flex: 1; padding: 14px 18px; background: var(--panel); border: 1px solid var(--border);
    border-radius: var(--radius-sm); color: var(--text); font-size: 17px; outline: none;
    font-family: inherit; resize: none; max-height: 140px; overflow-y: auto;
    transition: border-color .15s, box-shadow .15s;
  }
  #input-area textarea:focus { border-color: var(--accent); box-shadow: 0 0 0 3px rgba(79,70,229,0.15); }
  #input-area textarea:disabled { opacity: 0.55; }
  #input-area button {
    padding: 14px 28px; background: var(--accent); color: #fff; border: none;
    border-radius: var(--radius-sm); cursor: pointer; font-size: 17px; font-weight: 600;
    transition: background .15s, transform .05s;
  }
  #input-area button:hover:not(:disabled) { background: var(--accent-hover); }
  #input-area button:active:not(:disabled) { transform: translateY(1px); }
  #input-area button:disabled { opacity: 0.45; cursor: not-allowed; }
  #status {
    padding: 10px 28px; background: var(--bg); font-size: 14px; color: var(--muted-2);
    text-align: center; flex-shrink: 0; border-top: 1px solid var(--border-soft);
    display: flex; align-items: center; justify-content: center; gap: 8px;
  }
  #status::before {
    content: ''; width: 8px; height: 8px; border-radius: 50%;
    background: var(--muted-2); transition: background .2s;
  }
  #status.active { color: #15803d; }
  #status.active::before {
    background: #22c55e; box-shadow: 0 0 8px rgba(34,197,94,0.7);
    animation: pulse 1.4s ease-in-out infinite;
  }
  @keyframes pulse { 0%, 100% { opacity: 1; } 50% { opacity: 0.35; } }

  /* ===== 양식 단계 (좌우 분할) ===== */
  #form-area { flex: 1; display: none; flex-direction: row; overflow: hidden; }
  #form-chat {
    width: 50%; overflow-y: auto; padding: 28px; border-right: 1px solid var(--border);
    display: flex; flex-direction: column; gap: 14px; background: var(--bg-2);
  }
  #form-panel {
    width: 50%; padding: 32px 40px; overflow-y: auto;
    display: flex; flex-direction: column; gap: 18px;
  }
  #form-panel h2 { font-size: 22px; color: var(--text); letter-spacing: -0.01em; }
  #form-panel p.help { font-size: 15px; color: var(--muted); line-height: 1.7; margin-bottom: 4px; }
  .field { display: flex; flex-direction: column; gap: 8px; }
  .field label { font-size: 14px; color: var(--muted); font-weight: 600; }
  .field input, .field textarea {
    padding: 13px 16px; background: var(--panel); border: 1px solid var(--border);
    border-radius: var(--radius-sm); color: var(--text); font-size: 17px; outline: none;
    font-family: inherit; resize: vertical; transition: border-color .15s, box-shadow .15s;
  }
  .field input:focus, .field textarea:focus {
    border-color: var(--accent); box-shadow: 0 0 0 3px rgba(79,70,229,0.15);
  }
  .field textarea { min-height: 72px; line-height: 1.6; }
  #form-submit {
    padding: 15px 28px; background: var(--accent); color: #fff; border: none;
    border-radius: var(--radius-sm); cursor: pointer; font-size: 17px; font-weight: 600;
    margin-top: 8px; transition: background .15s, transform .05s;
  }
  #form-submit:hover:not(:disabled) { background: var(--accent-hover); }
  #form-submit:active:not(:disabled) { transform: translateY(1px); }
  #form-submit:disabled { opacity: 0.5; cursor: not-allowed; }
</style>
</head>
<body>

<div id="start-page">
  <h1>디자인 아이디에이션 실험</h1>
  <p>PM · UX/UI Designer · SW Engineer와 함께 모바일 앱 아이디어를 발전시킵니다.</p>
  <div class="form-group">
    <label>참가자 번호</label>
    <input id="participant-id" type="text" placeholder="예: P99"
           autocapitalize="characters" spellcheck="false"
           oninput="this.value = this.value.toUpperCase()">
  </div>
  <div class="form-group">
    <label>조건</label>
    <select id="condition-mode">
      <option value="centralized">조건 1</option>
      <option value="decentralized">조건 2</option>
    </select>
  </div>
  <div class="form-group">
    <label>태스크</label>
    <select id="task-mode" disabled>
      <option value="A" selected>태스크 A</option>
      <option value="B">태스크 B</option>
    </select>
  </div>
  <button onclick="openBriefModal()">세션 시작</button>
  <a class="secondary-link-button" href="/survey/">설문으로 이동</a>
</div>

<div id="brief-modal" class="modal-overlay">
  <div class="modal-box">
    <h2>세션 안내</h2>
    <div class="modal-topic-block">
      <span class="modal-topic-label">오늘의 주제</span>
      <span id="modal-topic-text" class="modal-topic-text"></span>
    </div>
    <div class="modal-steps">
      <p>위 주제로 PM · UX/UI Designer · SW Engineer 세 전문가와 함께 회의하며 서비스 아이디어를 발전시켜 나갑니다.</p>
      <p>회의 중간중간 의견을 남겨 대화 흐름에 개입하실 수 있고, 특별히 하실 말씀이 없다면 엔터만 눌러 넘어가시면 됩니다.</p>
      <p>회의가 끝나면 지금까지 논의된 내용 중 마음에 드는 부분을 참고하여 핵심 컨셉·주요 기능·차별점으로 아이디어를 발전시켜 제출하는 것으로 세션이 마무리됩니다.</p>
    </div>
    <button onclick="confirmStart()">시작하기</button>
  </div>
</div>

<div id="experiment-page">
  <header>
    <div class="title">
      <h1>디자인 아이디에이션</h1>
      <p id="topic"><span class="topic-label">주제</span><span id="topic-text"></span></p>
    </div>
    <div class="header-right">
      <div class="info" id="session-info"></div>
      <button id="reset-btn" onclick="resetToStart()" title="처음으로 되돌리기">처음으로</button>
    </div>
  </header>

  <div id="discussion-area">
    <div id="chat-wrap">
      <div id="chat"></div>
      <button id="scroll-down-btn" onclick="scrollChatToBottom()">↓ 새 메시지</button>
    </div>
    <div id="status">대기 중...</div>
    <div id="input-area">
      <textarea id="msg" rows="1" placeholder="의견을 입력하세요 (Enter 전송, Shift+Enter 줄바꿈, 빈칸 = 넘기기)" disabled></textarea>
      <button id="send" disabled onclick="sendMessage()">전송</button>
    </div>
  </div>

  <div id="form-area">
    <div id="form-chat"></div>
    <div id="form-panel">
      <h2>최종 아이디어 정리</h2>
      <p class="help">왼쪽 회의 내용을 참고하여 아이디어를 정리해 주세요. 모든 항목을 채워 제출하면 세션이 종료됩니다.</p>
      <div class="field">
        <label>핵심 컨셉 (한 줄)</label>
        <textarea id="f-concept" rows="2"></textarea>
      </div>
      <div class="field">
        <label>주요 기능 #1</label>
        <input id="f-feature1" type="text">
      </div>
      <div class="field">
        <label>주요 기능 #2</label>
        <input id="f-feature2" type="text">
      </div>
      <div class="field">
        <label>주요 기능 #3</label>
        <input id="f-feature3" type="text">
      </div>
      <div class="field">
        <label>차별점 (한 줄)</label>
        <textarea id="f-differentiator" rows="2"></textarea>
      </div>
      <button id="form-submit" onclick="submitForm()">제출</button>
    </div>
  </div>
</div>

<script>
const FORM_REQUEST_MARKER = '__FORM_REQUEST_MARKER__';
const TOPIC_MARKER = '__TOPIC_MARKER__';
const BRIEF_TEXT = __BRIEFS_JSON__;
const chat = document.getElementById('chat');
const msgInput = document.getElementById('msg');
const sendBtn = document.getElementById('send');
const statusEl = document.getElementById('status');
let sessionId = null;
let sessionSecret = null;
let pollActive = false;
let lastEventSequence = 0;
let waitingForInput = false;
let lastMsgHash = '';
let currentCondition = 'centralized';
let lastPmText = '';  // 서브에이전트 인용문에 쓸, 가장 최근 PM 발화

// 발화 렌더 큐 — 메시지가 겹치지 않게 한 번에 하나씩 타이핑(타자기 효과)
const TYPE_SPEED_MS = 38;
const MESSAGE_GAP_MS = 1200;  // 발화자가 바뀔 때 텍스트 시작 전 텀
let renderQueue = [];
let rendering = false;
let lastRenderSender = null;
function processQueue() {
  if (rendering) return;
  const item = renderQueue.shift();
  if (!item) return;
  rendering = true;
  const run = () => item.fn(() => { lastRenderSender = item.sender; rendering = false; processQueue(); });
  if (item.sender && lastRenderSender !== null) {
    setTimeout(run, MESSAGE_GAP_MS);  // 발화자 전환 시(같은 발화자 연속 포함) 잠깐 텀
  } else {
    run();
  }
}
function enqueue(fn, sender) { renderQueue.push({ fn, sender: sender || null }); processQueue(); }

// 채팅이 바닥 근처에 있을 때만 새 발화를 따라 스크롤한다. 사용자가 위로 스크롤해서
// 이전 내용을 보고 있으면 강제로 내리지 않고 "새 메시지" 버튼만 띄운다.
const scrollDownBtn = document.getElementById('scroll-down-btn');
function isChatNearBottom(threshold) {
  threshold = threshold || 80;
  return chat.scrollHeight - chat.scrollTop - chat.clientHeight <= threshold;
}
function showScrollDownBtn() { scrollDownBtn.classList.add('visible'); }
function hideScrollDownBtn() { scrollDownBtn.classList.remove('visible'); }
function scrollChatToBottom() { chat.scrollTop = chat.scrollHeight; hideScrollDownBtn(); }
function stickOrIndicate(stick) { if (stick) { chat.scrollTop = chat.scrollHeight; } else { showScrollDownBtn(); } }
chat.addEventListener('scroll', () => { if (isChatNearBottom()) hideScrollDownBtn(); });

// 전체 텍스트로 말풍선 높이를 미리 잡아두고, 안 보이는 부분만 투명 처리해서
// 한 글자씩 드러냄 → 타이핑 중 영역이 커지거나 스크롤이 튀지 않음.
function typeText(node, text, done, stick) {
  const shown = document.createElement('span');
  const rest = document.createElement('span');
  rest.style.visibility = 'hidden';
  rest.textContent = text;
  node.appendChild(shown);
  node.appendChild(rest);
  stickOrIndicate(stick);  // 말풍선 등장 시 1회만, 바닥 근처일 때만 스크롤
  let i = 0;
  const step = () => {
    if (i >= text.length) {
      node.textContent = text;
      done();
      return;
    }
    i += 1;
    shown.textContent = text.slice(0, i);
    rest.textContent = text.slice(i);
    setTimeout(step, TYPE_SPEED_MS);
  };
  step();
}

const DISPLAY_NAMES = {
  'PM': 'PM',
  'Designer': '디자이너',
  'Engineer': '엔지니어',
};

function nameClass(name) {
  if (name === 'PM') return 'pm';
  if (name === 'Designer') return 'designer';
  if (name === 'Engineer') return 'engineer';
  return '';
}

function stripThink(text) {
  return text.replace(/<think>[\s\S]*?<\/think>/g, '').trim();
}

const ROLE_ICONS = { 'PM': '🧑‍💼', 'Designer': '🎨', 'Engineer': '🛠️' };

function gistOf(text) {
  // 첫 문장을 미리보기로. 너무 길면 단어 경계에서 자름(단어 중간 X).
  let s = (text.trim().split(/(?<=[.!?。])\s+/)[0]) || text.trim();
  if (s.length <= 80) return s;
  let cut = s.slice(0, 80);
  const sp = cut.lastIndexOf(' ');
  if (sp > 40) cut = cut.slice(0, sp);
  return cut + '…';
}

function addMsg(type, content, sender, summary) {
  content = stripThink(content);
  if (!content) return;
  const hash = type + '|' + (sender || '') + '|' + content;
  if (hash === lastMsgHash) return;
  lastMsgHash = hash;
  const displayName = (sender && DISPLAY_NAMES[sender]) || sender;
  // Centralized 조건: 디자이너·엔지니어 발화는 PM을 거쳐 요약된 형태로만 노출 (원문 비공개,
  // 클릭으로 펼치는 기능 없음 — PM이 정보를 필터링한다는 걸 고정된 형태로 보여줌).
  const isSummaryOnly = (
    type === 'agent' && currentCondition === 'centralized' &&
    (sender === 'Designer' || sender === 'Engineer')
  );
  enqueue((done) => {
    const stick = isChatNearBottom();  // 새 발화 추가 전, 바닥 근처였는지 한 번만 판단
    if (isSummaryOnly) {
      // PM 아래 — 작은 아바타+이름은 박스 밖, 박스 안엔 PM 질문 인용 + 요약
      const row = document.createElement('div');
      row.className = 'sub-row';
      const avatar = document.createElement('div');
      avatar.className = 'avatar ' + nameClass(sender);
      avatar.textContent = ROLE_ICONS[sender] || displayName[0];
      const col = document.createElement('div');
      col.className = 'sub-col';
      const nameEl = document.createElement('div');
      nameEl.className = 'sub-name ' + nameClass(sender);
      nameEl.textContent = displayName;
      const box = document.createElement('div');
      box.className = 'sub-box';
      if (lastPmText) {
        const quote = document.createElement('div');
        quote.className = 'sub-quote';
        const qName = document.createElement('span');
        qName.className = 'sub-quote-name';
        qName.textContent = 'PM';
        const qText = document.createElement('span');
        qText.className = 'sub-quote-text';
        qText.textContent = lastPmText;
        quote.appendChild(qName);
        quote.appendChild(qText);
        box.appendChild(quote);
      }
      const gist = document.createElement('div');
      gist.className = 'sub-gist';
      gist.textContent = summary || gistOf(content);
      box.appendChild(gist);
      col.appendChild(nameEl);
      col.appendChild(box);
      row.appendChild(avatar);
      row.appendChild(col);
      chat.appendChild(row);
      stickOrIndicate(stick);
      done();
    } else if (type === 'agent' && sender) {
      if (sender === 'PM') lastPmText = content;
      const row = document.createElement('div');
      row.className = 'msg-row';
      const avatar = document.createElement('div');
      avatar.className = 'avatar ' + nameClass(sender);
      avatar.textContent = ROLE_ICONS[sender] || displayName[0];
      const col = document.createElement('div');
      col.className = 'msg-col';
      const nameEl = document.createElement('div');
      nameEl.className = 'msg-name ' + nameClass(sender);
      nameEl.textContent = displayName;
      const bubble = document.createElement('div');
      bubble.className = 'msg agent';
      col.appendChild(nameEl);
      col.appendChild(bubble);
      row.appendChild(avatar);
      row.appendChild(col);
      chat.appendChild(row);
      stickOrIndicate(stick);
      typeText(bubble, content, done, stick);  // 에이전트 발화는 한 글자씩
    } else {
      const div = document.createElement('div');
      div.className = 'msg ' + type;
      div.textContent = content;          // 시스템·사용자 발화는 즉시
      chat.appendChild(div);
      stickOrIndicate(stick);
      done();
    }
  }, sender);
}

function notifyInputReady() {
  // 렌더 큐가 밀린 발화들을 다 타이핑해서 입력창이 실제로 열리는 이 순간을,
  // 서버가 input_request를 쏜 시점 대신 "참가자 대기 시작"으로 기록하기 위한 신호.
  if (!sessionId) return;
  apiRequest(`/api/sessions/${sessionId}/input-ready`, { method: 'POST', body: JSON.stringify({}) }).catch(() => {});
}

function enableInput() {
  waitingForInput = true;
  msgInput.disabled = false;
  sendBtn.disabled = false;
  msgInput.focus();
  statusEl.textContent = '당신의 차례입니다.';
  statusEl.classList.add('active');
}

function disableInput() {
  waitingForInput = false;
  msgInput.disabled = true;
  sendBtn.disabled = true;
  statusEl.textContent = '에이전트가 작업 중...';
  statusEl.classList.remove('active');
}

async function apiRequest(path, options = {}) {
  const headers = {'Content-Type': 'application/json', ...(options.headers || {})};
  if (sessionSecret) headers['X-Session-Token'] = sessionSecret;
  const response = await fetch(path, {...options, headers});
  let body = {};
  try { body = await response.json(); } catch (e) {}
  if (!response.ok) {
    const error = new Error(body.error || `요청 실패 (${response.status})`);
    error.status = response.status;
    error.body = body;
    throw error;
  }
  return body;
}

async function sendMessage() {
  if (!waitingForInput || !sessionId) return;
  const text = msgInput.value;
  if (text) addMsg('user', text, 'Participant');
  msgInput.value = '';
  disableInput();
  try {
    await apiRequest(`/api/sessions/${sessionId}/messages`, {
      method: 'POST',
      body: JSON.stringify({message: text}),
    });
  } catch (error) {
    addMsg('system', error.message);
    enableInput();
  }
}

msgInput.addEventListener('keydown', (e) => {
  if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); sendMessage(); }
});

function showForm() {
  document.getElementById('discussion-area').style.display = 'none';
  document.getElementById('form-area').style.display = 'flex';
  // 채팅 내용을 좌측 패널로 복사
  const dst = document.getElementById('form-chat');
  dst.innerHTML = chat.innerHTML;
  dst.scrollTop = dst.scrollHeight;
}

async function submitForm() {
  const f = {
    concept: document.getElementById('f-concept').value.trim(),
    features: [
      document.getElementById('f-feature1').value.trim(),
      document.getElementById('f-feature2').value.trim(),
      document.getElementById('f-feature3').value.trim(),
    ],
    differentiator: document.getElementById('f-differentiator').value.trim(),
  };
  if (!f.concept || !f.features.every(x => x) || !f.differentiator) {
    alert('모든 항목을 채워주세요.');
    return;
  }
  const button = document.getElementById('form-submit');
  button.disabled = true;
  button.textContent = '제출 중...';
  try {
    await apiRequest(`/api/sessions/${sessionId}/idea`, {
      method: 'POST',
      body: JSON.stringify(f),
    });
    button.textContent = '제출됨';
  } catch (error) {
    button.disabled = false;
    button.textContent = '제출';
    alert(error.message);
  }
}

function handleServerMessage(data) {
  const t = data.type;
  const c = data.content;
  if (t === 'print') {
    const text = (c.objects || []).join(c.sep || ' ');
    if (!text.trim()) return;
    if (text.includes(FORM_REQUEST_MARKER)) {
      enqueue((done) => { showForm(); done(); });  // 마지막 발화 다 찍힌 뒤 양식 전환
      return;
    }
    if (text.includes(TOPIC_MARKER)) {
      const el = document.getElementById('topic-text');
      if (el) el.textContent = text.replace(TOPIC_MARKER, '').trim();
      return;  // 주제는 헤더로, 채팅엔 안 띄움
    }
    addMsg('system', text.replace(/\[시스템\]\s*/g, ''));
  } else if (t === 'text' || t === 'tool_call') {
    const sender = c.sender;
    const content = c.content;
    const summary = c.summary || '';
    if (!content || !content.trim()) return;
    if (sender === 'chat_manager' || sender === 'Participant') return;
    let clean = content.replace(/\s*TERMINATE\s*/g, '').trim();
    clean = stripThink(clean);
    if (clean) addMsg('agent', clean, sender, summary);
  } else if (t === 'input_request') {
    addMsg('waiting', '당신의 차례입니다. (빈칸 = 넘기기)');
    enqueue((done) => { enableInput(); notifyInputReady(); done(); });
  } else if (t === 'tool_response') {
    // tool 결과는 화면에 안 표시 (D/E 답은 별도 메커니즘으로 흘러야 함)
    return;
  }
}

let pendingStart = null;

function openBriefModal() {
  const participantInput = document.getElementById('participant-id');
  const pid = participantInput.value.trim().toUpperCase() || 'P99';
  participantInput.value = pid;
  const condition = document.getElementById('condition-mode').value;
  const task = document.getElementById('task-mode').value;
  pendingStart = { pid, condition, task };
  document.getElementById('modal-topic-text').textContent = BRIEF_TEXT[task] || '';
  document.getElementById('brief-modal').classList.add('show');
}

async function resetToStart() {
  const btn = document.getElementById('reset-btn');
  if (btn) { btn.disabled = true; btn.textContent = '종료 중...'; }
  pollActive = false;
  if (sessionId) {
    try { await apiRequest(`/api/sessions/${sessionId}`, {method: 'DELETE'}); } catch (e) {}
  }
  location.reload();
}

function delay(ms) {
  return new Promise(resolve => setTimeout(resolve, ms));
}

async function pollEvents() {
  pollActive = true;
  while (pollActive && sessionId) {
    try {
      const result = await apiRequest(
        `/api/sessions/${sessionId}/events?after=${lastEventSequence}&wait=20`,
        {method: 'GET'}
      );
      for (const event of result.events || []) {
        lastEventSequence = Math.max(lastEventSequence, event.seq);
        handleServerMessage(event.payload);
      }
      if (['completed', 'cancelled', 'error'].includes(result.status)) {
        pollActive = false;
        disableInput();
        statusEl.textContent = result.status === 'completed' ? '세션 종료' : '세션 중단';
        if (result.status === 'error') addMsg('system', result.error || '세션 실행 중 오류가 발생했습니다.');
      }
    } catch (error) {
      if (!pollActive) break;
      if (error.status === 410) {
        pollActive = false;
        disableInput();
        statusEl.textContent = '서버 재시작 감지 · 아이디어 제출 가능';
        addMsg('system', error.message);
        break;
      }
      statusEl.textContent = '연결 재시도 중...';
      await delay(1200);
    }
  }
}

async function confirmStart() {
  document.getElementById('brief-modal').classList.remove('show');
  const { pid, condition, task } = pendingStart;
  currentCondition = condition;
  const condLabel = condition === 'centralized' ? '조건 1' : '조건 2';

  statusEl.textContent = '서버에 연결 중...';
  try {
    const result = await apiRequest('/api/sessions', {
      method: 'POST',
      body: JSON.stringify({
        participant_id: pid,
        condition,
        task,
      }),
    });
    sessionId = result.session_id;
    sessionSecret = result.session_token;
    lastEventSequence = 0;
    document.getElementById('start-page').style.display = 'none';
    document.getElementById('experiment-page').style.display = 'flex';
    const resolvedParticipantId = result.participant_id || pid || 'P99';
    document.getElementById('session-info').textContent =
      `${resolvedParticipantId} / ${condLabel} / 태스크 ${task}`;
    statusEl.textContent = '연결됨 — 세션 시작';
    pollEvents();
  } catch (error) {
    alert(error.message);
    document.getElementById('start-page').style.display = 'flex';
  }
}
</script>
</body>
</html>"""


def _render_html_page():
    return (HTML_PAGE
            .replace("__FORM_REQUEST_MARKER__", FORM_REQUEST_MARKER)
            .replace("__TOPIC_MARKER__", TOPIC_MARKER)
            .replace("__BRIEFS_JSON__", json.dumps(BRIEFS, ensure_ascii=False)))


class FrontendHandler(http.server.SimpleHTTPRequestHandler):
    server_version = "ExperimentServer/1.0"

    def _send_json(self, status, payload, extra_headers=None):
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        try:
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("X-Robots-Tag", "noindex, nofollow")
            for name, value in (extra_headers or {}).items():
                self.send_header(name, value)
            self.end_headers()
            self.wfile.write(body)
        except (ConnectionAbortedError, ConnectionResetError, BrokenPipeError):
            pass

    def _read_json(self):
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError as error:
            raise ValueError("잘못된 Content-Length입니다.") from error
        if length <= 0 or length > 512 * 1024:
            raise ValueError("요청 본문 크기가 올바르지 않습니다.")
        try:
            body = json.loads(self.rfile.read(length).decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise ValueError("올바른 JSON 요청이 아닙니다.") from error
        if not isinstance(body, dict):
            raise ValueError("JSON 객체가 필요합니다.")
        return body

    def _read_form(self):
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError as error:
            raise ValueError("잘못된 Content-Length입니다.") from error
        if length <= 0 or length > 512 * 1024:
            raise ValueError("요청 본문 크기가 올바르지 않습니다.")
        try:
            raw = self.rfile.read(length).decode("utf-8")
        except UnicodeDecodeError as error:
            raise ValueError("올바른 폼 요청이 아닙니다.") from error
        return {
            key: values[0]
            for key, values in urllib.parse.parse_qs(raw).items()
            if values
        }

    def _redirect(self, location, status=303):
        self.send_response(status)
        self.send_header("Location", location)
        self.send_header("Cache-Control", "no-store")
        self.end_headers()

    def _send_robots_txt(self):
        body = b"User-agent: *\nDisallow: /\n"
        try:
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "public, max-age=86400")
            self.end_headers()
            self.wfile.write(body)
        except (ConnectionAbortedError, ConnectionResetError, BrokenPipeError):
            pass

    def _client_ip(self):
        forwarded = self.headers.get("X-Forwarded-For")
        if forwarded:
            return forwarded.split(",")[0].strip()
        return self.client_address[0]

    def _rate_limited(self):
        ip = self._client_ip()
        now = time.time()
        with _RATE_LIMIT_LOCK:
            window_start, count = _RATE_LIMIT_BUCKETS.get(ip, (now, 0))
            if now - window_start >= _RATE_LIMIT_WINDOW_SECONDS:
                window_start, count = now, 0
            count += 1
            _RATE_LIMIT_BUCKETS[ip] = (window_start, count)
            return count > _RATE_LIMIT_MAX_REQUESTS

    def _require_researcher(self, *, api):
        """로컬 전용 브랜치라 로그인 관문이 없다. 호출부를 그대로 두려고
        함수는 남기고 항상 같은 식별자를 돌려준다."""
        del api
        return LOCAL_RESEARCHER

    def _authorized_session(self, session_id, researcher_email):
        secret = self.headers.get("X-Session-Token", "")
        session = SESSION_REGISTRY.authorize(session_id, secret)
        if session is not None and session.researcher_email == researcher_email:
            return session
        try:
            session = STUDY_STORE.authorize_experiment_session(
                session_id,
                secret,
                researcher_email,
            )
        except StoreError:
            self._send_json(
                503,
                {"error": "세션 저장소에 연결할 수 없습니다. 잠시 후 다시 시도해 주세요."},
            )
            return None
        if session is None:
            self._send_json(401, {"error": "세션 인증에 실패했습니다."})
        return session

    def _serve_survey_asset(self, request_path):
        if request_path == "/survey":
            self.send_response(308)
            self.send_header("Location", "/survey/")
            self.end_headers()
            return True
        if not request_path.startswith("/survey/"):
            return False
        relative = urllib.parse.unquote(request_path[len("/survey/"):]) or "index.html"
        root = os.path.realpath(SURVEY_DIST_DIR)
        target = os.path.realpath(os.path.join(root, relative))
        try:
            inside_root = os.path.commonpath([root, target]) == root
        except ValueError:
            inside_root = False
        if not inside_root or not os.path.isfile(target):
            self._send_json(404, {"error": "설문 파일을 찾을 수 없습니다."})
            return True
        try:
            with open(target, "rb") as source:
                body = source.read()
            content_type = mimetypes.guess_type(target)[0] or "application/octet-stream"
            self.send_response(200)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header(
                "Cache-Control",
                "public, max-age=31536000, immutable"
                if "/assets/" in request_path
                else "no-store",
            )
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("X-Robots-Tag", "noindex, nofollow")
            self.end_headers()
            self.wfile.write(body)
        except (ConnectionAbortedError, ConnectionResetError, BrokenPipeError):
            pass
        return True

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path == "/robots.txt":
            self._send_robots_txt()
            return
        if parsed.path in ("/healthz", "/api/healthz"):
            self._send_json(200, {"status": "ok"})
            return
        if self._rate_limited():
            self._send_json(429, {"error": "요청이 너무 많습니다. 잠시 후 다시 시도해 주세요."})
            return
        researcher_email = self._require_researcher(
            api=parsed.path.startswith("/api/")
        )
        if parsed.path == "/api/admin/submissions":
            self._send_json(
                200,
                {"submissions": STUDY_STORE.list_survey_submissions()},
            )
            return
        if parsed.path == "/api/admin/sessions":
            self._send_json(
                200,
                {"sessions": STUDY_STORE.list_session_summaries()},
            )
            return
        parts = [part for part in parsed.path.split("/") if part]
        if len(parts) == 4 and parts[:2] == ["api", "sessions"] and parts[3] == "events":
            session = self._authorized_session(parts[2], researcher_email)
            if session is None:
                return
            if isinstance(session, PersistedExperimentSession):
                self._send_json(
                    410,
                    {
                        "error": (
                            "서버가 교체되어 진행 중이던 대화는 종료되었습니다. "
                            "작성 중인 최종 아이디어는 그대로 제출할 수 있습니다."
                        ),
                        "recoverable_idea": True,
                    },
                )
                return
            query = urllib.parse.parse_qs(parsed.query)
            try:
                after = max(0, int(query.get("after", ["0"])[0]))
                wait = max(0, min(25, float(query.get("wait", ["20"])[0])))
            except ValueError:
                self._send_json(400, {"error": "이벤트 조회 위치가 올바르지 않습니다."})
                return
            events = session.poll(after=after, wait_seconds=wait)
            self._send_json(200, {
                "events": events,
                "status": session.status,
                "error": session.error,
            })
            return
        if self._serve_survey_asset(parsed.path):
            return
        if parsed.path not in ("/", "/index.html"):
            self._send_json(404, {"error": "찾을 수 없습니다."})
            return
        try:
            body = _render_html_page().encode("utf-8")
            self.send_response(200)
            self.send_header("Content-type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("X-Robots-Tag", "noindex, nofollow")
            self.end_headers()
            self.wfile.write(body)
        except (ConnectionAbortedError, ConnectionResetError, BrokenPipeError):
            pass

    def do_POST(self):
        parsed = urllib.parse.urlparse(self.path)
        parts = [part for part in parsed.path.split("/") if part]
        if self._rate_limited():
            self._send_json(429, {"error": "요청이 너무 많습니다. 잠시 후 다시 시도해 주세요."})
            return
        try:
            researcher_email = self._require_researcher(api=True)
            body = self._read_json()
            if parts == ["api", "sessions"]:
                participant_id = str(body.get("participant_id", "")).strip()
                session = start_session(
                    participant_id,
                    str(body.get("condition", "")),
                    str(body.get("task", "")),
                    researcher_email,
                )
                self._send_json(201, {
                    "session_id": session.id,
                    "session_token": session.secret,
                    "participant_id": session.participant_id,
                })
                return

            if parts == ["api", "survey"]:
                participant_id = str(
                    body.get("participant_id", "")
                ).strip().upper()
                if not participant_id or len(participant_id) > 64:
                    raise ValueError("참가자 번호를 1~64자로 입력해 주세요.")
                environment = body.get("environment")
                round_index = body.get("round_index")
                condition = body.get("condition")
                responses = body.get("responses")
                scores = body.get("scores")
                if environment not in ("web", "vr"):
                    raise ValueError("올바르지 않은 실험 환경입니다.")
                if round_index not in (1, 2):
                    raise ValueError("올바르지 않은 설문 라운드입니다.")
                if condition not in ("condition_1", "condition_2"):
                    raise ValueError("올바르지 않은 실험 조건입니다.")
                if not isinstance(responses, list) or len(responses) > 500:
                    raise ValueError("설문 응답 형식이 올바르지 않습니다.")
                if not isinstance(scores, dict) or len(scores) > 100:
                    raise ValueError("설문 점수 형식이 올바르지 않습니다.")
                survey_payload = {
                    "participant_id": participant_id,
                    "researcher_email": researcher_email,
                    "environment": environment,
                    "round_index": round_index,
                    "condition": condition,
                    "gender": str(body.get("gender", ""))[:40] or None,
                    "age": body.get("age") if isinstance(body.get("age"), int) else None,
                    "design_experience": str(body.get("design_experience", ""))[:80] or None,
                    "llm_experience": str(body.get("llm_experience", ""))[:80] or None,
                    "submitted_at": body.get("submitted_at"),
                    "responses": responses,
                    "scores": scores,
                }
                STUDY_STORE.submit_survey(survey_payload)
                self._send_json(201, {"saved": True})
                return

            if len(parts) == 4 and parts[:2] == ["api", "sessions"]:
                session = self._authorized_session(parts[2], researcher_email)
                if session is None:
                    return
                if parts[3] == "messages":
                    if isinstance(session, PersistedExperimentSession):
                        self._send_json(
                            410,
                            {
                                "error": (
                                    "서버가 교체되어 대화를 계속할 수 없습니다. "
                                    "현재까지의 기록은 저장되어 있습니다."
                                )
                            },
                        )
                        return
                    message = body.get("message")
                    if not isinstance(message, str) or len(message) > 10_000:
                        raise ValueError("메시지는 10,000자 이하 문자열이어야 합니다.")
                    session.submit_message(message)
                    self._send_json(202, {"accepted": True})
                    return
                if parts[3] == "input-ready":
                    if not isinstance(session, PersistedExperimentSession):
                        session.record_intervention_wait_start()
                    self._send_json(202, {"accepted": True})
                    return
                if parts[3] == "idea":
                    concept = body.get("concept")
                    features = body.get("features")
                    differentiator = body.get("differentiator")
                    if (
                        not isinstance(concept, str)
                        or not isinstance(features, list)
                        or len(features) != 3
                        or not all(isinstance(item, str) and item.strip() for item in features)
                        or not isinstance(differentiator, str)
                        or not concept.strip()
                        or not differentiator.strip()
                    ):
                        raise ValueError("최종 아이디어의 모든 항목을 채워 주세요.")
                    if len(json.dumps(body, ensure_ascii=False)) > 20_000:
                        raise ValueError("최종 아이디어가 너무 깁니다.")
                    form = {
                        "concept": concept.strip(),
                        "features": [item.strip() for item in features],
                        "differentiator": differentiator.strip(),
                        "filled_at": datetime.datetime.now(
                            datetime.timezone.utc
                        ).isoformat(),
                    }
                    if isinstance(session, PersistedExperimentSession):
                        STUDY_STORE.record_recovered_idea(session, form)
                    else:
                        session.submit_form(form)
                    self._send_json(202, {"accepted": True})
                    return
            self._send_json(404, {"error": "찾을 수 없습니다."})
        except SessionCancelled as error:
            self._send_json(409, {"error": str(error)})
        except (ValueError, RuntimeError) as error:
            self._send_json(400, {"error": str(error)})
        except StoreError:
            self._send_json(503, {"error": "데이터 저장소에 연결할 수 없습니다."})
        except Exception:
            traceback.print_exc()
            self._send_json(500, {"error": "서버 오류가 발생했습니다."})

    def do_DELETE(self):
        parsed = urllib.parse.urlparse(self.path)
        researcher_email = self._require_researcher(api=True)
        parts = [part for part in parsed.path.split("/") if part]
        if len(parts) == 3 and parts[:2] == ["api", "sessions"]:
            session = self._authorized_session(parts[2], researcher_email)
            if session is None:
                return
            if isinstance(session, PersistedExperimentSession):
                self._send_json(
                    410,
                    {"error": "이미 종료된 서버 세션입니다."},
                )
                return
            try:
                session.cancel()
                self._send_json(200, {"cancelled": True})
            except Exception:
                self._send_json(500, {"error": "세션을 종료하지 못했습니다."})
            return
        self._send_json(404, {"error": "찾을 수 없습니다."})

    def log_message(self, format, *args):
        pass


if __name__ == "__main__":
    import signal
    import sys
    # `python web.py` 실행 시 이 파일은 '__main__' 모듈이 된다. 그러면 centralized.py의
    # `from web import ...`가 별도 web 인스턴스를 새로 만들어, 거기 _msg_file이 None이라
    # _log_msg가 조용히 아무것도 안 썼다 (→ PM 라우팅·종합·참가자 발언이 로그에서 누락,
    # hook이 잡은 내부 트리거만 'PM'으로 남았음). 같은 인스턴스를 'web' 이름으로 등록해
    # 모든 로깅이 동일한 파일 핸들을 쓰도록 한다.
    sys.modules["web"] = sys.modules[__name__]

    try:
        import colorama
        colorama.init()
    except ImportError:
        pass

    try:
        print("실험 서버 시작 중...")
        print()
        runtime_logging.start(logger=ContextRuntimeLogger(STUDY_STORE))
        port = int(os.getenv("PORT", "8000"))
        httpd = http.server.ThreadingHTTPServer(("0.0.0.0", port), FrontendHandler)
        httpd.daemon_threads = True
        print(f"http://127.0.0.1:{port} 을 열어주세요.")
        print(f"동시 세션 상한: {SESSION_REGISTRY.max_active_sessions}")
        print("종료: Ctrl+C")

        stopping = threading.Event()

        def graceful_stop(*args):
            if stopping.is_set():
                return
            stopping.set()
            print("\n종료 중...")
            threading.Thread(target=httpd.shutdown, daemon=True).start()

        signal.signal(signal.SIGINT, graceful_stop)
        signal.signal(signal.SIGTERM, graceful_stop)

        try:
            httpd.serve_forever()
        finally:
            SESSION_REGISTRY.close_all()
            runtime_logging.stop()
            STUDY_STORE.close()
            httpd.server_close()
    except Exception as e:
        print(f"\n오류 발생: {e}")
        raise

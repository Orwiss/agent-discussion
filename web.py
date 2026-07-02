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
import http.server
import datetime
import autogen
import autogen.runtime_logging as logging
from autogen.io.websockets import IOWebsockets

from config_uniform import (
    llm_config_pm, llm_config_designer, llm_config_engineer,
)
from agents.simple_agents import create_pm, create_designer, create_engineer
from meeting.centralized import run_centralized_discussion
from meeting.decentralized import run_decentralized_discussion
from meeting.guardrails import clean_message_hook, clean_history_hook

LOG_DIR = os.path.join(os.path.dirname(__file__), "logs", "experiment")
os.makedirs(LOG_DIR, exist_ok=True)

BRIEFS = {
    "A": "대학 신입생이 학교생활에 적응하도록 돕는 모바일 앱의 핵심 컨셉과 주요 기능을 제안하라.",
    "B": "자취를 시작하는 청년이 동네 생활에 정착하도록 돕는 모바일 앱의 핵심 컨셉과 주요 기능을 제안하라.",
}

# 양식 단계 진입 알림 마커 (백엔드 → 프론트)
FORM_REQUEST_MARKER = "[FORM_REQUEST]"
# 주제(brief)를 헤더로 보내는 마커 (채팅에 시스템 메시지로 안 띄움)
TOPIC_MARKER = "[TOPIC]"

# === 세션 상태 (파일 핸들·메타) ===
_log_file = None
_msg_file = None
_session_meta = None

# === 누적 CSV ===
MESSAGES_CSV = os.path.join(LOG_DIR, "messages.csv")
IDEAS_CSV = os.path.join(LOG_DIR, "ideas.csv")
SESSIONS_CSV = os.path.join(LOG_DIR, "sessions.csv")


def _init_session_files(participant_id, condition, task):
    global _log_file, _msg_file, _session_meta
    ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    base = f"P{participant_id}_{condition}_task{task}_{ts}"
    _log_file = open(os.path.join(LOG_DIR, f"{base}_log.jsonl"), "w", encoding="utf-8")
    _msg_file = open(os.path.join(LOG_DIR, f"{base}_messages.jsonl"), "w", encoding="utf-8")
    _session_meta = {
        "participant_id": participant_id,
        "condition": condition,
        "task": task,
        "started_at": datetime.datetime.now().isoformat(),
        "base": base,
        "phase": None,
        "utterances_total": 0,
        "by_speaker": {},
        "interventions": 0,
    }
    return base


def log_event(event_type, data):
    """전체 이벤트 jsonl 로그."""
    entry = {
        "time": datetime.datetime.now().isoformat(),
        "event": event_type,
        "data": data,
    }
    print(f"  [{event_type}] {data}")
    if _log_file:
        _log_file.write(json.dumps(entry, ensure_ascii=False) + "\n")
        _log_file.flush()
    # phase 이벤트는 세션 메타에도 반영
    if event_type == "phase_start" and _session_meta and isinstance(data, dict):
        _session_meta["phase"] = data.get("phase")


def log_message(speaker, content):
    """대화 jsonl 로그 (분석 직행용)."""
    if not _msg_file:
        return
    entry = {
        "time": datetime.datetime.now().isoformat(),
        "speaker": speaker,
        "content": content,
        "phase": _session_meta.get("phase") if _session_meta else None,
    }
    _msg_file.write(json.dumps(entry, ensure_ascii=False) + "\n")
    _msg_file.flush()
    if _session_meta:
        if speaker == "Participant":
            _session_meta["interventions"] += 1
        else:
            _session_meta["utterances_total"] += 1
            _session_meta["by_speaker"][speaker] = _session_meta["by_speaker"].get(speaker, 0) + 1


def _append_messages_csv(speaker, content):
    """누적 messages.csv에 한 줄 추가."""
    if not _session_meta:
        return
    new_file = not os.path.exists(MESSAGES_CSV)
    with open(MESSAGES_CSV, "a", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        if new_file:
            w.writerow(["participant_id", "condition", "task", "phase", "speaker", "time", "content"])
        w.writerow([
            _session_meta["participant_id"],
            _session_meta["condition"],
            _session_meta["task"],
            _session_meta.get("phase") or "",
            speaker,
            datetime.datetime.now().isoformat(),
            content,
        ])


def _append_ideas_csv(form_data):
    if not _session_meta or not form_data:
        return
    new_file = not os.path.exists(IDEAS_CSV)
    with open(IDEAS_CSV, "a", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        if new_file:
            w.writerow(["participant_id", "condition", "task", "concept",
                        "feature_1", "feature_2", "feature_3", "differentiator", "filled_at"])
        features = (form_data.get("features") or []) + ["", "", ""]
        features = features[:3]
        w.writerow([
            _session_meta["participant_id"],
            _session_meta["condition"],
            _session_meta["task"],
            form_data.get("concept", ""),
            features[0], features[1], features[2],
            form_data.get("differentiator", ""),
            form_data.get("filled_at", ""),
        ])


def _append_sessions_csv(summary):
    new_file = not os.path.exists(SESSIONS_CSV)
    with open(SESSIONS_CSV, "a", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        if new_file:
            w.writerow(["participant_id", "condition", "task", "started_at", "ended_at",
                        "utterances_total", "interventions", "PM", "Designer", "Engineer"])
        m = summary["meta"]
        c = summary["counts"]
        by = c.get("by_speaker", {})
        w.writerow([
            m["participant_id"], m["condition"], m["task"],
            m["started_at"], m["ended_at"],
            c["utterances_total"], c["interventions"],
            by.get("PM", 0), by.get("Designer", 0), by.get("Engineer", 0),
        ])


def _close_session_files(form_data=None):
    global _log_file, _msg_file, _session_meta
    if not _session_meta:
        return
    base = _session_meta["base"]

    # idea.json + 누적
    if form_data:
        idea_path = os.path.join(LOG_DIR, f"{base}_idea.json")
        with open(idea_path, "w", encoding="utf-8") as f:
            json.dump(form_data, f, ensure_ascii=False, indent=2)
        _append_ideas_csv(form_data)

    # summary.json + 누적
    _session_meta["ended_at"] = datetime.datetime.now().isoformat()
    summary = {
        "meta": {k: _session_meta[k] for k in
                 ("participant_id", "condition", "task", "started_at", "ended_at", "base")},
        "counts": {
            "utterances_total": _session_meta["utterances_total"],
            "by_speaker": _session_meta["by_speaker"],
            "interventions": _session_meta["interventions"],
        },
        "form": form_data,
    }
    summary_path = os.path.join(LOG_DIR, f"{base}_summary.json")
    with open(summary_path, "w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)
    _append_sessions_csv(summary)

    if _log_file:
        _log_file.close()
        _log_file = None
    if _msg_file:
        _msg_file.close()
        _msg_file = None
    _session_meta = None


# === 메시지 캡처 hook — 모든 발화를 messages 로그에 기록 ===
def _capture_msg_hook(sender, message, recipient, silent):
    content = ""
    if isinstance(message, str):
        content = message
    elif isinstance(message, dict):
        content = message.get("content", "") or ""
    name = getattr(sender, "name", str(sender))
    if content and content.strip():
        log_message(name, content.strip())
        _append_messages_csv(name, content.strip())
    return message


def on_connect(iostream: IOWebsockets) -> None:
    """클라이언트 연결 시 단일 세션 실행."""
    original_send = iostream._websocket.send

    def logging_send(data):
        try:
            parsed = json.loads(data) if isinstance(data, str) else data
            log_event("ws_send", parsed if isinstance(parsed, dict) else data)
        except (json.JSONDecodeError, TypeError):
            log_event("ws_send", data)
        return original_send(data)
    iostream._websocket.send = logging_send

    # 설정 수신
    try:
        first_msg = iostream._websocket.recv()
        settings = json.loads(first_msg)
        participant_id = settings.get("participant_id", "unknown")
        condition = settings.get("condition", "decentralized")
        task = settings.get("task", "A")
    except Exception:
        participant_id = "unknown"
        condition = "decentralized"
        task = "A"

    if condition not in ("centralized", "decentralized"):
        condition = "decentralized"

    brief = BRIEFS.get(task, BRIEFS["A"])
    base = _init_session_files(participant_id, condition, task)
    log_event("session_start", {
        "participant_id": participant_id,
        "condition": condition,
        "task": task,
        "base": base,
    })

    session_log_path = os.path.join(LOG_DIR, f"{base}_ag2.log")
    _ag2_logging_active = False
    try:
        logging.start(logger_type="file", config={"filename": session_log_path})
        _ag2_logging_active = True
    except Exception as e:
        # AG2 runtime_logging이 cwd 권한 문제 등으로 실패해도 세션은 진행
        log_event("ag2_logging_skip", {"error": str(e)})

    iostream.print(f"{TOPIC_MARKER}{brief}")  # 주제는 헤더로 (채팅엔 안 띄움)

    form_data = None
    try:
        _run_session(iostream, condition, brief)
        form_data = _collect_form(iostream)
    except Exception as e:
        iostream.print(f"[오류] {str(e)}")
        log_event("error", {"message": str(e)})
    finally:
        if _ag2_logging_active:
            try:
                logging.stop()
            except Exception:
                pass
        iostream.print("\n[시스템] 세션이 종료되었습니다.")
        log_event("session_end", {})
        _close_session_files(form_data)


def _run_session(iostream, condition, brief):
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

    # 가드레일 (양 조건 공통)
    for agent in agents:
        agent.register_hook("process_message_before_send", clean_message_hook)
        agent.register_hook("process_all_messages_before_reply", clean_history_hook)

    if condition == "centralized":
        # Centralized: _log_msg로 직접 로깅 (캡처 hook 미사용 — 내부 트리거 발화 오염 방지)
        asyncio.run(run_centralized_discussion(pm, designer, engineer, user, brief))
    else:
        # Decentralized: groupchat이라 발화 캡처를 hook으로 처리
        for agent in agents:
            agent.register_hook("process_message_before_send", _capture_msg_hook)
        user.register_hook("process_message_before_send", _capture_msg_hook)
        run_decentralized_discussion(agents, user, brief, iostream=iostream)


def _collect_form(iostream):
    """토론 종료 후 양식 단계.
    프론트에 FORM_REQUEST_MARKER 보내면 UI 분할 → 양식 표시.
    사용자가 제출하면 JSON 문자열로 한 줄 응답."""
    iostream.print(FORM_REQUEST_MARKER)
    try:
        raw = iostream._websocket.recv()
        form = json.loads(raw)
        form["filled_at"] = datetime.datetime.now().isoformat()
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
<title>디자인 아이디에이션 실험</title>
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

  #discussion-area { flex: 1; display: flex; flex-direction: column; overflow: hidden; }
  #chat {
    flex: 1; overflow-y: auto; padding: 28px;
    display: flex; flex-direction: column; gap: 14px;
  }
  @keyframes msgIn { from { opacity: 0; } to { opacity: 1; } }
  .msg {
    max-width: 78%; padding: 16px 20px; border-radius: var(--radius);
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
    border-bottom-left-radius: 5px; box-shadow: var(--shadow);
  }
  /* Centralized 조건: 디자이너·엔지니어는 PM 아래 보조 발화로 (들여쓰기 + 연하게) */
  .msg.agent.subagent {
    position: relative;
    margin-left: 56px; max-width: 64%; font-size: 16px;
    background: var(--bg-2); border: 1px solid var(--border-soft); box-shadow: none;
  }
  /* PM 말풍선에서 내려오는 스레드 레일 (유튜브 답글 스타일) */
  .msg.agent.subagent::before {
    content: ''; position: absolute; left: -24px; top: -14px;
    width: 2px; height: 28px; background: #b3bccb;
  }
  /* 뒤에 보조 발화가 더 있으면 레일을 아래까지 이어줌 (연속된 한 줄로) */
  .msg.agent.subagent:has(+ .subagent)::before {
    height: calc(100% + 28px);
  }
  /* 레일에서 말풍선으로 꺾여 들어가는 가지 */
  .msg.agent.subagent::after {
    content: ''; position: absolute; left: -24px; top: 14px;
    width: 22px; height: 14px;
    border-left: 2px solid #b3bccb; border-bottom: 2px solid #b3bccb;
    border-bottom-left-radius: 10px;
  }
  /* 클로드코드식: 보조 발화는 접힌 채로, 클릭하면 펼쳐짐 */
  .subagent .sub-head { display: flex; align-items: center; gap: 9px; cursor: pointer; }
  .subagent .sub-head .name { margin-bottom: 0; flex-shrink: 0; }
  .subagent .sub-gist {
    flex: 1; min-width: 0; color: var(--muted); font-size: 14px; font-weight: 400;
    white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
  }
  .subagent .sub-chevron {
    flex-shrink: 0; color: var(--muted-2); font-size: 10px; transition: transform .15s ease;
  }
  .subagent:not(.collapsed) .sub-chevron { transform: rotate(90deg); }
  .subagent:not(.collapsed) .sub-gist { display: none; }
  .subagent .sub-full { margin-top: 9px; }
  .subagent.collapsed .sub-full { display: none; }
  .msg .name {
    font-weight: 700; font-size: 14px; margin-bottom: 6px;
    display: flex; align-items: center; gap: 7px; letter-spacing: 0.01em;
  }
  .msg .name::before {
    content: ''; width: 8px; height: 8px; border-radius: 50%;
    background: currentColor; display: inline-block;
  }
  .name.pm { color: var(--pm); }
  .name.designer { color: var(--designer); }
  .name.engineer { color: var(--engineer); }
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
  #input-area input {
    flex: 1; padding: 14px 18px; background: var(--panel); border: 1px solid var(--border);
    border-radius: var(--radius-sm); color: var(--text); font-size: 17px; outline: none;
    transition: border-color .15s, box-shadow .15s;
  }
  #input-area input:focus { border-color: var(--accent); box-shadow: 0 0 0 3px rgba(79,70,229,0.15); }
  #input-area input:disabled { opacity: 0.55; }
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
    <input id="participant-id" type="text" placeholder="예: 01">
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
    <select id="task-mode">
      <option value="A">태스크 A</option>
      <option value="B">태스크 B</option>
    </select>
  </div>
  <button onclick="startExperiment()">세션 시작</button>
</div>

<div id="experiment-page">
  <header>
    <div class="title">
      <h1>디자인 아이디에이션</h1>
      <p id="topic"><span class="topic-label">주제</span><span id="topic-text"></span></p>
    </div>
    <div class="info" id="session-info"></div>
  </header>

  <div id="discussion-area">
    <div id="chat"></div>
    <div id="status">대기 중...</div>
    <div id="input-area">
      <input id="msg" type="text" placeholder="의견을 입력하세요 (Enter 전송, 빈칸 = 넘기기)" disabled>
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
const chat = document.getElementById('chat');
const msgInput = document.getElementById('msg');
const sendBtn = document.getElementById('send');
const statusEl = document.getElementById('status');
let ws = null;
let waitingForInput = false;
let lastMsgHash = '';
let currentCondition = 'centralized';

// 발화 렌더 큐 — 메시지가 겹치지 않게 한 번에 하나씩 타이핑(타자기 효과)
const TYPE_SPEED_MS = 30;
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
  if (item.sender && lastRenderSender !== null && item.sender !== lastRenderSender) {
    setTimeout(run, MESSAGE_GAP_MS);  // 발화자 전환 시 잠깐 텀
  } else {
    run();
  }
}
function enqueue(fn, sender) { renderQueue.push({ fn, sender: sender || null }); processQueue(); }
// 전체 텍스트로 말풍선 높이를 미리 잡아두고, 안 보이는 부분만 투명 처리해서
// 한 글자씩 드러냄 → 타이핑 중 영역이 커지거나 스크롤이 튀지 않음.
function typeText(node, text, done) {
  const shown = document.createElement('span');
  const rest = document.createElement('span');
  rest.style.visibility = 'hidden';
  rest.textContent = text;
  node.appendChild(shown);
  node.appendChild(rest);
  chat.scrollTop = chat.scrollHeight;  // 말풍선 등장 시 1회만 스크롤
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
  const isSubAgent = (
    type === 'agent' && currentCondition === 'centralized' &&
    (sender === 'Designer' || sender === 'Engineer')
  );
  enqueue((done) => {
    const div = document.createElement('div');
    div.className = 'msg ' + type;
    if (isSubAgent) {
      // 클로드코드식: 접힌 헤더(이름 + 한 줄 요약 + 화살표), 클릭하면 전문 펼침
      div.classList.add('subagent', 'collapsed');
      const head = document.createElement('div');
      head.className = 'sub-head';
      const nameSpan = document.createElement('span');
      nameSpan.className = 'name ' + nameClass(sender);
      nameSpan.textContent = displayName;
      const gistSpan = document.createElement('span');
      gistSpan.className = 'sub-gist';
      gistSpan.textContent = summary || gistOf(content);
      const chev = document.createElement('span');
      chev.className = 'sub-chevron';
      chev.textContent = '▶';
      head.appendChild(nameSpan);
      head.appendChild(gistSpan);
      head.appendChild(chev);
      const full = document.createElement('div');
      full.className = 'sub-full';
      full.textContent = content;
      head.addEventListener('click', () => {
        div.classList.toggle('collapsed');
        chat.scrollTop = chat.scrollHeight;
      });
      div.appendChild(head);
      div.appendChild(full);
      chat.appendChild(div);
      chat.scrollTop = chat.scrollHeight;
      done();
    } else if (type === 'agent' && sender) {
      const nameSpan = document.createElement('span');
      nameSpan.className = 'name ' + nameClass(sender);
      nameSpan.textContent = displayName;
      div.appendChild(nameSpan);
      const textNode = document.createElement('span');
      div.appendChild(textNode);
      chat.appendChild(div);
      chat.scrollTop = chat.scrollHeight;
      typeText(textNode, content, done);  // 에이전트 발화는 한 글자씩
    } else {
      div.textContent = content;          // 시스템·사용자 발화는 즉시
      chat.appendChild(div);
      chat.scrollTop = chat.scrollHeight;
      done();
    }
  }, sender);
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

function sendMessage() {
  if (!waitingForInput || !ws) return;
  const text = msgInput.value;
  ws.send(text);
  if (text) addMsg('user', text, 'Participant');
  msgInput.value = '';
  disableInput();
}

msgInput.addEventListener('keydown', (e) => {
  if (e.key === 'Enter') { e.preventDefault(); sendMessage(); }
});

function showForm() {
  document.getElementById('discussion-area').style.display = 'none';
  document.getElementById('form-area').style.display = 'flex';
  // 채팅 내용을 좌측 패널로 복사
  const dst = document.getElementById('form-chat');
  dst.innerHTML = chat.innerHTML;
  dst.scrollTop = dst.scrollHeight;
}

function submitForm() {
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
  ws.send(JSON.stringify(f));
  document.getElementById('form-submit').disabled = true;
  document.getElementById('form-submit').textContent = '제출됨';
}

function handleWsMessage(raw) {
  let data;
  try { data = JSON.parse(raw); } catch {
    if (raw.includes('Provide feedback') || raw.includes('Replying as')) {
      addMsg('waiting', '당신의 차례입니다. (빈칸 = 넘기기)');
      enqueue((done) => { enableInput(); done(); });  // 앞 발화 타이핑 끝난 뒤 입력 활성화
    } else if (raw.trim()) {
      addMsg('system', raw);
    }
    return;
  }
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
  } else if (t === 'tool_response') {
    // tool 결과는 화면에 안 표시 (D/E 답은 별도 메커니즘으로 흘러야 함)
    return;
  }
}

function startExperiment() {
  const pid = document.getElementById('participant-id').value.trim();
  if (!pid) { alert('참가자 번호를 입력해주세요.'); return; }
  const condition = document.getElementById('condition-mode').value;
  currentCondition = condition;
  const task = document.getElementById('task-mode').value;
  const condLabel = condition === 'centralized' ? '조건 1' : '조건 2';

  document.getElementById('start-page').style.display = 'none';
  document.getElementById('experiment-page').style.display = 'flex';
  document.getElementById('session-info').textContent = `P${pid} / ${condLabel} / 태스크 ${task}`;

  statusEl.textContent = '서버에 연결 중...';
  ws = new WebSocket('ws://127.0.0.1:8765');
  ws.onopen = () => {
    ws.send(JSON.stringify({participant_id: pid, condition: condition, task: task}));
    statusEl.textContent = '연결됨 — 세션 시작';
  };
  ws.onmessage = (event) => handleWsMessage(event.data);
  ws.onclose = () => { statusEl.textContent = '세션 종료'; disableInput(); addMsg('system', '세션이 종료되었습니다.'); };
  ws.onerror = () => { statusEl.textContent = '연결 오류'; addMsg('system', '서버 연결에 실패했습니다.'); };
}
</script>
</body>
</html>"""


def _render_html_page():
    return (HTML_PAGE
            .replace("__FORM_REQUEST_MARKER__", FORM_REQUEST_MARKER)
            .replace("__TOPIC_MARKER__", TOPIC_MARKER))


class FrontendHandler(http.server.SimpleHTTPRequestHandler):
    def do_GET(self):
        try:
            self.send_response(200)
            self.send_header("Content-type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(_render_html_page().encode("utf-8"))
        except (ConnectionAbortedError, ConnectionResetError, BrokenPipeError):
            pass

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
        with IOWebsockets.run_server_in_thread(
            host="127.0.0.1",
            port=8765,
            on_connect=on_connect,
        ) as ws_uri:
            print(f"웹소켓: {ws_uri}")

            httpd = http.server.HTTPServer(("127.0.0.1", 8000), FrontendHandler)
            print("http://127.0.0.1:8000 을 열어주세요.")
            print("종료: Ctrl+C")

            def force_exit(*args):
                _close_session_files()
                print("\n종료")
                os._exit(0)

            signal.signal(signal.SIGINT, force_exit)
            signal.signal(signal.SIGTERM, force_exit)

            try:
                httpd.serve_forever()
            except (KeyboardInterrupt, SystemExit):
                pass
            finally:
                _close_session_files()
                httpd.shutdown()
                os._exit(0)
    except Exception as e:
        print(f"\n오류 발생: {e}")
        input("\nEnter를 누르면 종료됩니다...")

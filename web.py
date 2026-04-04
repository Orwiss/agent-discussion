"""
실험용 웹 기반 디자인 아이디에이션 시스템
python web.py 로 실행 → 브라우저에서 http://127.0.0.1:8000

세션 단위 실행:
- 실험자가 참가자 번호, 조건(독립/토론), 태스크(A/B) 선택
- 한 세션 완료 → 설문/휴식 → 다시 접속해서 다른 조건+태스크로 세션 2
모델: 프리미엄 고정
로그: 실시간 자동 저장
"""
import json
import os
import http.server
import datetime
import autogen
import autogen.runtime_logging as logging
from autogen.io.websockets import IOWebsockets

from config_premium import (
    llm_config_ux, llm_config_visual,
    llm_config_engineer, llm_config_selector,
)

from agents.simple_agents import create_simple_ux, create_simple_visual, create_simple_engineer
from meeting.stage2 import run_stage2_discussion
from meeting.independent import run_stage2_independent
from meeting.guardrails import clean_message_hook, clean_history_hook

# 로그 디렉토리
LOG_DIR = os.path.join(os.path.dirname(__file__), "logs", "experiment")
os.makedirs(LOG_DIR, exist_ok=True)

# 고정 브리프 (시나리오 미확정 — 테스트용 플레이스홀더)
BRIEFS = {
    "A": (
        "대학 신입생이 학교생활에 적응하도록 돕는 모바일 앱의 "
        "핵심 컨셉과 주요 기능을 제안하라."
    ),
    "B": (
        "자취를 시작하는 청년이 동네 생활에 정착하도록 돕는 모바일 앱의 "
        "핵심 컨셉과 주요 기능을 제안하라."
    ),
}

# 실시간 로그 파일 핸들 — 다른 모듈에서도 import 가능
_log_file = None


def _init_log(participant_id, condition, task):
    """실시간 로그 파일 초기화"""
    global _log_file
    timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = f"P{participant_id}_{condition}_task{task}_{timestamp}.jsonl"
    _log_file = open(os.path.join(LOG_DIR, filename), "w", encoding="utf-8")
    return filename


def log_event(event_type, data):
    """이벤트를 실시간으로 파일 + 콘솔에 기록. 다른 모듈에서도 사용."""
    entry = {
        "time": datetime.datetime.now().isoformat(),
        "event": event_type,
        "data": data,
    }
    # 콘솔 출력
    print(f"  [{event_type}] {data}")
    # 파일 기록
    if _log_file:
        _log_file.write(json.dumps(entry, ensure_ascii=False) + "\n")
        _log_file.flush()


def _close_log():
    """로그 파일 닫기"""
    global _log_file
    if _log_file:
        _log_file.close()
        _log_file = None


def on_connect(iostream: IOWebsockets) -> None:
    """클라이언트 연결 시 단일 세션 실행"""
    # 웹소켓 메시지 로깅
    original_send = iostream._websocket.send
    def logging_send(data):
        try:
            parsed = json.loads(data) if isinstance(data, str) else data
            log_event("ws_send", parsed if isinstance(parsed, dict) else data)
        except (json.JSONDecodeError, TypeError):
            log_event("ws_send", data)
        return original_send(data)
    iostream._websocket.send = logging_send

    # 설정 수신 (JSON: {participant_id, condition, task})
    try:
        first_msg = iostream._websocket.recv()
        settings = json.loads(first_msg)
        participant_id = settings.get("participant_id", "unknown")
        condition = settings.get("condition", "independent")
        task = settings.get("task", "A")
    except Exception:
        participant_id = "unknown"
        condition = "independent"
        task = "A"

    brief = BRIEFS.get(task, BRIEFS["A"])
    cond_label = "토론" if condition == "discussion" else "독립"

    # 로그 초기화
    log_filename = _init_log(participant_id, condition, task)
    log_event("session_start", {
        "participant_id": participant_id,
        "condition": condition,
        "task": task,
        "log_file": log_filename,
    })

    # AG2 runtime logging
    session_log = os.path.join(LOG_DIR, f"ag2_P{participant_id}_{condition}_{datetime.datetime.now().strftime('%Y%m%d_%H%M%S')}.log")
    logging.start(logger_type="file", config={"filename": session_log})

    type_label = "유형 2" if condition == "discussion" else "유형 1"
    # 조건 안내 (조작적 설명만)
    if condition == "discussion":
        iostream.print("[시스템] 이번 세션에서는 에이전트들이 서로의 의견을 참고하며 작업합니다.")
    else:
        iostream.print("[시스템] 이번 세션에서는 에이전트들이 각각 독립적으로 작업합니다.")

    # 주제 안내
    iostream.print(f"\n[주제]\n{brief}\n")
    iostream.print("[시스템] 에이전트들이 각자의 전문 영역에서 아이디어를 제안합니다. 6발화마다 의견을 입력할 수 있습니다.")

    try:
        _run_session(iostream, condition, brief)
    except Exception as e:
        iostream.print(f"[오류] {str(e)}")
        log_event("error", {"message": str(e)})
    finally:
        logging.stop()
        iostream.print("\n[시스템] 세션이 종료되었습니다.")
        log_event("session_end", {})
        _close_log()


def _run_session(iostream, condition, brief):
    """단일 조건 세션 실행"""
    user = autogen.UserProxyAgent(
        name="Participant",
        human_input_mode="ALWAYS",
        code_execution_config=False,
        is_termination_msg=lambda m: "TERMINATE" in m.get("content", ""),
    )

    is_discussion = condition == "discussion"
    ux = create_simple_ux(llm_config_ux, brief=brief, discussion=is_discussion)
    visual = create_simple_visual(llm_config_visual, brief=brief, discussion=is_discussion)
    engineer = create_simple_engineer(llm_config_engineer, brief=brief, discussion=is_discussion)
    agents = [ux, visual, engineer]

    # 가드레일 등록 (양 조건 동일)
    for agent in agents:
        agent.register_hook("process_message_before_send", clean_message_hook)
        agent.register_hook("process_all_messages_before_reply", clean_history_hook)

    if condition == "discussion":
        result = run_stage2_discussion(agents, user, brief, iostream=iostream)
        summary = result.summary if result.summary else "요약 없음"
    else:
        result = run_stage2_independent(agents, user, brief, iostream=iostream)
        summary = result.get("summary", "요약 없음") if isinstance(result, dict) else "요약 없음"

    iostream.print(f"\n[시스템] === 최종 요약 ===\n{summary}")
    log_event("session_summary", {"summary": summary})


# === HTML 프론트엔드 ===
HTML_PAGE = r"""<!DOCTYPE html>
<html lang="ko">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>디자인 아이디에이션 실험</title>
<style>
  * { margin: 0; padding: 0; box-sizing: border-box; }
  body {
    font-family: 'Segoe UI', sans-serif;
    background: #0f172a;
    color: #e2e8f0;
    height: 100vh;
    display: flex;
    flex-direction: column;
  }
  #start-page {
    flex: 1; display: flex; flex-direction: column;
    align-items: center; justify-content: center; gap: 24px;
  }
  #start-page h1 { font-size: 28px; font-weight: 700; }
  #start-page p { color: #94a3b8; font-size: 14px; max-width: 500px; text-align: center; line-height: 1.6; }
  .form-group { display: flex; flex-direction: column; gap: 8px; align-items: center; }
  .form-group label { font-size: 14px; color: #94a3b8; }
  .form-group input, .form-group select {
    padding: 10px 16px; background: #1e293b; border: 1px solid #475569;
    border-radius: 8px; color: #e2e8f0; font-size: 16px; width: 240px; text-align: center;
  }
  #start-page button {
    padding: 14px 40px; background: #6366f1; color: white; border: none;
    border-radius: 8px; cursor: pointer; font-size: 16px; font-weight: 600; margin-top: 16px;
  }
  #start-page button:hover { background: #4f46e5; }

  #experiment-page { flex: 1; display: none; flex-direction: column; overflow: hidden; }
  header {
    padding: 16px 24px; background: #1e293b; border-bottom: 1px solid #334155;
    display: flex; justify-content: space-between; align-items: center;
    flex-shrink: 0;
  }
  header .title h1 { font-size: 20px; font-weight: 600; }
  header .title p { font-size: 13px; color: #94a3b8; margin-top: 4px; }
  header .info { font-size: 13px; color: #94a3b8; }
  #chat {
    flex: 1; overflow-y: auto; padding: 16px 24px;
    display: flex; flex-direction: column; gap: 8px;
  }
  .msg {
    max-width: 85%; padding: 10px 14px; border-radius: 12px;
    font-size: 14px; line-height: 1.6; white-space: pre-wrap; word-break: break-word;
  }
  .msg.system {
    align-self: center; background: #1e293b; color: #94a3b8;
    font-size: 13px; border: 1px solid #334155; text-align: center; max-width: 90%;
  }
  .msg.user { align-self: flex-end; background: #3b82f6; color: white; }
  .msg.agent { align-self: flex-start; background: #1e293b; border: 1px solid #334155; }
  .msg .name { font-weight: 700; font-size: 12px; margin-bottom: 4px; display: block; }
  .name.ux { color: #f472b6; }
  .name.visual { color: #fb923c; }
  .name.engineer { color: #4ade80; }
  .msg.waiting {
    align-self: center; background: #312e81; border: 1px solid #4f46e5;
    color: #c7d2fe; font-size: 13px;
  }
  #input-area {
    padding: 12px 24px; background: #1e293b; border-top: 1px solid #334155;
    display: flex; gap: 8px; flex-shrink: 0;
  }
  #input-area input {
    flex: 1; padding: 10px 14px; background: #0f172a;
    border: 1px solid #334155; border-radius: 8px;
    color: #e2e8f0; font-size: 14px; outline: none;
  }
  #input-area input:focus { border-color: #6366f1; }
  #input-area input:disabled { opacity: 0.5; }
  #input-area button {
    padding: 10px 20px; background: #6366f1; color: white;
    border: none; border-radius: 8px; cursor: pointer; font-size: 14px; font-weight: 600;
  }
  #input-area button:disabled { opacity: 0.5; cursor: not-allowed; }
  #status {
    padding: 8px 24px; background: #1e293b; font-size: 12px; color: #64748b; text-align: center;
    flex-shrink: 0;
  }
</style>
</head>
<body>

<div id="start-page">
  <h1>디자인 아이디에이션 실험</h1>
  <p>UX 리서처 · 비주얼 디자이너 · 소프트웨어 엔지니어와 함께 모바일 앱 아이디어를 발전시킵니다.</p>
  <div class="form-group">
    <label>참가자 번호</label>
    <input id="participant-id" type="text" placeholder="예: 01">
  </div>
  <div class="form-group">
    <label>조건</label>
    <select id="condition-mode">
      <option value="independent">유형 1</option>
      <option value="discussion">유형 2</option>
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
      <p>UX 리서처 · 비주얼 디자이너 · 소프트웨어 엔지니어</p>
    </div>
    <div class="info" id="session-info"></div>
  </header>
  <div id="chat"></div>
  <div id="status">대기 중...</div>
  <div id="input-area">
    <input id="msg" type="text" placeholder="의견을 입력하세요 (Enter 전송, 빈칸 = 넘기기)" disabled>
    <button id="send" disabled onclick="sendMessage()">전송</button>
  </div>
</div>

<script>
const chat = document.getElementById('chat');
const msgInput = document.getElementById('msg');
const sendBtn = document.getElementById('send');
const statusEl = document.getElementById('status');
let ws = null;
let waitingForInput = false;
let lastMsgHash = '';

const DISPLAY_NAMES = {
  'UXResearcher': 'UX 리서처',
  'VisualDesigner': '비주얼 디자이너',
  'SoftwareEngineer': '소프트웨어 엔지니어',
};

function nameClass(name) {
  if (name === 'UXResearcher') return 'ux';
  if (name === 'VisualDesigner') return 'visual';
  if (name === 'SoftwareEngineer') return 'engineer';
  return '';
}

function stripThink(text) {
  return text.replace(/<think>[\s\S]*?<\/think>/g, '').trim();
}

function addMsg(type, content, sender) {
  content = stripThink(content);
  if (!content) return;
  const hash = type + '|' + (sender || '') + '|' + content;
  if (hash === lastMsgHash) return;
  lastMsgHash = hash;
  const displayName = (sender && DISPLAY_NAMES[sender]) || sender;
  const div = document.createElement('div');
  div.className = 'msg ' + type;
  if (type === 'agent' && sender) {
    const nameSpan = document.createElement('span');
    nameSpan.className = 'name ' + nameClass(sender);
    nameSpan.textContent = displayName;
    div.appendChild(nameSpan);
    div.appendChild(document.createTextNode(content));
  } else {
    div.textContent = content;
  }
  chat.appendChild(div);
  chat.scrollTop = chat.scrollHeight;
}

function enableInput() {
  waitingForInput = true;
  msgInput.disabled = false;
  sendBtn.disabled = false;
  msgInput.focus();
  statusEl.textContent = '당신의 차례입니다.';
}

function disableInput() {
  waitingForInput = false;
  msgInput.disabled = true;
  sendBtn.disabled = true;
  statusEl.textContent = '에이전트가 작업 중...';
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

function handleWsMessage(raw) {
  let data;
  try { data = JSON.parse(raw); } catch {
    if (raw.includes('Provide feedback') || raw.includes('Replying as')) {
      addMsg('waiting', '당신의 차례입니다. (빈칸 = 넘기기)');
      enableInput();
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
    addMsg('system', text.replace(/\[시스템\]\s*/g, ''));
  } else if (t === 'text') {
    const sender = c.sender;
    const content = c.content;
    if (!content || !content.trim()) return;
    if (sender === 'chat_manager' || sender === 'Participant') return;
    let clean = content.replace(/\s*TERMINATE\s*/g, '').trim();
    clean = stripThink(clean);
    if (clean) addMsg('agent', clean, sender);
  }
}

function startExperiment() {
  const pid = document.getElementById('participant-id').value.trim();
  if (!pid) { alert('참가자 번호를 입력해주세요.'); return; }
  const condition = document.getElementById('condition-mode').value;
  const task = document.getElementById('task-mode').value;
  const typeLabel = condition === 'discussion' ? '유형 2' : '유형 1';

  document.getElementById('start-page').style.display = 'none';
  document.getElementById('experiment-page').style.display = 'flex';
  document.getElementById('session-info').textContent = `P${pid} / ${typeLabel} / 태스크 ${task}`;

  statusEl.textContent = '서버에 연결 중...';
  ws = new WebSocket('ws://127.0.0.1:8765');
  ws.onopen = () => {
    ws.send(JSON.stringify({participant_id: pid, condition: condition, task: task}));
    statusEl.textContent = '연결됨 — 세션 시작';
    addMsg('system', `${typeLabel} / 태스크 ${task}로 시작합니다.`);
  };
  ws.onmessage = (event) => handleWsMessage(event.data);
  ws.onclose = () => { statusEl.textContent = '세션 종료'; disableInput(); addMsg('system', '세션이 종료되었습니다.'); };
  ws.onerror = () => { statusEl.textContent = '연결 오류'; addMsg('system', '서버 연결에 실패했습니다.'); };
}
</script>
</body>
</html>"""


class FrontendHandler(http.server.SimpleHTTPRequestHandler):
    def do_GET(self):
        try:
            self.send_response(200)
            self.send_header("Content-type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(HTML_PAGE.encode("utf-8"))
        except (ConnectionAbortedError, ConnectionResetError, BrokenPipeError):
            pass  # 브라우저가 연결을 일찍 끊은 경우 무시

    def log_message(self, format, *args):
        pass


if __name__ == "__main__":
    import signal

    try:
        import colorama
        colorama.init()
    except ImportError:
        pass

    try:
        print("실험 서버 시작 중...")
        print("이 창을 닫지 마세요. 서버가 실행 중입니다.")
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
                _close_log()
                print("\n종료")
                os._exit(0)

            signal.signal(signal.SIGINT, force_exit)
            signal.signal(signal.SIGTERM, force_exit)

            try:
                httpd.serve_forever()
            except (KeyboardInterrupt, SystemExit):
                pass
            finally:
                _close_log()
                httpd.shutdown()
                os._exit(0)
    except Exception as e:
        print(f"\n오류 발생: {e}")
        input("\nEnter를 누르면 종료됩니다...")

"""
웹 기반 디자인 회의 시스템 (AG2 IOWebsockets + runtime_logging)
python web.py 로 실행 → 브라우저에서 http://127.0.0.1:8000
"""
import json
import os
import http.server
import threading
import datetime
import autogen
import autogen.runtime_logging as logging
from autogen.io.websockets import IOWebsockets

# 모델 선택: USE_PREMIUM=1 이면 프리미엄, 아니면 기본
USE_PREMIUM = os.getenv("USE_PREMIUM", "0") == "1"

if USE_PREMIUM:
    from config_premium import (
        llm_config_main, llm_config_ux, llm_config_3d,
        llm_config_unreal, llm_config_inner, llm_config_inner_alt,
        llm_config_selector,
    )
else:
    from config import (
        llm_config_main, llm_config_ux, llm_config_3d,
        llm_config_unreal, llm_config_inner, llm_config_inner_alt,
        llm_config_selector,
    )

from agents.simple_agents import create_simple_ux, create_simple_3d, create_simple_unreal
from agents.moderator import set_idea_board_ref, moderator_post_hook, transition_to_stage2
from meeting.stage1 import run_stage1
from meeting.stage2 import run_stage2
from meeting.idea_board import create_idea_board
from meeting.guardrails import (
    clean_message_hook, clean_history_hook, phase_aware_context_hook,
    board_exclusion_hook, set_banned_keywords,
)

# 로그 디렉토리
LOG_DIR = os.path.join(os.path.dirname(__file__), "logs")
os.makedirs(LOG_DIR, exist_ok=True)

# 대화 로그 (프론트엔드 다운로드용)
chat_log = []


def on_connect(iostream: IOWebsockets) -> None:
    """클라이언트가 웹소켓에 연결되면 회의 실행"""
    global chat_log
    chat_log = []

    timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")

    # AG2 runtime_logging 시작 (LLM 호출, 에이전트 이벤트 자동 기록)
    log_config = {"filename": os.path.join(LOG_DIR, f"session_{timestamp}.log")}
    session_id = logging.start(logger_type="file", config=log_config)
    iostream.print(f"[시스템] 세션 시작: {session_id}")

    # 웹소켓 메시지를 가로채서 chat_log에도 기록
    original_send = iostream._websocket.send
    def logging_send(data):
        try:
            parsed = json.loads(data) if isinstance(data, str) else data
            chat_log.append({
                "time": datetime.datetime.now().isoformat(),
                "raw": parsed if isinstance(parsed, dict) else data,
            })
        except (json.JSONDecodeError, TypeError):
            chat_log.append({
                "time": datetime.datetime.now().isoformat(),
                "raw": data,
            })
        return original_send(data)
    iostream._websocket.send = logging_send

    # 클라이언트에서 첫 메시지로 모델 모드 수신
    try:
        first_msg = iostream._websocket.recv()
        premium = first_msg.strip() == "premium"
    except Exception:
        premium = False

    try:
        _run_full_meeting(iostream, premium=premium)
    except Exception as e:
        iostream.print(f"[오류] {str(e)}")
    finally:
        logging.stop()

    # 대화 로그를 JSON으로 저장
    log_path = os.path.join(LOG_DIR, f"chat_{timestamp}.json")
    with open(log_path, "w", encoding="utf-8") as f:
        json.dump(chat_log, f, ensure_ascii=False, indent=2)
    iostream.print(f"[시스템] 대화 로그 저장됨: {log_path}")


def _extract_and_set_bans(brief):
    """브리프에서 '~ 안 함' 패턴을 찾아 금지 키워드로 설정"""
    import re
    ban_map = {
        "성능": ["드로우콜", "LOD", "폴리곤", "Nanite", "최적화", "FPS", "프레임"],
        "접근성": ["접근성", "색약", "저시력", "장애"],
        "애니메이션": ["애니메이션", "모션"],
        "동적": ["동적", "실시간 변화", "실시간으로 변"],
    }
    banned = []
    for key, keywords in ban_map.items():
        if re.search(rf'{key}\s*(논의\s*)?(안\s*함|제외|금지|불필요)', brief):
            banned.extend(keywords)
    if banned:
        set_banned_keywords(banned)


def _run_full_meeting(iostream, premium=False):
    """전체 회의 실행"""
    if premium:
        # 런타임에 프리미엄 config 로드
        from config_premium import (
            llm_config_main as lc_main, llm_config_ux as lc_ux,
            llm_config_3d as lc_3d, llm_config_unreal as lc_unreal,
            llm_config_inner as lc_inner, llm_config_inner_alt as lc_inner_alt,
            llm_config_selector as lc_selector,
        )
        iostream.print("[시스템] 프리미엄 모델로 시작합니다.")
    else:
        lc_main = llm_config_main
        lc_ux = llm_config_ux
        lc_3d = llm_config_3d
        lc_unreal = llm_config_unreal
        lc_inner = llm_config_inner
        lc_inner_alt = llm_config_inner_alt
        lc_selector = llm_config_selector
        iostream.print("[시스템] 기본 모델로 시작합니다.")

    iostream.print("[시스템] Stage 1: 사전 정제를 시작합니다...")

    user = autogen.UserProxyAgent(
        name="Orwiss",
        human_input_mode="ALWAYS",
        code_execution_config=False,
        is_termination_msg=lambda m: "TERMINATE" in m.get("content", ""),
    )

    # Stage 1 (에이전트 생성 전 — 브리프부터 확보)
    stage1_result, moderator = run_stage1(user, lc_main)

    brief_text = stage1_result.summary or ""
    # 자연어 브리프에서 회의 유형 추출
    meeting_type = "발산형"
    for mt in ["발산형", "개선형", "선택형"]:
        if mt in brief_text:
            meeting_type = mt
            break
    iostream.print(f"[시스템] 회의 유형: {meeting_type}")

    # 에이전트 생성 (단순 에이전트 — SocietyOfMind 미사용)
    ux_researcher = create_simple_ux(lc_ux, brief=brief_text)
    designer_3d = create_simple_3d(lc_3d, brief=brief_text)
    unreal_dev = create_simple_unreal(lc_unreal, brief=brief_text)

    # Moderator: Stage 1에서 이어서 사용 → 브리프 전달 불필요
    transition_to_stage2(moderator, brief_text)
    agents = [ux_researcher, designer_3d, unreal_dev, moderator]

    # 브리프에서 금지 키워드 추출 후 서버단 강제
    _extract_and_set_bans(brief_text)

    for agent in agents:
        agent.register_hook("process_message_before_send", clean_message_hook)
        agent.register_hook("process_all_messages_before_reply", clean_history_hook)
    for agent in [ux_researcher, designer_3d, unreal_dev]:
        agent.register_hook("process_all_messages_before_reply", phase_aware_context_hook)
        agent.register_hook("update_agent_state", board_exclusion_hook)
    moderator.register_hook("process_message_before_send", moderator_post_hook)

    idea_board = create_idea_board(brief_text, meeting_type)
    set_idea_board_ref(idea_board)
    for a in agents + [user]:
        a.context_variables = idea_board

    # Stage 2 — 단일 GroupChat
    iostream.print(f"[시스템] Stage 2: 디자인 회의 ({meeting_type})")

    result = run_stage2(
        agents=agents,
        user=user,
        brief=brief_text,
        meeting_type=meeting_type,
        llm_config_selector=lc_selector,
    )

    iostream.print("[시스템] === 최종 회의록 ===")
    iostream.print(result.summary if result.summary else "요약 없음")
    iostream.print("[시스템] === 최종 아이디어 보드 ===")
    iostream.print(idea_board.get("idea_board", "없음"))
    iostream.print("[시스템] 회의 종료")


# === HTML 프론트엔드 ===
HTML_PAGE = r"""<!DOCTYPE html>
<html lang="ko">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>디자인 회의</title>
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
  header {
    padding: 16px 24px;
    background: #1e293b;
    border-bottom: 1px solid #334155;
    display: flex;
    justify-content: space-between;
    align-items: center;
  }
  header .title h1 { font-size: 20px; font-weight: 600; }
  header .title p { font-size: 13px; color: #94a3b8; margin-top: 4px; }
  header .actions button {
    padding: 8px 16px;
    background: #334155;
    color: #e2e8f0;
    border: 1px solid #475569;
    border-radius: 6px;
    cursor: pointer;
    font-size: 13px;
  }
  header .actions button:hover { background: #475569; }
  #chat {
    flex: 1;
    overflow-y: auto;
    padding: 16px 24px;
    display: flex;
    flex-direction: column;
    gap: 8px;
  }
  .msg {
    max-width: 85%;
    padding: 10px 14px;
    border-radius: 12px;
    font-size: 14px;
    line-height: 1.6;
    white-space: pre-wrap;
    word-break: break-word;
  }
  .msg.system {
    align-self: center;
    background: #1e293b;
    color: #94a3b8;
    font-size: 13px;
    border: 1px solid #334155;
    text-align: center;
    max-width: 90%;
  }
  .msg.user {
    align-self: flex-end;
    background: #3b82f6;
    color: white;
  }
  .msg.agent {
    align-self: flex-start;
    background: #1e293b;
    border: 1px solid #334155;
  }
  .msg .name {
    font-weight: 700;
    font-size: 12px;
    margin-bottom: 4px;
    display: block;
  }
  .name.facilitator { color: #a78bfa; }
  .name.ux { color: #f472b6; }
  .name.designer { color: #fb923c; }
  .name.unreal { color: #4ade80; }
  .name.synthesizer { color: #60a5fa; }
  .msg.waiting {
    align-self: center;
    background: #312e81;
    border: 1px solid #4f46e5;
    color: #c7d2fe;
    font-size: 13px;
  }
  #input-area {
    padding: 12px 24px;
    background: #1e293b;
    border-top: 1px solid #334155;
    display: flex;
    gap: 8px;
  }
  #input-area input {
    flex: 1;
    padding: 10px 14px;
    background: #0f172a;
    border: 1px solid #334155;
    border-radius: 8px;
    color: #e2e8f0;
    font-size: 14px;
    outline: none;
  }
  #input-area input:focus { border-color: #6366f1; }
  #input-area input:disabled { opacity: 0.5; }
  #input-area button {
    padding: 10px 20px;
    background: #6366f1;
    color: white;
    border: none;
    border-radius: 8px;
    cursor: pointer;
    font-size: 14px;
    font-weight: 600;
  }
  #input-area button:hover { background: #4f46e5; }
  #input-area button:disabled { opacity: 0.5; cursor: not-allowed; }
  #status {
    padding: 8px 24px;
    background: #1e293b;
    font-size: 12px;
    color: #64748b;
    text-align: center;
  }
</style>
</head>
<body>

<header>
  <div class="title">
    <h1>멀티 에이전트 디자인 회의</h1>
    <p>UX 리서처 · 3D 디자이너 · 언리얼 개발자 · 진행자</p>
  </div>
  <div class="actions">
    <select id="model-mode" style="padding:8px;background:#334155;color:#e2e8f0;border:1px solid #475569;border-radius:6px;font-size:13px;">
      <option value="standard">기본 모델 (~70원)</option>
      <option value="premium">프리미엄 (~490원)</option>
    </select>
    <button id="start-btn" onclick="connect()" style="padding:8px 16px;background:#6366f1;color:white;border:none;border-radius:6px;cursor:pointer;font-size:13px;">회의 시작</button>
    <button onclick="downloadLog()">대화 로그 저장</button>
  </div>
</header>

<div id="chat"></div>
<div id="status">연결 대기중...</div>
<div id="input-area">
  <input id="msg" type="text" placeholder="의견을 입력하세요 (Enter 전송, 빈칸 = 넘기기)" disabled>
  <button id="send" disabled onclick="sendMessage()">전송</button>
</div>

<script>
const chat = document.getElementById('chat');
const msgInput = document.getElementById('msg');
const sendBtn = document.getElementById('send');
const statusEl = document.getElementById('status');

let ws = null;
let waitingForInput = false;
const chatLog = []; // 대화 로그 (다운로드용)
let lastMsgHash = ''; // 메시지 중복 방지

// 에이전트 표시 이름 매핑
const DISPLAY_NAMES = {
  'UXResearcher': 'UX 리서처',
  'Designer3D': '3D 디자이너',
  'UnrealDev': '언리얼 개발자',
  'Moderator': '진행자',
  'Facilitator': 'Facilitator',
};

function nameClass(name) {
  if (!name) return '';
  if (name === 'Facilitator') return 'facilitator';
  if (name === 'UXResearcher') return 'ux';
  if (name === 'Designer3D') return 'designer';
  if (name === 'UnrealDev') return 'unreal';
  if (name === 'Moderator') return 'synthesizer';
  return '';
}

function stripThink(text) {
  return text.replace(/<think>[\s\S]*?<\/think>/g, '').trim();
}

function addMsg(type, content, sender) {
  // <think> 태그 제거
  content = stripThink(content);
  if (!content) return;

  // 중복 메시지 방지
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

  chatLog.push({
    time: new Date().toISOString(),
    type: type,
    sender: displayName || null,
    content: content
  });
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
  statusEl.textContent = '에이전트가 논의 중...';
}

function sendMessage() {
  if (!waitingForInput || !ws) return;
  const text = msgInput.value;
  ws.send(text);
  if (text) addMsg('user', text, 'Orwiss');
  msgInput.value = '';
  disableInput();
}

function downloadLog() {
  const blob = new Blob([JSON.stringify(chatLog, null, 2)], {type: 'application/json'});
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = 'meeting_log_' + new Date().toISOString().slice(0,19).replace(/:/g,'-') + '.json';
  a.click();
  URL.revokeObjectURL(url);
}

msgInput.addEventListener('keydown', (e) => {
  if (e.key === 'Enter') { e.preventDefault(); sendMessage(); }
});

// 내부 검토 에이전트 (SocietyOfMind 내부 — UI에 안 보임)
const INTERNAL = new Set([
  '시나리오분석가', '인상평가자', '탈선유도자',
  '조형분석가', '시각레퍼런스전문가',
  '퍼포먼스분석가', '구현전문가',
]);

function handleWsMessage(raw) {
  let data;
  try {
    data = JSON.parse(raw);
  } catch {
    if (raw.includes('Provide feedback') || raw.includes('Replying as')) {
      addMsg('waiting', '당신의 차례입니다. (빈칸 = 넘기기, exit = 종료)');
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
    if (text.includes('[시스템]') || text.startsWith('===')) {
      addMsg('system', text.replace(/\[시스템\]\s*/g, ''));
    } else {
      addMsg('system', text);
    }
  }
  else if (t === 'text') {
    const sender = c.sender;
    const content = c.content;
    if (!content || !content.trim()) return;

    // 내부 검토 에이전트 + chat_manager 숨김
    if (INTERNAL.has(sender) || sender === 'chat_manager') return;

    let clean = content.replace(/\s*TERMINATE\s*/g, '').trim();
    clean = stripThink(clean);
    if (!clean) return;

    if (sender === 'Orwiss') return;

    addMsg('agent', clean, sender);
  }
  // 나머지 타입은 전부 무시
}

function connect() {
  statusEl.textContent = '서버에 연결 중...';
  ws = new WebSocket('ws://127.0.0.1:8765');

  ws.onopen = () => {
    // 모델 모드 전송
    const mode = document.getElementById('model-mode').value;
    ws.send(mode);
    const modeLabel = mode === 'premium' ? '프리미엄' : '기본';
    statusEl.textContent = `연결됨 — ${modeLabel} 모델로 회의 시작`;
    addMsg('system', `${modeLabel} 모델로 회의를 시작합니다.`);
    // 선택 + 시작 버튼 비활성화
    document.getElementById('model-mode').disabled = true;
    document.getElementById('start-btn').disabled = true;
    document.getElementById('start-btn').textContent = '회의 진행 중';
  };

  ws.onmessage = (event) => handleWsMessage(event.data);

  ws.onclose = () => {
    statusEl.textContent = '연결 종료됨';
    disableInput();
    addMsg('system', '회의가 종료되었습니다.');
  };

  ws.onerror = () => {
    statusEl.textContent = '연결 오류';
    addMsg('system', '서버 연결에 실패했습니다. 서버가 실행 중인지 확인하세요.');
  };
}

// 자동 연결 안 함 — 유저가 모델 선택 후 "회의 시작" 클릭
statusEl.textContent = '모델을 선택하고 [회의 시작]을 누르세요.';
</script>
</body>
</html>"""


class FrontendHandler(http.server.SimpleHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-type", "text/html; charset=utf-8")
        self.end_headers()
        self.wfile.write(HTML_PAGE.encode("utf-8"))

    def log_message(self, format, *args):
        pass


if __name__ == "__main__":
    import signal
    import sys

    print("웹소켓 서버 시작 중...")
    with IOWebsockets.run_server_in_thread(
        host="127.0.0.1",
        port=8765,
        on_connect=on_connect,
    ) as ws_uri:
        print(f"웹소켓: {ws_uri}")

        httpd = http.server.HTTPServer(("127.0.0.1", 8000), FrontendHandler)
        print("http://127.0.0.1:8000 을 열어주세요.")
        print("종료: Ctrl+C (두 번 누르면 강제 종료)")

        def force_exit(*args):
            print("\n강제 종료")
            os._exit(0)

        signal.signal(signal.SIGINT, force_exit)
        signal.signal(signal.SIGTERM, force_exit)

        try:
            httpd.serve_forever()
        except (KeyboardInterrupt, SystemExit):
            pass
        finally:
            print("\n서버 종료 중...")
            httpd.shutdown()
            os._exit(0)  # 웹소켓 스레드 포함 모든 스레드 강제 종료

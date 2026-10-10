"""VR 참가자 음성 입력 (STT). VR 헤드셋이 연결된 PC에서 돌린다.

참가자 차례가 열리면 헤드셋 마이크를 켜고 ElevenLabs Scribe v2 Realtime으로 실시간 받아 적는다.
받아 적는 중인 글자는 실험자 웹 화면에만 보이고, 참가자가 오른쪽 컨트롤러 검지 트리거를 누르면
(언리얼이 이 프로그램으로 OSC /stt/done을 보낸다) 받아 적은 문장 전체를 web.py로 한 번에 보낸다.
웹에서 직접 친 답과 똑같이 처리된다. 아무 말 없이 누르면 빈 문장 = 넘기기.
마이크는 참가자 차례에만 켜진다. 소리 파일은 저장하지 않는다 (글자만 web.py 로그에 남는다).

  python stt_client.py                  # .env 설정대로 실행. 엔터를 쳐도 트리거와 같다 (시험용)
  python stt_client.py --list-mics      # 마이크 목록
  python stt_client.py --test-audio recordings/20261001_155948/0001_PM.json --auto-done 1.5
                                        # 마이크 대신 녹음본을 흘리고, 끝나고 1.5초 뒤 자동 트리거

.env (web.py와 같은 파일을 써도 된다)
  STT_SERVER=http://10.97.136.247:8080  web.py 주소 (.env의 PORT. web.py가 같은 PC에서 돌면 http://127.0.0.1:8080)
  STT_SECRET=...                        web.py 쪽과 같은 값. 비어 있으면 web.py가 STT 요청을 안 받는다
  STT_MIC=Headset                       마이크 이름 일부 (비우면 윈도우 기본 마이크)
  STT_TRIGGER_PORT=7410                 언리얼이 /stt/done을 보내는 포트
  STT_VAD_SILENCE=1.0                   이만큼 쉬면 한 구간을 확정한다 (전송은 트리거로만)
  STT_MIN_OPEN_SEC=1.0                  차례가 열린 뒤 이 시간 안에 누른 트리거는 무시 (실수 방지)
  ELEVENLABS_API_KEY=...
"""
from __future__ import annotations

import argparse
import asyncio
import base64
import json
import os
import sys
import threading
import time
import urllib.error
import urllib.request

from dotenv import load_dotenv

load_dotenv()

SERVER = os.getenv("STT_SERVER", "http://127.0.0.1:8080").rstrip("/")
SECRET = os.getenv("STT_SECRET", "")
MIC = os.getenv("STT_MIC", "").strip()
TRIGGER_PORT = int(os.getenv("STT_TRIGGER_PORT", "7410"))
VAD_SILENCE = float(os.getenv("STT_VAD_SILENCE", "1.0"))
MIN_OPEN_SEC = float(os.getenv("STT_MIN_OPEN_SEC", "1.0"))

SAMPLE_RATE = 16000
CHUNK_BYTES = 3200          # 100ms (16kHz, 16bit, mono)
TAIL_SEC = 0.4              # 트리거를 누른 뒤에도 이만큼 더 듣는다 — 마지막 음절이 잘리지 않게
FINAL_WAIT_SEC = 2.0        # 마지막 구간 확정을 기다리는 최대 시간
PARTIAL_EVERY_SEC = 0.3     # 실험자 화면 갱신 간격

_t0 = time.monotonic()
# 창에 찍는 것과 같은 내용을 파일에도 남긴다 (창이 닫히거나 지나가 버려도 원인을 볼 수 있게)
_LOG_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "logs", "stt")
_LOG_PATH = os.path.join(_LOG_DIR, f"stt_client_{time.strftime('%Y%m%d')}.log")


def log(*parts) -> None:
    line = f"[{time.monotonic() - _t0:7.1f}s] " + " ".join(str(p) for p in parts)
    try:
        print(line, flush=True)
    except UnicodeEncodeError:  # 창 인코딩이 UTF-8이 아니어도 로그 때문에 죽지 않게
        print(line.encode("ascii", "replace").decode("ascii"), flush=True)
    try:
        os.makedirs(_LOG_DIR, exist_ok=True)
        with open(_LOG_PATH, "a", encoding="utf-8") as f:
            f.write(f"{time.strftime('%H:%M:%S')} {line}\n")
    except OSError:
        pass


# -- web.py와 주고받기 (HTTP) --
def _request(method: str, path: str, body: dict | None = None, timeout: float = 30.0):
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(
        SERVER + path, data=data, method=method,
        headers={"Content-Type": "application/json", "X-STT-Secret": SECRET},
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.status, json.loads(resp.read().decode("utf-8") or "{}")


def get_turn(turn_id: int, is_open: bool, wait: float = 20.0) -> dict | None:
    """차례 상태가 (turn_id, is_open)과 달라질 때까지 web.py에서 기다린다. 연결 실패면 None."""
    try:
        _, state = _request("GET", f"/api/stt/turn?turn_id={turn_id}&open={int(is_open)}&wait={wait}",
                            timeout=wait + 10)
        return state
    except Exception as e:
        log("web.py 연결 실패:", e)
        return None


def post(path: str, turn_id: int, text: str) -> int:
    try:
        status, _ = _request("POST", path, {"turn_id": turn_id, "text": text}, timeout=10)
        return status
    except urllib.error.HTTPError as e:
        return e.code
    except Exception as e:
        log("전송 실패:", path, e)
        return 0


# -- 소리 입력: 헤드셋 마이크, 또는 시험용 녹음본 --
def find_mic(name: str) -> int | None:
    """이름에 name이 들어간 입력 장치. 윈도우 MME 쪽을 먼저 고른다 (16kHz로 알아서 바꿔 준다)."""
    import sounddevice as sd

    if not name:
        return None
    apis = sd.query_hostapis()
    hits = [
        (i, d) for i, d in enumerate(sd.query_devices())
        if d["max_input_channels"] > 0 and name.lower() in d["name"].lower()
    ]
    if not hits:
        raise SystemExit(f"마이크를 못 찾음: {name!r} — python stt_client.py --list-mics 로 이름을 확인하세요")
    hits.sort(key=lambda h: 0 if apis[h[1]["hostapi"]]["name"] == "MME" else 1)
    return hits[0][0]


class MicSource:
    def __init__(self, device: int | None) -> None:
        self.device = device
        self.stream = None

    def start(self, push) -> None:
        import sounddevice as sd

        def callback(indata, frames, t, status):
            push(bytes(indata))

        self.stream = sd.RawInputStream(
            samplerate=SAMPLE_RATE, channels=1, dtype="int16",
            blocksize=CHUNK_BYTES // 2, device=self.device, callback=callback,
        )
        self.stream.start()

    def stop(self) -> None:
        if self.stream is not None:
            self.stream.stop()
            self.stream.close()
            self.stream = None


class FileSource:
    """녹음본(vr 녹화 json의 audio_b64)을 실시간 속도로 흘린다. 끝나면 무음을 계속 흘린다."""

    def __init__(self, path: str, auto_done: float | None) -> None:
        with open(path, encoding="utf-8") as f:
            self.pcm = base64.b64decode(json.load(f)["audio_b64"])
        self.auto_done = auto_done
        self.running = False

    def start(self, push, on_done=None) -> None:
        self.running = True

        def run():
            done_cb = on_done
            i = 0
            end_at = None
            while self.running:
                chunk = self.pcm[i:i + CHUNK_BYTES]
                if len(chunk) < CHUNK_BYTES:
                    chunk = chunk + bytes(CHUNK_BYTES - len(chunk))
                    if end_at is None:
                        end_at = time.monotonic()
                push(chunk)
                i += CHUNK_BYTES
                if end_at and self.auto_done is not None and done_cb and time.monotonic() - end_at >= self.auto_done:
                    done_cb()
                    done_cb = None
                time.sleep(0.1)

        threading.Thread(target=run, daemon=True).start()

    def stop(self) -> None:
        self.running = False


# -- 한 번의 참가자 차례 --
class Turn:
    def __init__(self, turn_id: int, loop: asyncio.AbstractEventLoop) -> None:
        self.turn_id = turn_id
        self.loop = loop
        self.opened_at = time.monotonic()
        self.done = asyncio.Event()      # 트리거
        self.closed = asyncio.Event()    # 실험자가 직접 입력했거나 세션이 끝남 → 버린다

    def trigger(self) -> None:
        """다른 스레드(OSC·키보드)에서 부른다."""
        if time.monotonic() - self.opened_at < MIN_OPEN_SEC:
            log("트리거 무시 (차례가 열린 직후)")
            return
        self.loop.call_soon_threadsafe(self.done.set)


CURRENT: Turn | None = None


async def run_turn(turn: Turn, make_source) -> None:
    from elevenlabs.client import ElevenLabs
    from elevenlabs.realtime.connection import RealtimeEvents
    from elevenlabs.realtime.scribe import AudioFormat, CommitStrategy

    loop = asyncio.get_running_loop()
    audio_q: asyncio.Queue[bytes] = asyncio.Queue()
    segments: list[str] = []
    state = {"partial": "", "last_post": 0.0, "posting": False, "broken": None, "final": asyncio.Event()}

    def full_text() -> str:
        return " ".join(s for s in segments + [state["partial"]] if s).strip()

    def push_partial() -> None:
        now = time.monotonic()
        if state["posting"] or now - state["last_post"] < PARTIAL_EVERY_SEC:
            return
        state["posting"], state["last_post"] = True, now
        text = full_text()

        async def send():
            try:
                await asyncio.to_thread(post, "/api/stt/partial", turn.turn_id, text)
            finally:
                state["posting"] = False
        asyncio.ensure_future(send())

    def on_partial(data) -> None:
        state["partial"] = (data or {}).get("text", "")
        push_partial()

    def on_committed(data) -> None:
        text = ((data or {}).get("text") or "").strip()
        if text:
            segments.append(text)
        state["partial"] = ""
        state["final"].set()
        log("확정:", text)
        push_partial()

    def on_error(data) -> None:
        kind = (data or {}).get("message_type") or (data or {}).get("error")
        if kind in ("commit_throttled", "insufficient_audio_activity"):
            # 마지막 확정 요청 때 남은 소리가 0.3초 미만이면 오는 응답 — 더 받을 글자가 없다는 뜻
            state["final"].set()
            return
        state["broken"] = kind or "error"
        log("STT 오류:", data)

    client = ElevenLabs(api_key=os.getenv("ELEVENLABS_API_KEY"))
    options = {
        "model_id": "scribe_v2_realtime",
        "audio_format": AudioFormat.PCM_16000,
        "sample_rate": SAMPLE_RATE,
        "commit_strategy": CommitStrategy.VAD,
        "vad_silence_threshold_secs": VAD_SILENCE,
        "language_code": "ko",
    }

    # 마이크는 먼저 켠다 — 연결되는 0.3초 동안의 소리도 대기열에 쌓였다가 같이 간다
    source = make_source()
    push = lambda chunk: loop.call_soon_threadsafe(audio_q.put_nowait, chunk)
    if isinstance(source, FileSource):
        source.start(push, on_done=turn.trigger)
    else:
        source.start(push)
    log(f"차례 {turn.turn_id} 시작 — 마이크 켜짐")

    conn = None
    try:
        conn = await client.speech_to_text.realtime.connect(options)
        conn.on(RealtimeEvents.PARTIAL_TRANSCRIPT, on_partial)
        conn.on(RealtimeEvents.COMMITTED_TRANSCRIPT, on_committed)
        conn.on(RealtimeEvents.ERROR, on_error)

        async def sender():
            while True:
                chunk = await audio_q.get()
                await conn.send({"audio_base_64": base64.b64encode(chunk).decode("ascii")})

        send_task = asyncio.create_task(sender())
        waiters = [asyncio.create_task(turn.done.wait()), asyncio.create_task(turn.closed.wait())]
        await asyncio.wait(waiters, return_when=asyncio.FIRST_COMPLETED)
        for w in waiters:
            w.cancel()

        if turn.closed.is_set():
            log(f"차례 {turn.turn_id} 닫힘 (직접 입력 또는 세션 종료) — 받아 적은 것은 버림")
            send_task.cancel()
            return

        # 트리거: 조금 더 듣고, 남은 구간을 확정해서 다 모은 뒤 보낸다
        await asyncio.sleep(TAIL_SEC)
        if send_task.done() and not send_task.cancelled() and send_task.exception():
            state["broken"] = f"소리 전송 끊김: {send_task.exception()}"
        send_task.cancel()
        if state["broken"]:
            raise RuntimeError(state["broken"])
        state["final"].clear()
        await conn.commit()
        try:
            await asyncio.wait_for(state["final"].wait(), FINAL_WAIT_SEC)
        except asyncio.TimeoutError:
            log("마지막 구간 확정이 늦어 받아 적은 데까지 보냄")
        text = full_text()
        status = await asyncio.to_thread(post, "/api/stt/submit", turn.turn_id, text)
        if status == 202:
            log(f"전송 완료 ({len(text)}자):", text or "(넘기기)")
        else:
            log(f"전송 거절 ({status}) — 이미 닫힌 차례:", text)
    except Exception as e:
        log("STT 실패 — 실험자가 웹에서 직접 입력해 주세요:", e)
        await asyncio.to_thread(post, "/api/stt/partial", turn.turn_id,
                                "[음성 인식 오류 — 참가자 말을 직접 입력해 주세요]")
    finally:
        source.stop()
        if conn is not None:
            try:
                await conn.close()
            except Exception:
                pass
        log(f"차례 {turn.turn_id} 끝 — 마이크 꺼짐")


# -- 트리거 입력: 언리얼 OSC /stt/done, 그리고 시험용 엔터 --
def start_trigger_listeners(keyboard: bool) -> None:
    from pythonosc import dispatcher as osc_dispatcher
    from pythonosc import osc_server

    def fire(*_):
        turn = CURRENT
        if turn is not None and not turn.done.is_set():
            log("트리거")
            turn.trigger()

    disp = osc_dispatcher.Dispatcher()
    disp.map("/stt/done", fire)
    server = osc_server.ThreadingOSCUDPServer(("0.0.0.0", TRIGGER_PORT), disp)
    threading.Thread(target=server.serve_forever, daemon=True, name="stt-trigger").start()
    log(f"트리거 대기: OSC /stt/done (포트 {TRIGGER_PORT})" + (" 또는 엔터" if keyboard else ""))

    if keyboard:
        def read_keys():
            for _ in sys.stdin:
                fire()
        threading.Thread(target=read_keys, daemon=True, name="stt-keys").start()


def _log_task_error(task: asyncio.Task) -> None:
    """차례 처리 중 잡히지 않은 오류(예: 마이크 열기 실패)가 조용히 사라지지 않게 남긴다."""
    if task.cancelled():
        return
    exc = task.exception()
    if exc is not None:
        log("차례 처리 중 오류 — 이번 차례는 STT 없이 진행:", repr(exc))


async def main(args) -> None:
    global CURRENT
    if not SECRET:
        raise SystemExit("STT_SECRET이 비어 있음 — web.py와 같은 값을 .env에 넣으세요")
    if not os.getenv("ELEVENLABS_API_KEY"):
        raise SystemExit("ELEVENLABS_API_KEY가 비어 있음")
    if args.test_audio:
        make_source = lambda: FileSource(args.test_audio, args.auto_done)
        log("시험 모드: 마이크 대신", args.test_audio)
    else:
        device = find_mic(MIC)
        import sounddevice as sd
        name = sd.query_devices(device if device is not None else sd.default.device[0])["name"]
        log("마이크:", name)
        make_source = lambda: MicSource(device)
    start_trigger_listeners(keyboard=not args.test_audio)
    log("web.py:", SERVER)

    loop = asyncio.get_running_loop()
    turn_id, is_open = -1, False
    task: asyncio.Task | None = None
    while True:
        state = await asyncio.to_thread(get_turn, turn_id, is_open)
        if state is None:
            await asyncio.sleep(2)
            continue
        turn_id, is_open = state["turn_id"], state["open"]
        busy = task is not None and not task.done()
        if is_open and not busy:
            CURRENT = Turn(turn_id, loop)
            task = asyncio.create_task(run_turn(CURRENT, make_source))
            task.add_done_callback(_log_task_error)
        elif not is_open and busy and CURRENT is not None and not CURRENT.done.is_set():
            CURRENT.closed.set()
        if args.once and task is not None and not is_open:
            await task
            return


def list_mics() -> None:
    import sounddevice as sd

    apis = sd.query_hostapis()
    for i, d in enumerate(sd.query_devices()):
        if d["max_input_channels"] > 0:
            print(f"{i:3d}  [{apis[d['hostapi']]['name']}]  {d['name']}")


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser()
    ap.add_argument("--list-mics", action="store_true")
    ap.add_argument("--test-audio", help="마이크 대신 흘릴 녹화 json (recordings/.../0001_PM.json)")
    ap.add_argument("--auto-done", type=float, default=None, help="--test-audio가 끝나고 이 초 뒤 자동 트리거")
    ap.add_argument("--once", action="store_true", help="차례 하나만 처리하고 끝낸다 (시험용)")
    a = ap.parse_args()
    if a.list_mics:
        list_mics()
    else:
        try:
            asyncio.run(main(a))
        except KeyboardInterrupt:
            pass

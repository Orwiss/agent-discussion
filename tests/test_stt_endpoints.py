"""STT 프로그램(stt_client.py)이 쓰는 web.py 주소 — /api/stt/turn, /partial, /submit.

ElevenLabs도 마이크도 안 쓴다. 가짜 세션이 참가자 차례를 열고, 이 테스트가 STT 프로그램 대신
HTTP로 문장을 보낸다. 지난 차례 문장·이중 답·비밀값 없는 요청이 걸러지는지 본다.
"""
import json
import os
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from unittest.mock import patch

from autogen.io import IOStream

import vr_output
import web
from experiment_runtime import ExperimentSessionRegistry, SessionCancelled, SessionIOStream, session_scope


class TestSTTEndpoints(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.registry = ExperimentSessionRegistry(self.temp_dir.name, max_active_sessions=3)
        self.answers: list[str] = []

        def fake_worker(session):
            stream = SessionIOStream(session)
            with session_scope(session), IOStream.set_default(stream):
                session.set_status("running")
                try:
                    for _ in range(2):
                        self.answers.append(stream.input("reply"))
                    session.finish("completed")
                except SessionCancelled:
                    pass

        self.patches = [
            patch.object(vr_output, "_enabled_cache", False),   # UE로 신호를 안 보낸다
            patch.object(web, "SESSION_REGISTRY", self.registry),
            patch.object(web, "_session_worker", fake_worker),
            patch.object(web, "STUDY_STORE", unittest.mock.MagicMock()),
            patch.dict(os.environ, {"STT_SECRET": "s3cret"}),
        ]
        for item in self.patches:
            item.start()
        self.server = web.http.server.ThreadingHTTPServer(("127.0.0.1", 0), web.FrontendHandler)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        host, port = self.server.server_address
        self.base = f"http://{host}:{port}"

    def tearDown(self):
        self.registry.close_all()
        self.server.shutdown()
        self.server.server_close()
        for item in reversed(self.patches):
            item.stop()
        self.temp_dir.cleanup()

    def call(self, method, path, body=None, secret="s3cret", token=None):
        headers = {"Content-Type": "application/json", "X-STT-Secret": secret}
        if token:
            headers["X-Session-Token"] = token
        req = urllib.request.Request(
            self.base + path, method=method, headers=headers,
            data=json.dumps(body).encode() if body is not None else None,
        )
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                return resp.status, json.loads(resp.read() or b"{}")
        except urllib.error.HTTPError as error:
            return error.code, json.loads(error.read() or b"{}")

    def open_turn(self, after_id=-1):
        status, state = self.call("GET", f"/api/stt/turn?turn_id={after_id}&open=0&wait=5")
        self.assertEqual(status, 200)
        self.assertTrue(state["open"], "참가자 차례가 안 열렸다")
        return state["turn_id"]

    def start_session(self):
        status, created = self.call("POST", "/api/sessions",
                                    {"participant_id": "P01", "condition": "centralized", "task": "A"})
        self.assertEqual(status, 201)
        return created

    def test_wrong_secret_is_hidden(self):
        status, _ = self.call("GET", "/api/stt/turn?turn_id=-1&open=0&wait=0", secret="nope")
        self.assertEqual(status, 404)
        status, _ = self.call("POST", "/api/stt/submit", {"turn_id": 1, "text": "x"}, secret="nope")
        self.assertEqual(status, 404)

    def test_voice_answer_goes_in_like_a_typed_one(self):
        created = self.start_session()
        tid = self.open_turn()
        self.assertEqual(self.call("POST", "/api/stt/partial", {"turn_id": tid, "text": "좋은"})[0], 202)
        self.assertEqual(self.call("POST", "/api/stt/submit", {"turn_id": tid, "text": "좋은 생각입니다"})[0], 202)
        # 같은 차례에 두 번째 답은 거절된다
        self.assertEqual(self.call("POST", "/api/stt/submit", {"turn_id": tid, "text": "또"})[0], 409)
        # 다음 차례가 열리면 지난 차례 번호로 온 문장은 거절된다
        tid2 = self.open_turn(after_id=tid)
        self.assertGreater(tid2, tid)
        self.assertEqual(self.call("POST", "/api/stt/submit", {"turn_id": tid, "text": "늦은 문장"})[0], 409)
        self.assertEqual(self.call("POST", "/api/stt/submit", {"turn_id": tid2, "text": ""})[0], 202)  # 넘기기
        deadline = time.time() + 5
        while len(self.answers) < 2 and time.time() < deadline:
            time.sleep(0.05)
        self.assertEqual(self.answers, ["좋은 생각입니다", ""])
        # 실험자 화면에 실시간 글자와 최종 답이 이벤트로 갔다
        _, polled = self.call("GET", f"/api/sessions/{created['session_id']}/events?after=0&wait=0",
                              token=created["session_token"])
        kinds = [e["payload"]["type"] for e in polled["events"]]
        self.assertIn("stt_partial", kinds)
        self.assertEqual(kinds.count("stt_final"), 2)

    def test_typed_answer_wins_and_late_voice_is_rejected(self):
        created = self.start_session()
        tid = self.open_turn()
        status, _ = self.call("POST", f"/api/sessions/{created['session_id']}/messages",
                              {"message": "직접 입력"}, token=created["session_token"])
        self.assertEqual(status, 202)
        self.assertEqual(self.call("POST", "/api/stt/submit", {"turn_id": tid, "text": "음성"})[0], 409)
        deadline = time.time() + 5
        while not self.answers and time.time() < deadline:
            time.sleep(0.05)
        self.assertEqual(self.answers, ["직접 입력"])


if __name__ == "__main__":
    unittest.main()

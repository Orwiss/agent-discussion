import json
import tempfile
import threading
import unittest
import urllib.error
import urllib.parse
import urllib.request
from unittest.mock import MagicMock, patch

import web
from autogen.io import IOStream
from experiment_runtime import (
    ExperimentSessionRegistry,
    SessionCancelled,
    SessionIOStream,
    session_scope,
)
from study_store import PersistedExperimentSession


class TestHTTPTransport(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.registry = ExperimentSessionRegistry(self.temp_dir.name, max_active_sessions=3)
        self.store = MagicMock()
        self.store.authorize_experiment_session.return_value = None
        def fake_worker(session):
            stream = SessionIOStream(session)
            with session_scope(session), IOStream.set_default(stream):
                session.set_status("running")
                stream.print(f"topic:{session.participant_id}")
                try:
                    reply = stream.input("reply")
                    stream.send_text("PM", f"{session.participant_id}:{reply}")
                    session.finish("completed")
                except SessionCancelled:
                    pass

        self.patches = [
            patch.object(web, "SESSION_REGISTRY", self.registry),
            patch.object(web, "_session_worker", fake_worker),
            patch.object(web, "STUDY_STORE", self.store),
        ]
        for item in self.patches:
            item.start()

        self.server = web.http.server.ThreadingHTTPServer(
            ("127.0.0.1", 0),
            web.FrontendHandler,
        )
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        host, port = self.server.server_address
        self.base_url = f"http://{host}:{port}"

    def tearDown(self):
        self.registry.close_all()
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)
        for item in reversed(self.patches):
            item.stop()
        self.temp_dir.cleanup()

    def request(self, method, path, body=None, token=None):
        payload = None if body is None else json.dumps(body).encode("utf-8")
        headers = {"Content-Type": "application/json"}
        if token:
            headers["X-Session-Token"] = token
        request = urllib.request.Request(
            self.base_url + path,
            data=payload,
            headers=headers,
            method=method,
        )
        with urllib.request.urlopen(request, timeout=3) as response:
            return response.status, json.loads(response.read())

    def create_session(self, participant_id):
        _, result = self.request(
            "POST",
            "/api/sessions",
            {
                "participant_id": participant_id,
                "condition": "centralized",
                "task": "A",
            },
        )
        return result

    def test_api_health_check(self):
        status, result = self.request("GET", "/api/healthz")
        self.assertEqual(status, 200)
        self.assertEqual(result, {"status": "ok"})

    def test_two_http_sessions_receive_only_their_own_events(self):
        first = self.create_session("P01")
        second = self.create_session("P02")

        self.request(
            "POST",
            f"/api/sessions/{second['session_id']}/messages",
            {"message": "two"},
            token=second["session_token"],
        )
        self.request(
            "POST",
            f"/api/sessions/{first['session_id']}/messages",
            {"message": "one"},
            token=first["session_token"],
        )

        _, first_poll = self.request(
            "GET",
            f"/api/sessions/{first['session_id']}/events?after=0&wait=1",
            token=first["session_token"],
        )
        _, second_poll = self.request(
            "GET",
            f"/api/sessions/{second['session_id']}/events?after=0&wait=1",
            token=second["session_token"],
        )
        first_text = json.dumps(first_poll["events"], ensure_ascii=False)
        second_text = json.dumps(second_poll["events"], ensure_ascii=False)
        self.assertIn("P01:one", first_text)
        self.assertNotIn("P02:two", first_text)
        self.assertIn("P02:two", second_text)
        self.assertNotIn("P01:one", second_text)

    def test_wrong_session_secret_is_rejected(self):
        session = self.create_session("P01")
        with self.assertRaises(urllib.error.HTTPError) as raised:
            self.request(
                "GET",
                f"/api/sessions/{session['session_id']}/events?after=0&wait=0",
                token="wrong",
            )
        self.assertEqual(raised.exception.code, 401)

    def test_recovered_session_stops_polling_but_accepts_final_idea(self):
        recovered = PersistedExperimentSession(
            id="persisted-session",
            participant_id="P01",
            researcher_email="researcher@example.com",
            condition="centralized",
            task="A",
            status="running",
        )
        self.store.authorize_experiment_session.return_value = recovered

        with self.assertRaises(urllib.error.HTTPError) as raised:
            self.request(
                "GET",
                "/api/sessions/persisted-session/events?after=0&wait=0",
                token="persisted-token",
            )
        self.assertEqual(raised.exception.code, 410)
        error_body = json.loads(raised.exception.read())
        self.assertTrue(error_body["recoverable_idea"])

        status, result = self.request(
            "POST",
            "/api/sessions/persisted-session/idea",
            {
                "concept": "Recovered concept",
                "features": ["One", "Two", "Three"],
                "differentiator": "Recovered differentiator",
            },
            token="persisted-token",
        )
        self.assertEqual(status, 202)
        self.assertTrue(result["accepted"])
        saved_session, saved_form = (
            self.store.record_recovered_idea.call_args.args
        )
        self.assertEqual(saved_session, recovered)
        self.assertEqual(saved_form["concept"], "Recovered concept")
        self.assertIn("filled_at", saved_form)

    def test_survey_submission_is_authorized_and_server_side(self):
        status, result = self.request(
            "POST",
            "/api/survey",
            {
                "participant_id": "P01",
                "environment": "web",
                "round_index": 1,
                "condition": "condition_1",
                "gender": "female",
                "age": 24,
                "design_experience": "1_to_3_years",
                "llm_experience": "daily",
                "submitted_at": "2026-07-24T00:00:00Z",
                "responses": [],
                "scores": {},
            },
        )
        self.assertEqual(status, 201)
        self.assertTrue(result["saved"])
        stored = self.store.submit_survey.call_args.args[0]
        self.assertNotIn("access_code", stored)
        self.assertEqual(stored["participant_id"], "P01")
        self.assertEqual(stored["researcher_email"], web.LOCAL_RESEARCHER)


if __name__ == "__main__":
    unittest.main()

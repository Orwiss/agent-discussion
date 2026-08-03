"""다중 참가자 세션 격리 회귀 테스트.

LLM 호출이나 네트워크 없이 세션 레지스트리와 HTTP용 IO 큐만 검증한다.
"""
import tempfile
import threading
import time
import unittest

from experiment_runtime import (
    ExperimentSessionRegistry,
    SessionCancelled,
    SessionIOStream,
    current_session,
    session_scope,
)


class TestMultiSessionIsolation(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.registry = ExperimentSessionRegistry(
            log_dir=self.temp_dir.name,
            max_active_sessions=2,
        )

    def tearDown(self):
        self.registry.close_all()
        self.temp_dir.cleanup()

    def _new(self, participant):
        return self.registry.create(
            participant_id=participant,
            condition="centralized",
            task="A",
            brief="test brief",
        )

    def test_context_and_event_streams_are_isolated(self):
        first = self._new("P01")
        second = self._new("P02")

        with session_scope(first):
            self.assertIs(current_session(), first)
            first.emit({"type": "text", "content": {"content": "first"}})
        with session_scope(second):
            self.assertIs(current_session(), second)
            second.emit({"type": "text", "content": {"content": "second"}})

        first_events = first.poll(after=0, wait_seconds=0)
        second_events = second.poll(after=0, wait_seconds=0)
        self.assertEqual(first_events[0]["payload"]["content"]["content"], "first")
        self.assertEqual(second_events[0]["payload"]["content"]["content"], "second")

    def test_input_queues_are_isolated(self):
        first = self._new("P01")
        second = self._new("P02")
        first_io = SessionIOStream(first)
        second_io = SessionIOStream(second)
        results = {}

        one = threading.Thread(
            target=lambda: results.setdefault("first", first_io.input("first prompt"))
        )
        two = threading.Thread(
            target=lambda: results.setdefault("second", second_io.input("second prompt"))
        )
        one.start()
        two.start()

        time.sleep(0.05)
        second.submit_message("answer two")
        first.submit_message("answer one")
        one.join(timeout=1)
        two.join(timeout=1)

        self.assertEqual(results, {"first": "answer one", "second": "answer two"})

    def test_cancelling_one_session_does_not_cancel_another(self):
        first = self._new("P01")
        second = self._new("P02")
        first.cancel()

        with self.assertRaises(SessionCancelled):
            SessionIOStream(first).input()

        second.submit_message("still alive")
        self.assertEqual(SessionIOStream(second).input(), "still alive")
        self.assertTrue(second.is_current)

    def test_session_secret_is_required(self):
        session = self._new("P01")
        self.assertIs(self.registry.authorize(session.id, session.secret), session)
        self.assertIsNone(self.registry.authorize(session.id, "wrong-secret"))
        self.assertIsNone(self.registry.authorize("missing", session.secret))

    def test_active_session_limit_rejects_only_new_session(self):
        first = self._new("P01")
        second = self._new("P02")
        with self.assertRaisesRegex(RuntimeError, "동시 세션"):
            self._new("P03")

        first.mark_completed()
        third = self._new("P03")
        self.assertTrue(second.is_current)
        self.assertTrue(third.is_current)


if __name__ == "__main__":
    unittest.main()

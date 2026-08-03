import tempfile
import unittest

from experiment_runtime import ExperimentSessionRegistry, session_scope
from runtime_llm_logger import ContextRuntimeLogger


class FakeStore:
    def __init__(self):
        self.calls = []

    def record_llm_call(self, session, call):
        self.calls.append((session.id, call))


class TestRuntimeLLMLogger(unittest.TestCase):
    def test_usage_is_routed_to_current_session(self):
        with tempfile.TemporaryDirectory() as log_dir:
            registry = ExperimentSessionRegistry(log_dir, max_active_sessions=2)
            first = registry.create(
                participant_id="P01",
                condition="centralized",
                task="A",
                brief="brief",
            )
            second = registry.create(
                participant_id="P02",
                condition="decentralized",
                task="B",
                brief="brief",
            )
            store = FakeStore()
            logger = ContextRuntimeLogger(store)
            response = {
                "id": "provider-id",
                "model": "model-a",
                "usage": {
                    "prompt_tokens": 11,
                    "completion_tokens": 7,
                    "total_tokens": 18,
                    "prompt_tokens_details": {"cached_tokens": 3},
                    "completion_tokens_details": {"reasoning_tokens": 2},
                },
            }

            with session_scope(first):
                logger.log_chat_completion(
                    "call-1", 1, 1, "PM", {"model": "model-a"}, response,
                    0, 0.0, "2026-07-24T00:00:00+00:00",
                )
            with session_scope(second):
                logger.log_chat_completion(
                    "call-2", 1, 1, "Designer", {"model": "model-a"}, response,
                    0, 0.0, "2026-07-24T00:00:00+00:00",
                )

            self.assertEqual([item[0] for item in store.calls], [first.id, second.id])
            self.assertEqual(store.calls[0][1]["prompt_tokens"], 11)
            self.assertEqual(store.calls[0][1]["completion_tokens"], 7)
            self.assertEqual(store.calls[0][1]["cached_tokens"], 3)
            self.assertEqual(store.calls[0][1]["reasoning_tokens"], 2)
            registry.close_all()


if __name__ == "__main__":
    unittest.main()

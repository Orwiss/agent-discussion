import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock

from study_store import StudyStore


class TestPersistedSessionAuthorization(unittest.TestCase):
    def setUp(self):
        self.store = StudyStore()
        self.store.client = MagicMock()

    def tearDown(self):
        self.store.client = None
        self.store.close()

    def test_session_start_synchronously_stores_token_hash(self):
        session = SimpleNamespace(
            id="session-1",
            participant_id="P01",
            researcher_email="researcher@example.com",
            condition="centralized",
            task="A",
            created_at="2026-07-24T00:00:00+00:00",
            secret="browser-secret",
        )

        self.store.record_session_start(session)

        request = self.store.client.request
        request.assert_called_once()
        args, kwargs = request.call_args
        self.assertEqual(args, ("POST", "experiment_sessions"))
        self.assertEqual(
            kwargs["body"]["counts"]["_session_token_hash"],
            self.store.hash_session_token("browser-secret"),
        )

    def test_matching_database_session_is_authorized(self):
        self.store.client.request.return_value = [
            {
                "id": "session-1",
                "participant_id": "P01",
                "researcher_email": "researcher@example.com",
                "condition": "centralized",
                "task": "A",
                "status": "running",
                "started_at": "2026-07-24T00:00:00+00:00",
                "phase": "convergence",
                "counts": {
                    "_session_token_hash": self.store.hash_session_token(
                        "browser-secret"
                    )
                },
            }
        ]

        session = self.store.authorize_experiment_session(
            "session-1",
            "browser-secret",
            "researcher@example.com",
        )

        self.assertIsNotNone(session)
        self.assertEqual(session.participant_id, "P01")
        self.assertEqual(session.phase, "convergence")

    def test_wrong_database_session_token_is_rejected(self):
        self.store.client.request.return_value = [
            {
                "id": "session-1",
                "participant_id": "P01",
                "researcher_email": "researcher@example.com",
                "condition": "centralized",
                "task": "A",
                "status": "running",
                "counts": {
                    "_session_token_hash": self.store.hash_session_token(
                        "browser-secret"
                    )
                },
            }
        ]

        session = self.store.authorize_experiment_session(
            "session-1",
            "wrong-secret",
            "researcher@example.com",
        )

        self.assertIsNone(session)


if __name__ == "__main__":
    unittest.main()

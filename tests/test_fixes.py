"""토론 라운드 예산과 최종 양식 전환의 가벼운 회귀 테스트."""
import unittest
from unittest.mock import MagicMock


class TestRoundBudget(unittest.TestCase):
    def test_phase_round_budgets_cover_agent_and_participant_turns(self):
        from meeting.decentralized import PHASE_MAX_ROUNDS

        self.assertGreaterEqual(PHASE_MAX_ROUNDS["divergence"], 15)
        self.assertGreaterEqual(PHASE_MAX_ROUNDS["elaboration"], 15)
        self.assertGreaterEqual(PHASE_MAX_ROUNDS["convergence"], 14)

    def test_speaker_selection_completes_six_agent_turns(self):
        from meeting.decentralized import _create_6turn_speaker_selection

        agents = [MagicMock() for _ in range(3)]
        for index, agent in enumerate(agents):
            agent.name = f"Agent{index}"
        user = MagicMock()
        user.name = "Participant"
        select = _create_6turn_speaker_selection(agents, user)

        last = user
        sequence = []
        for _ in range(7):
            last = select(last, MagicMock())
            sequence.append(last.name)

        self.assertEqual(sequence[:6], [
            "Agent0", "Agent1", "Agent2", "Agent0", "Agent1", "Agent2",
        ])
        self.assertEqual(sequence[6], "Participant")


class TestConvergeConfirmation(unittest.TestCase):
    def test_empty_and_nonempty_feedback(self):
        from meeting.decentralized import confirm_convergence

        empty = MagicMock()
        empty.input.return_value = ""
        self.assertIsNone(confirm_convergence(empty))

        filled = MagicMock()
        filled.input.return_value = "좀 더 구체적으로 가주세요"
        self.assertEqual(
            confirm_convergence(filled),
            "좀 더 구체적으로 가주세요",
        )


class TestFormMarker(unittest.TestCase):
    def test_frontend_contains_form_and_topic_markers(self):
        import web

        html = web._render_html_page()
        self.assertIn(web.FORM_REQUEST_MARKER, html)
        self.assertIn(web.TOPIC_MARKER, html)
        self.assertNotIn("new WebSocket", html)
        self.assertIn("/api/sessions", html)
        self.assertIn("toUpperCase() || 'P99'", html)
        self.assertIn("result.participant_id || pid || 'P99'", html)
        self.assertIn("text-overflow: ellipsis", html)
        self.assertIn(".sub-row .avatar", html)
        self.assertIn("border-radius: 50%", html)
        self.assertIn("max-width: 50%; overflow: hidden", html)
        self.assertIn("padding: 12px 16px;", html)
        self.assertIn(".sub-gist { font-size: 16px", html)
        self.assertIn("cursor: pointer; padding: 0; margin: 0;", html)

    def test_blank_participant_id_falls_back_to_p99(self):
        from unittest.mock import patch

        import web

        session = MagicMock()
        with (
            patch.object(web.SESSION_REGISTRY, "create", return_value=session) as create,
            patch.object(web.STUDY_STORE, "record_session_start"),
            patch.object(web.threading, "Thread") as thread,
        ):
            result = web.start_session(
                "",
                "centralized",
                next(iter(web.BRIEFS)),
                "orwiss.design@gmail.com",
            )

        self.assertIs(result, session)
        self.assertEqual(create.call_args.kwargs["participant_id"], "P99")
        thread.return_value.start.assert_called_once_with()


if __name__ == "__main__":
    unittest.main(verbosity=2)

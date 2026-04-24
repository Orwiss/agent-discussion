"""
#2 #3 #4 수정에 대한 단위 테스트.
실제 LLM 호출 없이 로직만 검증.

실행: python -m unittest tests.test_fixes
"""
import sys
import os
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


# ──────────────────────────────────────────────────────────────────────
# #2: 라운드 예산이 18 에이전트 발화 + 사용자 응답 사이클을 다 수용해야 함
# ──────────────────────────────────────────────────────────────────────
class TestRoundBudget(unittest.TestCase):
    """페이즈당 max_round가 18 에이전트 발화 + 사용자 개입을 끊지 않고 수용하는지."""

    def test_max_round_constant_exists(self):
        """PHASE_MAX_ROUND 상수가 명시되어 있어야 한다."""
        from meeting import stage2
        self.assertTrue(
            hasattr(stage2, "PHASE_MAX_ROUND"),
            "stage2.PHASE_MAX_ROUND 상수가 필요하다 (매직넘버 24 제거)",
        )

    def test_max_round_fits_three_full_cycles(self):
        """max_round는 3 full cycle (initial + 3*(6 agents + 1 user))을 수용해야 한다.

        cycle 구조: 1 initial msg + 3 * (6 agent + 1 user) = 22.
        사용자가 마지막 사이클에서 의견 내면 한 번 더 응답 사이클이 필요 → +6 = 28.
        SE 누락 방지를 위해 최소 22, 마지막 사용자 의견 응답 보장 위해 28 권장.
        """
        from meeting import stage2
        self.assertGreaterEqual(
            stage2.PHASE_MAX_ROUND, 22,
            "최소 18 agent + 2 user + 1 initial = 21 이상이어야 한다",
        )

    def test_speaker_selection_completes_cycle(self):
        """speaker_selection이 6 에이전트 발화 후에만 user를 부르고, 중간 truncate되지 않는다."""
        from meeting.stage2 import _create_6turn_speaker_selection

        # mock agents/user
        agents = [MagicMock(name=f"A{i}") for i in range(3)]
        for i, a in enumerate(agents):
            a.name = f"Agent{i}"
            a.system_message = "base"
            a.update_system_message = MagicMock(side_effect=lambda m, agent=a: setattr(agent, "system_message", m))
        user = MagicMock(name="user")
        user.name = "Participant"

        groupchat = MagicMock()
        select_fn = _create_6turn_speaker_selection(agents, user)

        # initial 후 user → agent[0]
        last = user
        sequence = []
        for _ in range(20):
            nxt = select_fn(last, groupchat)
            if nxt is None:
                break
            sequence.append(nxt.name)
            last = nxt
            if nxt is user:
                # user가 다시 발화하면 다음 라운드는 user 입력으로 처리
                pass

        # 첫 6개는 모두 에이전트, 7번째에 user 등장
        first_seven = sequence[:7]
        agent_count = sum(1 for n in first_seven if n != "Participant")
        self.assertEqual(
            agent_count, 6,
            f"user 호출 전에 정확히 6 에이전트 발화 필요: {first_seven}"
        )
        self.assertEqual(
            first_seven[6], "Participant",
            f"7번째는 Participant여야 한다: {first_seven}"
        )


# ──────────────────────────────────────────────────────────────────────
# #3: 수렴 페이즈 종료 전 사용자 확인 기회
# ──────────────────────────────────────────────────────────────────────
class TestConvergeConfirmation(unittest.TestCase):
    """최종 요약 직전 사용자에게 추가 의견 기회를 주는지."""

    def test_iostream_input_called_before_summary(self):
        """run_stage2_discussion 종료 직전 iostream.input이 한 번 더 호출되어야 한다.

        구현 방식: run_stage2_discussion이 마지막 페이즈 후 iostream.input을 호출.
        빈 입력이면 그대로 종료, 비어있지 않으면 로그에 사용자 추가 의견 기록.
        """
        from meeting import stage2

        # confirm_convergence 헬퍼가 export 되어 있어야 한다
        self.assertTrue(
            hasattr(stage2, "confirm_convergence"),
            "stage2.confirm_convergence(iostream) 함수가 필요하다",
        )

        # 빈 입력이면 None 반환
        io = MagicMock()
        io.input = MagicMock(return_value="")
        result = stage2.confirm_convergence(io)
        self.assertIsNone(result, "빈 입력은 None을 반환해야 한다")
        io.input.assert_called_once()

        # 비어있지 않은 입력은 그대로 반환
        io2 = MagicMock()
        io2.input = MagicMock(return_value="좀 더 구체적으로 가주세요")
        result2 = stage2.confirm_convergence(io2)
        self.assertEqual(result2, "좀 더 구체적으로 가주세요")


# ──────────────────────────────────────────────────────────────────────
# #4: 요약 메시지가 마크다운 마커와 함께 전송됨
# ──────────────────────────────────────────────────────────────────────
class TestSummaryMarker(unittest.TestCase):
    """최종 요약 출력이 프론트에서 분기 가능한 마커를 포함하는지."""

    def test_summary_marker_constant_exists(self):
        """SUMMARY_MARKER 상수가 web.py에 있어야 한다."""
        import web
        self.assertTrue(
            hasattr(web, "SUMMARY_MARKER"),
            "web.SUMMARY_MARKER 상수가 필요하다",
        )
        self.assertTrue(
            web.SUMMARY_MARKER.startswith("[") and web.SUMMARY_MARKER.endswith("]"),
            "마커는 식별 가능한 [...] 형태여야 한다",
        )

    def _run_session_capture(self, condition):
        """_run_session 1회 실행, print된 메시지 리스트 반환."""
        import web

        printed = []
        io = MagicMock()
        io.print = lambda *args, **kw: printed.append(" ".join(str(a) for a in args))
        io.input = MagicMock(return_value="")

        with patch("web.create_simple_ux"), \
             patch("web.create_simple_visual"), \
             patch("web.create_simple_engineer"), \
             patch("web.run_stage2_discussion") as mock_disc, \
             patch("web.run_stage2_independent") as mock_indep:
            mock_result = MagicMock()
            mock_result.summary = "## 테스트 요약\n\n| 축 | 값 |\n|---|---|\n| A | 1 |"
            mock_disc.return_value = mock_result
            mock_indep.return_value = mock_result

            web._run_session(io, condition, "test brief")
        return printed

    def test_run_session_emits_summary_with_marker_discussion(self):
        """discussion 조건에서 SUMMARY_MARKER + 요약 본문이 print된다."""
        import web
        printed = self._run_session_capture("discussion")
        marker_msgs = [p for p in printed if web.SUMMARY_MARKER in p and "테스트 요약" in p]
        self.assertGreater(
            len(marker_msgs), 0,
            f"discussion: SUMMARY_MARKER+본문 print 필요. printed={printed}"
        )

    def test_run_session_emits_summary_with_marker_independent(self):
        """independent 조건도 동일하게 요약 본문이 마커와 함께 전달되어야 한다.

        regression: 이전에는 isinstance(result, dict) 체크로 인해 항상 '요약 없음'으로 죽었음.
        """
        import web
        printed = self._run_session_capture("independent")
        marker_msgs = [p for p in printed if web.SUMMARY_MARKER in p and "테스트 요약" in p]
        self.assertGreater(
            len(marker_msgs), 0,
            f"independent: 요약이 마커와 함께 print되어야 한다. printed={printed}"
        )

    def test_frontend_renders_summary_as_markdown(self):
        """렌더링된 HTML에 마크다운 라이브러리(marked)와 SUMMARY_MARKER 분기 처리가 포함되어야 한다."""
        import web
        html = web._render_html_page()

        # 마크다운 라이브러리 로드
        self.assertIn(
            "marked", html.lower(),
            "marked.js 라이브러리 로드가 필요하다",
        )

        # SUMMARY_MARKER가 JS 코드에 박혀있어야 한다 (placeholder가 치환된 결과)
        self.assertIn(
            web.SUMMARY_MARKER, html,
            "프론트엔드 JS에 SUMMARY_MARKER 상수가 주입되어야 한다",
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)

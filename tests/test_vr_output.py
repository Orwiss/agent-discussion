"""VR 출력 레이어 연결부 검증.

외부 서비스를 전혀 안 쓴다 — tts_pipeline을 가짜 모듈로 갈아끼워서,
"화면에 뜨는 발화가 그대로, 순서대로, 메타휴먼 쪽으로 넘어가는가"만 본다.
ElevenLabs·Audio2Face·UE5가 없어도 돌아간다.
"""
import importlib
import sys
import tempfile
import threading
import time
import types
import unittest

import vr_output
from experiment_runtime import ExperimentSessionRegistry, SessionIOStream


def text_event(sender: str, content: str, summary: str = "") -> dict:
    """centralized의 _push_to_ui / decentralized의 AG2 메시지가 만드는 것과 같은 모양."""
    body = {"sender": sender, "recipient": "PM", "content": content}
    if summary:
        body["summary"] = summary
    return {"type": "text", "content": body}


class FakeTTS:
    """tts_pipeline 대역. trigger()가 받은 발화를 순서대로 쌓아둔다."""

    def __init__(self, speak_seconds: float = 0.0) -> None:
        self.spoken: list[tuple[str, str]] = []
        self.finished_at: list[float] = []
        self.speak_seconds = speak_seconds
        self._idle = threading.Event()
        self._idle.set()
        self._lock = threading.Lock()

    def trigger(self, agent_name: str, text: str) -> None:
        # 진짜 trigger()와 같은 규칙: 직전 발화가 끝날 때까지 기다렸다가,
        # 이번 발화는 백그라운드로 돌리고 바로 리턴한다.
        self._idle.wait()
        self._idle.clear()

        def _run():
            try:
                time.sleep(self.speak_seconds)
                with self._lock:
                    self.spoken.append((agent_name, text))
                    # idle 신호를 올리기 전에 찍는다 — 대기 해제보다 반드시 먼저다.
                    self.finished_at.append(time.perf_counter())
            finally:
                self._idle.set()

        threading.Thread(target=_run, daemon=True).start()

    def wait_until_idle(self, timeout=None) -> bool:
        return self._idle.wait(timeout)

    def as_module(self) -> types.ModuleType:
        module = types.ModuleType("tts_pipeline")
        module.trigger = self.trigger
        module.wait_until_idle = self.wait_until_idle
        return module


class VROutputTestCase(unittest.TestCase):
    """vr_output은 모듈 전역 상태(플래그 캐시·워커 스레드)를 들고 있어서
    테스트마다 새로 불러온다."""

    def reload_vr(self, enabled: bool, fake: FakeTTS | None = None):
        global vr_output
        if fake is not None:
            sys.modules["tts_pipeline"] = fake.as_module()
        else:
            sys.modules.pop("tts_pipeline", None)
        import vr_output as module

        module = importlib.reload(module)
        module._enabled_cache = enabled
        vr_output = module
        # experiment_runtime이 붙들고 있는 참조도 같이 갈아끼운다.
        import experiment_runtime

        experiment_runtime.vr_output = module
        self.addCleanup(lambda: sys.modules.pop("tts_pipeline", None))
        return module


class TestExtract(VROutputTestCase):
    """어떤 이벤트를 메타휴먼이 말해야 하는지 — 프론트엔드 필터와 같아야 한다."""

    def setUp(self):
        self.vr = self.reload_vr(enabled=False)

    def test_agent_utterance_passes(self):
        self.assertEqual(
            self.vr.extract(text_event("Designer", "사용자가 먼저 고르게 하죠.")),
            ("Designer", "사용자가 먼저 고르게 하죠."),
        )

    def test_chat_manager_and_participant_are_skipped(self):
        # 둘 다 화면에 말풍선으로 안 그려진다. 특히 Participant로 나가는
        # 오프닝 안내를 메타휴먼이 읽으면 안 된다.
        self.assertIsNone(self.vr.extract(text_event("chat_manager", "다음 차례")))
        self.assertIsNone(self.vr.extract(text_event("Participant", "회의를 시작합니다.")))

    def test_non_text_events_are_skipped(self):
        self.assertIsNone(self.vr.extract({"type": "print", "content": {"objects": ["[시스템]"]}}))
        self.assertIsNone(self.vr.extract({"type": "input_request", "content": {"prompt": ""}}))

    def test_terminate_and_think_are_stripped(self):
        sender, text = self.vr.extract(
            text_event("PM", "<think>고민 중</think>정리하면 이렇습니다. TERMINATE")
        )
        self.assertEqual(sender, "PM")
        self.assertEqual(text, "정리하면 이렇습니다.")

    def test_empty_after_stripping_is_skipped(self):
        self.assertIsNone(self.vr.extract(text_event("PM", "  TERMINATE  ")))
        self.assertIsNone(self.vr.extract(text_event("PM", "")))

    def test_summary_is_ignored_full_text_is_spoken(self):
        # 화면에서는 접힘 미리보기가 보이지만, 말하는 건 전문이다.
        _, text = self.vr.extract(
            text_event("Engineer", "기술적으로는 가능합니다.", summary="가능함")
        )
        self.assertEqual(text, "기술적으로는 가능합니다.")


class TestDisabled(VROutputTestCase):
    """플래그가 꺼져 있으면 아무것도 안 하고, tts_pipeline을 import도 안 한다."""

    def setUp(self):
        self.vr = self.reload_vr(enabled=False)

    def test_dispatch_is_a_noop(self):
        self.vr.dispatch(text_event("PM", "안녕하세요."))
        self.assertIsNone(self.vr._worker)
        self.assertTrue(self.vr._queue.empty())

    def test_tts_pipeline_is_never_imported(self):
        self.vr.dispatch(text_event("PM", "안녕하세요."))
        self.assertNotIn("tts_pipeline", sys.modules)

    def test_wait_until_idle_returns_immediately(self):
        started = time.perf_counter()
        self.assertTrue(self.vr.wait_until_idle())
        self.assertLess(time.perf_counter() - started, 0.5)


class TestEnabledThroughSession(VROutputTestCase):
    """진짜 ExperimentSession.emit()을 통과시켜서, 실제 발화 흐름이
    메타휴먼 쪽으로 어떻게 넘어가는지 본다."""

    def setUp(self):
        self.fake = FakeTTS()
        self.vr = self.reload_vr(enabled=True, fake=self.fake)
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        registry = ExperimentSessionRegistry(self.temp_dir.name, max_active_sessions=3)
        self.session = registry.create(
            participant_id="P01", condition="centralized", task="A", brief="테스트 과제"
        )
        # 윈도우에서는 로그 파일 핸들이 열려 있으면 임시폴더가 안 지워진다.
        self.addCleanup(self.session.cancel)

    def drain(self, timeout=5.0):
        self.assertTrue(self.vr.wait_until_idle(timeout), "발화가 시간 안에 안 끝남")

    def test_only_displayed_utterances_reach_the_metahuman(self):
        # centralized 한 라운드를 그대로 재현한다.
        self.session.emit(text_event("Participant", "회의를 시작합니다."))   # 화면 X
        self.session.emit(text_event("PM", "디자이너, 어떤 방향이 있을까요?"))
        self.session.emit(text_event("Designer", "고르는 재미를 주면 좋겠습니다.", summary="선택"))
        self.session.emit(text_event("Engineer", "구현은 어렵지 않습니다.", summary="가능"))
        self.session.emit({"type": "print", "content": {"objects": ["[시스템] 안내"]}})  # 화면 O, 말은 X
        self.session.emit(text_event("PM", "정리하면 이렇습니다."))
        self.drain()

        self.assertEqual(
            self.fake.spoken,
            [
                ("PM", "디자이너, 어떤 방향이 있을까요?"),
                ("Designer", "고르는 재미를 주면 좋겠습니다."),
                ("Engineer", "구현은 어렵지 않습니다."),
                ("PM", "정리하면 이렇습니다."),
            ],
        )

    def test_emit_does_not_block_on_speech(self):
        # trigger()가 오래 걸려도 emit()은 바로 돌아와야 한다.
        # 여기서 막히면 이벤트 큐 락을 쥔 채로 멈춰서 브라우저 폴링까지 멈춘다.
        slow = FakeTTS(speak_seconds=1.0)
        self.vr = self.reload_vr(enabled=True, fake=slow)
        started = time.perf_counter()
        for i in range(3):
            self.session.emit(text_event("PM", f"{i}번째 발언입니다."))
        elapsed = time.perf_counter() - started
        self.assertLess(elapsed, 0.5, f"emit()이 발화에 붙잡혔다 ({elapsed:.2f}초)")

    def test_events_still_reach_the_browser(self):
        # 메타휴먼으로 보내는 것과 별개로, 브라우저 이벤트는 그대로 쌓여야 한다.
        self.session.emit(text_event("PM", "첫 발언입니다."))
        events = self.session.poll(after=0, wait_seconds=0)
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["payload"]["content"]["sender"], "PM")


class TestParticipantTurnWaitsForSpeech(VROutputTestCase):
    """메타휴먼이 말하는 중에 참가자 입력창이 열리면 안 된다."""

    def setUp(self):
        self.fake = FakeTTS(speak_seconds=0.6)
        self.vr = self.reload_vr(enabled=True, fake=self.fake)
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        registry = ExperimentSessionRegistry(self.temp_dir.name, max_active_sessions=3)
        self.session = registry.create(
            participant_id="P02", condition="centralized", task="A", brief="테스트 과제"
        )
        self.addCleanup(self.session.cancel)

    def test_input_request_is_emitted_after_the_speech_finishes(self):
        """이게 이 포팅의 핵심 보증이다 — 발화가 끝난 뒤에야 입력창이 열려야 한다."""
        self.session.emit(text_event("PM", "어떻게 생각하세요?"))

        asked_at: list[float] = []
        real_emit = self.session.emit

        def spy(payload):
            if payload.get("type") == "input_request":
                asked_at.append(time.perf_counter())
            return real_emit(payload)

        self.session.emit = spy

        # input()은 참가자 답을 기다리며 막히므로, 답은 미리 넣어둔다.
        self.session.submit_message("좋습니다.")
        SessionIOStream(self.session).input("당신의 차례입니다.")

        self.assertEqual(len(asked_at), 1, "input_request가 한 번 나와야 한다")
        self.assertEqual(self.fake.spoken, [("PM", "어떻게 생각하세요?")])
        self.assertTrue(self.fake.finished_at, "발화 종료 시각을 못 잡았다")
        self.assertLess(
            self.fake.finished_at[0], asked_at[0],
            "메타휴먼이 아직 말하는 중인데 입력창이 열렸다",
        )

    def test_drain_timeout_does_not_trap_the_participant(self):
        # TTS가 멈춰도 참가자가 영영 기다리게 되면 안 된다.
        stuck = FakeTTS(speak_seconds=30.0)
        self.vr = self.reload_vr(enabled=True, fake=stuck)
        self.session.emit(text_event("PM", "멈춘 발화입니다."))
        started = time.perf_counter()
        self.assertFalse(self.vr.wait_until_idle(timeout=0.3))
        self.assertLess(time.perf_counter() - started, 3.0)


if __name__ == "__main__":
    unittest.main()

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


def text_event(sender: str, content: str, summary: str = "", step: str = "") -> dict:
    """centralized의 _push_to_ui / decentralized의 AG2 메시지가 만드는 것과 같은 모양."""
    body = {"sender": sender, "recipient": "PM", "content": content}
    if summary:
        body["summary"] = summary
    if step:
        body["step"] = step
    return {"type": "text", "content": body}


class FakeTTS:
    """tts_pipeline 대역. trigger()가 받은 발화를 순서대로 쌓아둔다."""

    def __init__(self, speak_seconds: float = 0.0) -> None:
        self.spoken: list[tuple[str, str]] = []
        self.finished_at: list[float] = []
        self.started_at: list[float] = []
        self.speak_seconds = speak_seconds
        self.turns: list[str] = []
        self.resets: list[float] = []
        # centralized 타이핑 신호: (시각, 사람, True=시작/False=끝), 요약 자막: (시각, 사람, 텍스트)
        self.typing: list[tuple[float, str, bool]] = []
        self.summaries: list[tuple[float, str, str]] = []
        self.conditions: list[str] = []
        self.resent: list[str | None] = []
        self.resent_typing: list[str] = []
        self.routing_flags: list[bool] = []
        self.gate = threading.Event()  # 진짜 hold_playback()처럼 재생만 막는다
        self.gate.set()
        self._idle = threading.Event()
        self._idle.set()
        self._lock = threading.Lock()

    def trigger(self, agent_name: str, text: str, recipient: str = "", routing: bool = False) -> None:
        # 진짜 trigger()와 같은 규칙: 직전 발화가 끝날 때까지 기다렸다가,
        # 이번 발화는 백그라운드로 돌리고 바로 리턴한다.
        self.routing_flags.append(routing)
        self._idle.wait()
        self._idle.clear()
        resets = len(self.resets)

        def _run():
            try:
                self.gate.wait()
                if len(self.resets) != resets:  # 재생을 기다리는 동안 취소됐다 — 진짜처럼 버린다
                    return
                self.started_at.append(time.perf_counter())
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
        module.send_turn = lambda state: self.turns.append(state)
        module.reset = self._reset
        module.send_typing = lambda agent, on: self.typing.append((time.perf_counter(), agent, on))
        module.send_summary_subtitle = lambda agent, text: self.summaries.append((time.perf_counter(), agent, text))
        module.send_condition = self.conditions.append
        module.resend_condition = lambda condition=None: self.resent.append(condition)
        module.resend_typing = self.resent_typing.append
        module.hold_playback = self.gate.clear
        module.release_playback = self.gate.set
        return module

    def _reset(self) -> None:
        self.resets.append(time.perf_counter())
        self.gate.set()

    def typing_times(self, agent: str, on: bool) -> list[float]:
        return [t for t, a, o in self.typing if a == agent and o == on]


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
            ("Designer", "사용자가 먼저 고르게 하죠.", "PM"),
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
        sender, text, _ = self.vr.extract(
            text_event("PM", "<think>고민 중</think>정리하면 이렇습니다. TERMINATE")
        )
        self.assertEqual(sender, "PM")
        self.assertEqual(text, "정리하면 이렇습니다.")

    def test_empty_after_stripping_is_skipped(self):
        self.assertIsNone(self.vr.extract(text_event("PM", "  TERMINATE  ")))
        self.assertIsNone(self.vr.extract(text_event("PM", "")))

    def test_summary_is_ignored_full_text_is_spoken(self):
        # 화면에서는 접힘 미리보기가 보이지만, 말하는 건 전문이다.
        _, text, _ = self.vr.extract(
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


class TestBackpressureAndCancel(VROutputTestCase):
    """대화 생성이 음성보다 너무 앞서가지 않고, 세션을 취소하면 남은 대사가 버려진다."""

    def setUp(self):
        self.fake = FakeTTS(speak_seconds=1.0)
        self.vr = self.reload_vr(enabled=True, fake=self.fake)
        self.vr.MAX_AHEAD = 1
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        registry = ExperimentSessionRegistry(self.temp_dir.name, max_active_sessions=3)
        self.session = registry.create(
            participant_id="P03", condition="centralized", task="A", brief="테스트 과제"
        )

    def test_generation_waits_when_far_ahead_and_cancel_releases_it(self):
        done = threading.Event()

        def generate():
            for i in range(4):
                self.session.emit(text_event("PM", f"{i}번째 발언입니다."))
            done.set()

        threading.Thread(target=generate, daemon=True).start()
        # 0번은 말하는 중, 1번은 TTS가 붙잡고 있고, 2번이 대기열에 있으니 3번은 기다려야 한다
        self.assertFalse(done.wait(0.4), "대화 생성이 음성보다 한없이 앞서갔다")
        # 기다리는 중에도 브라우저에는 이미 만든 발화가 다 보인다
        texts = [e["payload"]["content"]["content"] for e in self.session.poll(after=0, wait_seconds=0)]
        self.assertEqual(len(texts), 4)

        self.session.cancel()
        self.assertTrue(done.wait(1.0), "취소했는데 대화 생성이 계속 붙잡혀 있다")
        self.assertTrue(self.vr.wait_until_idle(5))
        self.session.emit(text_event("PM", "취소 뒤 발언입니다."))  # 취소된 세션 — 받으면 안 된다
        time.sleep(0.3)
        self.assertTrue(self.vr.wait_until_idle(5))
        spoken = [text for _, text in self.fake.spoken]
        self.assertEqual(spoken, ["0번째 발언입니다.", "1번째 발언입니다."],
                         "취소 전에 이미 만들던 것까지만 말하고 나머지는 버려야 한다")
        self.assertEqual(len(self.fake.resets), 1)
        self.assertEqual(self.fake.turns[-1], "agents", "취소하면 참가자 차례를 닫아야 한다")


class TestResponseTimingInVR(VROutputTestCase):
    """VR에서는 참가자 응답 시간을 메타휴먼 말이 끝나고 차례가 열린 순간부터 잰다."""

    def test_wait_is_measured_from_turn_open(self):
        fake = FakeTTS()
        self.reload_vr(enabled=True, fake=fake)
        temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(temp_dir.cleanup)
        session = ExperimentSessionRegistry(temp_dir.name).create(
            participant_id="P04", condition="centralized", task="A", brief="테스트 과제"
        )
        self.addCleanup(session.cancel)
        threading.Timer(0.3, session.submit_message, args=("좋아요",)).start()
        SessionIOStream(session).input("당신의 차례입니다.")
        wait_s = session.intervention_timings[0]["wait_s"]
        self.assertAlmostEqual(wait_s, 0.3, delta=0.15)
        self.assertEqual(fake.turns, ["participant", "agents"])


class TestCentralizedTyping(VROutputTestCase):
    """centralized: 디자이너·엔지니어의 sub-chat 답은 말하지 않고 타이핑한다.
    라우팅이 끝나면 둘이 같이 시작 → 각자 말했을 시간만큼 → 끝나면서 요약 자막 → 둘 다 끝나야 PM 종합."""

    ROUTING = "디자이너와 엔지니어, 각각 어떻게 보세요?"
    D_TEXT = "가" * 40   # 0.4초 (아래에서 초당 100자로 둔다)
    E_TEXT = "나" * 40   # 0.8초 (초당 50자)

    def setUp(self):
        self.fake = FakeTTS(speak_seconds=0.3)
        self.vr = self.reload_vr(enabled=True, fake=self.fake)
        self.vr.TTS_CHARS_PER_SEC = {"Designer": 100.0, "Engineer": 50.0}
        # 시간 조정(축소·요약 간격)은 아래 test_summaries_get_a_gap 에서만 켠다
        self.vr.TYPING_TIME_SCALE = 1.0
        self.vr.SUMMARY_GAP = 0.0
        self.vr.MIN_TYPING_SEC = 0.0
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        registry = ExperimentSessionRegistry(self.temp_dir.name, max_active_sessions=3)
        self.session = registry.create(
            participant_id="P05", condition="centralized", task="A", brief="테스트 과제"
        )
        self.addCleanup(self.session.cancel)

    def emit_subchats(self, d_text=None, e_text=None):
        if d_text is not False:
            self.session.emit(text_event("Designer", d_text or self.D_TEXT, summary="디자인 요약", step="subchat"))
        if e_text is not False:
            self.session.emit(text_event("Engineer", e_text or self.E_TEXT, summary="기술 요약", step="subchat"))

    def test_one_round(self):
        generation = threading.Thread(target=lambda: (
            self.session.emit(text_event("PM", self.ROUTING, step="routing")),
            self.emit_subchats(),
            self.session.emit(text_event("PM", "정리하면 이렇습니다.")),
        ), daemon=True)
        generation.start()
        generation.join(10)
        self.assertTrue(self.vr.wait_until_idle(10))
        f = self.fake

        # sub-chat 답은 말하지 않는다
        self.assertEqual(f.spoken, [("PM", self.ROUTING), ("PM", "정리하면 이렇습니다.")])

        # 라우팅 발화가 끝난 뒤에 둘이 같이 시작
        d_on, e_on = f.typing_times("Designer", True)[0], f.typing_times("Engineer", True)[0]
        self.assertGreaterEqual(d_on, f.finished_at[0])
        self.assertAlmostEqual(d_on, e_on, delta=0.3)

        # 각자 말했을 시간만큼 (서로 상관없이)
        d_off, e_off = f.typing_times("Designer", False)[0], f.typing_times("Engineer", False)[0]
        self.assertAlmostEqual(d_off - d_on, 0.4, delta=0.15)
        self.assertAlmostEqual(e_off - e_on, 0.8, delta=0.15)

        # 끝나자마자 그 사람의 요약(전문이 아니라)이 자막으로
        self.assertEqual([(a, t) for _, a, t in f.summaries], [("Designer", "디자인 요약"), ("Engineer", "기술 요약")])
        self.assertAlmostEqual(f.summaries[0][0], d_off, delta=0.05)
        self.assertAlmostEqual(f.summaries[1][0], e_off, delta=0.05)

        # PM 종합은 둘 다 끝난 뒤에야 재생
        self.assertGreaterEqual(f.started_at[1], e_off)

    def test_summaries_get_a_gap(self):
        """두 요약이 SUMMARY_GAP보다 붙으면 먼저 끝나는 쪽을 당기고, 마지막 요약 뒤 SUMMARY_GAP 뒤에 PM 종합."""
        self.vr.SUMMARY_GAP = 0.5
        self.vr.MIN_TYPING_SEC = 0.1
        generation = threading.Thread(target=lambda: (
            self.session.emit(text_event("PM", self.ROUTING, step="routing")),
            self.emit_subchats(),
            self.session.emit(text_event("PM", "정리하면 이렇습니다.")),
        ), daemon=True)
        generation.start()
        generation.join(10)
        self.assertTrue(self.vr.wait_until_idle(10))
        f = self.fake
        d_on = f.typing_times("Designer", True)[0]
        d_off, e_off = f.typing_times("Designer", False)[0], f.typing_times("Engineer", False)[0]
        self.assertGreaterEqual(e_off - d_off, 0.5 - 0.05, "요약 사이 간격")
        self.assertAlmostEqual(d_off - d_on, 0.3, delta=0.15)   # 0.4초에서 당겨짐
        self.assertGreaterEqual(f.started_at[1], e_off + 0.5 - 0.05, "마지막 요약 뒤 간격")

    def test_keeps_typing_until_the_reply_exists(self):
        self.session.emit(text_event("PM", self.ROUTING, step="routing"))
        time.sleep(1.2)  # 답 생성이 늦다 — 말했을 시간(0.4/0.8초)은 이미 지났다
        ready = time.perf_counter()
        self.emit_subchats()
        self.assertTrue(self.vr.wait_until_idle(10))
        d_off = self.fake.typing_times("Designer", False)[0]
        e_off = self.fake.typing_times("Engineer", False)[0]
        self.assertGreaterEqual(d_off, ready - 0.05)
        self.assertGreaterEqual(e_off, ready - 0.05)
        self.assertLess(e_off - ready, 0.3, "답이 오면 바로 끝내야 한다")

    def test_participant_turn_waits_for_typing(self):
        self.session.emit(text_event("PM", self.ROUTING, step="routing"))
        self.emit_subchats()
        self.assertTrue(self.vr.wait_until_idle(10))
        self.assertEqual(len(self.fake.typing_times("Engineer", False)), 1,
                         "타이핑이 끝나기 전에 참가자 차례가 열린다")

    def test_empty_reply_does_not_hang(self):
        # 엔지니어 답이 비면 centralized는 그 이벤트를 안 보낸다 — 엔지니어 타이핑도 끝나야 한다
        self.session.emit(text_event("PM", self.ROUTING, step="routing"))
        self.emit_subchats(e_text=False)
        self.session.emit(text_event("PM", "정리하면 이렇습니다."))
        self.assertTrue(self.vr.wait_until_idle(5))
        self.assertEqual(len(self.fake.typing_times("Engineer", False)), 1)
        self.assertEqual([a for _, a, _ in self.fake.summaries], ["Designer"])
        self.assertEqual(self.fake.spoken[-1], ("PM", "정리하면 이렇습니다."))

    def test_without_routing_utterance_typing_starts_at_the_reply(self):
        self.emit_subchats()
        self.session.emit(text_event("PM", "정리하면 이렇습니다."))
        self.assertTrue(self.vr.wait_until_idle(5))
        self.assertEqual(self.fake.spoken, [("PM", "정리하면 이렇습니다.")])
        self.assertEqual(len(self.fake.summaries), 2)

    def test_cancel_stops_typing(self):
        self.vr.TTS_CHARS_PER_SEC = {"Designer": 1.0, "Engineer": 1.0}  # 40초 — 취소 전에 안 끝난다
        self.session.emit(text_event("PM", self.ROUTING, step="routing"))
        self.emit_subchats()
        self.session.emit(text_event("PM", "정리하면 이렇습니다."))
        deadline = time.perf_counter() + 5
        while len(self.fake.typing) < 2 and time.perf_counter() < deadline:
            time.sleep(0.05)
        self.session.cancel()
        time.sleep(0.3)
        self.assertEqual(self.fake.summaries, [], "취소했는데 요약 자막이 떴다")
        self.assertEqual(self.fake.spoken, [("PM", self.ROUTING)], "취소했는데 PM 종합이 재생됐다")
        self.assertEqual(sorted(a for _, a, on in self.fake.typing if not on), ["Designer", "Engineer"])
        self.assertEqual(self.fake.conditions[-1], "decentralized")

    def test_only_the_routing_utterance_gets_per_sentence_recipients(self):
        self.session.emit(text_event("PM", self.ROUTING, step="routing"))
        self.emit_subchats()
        self.session.emit(text_event("PM", "정리하면 이렇습니다."))
        self.assertTrue(self.vr.wait_until_idle(10))
        self.assertEqual(self.fake.routing_flags, [True, False], "종합 발화까지 라우팅으로 보냈다")

    def test_heartbeat_resends_current_typing(self):
        """UE가 타이핑 도중 다시 켜져도 이어서 치도록, 주기 신호에 지금 타이핑 중인 사람을 넣는다."""
        self.vr.CONDITION_INTERVAL = 0.1
        self.vr.TTS_CHARS_PER_SEC = {"Designer": 100.0, "Engineer": 20.0}  # D 0.4초, E 2초
        self.vr.set_condition("centralized", owner="P05-hb")
        self.addCleanup(self.vr.end_condition, "P05-hb")
        self.session.emit(text_event("PM", self.ROUTING, step="routing"))
        self.emit_subchats()
        time.sleep(1.2)  # D는 끝났고 E는 아직 친다
        self.fake.resent_typing.clear()
        time.sleep(0.35)
        self.assertIn("Engineer", self.fake.resent_typing)
        self.assertNotIn("Designer", self.fake.resent_typing, "끝난 사람을 다시 켰다")
        self.assertTrue(self.vr.wait_until_idle(10))
        time.sleep(0.15)
        self.fake.resent_typing.clear()
        time.sleep(0.3)
        self.assertEqual(self.fake.resent_typing, [], "타이핑이 끝났는데 계속 보낸다")

    def test_unmarked_replies_are_still_spoken(self):
        # decentralized에서도 PM에게 말하는 발화가 있다 — 표시(step)가 없으면 지금처럼 말한다
        self.session.emit(text_event("Designer", "PM, 제 생각은 이렇습니다."))
        self.assertTrue(self.vr.wait_until_idle(5))
        self.assertEqual(self.fake.spoken, [("Designer", "PM, 제 생각은 이렇습니다.")])
        self.assertEqual(self.fake.typing, [])


class TestConditionSignal(VROutputTestCase):
    def test_condition_is_sent_when_enabled(self):
        fake = FakeTTS()
        vr = self.reload_vr(enabled=True, fake=fake)
        vr.set_condition("centralized")
        self.assertEqual(fake.conditions, ["centralized"])

    def test_condition_is_resent_while_the_session_runs(self):
        """UE가 세션 도중 다시 켜져도 몇 초 안에 조건을 알도록 주기적으로 다시 보낸다."""
        fake = FakeTTS()
        vr = self.reload_vr(enabled=True, fake=fake)
        vr.CONDITION_INTERVAL = 0.1
        vr.set_condition("centralized", owner="S1")
        self.addCleanup(vr.end_condition, "S1")
        time.sleep(0.45)
        self.assertGreaterEqual(fake.resent.count("centralized"), 3)
        vr.end_condition("S2")  # 다른 세션이 끝나도 멈추지 않는다
        n = len(fake.resent)
        time.sleep(0.25)
        self.assertGreater(len(fake.resent), n)
        vr.end_condition("S1")
        time.sleep(0.15)
        n = len(fake.resent)
        time.sleep(0.3)
        self.assertEqual(len(fake.resent), n, "세션이 끝났는데 계속 보낸다")

    def test_cancel_stops_resending_and_falls_back_to_decentralized(self):
        fake = FakeTTS()
        vr = self.reload_vr(enabled=True, fake=fake)
        vr.CONDITION_INTERVAL = 0.1
        vr.set_condition("centralized", owner="S1")
        vr.reset(owner="S1")
        self.assertEqual(fake.conditions[-1], "decentralized")
        time.sleep(0.15)
        n = len(fake.resent)
        time.sleep(0.3)
        self.assertEqual(len(fake.resent), n)

    def test_condition_is_a_noop_when_disabled(self):
        vr = self.reload_vr(enabled=False)
        vr.set_condition("centralized")
        self.assertNotIn("tts_pipeline", sys.modules)


class TestSpeakingRate(VROutputTestCase):
    def test_rate_per_agent(self):
        vr = self.reload_vr(enabled=False)
        self.assertAlmostEqual(vr.speaking_seconds("Designer", "가" * 629), 100.0 * vr.TYPING_TIME_SCALE, places=3)
        self.assertAlmostEqual(vr.speaking_seconds("Unknown", "가" * 739), 100.0 * vr.TYPING_TIME_SCALE, places=3)


if __name__ == "__main__":
    unittest.main()

"""OSC로 UE5에 나가는 패킷이 제대로 만들어지는지 확인.

ElevenLabs도 Audio2Face도 안 쓴다 — 블렌드셰이프와 오디오를 가짜로 만들어
넣고, 로컬에 띄운 OSC 수신 서버가 받은 내용을 원본과 맞춰본다.
UE5가 없어도 돌아가고, 유료 API를 한 번도 안 부른다.
"""
import base64
import os
import socket
import threading
import time
import unittest

from pythonosc import dispatcher as osc_dispatcher
from pythonosc import osc_server


class OSCListener:
    """로컬 UDP 포트에서 /mh/* 메시지를 받아 순서대로 쌓아둔다."""

    def __init__(self) -> None:
        self.received: list[tuple[str, tuple]] = []
        self.stamps: list[tuple[str, tuple, float]] = []
        self._lock = threading.Lock()
        disp = osc_dispatcher.Dispatcher()
        disp.set_default_handler(self._record)
        # 포트 0으로 열면 OS가 빈 포트를 골라준다 — 테스트끼리 안 부딪힌다.
        self.server = osc_server.ThreadingOSCUDPServer(("127.0.0.1", 0), disp)
        # socketserver 기본값은 한 번에 8192바이트만 읽는다. 오디오 청크는 40KB라
        # 그대로 두면 잘려서 파싱에 실패하고 조용히 사라진다.
        self.server.max_packet_size = 65535
        # 수신 버퍼도 키운다. 기본 64KB로 두면 청크를 연달아 쏠 때 넘쳐서 뒷부분이
        # 버려진다 — 실측으로 40KB 청크는 4개 중 2개만 도착했고, 청크를 8KB로 줄여도
        # 13개 중 12개만 도착했다. 두 경우 모두 audio_end까지 사라졌다. 청크를 줄여도
        # 마찬가지라는 건 원인이 크기가 아니라 '쉬지 않고 연달아 보내는 것'이라는 뜻이고,
        # 그건 _send_audio_via_osc 쪽 문제다(받는 쪽 설정 문제가 아니다).
        # 이 테스트는 패킷을 제대로 만드는지만 보려는 것이라 그 변수를 빼고 본다.
        self.server.socket.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 1 << 20)
        self.port = self.server.server_address[1]
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def _record(self, address, *args):
        with self._lock:
            self.received.append((address, args))
            self.stamps.append((address, args, time.perf_counter()))

    def wait_for(self, address: str, timeout: float = 5.0) -> bool:
        deadline = time.perf_counter() + timeout
        while time.perf_counter() < deadline:
            with self._lock:
                if any(addr == address for addr, _ in self.received):
                    return True
            time.sleep(0.01)
        return False

    def times(self, address: str, character: str) -> list[float]:
        """그 캐릭터의 메시지가 도착한 시각들 (perf_counter)."""
        with self._lock:
            return [t for addr, args, t in self.stamps if addr == address and args and args[0] == character]

    def by_address(self, address: str) -> list[tuple]:
        with self._lock:
            return [args for addr, args in self.received if addr == address]

    def close(self) -> None:
        self.server.shutdown()
        self.server.server_close()


class TestOSCWire(unittest.TestCase):
    def setUp(self):
        self.listener = OSCListener()
        self.addCleanup(self.listener.close)
        os.environ["UE5_OSC_HOST"] = "127.0.0.1"
        os.environ["UE5_OSC_PORT"] = str(self.listener.port)

        import tts_pipeline

        self.tts = tts_pipeline
        # 모듈이 클라이언트를 캐시하므로 이번 포트로 새로 만들게 비운다.
        tts_pipeline._osc_client = None

        def drop_client():
            client = getattr(tts_pipeline, "_osc_client", None)
            if client is not None:
                for c in client.clients:
                    c._sock.close()
            tts_pipeline._osc_client = None

        self.addCleanup(drop_client)

        from performance_packet import PerformancePacket

        self.packet = PerformancePacket(
            agent_name="Designer",
            character_id="MH_VisualDesigner",
            text="테스트 발화입니다.",
            audio_bytes=bytes(range(256)) * 400,   # 102,400바이트 — 청크가 여러 개 나오는 크기
            blendshape_fps=30,
            weight_count=3,
            blendshape_frames=[[0.1, 0.2, 0.3], [0.4, 0.5, 0.6], [0.7, 0.8, 0.9]],
        )

    def test_blendshape_frames_arrive_in_order(self):
        self.tts._send_blendshapes_via_osc(self.packet)
        self.assertTrue(self.listener.wait_for("/mh/bs_end"), "bs_end가 안 왔다")

        start = self.listener.by_address("/mh/bs_start")
        self.assertEqual(start, [("MH_VisualDesigner", 3, 3, 30)])

        frames = self.listener.by_address("/mh/bs")
        self.assertEqual([args[1] for args in frames], [0, 1, 2], "프레임 순서가 틀렸다")
        for sent, got in zip(self.packet.blendshape_frames, frames):
            self.assertEqual(got[0], "MH_VisualDesigner")
            for expected, actual in zip(sent, got[2:]):
                self.assertAlmostEqual(expected, actual, places=5)

        self.assertEqual(self.listener.by_address("/mh/bs_end"), [("MH_VisualDesigner",)])

    def test_audio_survives_the_chunking(self):
        self.tts._send_audio_via_osc(self.packet)
        self.assertTrue(self.listener.wait_for("/mh/audio_end"), "audio_end가 안 왔다")

        start = self.listener.by_address("/mh/audio_start")
        self.assertEqual(len(start), 1)
        character, chunk_count = start[0]
        self.assertEqual(character, "MH_VisualDesigner")

        chunks = self.listener.by_address("/mh/audio_chunk")
        self.assertEqual(len(chunks), chunk_count, "예고한 청크 수와 실제가 다르다")
        self.assertGreater(chunk_count, 1, "여러 청크로 쪼개지는 크기여야 의미가 있다")
        self.assertEqual([args[1] for args in chunks], list(range(chunk_count)))

        rejoined = "".join(args[2] for args in sorted(chunks, key=lambda a: a[1]))
        self.assertEqual(
            base64.b64decode(rejoined), self.packet.audio_bytes,
            "UE5가 이어붙이면 원본 오디오가 나와야 한다",
        )

    def test_playback_presends_next_sentence_and_starts_it_after_the_gap(self):
        """앞 문장이 재생되는 동안 다음 문장 데이터가 미리 가고, audio_end(=UE 재생 시작)는
        앞 문장 길이 + 공백 뒤에 간다. 다 끝나야 wait_until_idle이 풀린다."""
        from performance_packet import PerformancePacket

        def packet(char, seconds):
            return PerformancePacket(
                agent_name=char, character_id=char, text="문장.",
                audio_bytes=b"\x00\x01" * int(16000 * seconds),
                blendshape_fps=30, weight_count=3,
                blendshape_frames=[[0.1, 0.2, 0.3]] * 3,
            )

        made = iter([packet("MH_PM", 0.6), packet("MH_PM", 0.4), packet("MH_Designer", 0.4)])
        original = self.tts._process_one
        self.tts._process_one = lambda *a: next(made)
        self.addCleanup(setattr, self.tts, "_process_one", original)

        self.tts.trigger("PM", "첫 문장. 둘째 문장.", "Designer")
        self.tts.trigger("Designer", "셋째 문장.", "PM")
        self.assertFalse(self.tts.wait_until_idle(0.3), "재생 중인데 idle이라고 했다")
        self.assertTrue(self.tts.wait_until_idle(10), "재생이 시간 안에 안 끝났다")
        idle_at = time.perf_counter()

        L = self.listener
        self.assertEqual([a[0] for a in L.by_address("/mh/audio_end")], ["MH_PM", "MH_PM", "MH_Designer"])
        self.assertEqual([a[0] for a in L.by_address("/mh/speaker")], ["MH_PM", "MH_PM", "MH_Designer"])
        pm_end1, pm_end2 = L.times("/mh/audio_end", "MH_PM")
        des_end = L.times("/mh/audio_end", "MH_Designer")[0]
        # PM 둘째 문장 데이터는 첫 문장이 재생되는 동안(첫 audio_end 뒤, 둘째 audio_end 전) 이미 갔다
        pm_start2 = L.times("/mh/audio_start", "MH_PM")[1]
        self.assertLess(pm_start2, pm_end1 + 0.6, "다음 문장을 앞 문장 재생 중에 미리 보내야 한다")
        # 재생 시작 간격 = 앞 문장 길이 + 공백 (같은 사람 SENTENCE_GAP / 바뀌면 SPEAKER_GAP)
        self.assertAlmostEqual(pm_end2 - pm_end1, 0.6 + self.tts.SENTENCE_GAP, delta=0.15)
        self.assertAlmostEqual(des_end - pm_end2, 0.4 + self.tts.SPEAKER_GAP, delta=0.15)
        # /mh/speaker는 앞 문장이 끝나는 순간(= audio_end보다 공백만큼 먼저) 간다
        pm_spk2 = L.times("/mh/speaker", "MH_PM")[1]
        des_spk = L.times("/mh/speaker", "MH_Designer")[0]
        self.assertAlmostEqual(pm_end2 - pm_spk2, self.tts.SENTENCE_GAP, delta=0.1)
        self.assertAlmostEqual(des_end - des_spk, self.tts.SPEAKER_GAP, delta=0.1)
        # 마지막 문장이 끝나는 시점에 idle
        self.assertAlmostEqual(idle_at - des_end, 0.4, delta=0.15)

    def test_reset_drops_sentences_that_have_not_started(self):
        """세션 취소: 재생 중인 문장은 끝까지, 아직 시작 안 한 문장은 재생하지 않는다."""
        from performance_packet import PerformancePacket

        def packet(seconds):
            return PerformancePacket(
                agent_name="PM", character_id="MH_PM", text="문장.",
                audio_bytes=bytes(2) * int(16000 * seconds),
                blendshape_fps=30, weight_count=3, blendshape_frames=[[0.1, 0.2, 0.3]],
            )

        made = iter([packet(0.5), packet(0.5), packet(0.5)])
        original = self.tts._process_one
        self.tts._process_one = lambda *a: next(made)
        self.addCleanup(setattr, self.tts, "_process_one", original)

        self.tts.trigger("PM", "하나. 둘. 셋.", "")
        self.assertTrue(self.listener.wait_for("/mh/audio_end"), "첫 문장이 시작 안 됐다")
        self.tts.reset()
        self.assertTrue(self.tts.wait_until_idle(5), "취소 뒤에도 idle이 안 됐다")
        time.sleep(0.5)
        self.assertEqual(len(self.listener.by_address("/mh/audio_end")), 1,
                         "취소 뒤에 남은 문장이 재생됐다")

    def test_no_blendshapes_sends_nothing(self):
        self.packet.blendshape_frames = []
        self.tts._send_blendshapes_via_osc(self.packet)
        time.sleep(0.2)
        self.assertEqual(self.listener.received, [])


if __name__ == "__main__":
    unittest.main()

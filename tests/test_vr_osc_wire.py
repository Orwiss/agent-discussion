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
        self._lock = threading.Lock()
        disp = osc_dispatcher.Dispatcher()
        disp.set_default_handler(self._record)
        # 포트 0으로 열면 OS가 빈 포트를 골라준다 — 테스트끼리 안 부딪힌다.
        self.server = osc_server.ThreadingOSCUDPServer(("127.0.0.1", 0), disp)
        # socketserver 기본값은 한 번에 8192바이트만 읽는다. 오디오 청크는 40KB라
        # 그대로 두면 잘려서 파싱에 실패하고 조용히 사라진다.
        self.server.max_packet_size = 65535
        # 수신 버퍼도 키운다. 기본 64KB로 두면 40KB 청크를 연달아 쏠 때 넘쳐서
        # 뒷부분이 버려진다(실측: 4청크 중 2개 + audio_end 유실). 이 테스트는
        # '패킷을 제대로 만드는가'를 보는 것이라 유실 변수를 빼고 본다 —
        # 버스트 유실 자체는 UE5 수신 설정과 함께 따로 다뤄야 할 문제다.
        self.server.socket.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 1 << 20)
        self.port = self.server.server_address[1]
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def _record(self, address, *args):
        with self._lock:
            self.received.append((address, args))

    def wait_for(self, address: str, timeout: float = 5.0) -> bool:
        deadline = time.perf_counter() + timeout
        while time.perf_counter() < deadline:
            with self._lock:
                if any(addr == address for addr, _ in self.received):
                    return True
            time.sleep(0.01)
        return False

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
                client._sock.close()
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

    def test_no_blendshapes_sends_nothing(self):
        self.packet.blendshape_frames = []
        self.tts._send_blendshapes_via_osc(self.packet)
        time.sleep(0.2)
        self.assertEqual(self.listener.received, [])


if __name__ == "__main__":
    unittest.main()

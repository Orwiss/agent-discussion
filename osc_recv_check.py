"""UE5 자리에서 OSC를 받아보는 확인용 수신기.

UE 없이 네트워크 경로만 검증한다. UE5를 돌릴 PC에서 이걸 띄우고,
보내는 PC에서 test_osc.py 를 돌리면 된다.

UE5의 OSC 플러그인이 하는 일을 흉내낸다 — 수신 버퍼를 일부러 키우지 않고
OS 기본값을 쓴다. 여기서 안 새면 UE에서도 안 샐 가능성이 높다.

    python osc_recv_check.py            # 0.0.0.0:7400 에서 대기
    python osc_recv_check.py 7400       # 포트 지정
"""
import socket
import sys
import threading
import time

from pythonosc import dispatcher as osc_dispatcher
from pythonosc import osc_server

PORT = int(sys.argv[1]) if len(sys.argv) > 1 else 7400


class Session:
    """char_id 하나의 발화 한 건. start에서 열리고 end에서 닫힌다."""

    def __init__(self, kind, char_id, expected):
        self.kind = kind
        self.char_id = char_id
        self.expected = expected
        self.seen = set()
        self.t0 = time.perf_counter()

    def close(self):
        dt = time.perf_counter() - self.t0
        got, exp = len(self.seen), self.expected
        missing = sorted(set(range(exp)) - self.seen)[:10] if exp else []
        verdict = "OK" if got == exp else f"*** {exp - got}개 유실 ***"
        print(f"  [{self.kind}] {self.char_id}: {got}/{exp}  {dt:.2f}s  {verdict}")
        if missing:
            print(f"        빠진 인덱스(앞 10개): {missing}")


class Recv:
    def __init__(self):
        self.audio = None
        self.bs = None
        self.lock = threading.Lock()

    def on(self, address, *args):
        with self.lock:
            try:
                if address == "/mh/audio_start":
                    self.audio = Session("AUDIO", args[0], int(args[1]))
                    print(f"\n/mh/audio_start  {args[0]}  청크 {args[1]}개 예고")
                elif address == "/mh/audio_chunk" and self.audio:
                    self.audio.seen.add(int(args[1]))
                elif address == "/mh/audio_end":
                    print("/mh/audio_end 수신")
                    if self.audio:
                        self.audio.close()
                        self.audio = None
                elif address == "/mh/bs_start":
                    self.bs = Session("BS", args[0], int(args[1]))
                    print(f"\n/mh/bs_start  {args[0]}  {args[1]}프레임 "
                          f"x {args[2]}weights @ {args[3]}fps")
                elif address == "/mh/bs" and self.bs:
                    self.bs.seen.add(int(args[1]))
                elif address == "/mh/bs_end":
                    print("/mh/bs_end 수신")
                    if self.bs:
                        self.bs.close()
                        self.bs = None
                else:
                    print(f"(기타) {address} {str(args)[:80]}")
            except Exception as e:
                print(f"  파싱 실패 {address}: {e}")


def main():
    r = Recv()
    disp = osc_dispatcher.Dispatcher()
    disp.set_default_handler(r.on)

    server = osc_server.ThreadingOSCUDPServer(("0.0.0.0", PORT), disp)
    # OSC 메시지 하나가 8192바이트를 넘으면 기본값으로는 잘려서 조용히 사라진다.
    server.max_packet_size = 65535
    # SO_RCVBUF 는 일부러 건드리지 않는다 — UE 기본 조건에 맞춘다.
    rcvbuf = server.socket.getsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF)

    ips = socket.gethostbyname_ex(socket.gethostname())[2]
    print(f"수신 대기 0.0.0.0:{PORT}   SO_RCVBUF={rcvbuf}")
    print(f"이 PC의 주소: {', '.join(ips)}")
    print(f"보내는 쪽 .env 를 UE5_OSC_HOST=<위 주소 중 ZeroTier 것>, "
          f"UE5_OSC_PORT={PORT} 으로 맞출 것")
    print("Ctrl+C 로 종료\n")

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n종료")
        server.shutdown()
        server.server_close()


if __name__ == "__main__":
    main()

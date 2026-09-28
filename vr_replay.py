"""녹화해 둔 실제 A2F 결과(음성 + 블렌드셰이프)를 UE로 다시 보낸다.

노트북 한 대에서는 UE PIE와 A2F가 VRAM을 같이 못 쓰므로:
  1) 녹화: UE 끄고 A2F 켜고, .env에 VR_RECORD_DIR=recordings 넣고 web.py로 세션 진행
  2) 재생: A2F 끄고 UE PIE 켜고, python vr_replay.py recordings/<폴더>

보내는 방식은 tts_pipeline의 재생 워커와 같다(블렌드셰이프와 오디오를 동시에 보내고
오디오 길이만큼 기다린 뒤 다음 문장).

  python vr_replay.py recordings/20260926_190000
  python vr_replay.py recordings/20260926_190000 --from 12      # 12번 문장부터
  python vr_replay.py recordings/20260926_190000 --gap 1.0      # 문장 사이 1초
"""
import argparse
import base64
import glob
import json
import os
import sys
import threading
import time

from dotenv import load_dotenv

load_dotenv()

from performance_packet import PerformancePacket  # noqa: E402
from tts_pipeline import SAMPLE_RATE, _send_audio_via_osc, _send_blendshapes_via_osc  # noqa: E402


def load(folder: str) -> list[dict]:
    files = sorted(glob.glob(os.path.join(folder, "*.json")))
    items = []
    for path in files:
        with open(path, encoding="utf-8") as f:
            d = json.load(f)
        d["_file"] = os.path.basename(path)
        items.append(d)
    return items


def to_packet(d: dict) -> PerformancePacket:
    return PerformancePacket(
        agent_name=d["agent_name"],
        character_id=d["character_id"],
        text=d["text"],
        audio_bytes=base64.b64decode(d["audio_b64"]),
        blendshape_fps=d["blendshape_fps"],
        weight_count=d["weight_count"],
        blendshape_frames=d["blendshape_frames"],
    )


def play(packet: PerformancePacket) -> float:
    bs = threading.Thread(target=_send_blendshapes_via_osc, args=(packet,), daemon=True)
    au = threading.Thread(target=_send_audio_via_osc, args=(packet,), daemon=True)
    bs.start(); au.start(); bs.join(); au.join()
    return len(packet.audio_bytes) / (SAMPLE_RATE * 2)


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser()
    ap.add_argument("folder")
    ap.add_argument("--from", dest="start", type=int, default=1, help="이 번호 문장부터 재생")
    ap.add_argument("--gap", type=float, default=0.3, help="문장 사이 추가 대기(초)")
    args = ap.parse_args()

    items = [d for d in load(args.folder) if int(d["_file"][:4]) >= args.start]
    if not items:
        print("재생할 파일 없음:", args.folder)
        return
    print(f"{len(items)}문장 재생 → {os.getenv('UE5_OSC_HOST', '127.0.0.1')}:{os.getenv('UE5_OSC_PORT', '7400')}")

    for i, d in enumerate(items):
        packet = to_packet(d)
        face = f"{len(packet.blendshape_frames)}frames" if packet.blendshape_frames else "입모양 없음"
        print(f"[{d['_file'][:4]}] {packet.agent_name:8} {face:12} {packet.text}")
        duration = play(packet)
        time.sleep(duration)
        if i + 1 < len(items):
            time.sleep(args.gap)
    print("끝")


if __name__ == "__main__":
    main()

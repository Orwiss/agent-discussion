"""녹화본을 UE로 재생하면서 화자 신호(/mh/speaker, /mh/turn)도 같이 보낸다.

vr_replay.py와 달리 실제 파이프라인과 같은 재생 경로(tts_pipeline.trigger → 재생 워커)를
그대로 쓴다. TTS·A2F만 녹화본으로 바꿔 끼우므로 문장 사이 공백, 미리 보내기,
/mh/speaker 타이밍이 실험 때와 같다. 움직임 로직(시선·손짓·끄덕임·빈틈) 테스트용.
옛 녹화본에는 받는 사람/마지막 여부가 없어서 다음 화자로 추정한다.

  python vr_test_send.py                       # 기본 녹화본 앞 9문장 → PC
  python vr_test_send.py --count 5             # 앞 5문장
  python vr_test_send.py --host 127.0.0.1      # 노트북 PIE로
  python vr_test_send.py --folder recordings/<폴더> --gap 3
"""
import argparse
import os
import sys
import time

DEFAULT_FOLDER = "recordings/20261001_155948"
PC_HOST = "10.97.136.145"


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser()
    ap.add_argument("--folder", default=DEFAULT_FOLDER)
    ap.add_argument("--count", type=int, default=9, help="앞에서부터 재생할 문장 수")
    ap.add_argument("--host", default=PC_HOST, help="보낼 곳 (쉼표로 여러 곳)")
    ap.add_argument("--gap", type=float, default=None, help="화자가 바뀔 때 공백(초). 기본은 VR_SPEAKER_GAP")
    ap.add_argument("--sentence-gap", type=float, default=None, help="같은 화자 문장 사이 공백(초). 기본은 VR_SENTENCE_GAP")
    ap.add_argument("--process-sec", type=float, default=0.0, help="문장당 TTS+A2F 처리 시간 흉내(초). 실측 약 4~5초")
    args = ap.parse_args()

    # tts_pipeline이 import될 때 .env를 읽으므로 그 전에 보낼 곳을 정한다
    os.environ["UE5_OSC_HOST"] = args.host
    os.chdir(os.path.dirname(os.path.abspath(__file__)))
    import vr_replay as R
    import tts_pipeline as T

    if args.gap is not None:
        T.SPEAKER_GAP = args.gap
    if args.sentence_gap is not None:
        T.SENTENCE_GAP = args.sentence_gap

    items = R.load(args.folder)[: args.count]
    if not items:
        print("재생할 파일 없음:", args.folder)
        return
    print(f"{len(items)}문장 → {args.host} (공백 같은 화자 {T.SENTENCE_GAP}s / 화자 바뀜 {T.SPEAKER_GAP}s)")

    # 같은 화자가 이어 말한 문장을 한 발화로 묶는다 — 실제로 trigger()가 받는 단위
    turns: list[list[dict]] = []
    for d in items:
        if turns and turns[-1][0]["character_id"] == d["character_id"]:
            turns[-1].append(d)
        else:
            turns.append([d])

    t0 = time.time()
    queued = iter(items)

    def recorded(agent_name, sentence, idx, total):
        d = next(queued)
        if args.process_sec:
            time.sleep(args.process_sec)
        print(f"{time.time() - t0:6.1f}s 준비 [{d['_file'][:4]}] {d['agent_name']:8} {idx + 1}/{total}", flush=True)
        return R.to_packet(d)

    T._process_one = recorded

    T.send_turn("agents")
    for k, turn in enumerate(turns):
        nxt = turns[k + 1] if k + 1 < len(turns) else None
        recipient = nxt[0]["agent_name"] if nxt else ""   # 빈 값이면 /mh/speaker에 Participant로 나간다
        # 문장 수만큼 문장을 만든다 — trigger()가 마침표로 나눠 _process_one을 문장마다 부른다
        T.trigger(turn[0]["agent_name"], " ".join("문장." for _ in turn), recipient)
    T.wait_until_idle()
    T.send_turn("participant")
    print(f"{time.time() - t0:6.1f}s 참가자 차례")


if __name__ == "__main__":
    main()

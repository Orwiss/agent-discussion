# test_tts.py — 전체 파이프라인 테스트 (ElevenLabs → A2F → 블렌드셰이프)
from tts_pipeline import _synthesize, _pcm_to_wav, _generate_blendshapes
from performance_packet import build_packet
import os

print("=== Step 1: ElevenLabs TTS ===")
p = build_packet("UXResearcher", "안녕하세요, 테스트입니다.")
p.audio_bytes = _synthesize(p)
print(f"PCM 생성: {len(p.audio_bytes)} bytes")

print("\n=== Step 2: PCM → WAV ===")
wav_path = _pcm_to_wav(p.audio_bytes)
print(f"WAV 저장: {wav_path}")

print("\n=== Step 3: Audio2Face 블렌드셰이프 ===")
bs_data = _generate_blendshapes(wav_path)
if bs_data:
    print(f"weight_count: {bs_data['weight_count']}")
    print(f"num_frames: {bs_data['num_frames']}")
    print(f"fps: {bs_data['fps']}")
    print(f"첫 프레임 (앞 5개): {bs_data['frames'][0][:5]}")
    print("성공!")
else:
    print("블렌드셰이프 생성 실패 (A2F 경로 확인)")

# 임시 파일 정리
os.unlink(wav_path)

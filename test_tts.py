# test_tts.py — 전체 파이프라인 테스트 (ElevenLabs → A2F gRPC → 블렌드셰이프)
from tts_pipeline import _synthesize, _generate_blendshapes
from performance_packet import build_packet

print("=== Step 1: ElevenLabs TTS ===")
p = build_packet("UXResearcher", "안녕하세요, 테스트입니다.")
p.audio_bytes = _synthesize(p)
print(f"PCM 생성: {len(p.audio_bytes)} bytes")

print("\n=== Step 2: Audio2Face gRPC 블렌드셰이프 ===")
bs_data = _generate_blendshapes(p.audio_bytes)
if bs_data:
    print(f"weight_count: {bs_data['weight_count']}")
    print(f"num_frames: {bs_data['num_frames']}")
    print(f"fps: {bs_data['fps']}")
    print(f"bs_names (앞 5개): {bs_data['bs_names'][:5]}")
    print(f"첫 프레임 (앞 5개): {bs_data['frames'][0][:5]}")
    print("성공!")
else:
    print("블렌드셰이프 생성 실패 (A2F gRPC 서비스 확인: docker run ...)")

# test_tts.py — ElevenLabs TTS 연결 테스트
from tts_pipeline import _synthesize
from performance_packet import build_packet

p = build_packet("UXResearcher", "안녕하세요, 테스트입니다.")
p.audio_bytes = _synthesize(p)
print(f"성공! PCM {len(p.audio_bytes)} bytes")

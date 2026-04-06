# test_osc.py — TTS + OSC 전송 테스트
# UE5가 켜져 있으면 MetaHuman이 말하고, 안 켜져 있어도 에러 없이 전송됨 (UDP라서)
from tts_pipeline import trigger
import time

print("UXResearcher 발화 전송 중...")
trigger("UXResearcher", "안녕하세요, OSC 전송 테스트입니다.")
time.sleep(5)
print("완료! UE5가 켜져 있었으면 MetaHuman이 말했을 거예요.")

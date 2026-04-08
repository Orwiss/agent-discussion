# test_osc.py — TTS + OSC 전송 테스트
# 스페이스바: 3명이 번갈아 대화 시뮬레이션
# 1: UXResearcher만 | 2: VisualDesigner만 | 3: SoftwareEngineer만
# q: 종료
import logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s %(message)s', datefmt='%H:%M:%S')

from tts_pipeline import trigger
import random

AGENTS = {
    "UXResearcher": [
        "사실 신입생들이 가장 많이 느끼는 건 정보 과부하예요. 입학식부터 수강 신청까지 동시에 쏟아지니까요.",
        "사용자가 처음 앱을 설치했을 때, 너무 많은 정보를 한 번에 보여주기보다는 오늘 당신에게 필요한 건 세 가지라는 식이 좋아요.",
        "처음엔 내가 이 학교에 속해 있구나라는 소속감을 주는 게 가장 큰 차별점이 될 수 있다고 봐요.",
        "오늘의 적응 미션 같은 걸 제안하고 싶어요. 하루에 하나, 작지만 꼭 해야 할 걸 알려주는 거예요.",
        "기존 앱들은 정보 제공에 치중하는데, 우리는 감정적 적응까지 엮어보는 건 어때요?",
        "마치 선배가 조용히 옆에서 도와주는 느낌, 그게 핵심 가치가 되면 좋겠어요.",
        "매일 조금씩 성취감을 느낄 수 있도록 점진적으로 노출하는 게 중요하다고 생각해요.",
    ],
    "VisualDesigner": [
        "비주얼 측면에서는 앱의 첫 화면이 따뜻하고 환영받는 느낌을 주도록 디자인하는 게 좋겠어요.",
        "인터페이스는 직관적이고 간결하게 구성해서 복잡함을 줄이는 것도 중요하겠네요.",
        "색상이나 타이포그래피도 지나치게 튀는 요소보단 안정적이고 친근한 느낌을 주도록 해야죠.",
        "에어비앤비 앱처럼 카드 스와이프 방식을 사용하면 탐색하기 쉬운 경험을 줄 수 있을 거예요.",
        "헤드스페이스라는 명상 앱 아세요? 전체적인 비주얼이 굉장히 부드럽고 편안해요. 우리도 그런 방향이 좋겠어요.",
        "한 번에 너무 많은 요소를 화면에 쏟아붓지 않는 게 중요할 것 같아요.",
        "사용자에게 압박감을 주지 않고 편안하게 사용할 수 있는 환경을 만들어야 해요.",
    ],
    "SoftwareEngineer": [
        "기술적으로 보면 상황 인식 기반 개인화가 핵심일 것 같아요. 학과별로 필수 체크리스트를 자동 생성해주는 거죠.",
        "파이어베이스로 유저 프로필과 체크리스트 데이터를 묶어두면 초기 버전은 2주 안에 뽑을 수 있어요.",
        "위치 기반 알림은 GeoFire 라이브러리 쓰면 간단하게 연동되고, 나중에 트래픽 늘어나면 분리하면 되구요.",
        "선배들의 15초 음성 팁 같은 미디어 요소를 붙이는 것도 고려해볼 만하네요.",
        "음성 파일 저장은 파이어베이스 스토리지로 시작하고 트랜스코딩 필요하면 클라우드 함수 추가하면 됩니다.",
        "리액트 네이티브로 시작하면 iOS랑 안드로이드 동시에 커버할 수 있어요.",
        "사용자 위치와 시간표를 결합하면 지금 어디로 가야 하는지 자동으로 알려줄 수 있어요.",
    ],
}

def _on_send():
    agent_name = random.choice(list(AGENTS.keys()))
    text = random.choice(AGENTS[agent_name])
    print(f"\n[{agent_name}] {text[:40]}...")
    trigger(agent_name, text)

# 워밍업
print("초기화 중...")
from tts_pipeline import _eleven, _osc
try: _eleven()
except: pass
try: _osc()
except: pass
import sounddevice as sd
import numpy as np
sd.play(np.zeros(1600, dtype=np.float32), samplerate=16000)
sd.wait()
print("준비 완료!")
print("=" * 50)
print("스페이스바: 3명 번갈아 대화 | 1/2/3: 개별 에이전트")
print("q: 종료")
print("=" * 50)

try:
    import msvcrt
    while True:
        if msvcrt.kbhit():
            key = msvcrt.getch()
            if key == b' ':
                _on_send()
            elif key == b'1':
                _on_send("UXResearcher")
            elif key == b'2':
                _on_send("VisualDesigner")
            elif key == b'3':
                _on_send("SoftwareEngineer")
            elif key == b'q':
                print("종료")
                break
except ImportError:
    import sys, tty, termios
    fd = sys.stdin.fileno()
    old = termios.tcgetattr(fd)
    try:
        tty.setraw(fd)
        while True:
            ch = sys.stdin.read(1)
            if ch == ' ':
                _on_send()
            elif ch == '1':
                _on_send("UXResearcher")
            elif ch == '2':
                _on_send("VisualDesigner")
            elif ch == '3':
                _on_send("SoftwareEngineer")
            elif ch == 'q':
                print("종료")
                break
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old)

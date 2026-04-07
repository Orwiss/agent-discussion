# 노트북 세팅 가이드 (추론 서버용)

> 이 노트북은 agent-discussion + Audio2Face 추론을 담당합니다.
> 데스크탑은 UE5 VR 렌더링만 합니다.

---

## 요구사항

- NVIDIA GPU (RTX 3000 이상, VRAM 8GB+)
- Windows 10/11
- 같은 네트워크에 데스크탑과 연결

---

## 1. 기본 도구 설치

### CUDA Toolkit (12.8 이상)
1. https://developer.nvidia.com/cuda-downloads (최신 버전)
   또는 https://developer.nvidia.com/cuda-12-8-0-download-archive (12.8)
2. Windows → x86_64 → exe (local) → 다운로드 → 설치 (기본 옵션)
3. 12.8 이상이면 아무 버전이나 OK

### TensorRT 10.13
1. https://developer.nvidia.com/tensorrt → Download
2. TensorRT 10.13 for Windows ZIP 다운로드
3. 압축 풀어서 `C:\TensorRT-10.16.0.72`에 배치

### CMake
1. https://cmake.org/download/ → Windows x64 Installer
2. 설치 시 **Add to PATH** 체크

### PATH 설정 (cmd에서):
```
setx PATH "%PATH%;C:\Program Files\NVIDIA GPU Computing Toolkit\CUDA\v12.8\bin;C:\Program Files\CMake\bin"
```
설정 후 터미널 재시작.

---

## 2. Audio2Face SDK 빌드

```
cd C:\Users\user\Desktop\Sunghun\GitHub
git clone https://github.com/NVIDIA/Audio2Face-3D-SDK.git
cd Audio2Face-3D-SDK
```

### 모델 다운로드 (Python 필요):
```
pip install huggingface_hub[cli]
python -c "from huggingface_hub import snapshot_download; snapshot_download('nvidia/Audio2Face-3D-v3.0', local_dir='_data/audio2face-models/audio2face-3d-v3.0')"
python -c "from huggingface_hub import snapshot_download; snapshot_download('nvidia/Audio2Face-3D-v2.3.1-Claire', local_dir='_data/audio2face-models/audio2face-3d-v2.3.1-claire')"
python -c "from huggingface_hub import snapshot_download; snapshot_download('nvidia/Audio2Face-3D-v2.3.1-James', local_dir='_data/audio2face-models/audio2face-3d-v2.3.1-james')"
python -c "from huggingface_hub import snapshot_download; snapshot_download('nvidia/Audio2Face-3D-v2.3-Mark', local_dir='_data/audio2face-models/audio2face-3d-v2.3-mark')"
python -c "from huggingface_hub import snapshot_download; snapshot_download('nvidia/Audio2Emotion-v2.2', local_dir='_data/audio2emotion-models/audio2emotion-v2.2')"
```

### 의존성 + 빌드:
```
fetch_deps.bat

set TENSORRT_ROOT_DIR=C:\TensorRT-10.16.0.72
gen_testdata.bat
build.bat
```

### ONNX → TRT 엔진 변환:
```
set PATH=%PATH%;C:\TensorRT-10.16.0.72\bin
trtexec --onnx=_data\audio2face-models\audio2face-3d-v2.3-mark\network.onnx --saveEngine=_data\audio2face-models\audio2face-3d-v2.3-mark\network.trt
```

### a2f-bridge 테스트:
```
set PATH=%PATH%;C:\TensorRT-10.16.0.72\bin;_build\release\audio2x-sdk\bin
_build\release\a2f-bridge\bin\a2f-bridge.exe --model _data\audio2face-models\audio2face-3d-v2.3-mark --audio sample-data\audio_4sec_16k_s16le.wav
```
JSON 출력 (fps, weight_count, frames) 나오면 성공.

---

## 3. agent-discussion 세팅

```
cd C:\Users\user\Desktop\Sunghun\GitHub
git clone https://github.com/<your-repo>/agent-discussion.git
cd agent-discussion
pip install -r requirements.txt
```

### .env 설정

`.env` 파일에서 아래 항목을 **노트북 환경에 맞게** 수정:

```env
# 데스크탑 IP (UE5가 돌아가는 PC)
UE5_OSC_HOST=192.168.x.x
UE5_OSC_PORT=7400

# Audio2Face 경로 (노트북 기준)
A2F_BRIDGE_PATH=C:\Users\<노트북유저>\Desktop\...\Audio2Face-3D-SDK\_build\release\a2f-bridge\bin\a2f-bridge.exe
A2F_MODEL_PATH=C:\Users\<노트북유저>\Desktop\...\Audio2Face-3D-SDK\_data\audio2face-models\audio2face-3d-v2.3-mark
TENSORRT_BIN=C:\TensorRT-10.16.0.72\bin
A2F_SDK_BIN=C:\Users\<노트북유저>\Desktop\...\Audio2Face-3D-SDK\_build\release\audio2x-sdk\bin
```

### 테스트:
```
python test_tts.py
```
3단계 모두 성공 나오면 OK.

---

## 4. 실행

```
python web.py
```
→ 브라우저에서 localhost:8000 접속 → 세션 시작
→ 에이전트 발화 시 OSC로 데스크탑 UE5에 블렌드셰이프 + 오디오 전송

---

## 5. 데스크탑 코드 수정 반영 시

데스크탑에서 코드 수정 → git push 후:
```
cd C:\Users\<노트북유저>\Desktop\...\agent-discussion
git pull
```
→ 바로 실행 가능. SDK 재빌드 필요 없음.

# 노트북 세팅 가이드 (추론 서버용)

> 이 노트북은 agent-discussion + Audio2Face 추론을 담당합니다.
> 데스크탑은 UE5 VR 렌더링만 합니다.

---

## 요구사항

- NVIDIA GPU (RTX 3000 이상, VRAM 8GB+)
- Windows 10/11 또는 Linux
- Docker Desktop 설치 (https://www.docker.com/products/docker-desktop/)
- 같은 네트워크에 데스크탑과 연결

---

## 1. Audio2Face Microservice 실행

### NGC API 키 발급 (무료)

1. https://build.nvidia.com 가입
2. https://build.nvidia.com/settings/api-keys 에서 API 키 생성
3. `nvapi-` 로 시작하는 키를 복사

### Docker 컨테이너 실행

```bash
# 캐시 디렉토리 생성 (TensorRT 엔진 캐싱, 최초 실행 시 자동 생성됨)
mkdir -p ~/.cache/audio2face-3d

# NGC API 키 설정
export NGC_API_KEY=nvapi-여기에키입력

# Audio2Face Microservice 실행
docker run -it --rm --name audio2face-3d \
  --gpus all \
  --network=host \
  -e NGC_API_KEY=$NGC_API_KEY \
  -v ~/.cache/audio2face-3d:/tmp/a2x \
  nvcr.io/nim/nvidia/audio2face-3d:2.0
```

- 최초 실행 시 모델 다운로드 + TensorRT 엔진 빌드로 시간이 걸림
- 이후 실행부터는 캐시된 엔진 사용으로 빠르게 시작
- gRPC 서버가 `localhost:52000`에서 대기

### 정상 동작 확인

```bash
curl http://localhost:8000/v1/health/ready
```
`{"status":"ready"}` 응답 나오면 성공.

---

## 2. agent-discussion 세팅

```bash
git clone https://github.com/<your-repo>/agent-discussion.git
cd agent-discussion
pip install -r requirements.txt
```

### .env 설정

`.env` 파일에서 아래 항목을 **노트북 환경에 맞게** 수정:

```env
# Audio2Face gRPC Microservice
A2F_GRPC_HOST=localhost
A2F_GRPC_PORT=52000

# 데스크탑 IP (UE5가 돌아가는 PC)
UE5_OSC_HOST=192.168.x.x
UE5_OSC_PORT=7400
```

### 테스트

```bash
python test_tts.py
```
Step 1 (TTS)과 Step 2 (A2F gRPC 블렌드셰이프) 모두 성공 나오면 OK.

---

## 3. 실행

```bash
# 1. Audio2Face Docker가 실행 중인지 확인
# 2. agent-discussion 실행
python web.py
```
→ 브라우저에서 localhost:8000 접속 → 세션 시작
→ 에이전트 발화 시 OSC로 데스크탑 UE5에 블렌드셰이프 + 오디오 전송

---

## 4. 데스크탑 코드 수정 반영 시

데스크탑에서 코드 수정 → git push 후:
```bash
cd agent-discussion
git pull
```
→ 바로 실행 가능. Docker 컨테이너 재시작 필요 없음.

---

## 트러블슈팅

### Docker 컨테이너가 안 뜰 때
- `docker info`로 Docker Desktop 실행 확인
- `--gpus all` 에러 → NVIDIA Container Toolkit 설치 필요:
  https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/install-guide.html

### gRPC 연결 실패 (UNAVAILABLE)
- Docker 컨테이너가 실행 중인지 확인: `docker ps`
- health check: `curl http://localhost:8000/v1/health/ready`
- 포트 충돌: `.env`의 `A2F_GRPC_PORT`가 52000인지 확인

### 블렌드셰이프가 안 나올 때
- Docker 로그 확인: `docker logs audio2face-3d`
- VRAM 부족 → 다른 GPU 프로세스 종료 후 재시도

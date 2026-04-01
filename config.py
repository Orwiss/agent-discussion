import os
import logging
from dotenv import load_dotenv

load_dotenv()

# AG2 가격 경고 끄기
logging.getLogger("autogen.oai.client").setLevel(logging.ERROR)

OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY")
OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"

# === 공통 provider 설정 ===
_PROVIDER_FILTER = {
    "provider": {
        "quantizations": ["fp16", "bf16", "fp8"],
    },
}

# OpenRouter timeout (초)
_TIMEOUT = 120

# === 에이전트별 모델 배정 (OpenRouter) ===

# UX 리서처 — qwen3-32b: 한국어 최강
llm_config_ux = {
    "config_list": [{
        "model": "qwen/qwen3-32b",
        "api_key": OPENROUTER_API_KEY,
        "base_url": OPENROUTER_BASE_URL,
        "extra_body": {
            "frequency_penalty": 0.4,
            "presence_penalty": 0.2,
            **_PROVIDER_FILTER,
        },
    }],
    "temperature": 0.6,
    "cache_seed": None,
    "timeout": _TIMEOUT,
}

# 비주얼 디자이너 — kimi-k2: 창의적 발상
llm_config_visual = {
    "config_list": [{
        "model": "moonshotai/kimi-k2",
        "api_key": OPENROUTER_API_KEY,
        "base_url": OPENROUTER_BASE_URL,
        "extra_body": {
            "frequency_penalty": 0.3,
            "presence_penalty": 0.3,
            **_PROVIDER_FILTER,
        },
    }],
    "temperature": 0.9,
    "cache_seed": None,
    "timeout": _TIMEOUT,
}

# 인터랙션 엔지니어 — gpt-oss-120b: 분석/추론
llm_config_engineer = {
    "config_list": [{
        "model": "openai/gpt-oss-120b",
        "api_key": OPENROUTER_API_KEY,
        "base_url": OPENROUTER_BASE_URL,
        "extra_body": {
            "frequency_penalty": 0.5,
            "presence_penalty": 0.3,
            **_PROVIDER_FILTER,
        },
    }],
    "temperature": 0.5,
    "cache_seed": None,
    "timeout": _TIMEOUT,
}

# Synthesizer, Facilitator — llama 3.3 70b
llm_config_main = {
    "config_list": [{
        "model": "meta-llama/llama-3.3-70b-instruct",
        "api_key": OPENROUTER_API_KEY,
        "base_url": OPENROUTER_BASE_URL,
        "extra_body": {
            "frequency_penalty": 0.5,
            "presence_penalty": 0.3,
            **_PROVIDER_FILTER,
        },
    }],
    "temperature": 0.3,
    "cache_seed": None,
    "timeout": _TIMEOUT,
}

# 내부 검토 기본 — llama 3.3 70b
llm_config_inner = {
    "config_list": [{
        "model": "meta-llama/llama-3.3-70b-instruct",
        "api_key": OPENROUTER_API_KEY,
        "base_url": OPENROUTER_BASE_URL,
        "extra_body": {
            "frequency_penalty": 0.5,
            "presence_penalty": 0.3,
            **_PROVIDER_FILTER,
        },
    }],
    "temperature": 0.9,
    "cache_seed": None,
    "timeout": _TIMEOUT,
}

# 내부 검토 이질성 — qwen3
llm_config_inner_alt = {
    "config_list": [{
        "model": "qwen/qwen3-32b",
        "api_key": OPENROUTER_API_KEY,
        "base_url": OPENROUTER_BASE_URL,
        "extra_body": {
            "frequency_penalty": 0.5,
            "presence_penalty": 0.3,
            **_PROVIDER_FILTER,
        },
    }],
    "temperature": 0.9,
    "cache_seed": None,
    "timeout": _TIMEOUT,
}

# speaker selection — llama 3.3 70b
llm_config_selector = {
    "config_list": [{
        "model": "meta-llama/llama-3.3-70b-instruct",
        "api_key": OPENROUTER_API_KEY,
        "base_url": OPENROUTER_BASE_URL,
        "extra_body": {**_PROVIDER_FILTER},
    }],
    "temperature": 0.1,
    "cache_seed": None,
    "timeout": _TIMEOUT,
}

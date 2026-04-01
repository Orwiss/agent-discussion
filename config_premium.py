"""B안: 프리미엄 모델 구성 (OpenRouter)"""
import os
import logging
from dotenv import load_dotenv

load_dotenv()

logging.getLogger("autogen.oai.client").setLevel(logging.ERROR)

OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY")
OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"

_PROVIDER_FILTER = {
    "provider": {
        "quantizations": ["fp16", "bf16", "fp8"],
    },
}
_TIMEOUT = 120

# UX 리서처 — qwen3-235b-2507: 한국어 최강 대형 모델
llm_config_ux = {
    "config_list": [{
        "model": "qwen/qwen3-235b-a22b-2507",
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

# 비주얼 디자이너 — gpt-4o: 창의성+한국어+fp 전부
llm_config_visual = {
    "config_list": [{
        "model": "openai/gpt-4o",
        "api_key": OPENROUTER_API_KEY,
        "base_url": OPENROUTER_BASE_URL,
        "extra_body": {
            "frequency_penalty": 0.5,
            "presence_penalty": 0.4,
            # OpenAI 자체 서빙 — 양자화 필터 불필요
        },
    }],
    "temperature": 0.9,
    "cache_seed": None,
    "timeout": _TIMEOUT,
}

# 인터랙션 엔지니어 — deepseek-r1: 추론 특화
llm_config_engineer = {
    "config_list": [{
        "model": "deepseek/deepseek-r1-0528",
        "api_key": OPENROUTER_API_KEY,
        "base_url": OPENROUTER_BASE_URL,
        "extra_body": {
            "frequency_penalty": 0.5,
            "presence_penalty": 0.3,
        },
    }],
    "temperature": 0.5,
    "cache_seed": None,
    "timeout": _TIMEOUT,
}

# Synthesizer, Facilitator — gemini-2.5-pro: Arena 1위
# Google 자체 서빙이라 양자화 필터 불필요
llm_config_main = {
    "config_list": [{
        "model": "google/gemini-2.5-pro",
        "api_key": OPENROUTER_API_KEY,
        "base_url": OPENROUTER_BASE_URL,
    }],
    "temperature": 0.3,
    "cache_seed": None,
    "timeout": _TIMEOUT,
}

# 내부 검토 — qwen3-235b-2507 (가격이 싸니까 내부에도)
llm_config_inner = {
    "config_list": [{
        "model": "qwen/qwen3-235b-a22b-2507",
        "api_key": OPENROUTER_API_KEY,
        "base_url": OPENROUTER_BASE_URL,
        "extra_body": {
            "frequency_penalty": 0.4,
            "presence_penalty": 0.2,
            **_PROVIDER_FILTER,
        },
    }],
    "temperature": 0.9,
    "cache_seed": None,
    "timeout": _TIMEOUT,
}

# 내부 검토 이질성 — gpt-4o (다른 회사 모델)
llm_config_inner_alt = {
    "config_list": [{
        "model": "openai/gpt-4o",
        "api_key": OPENROUTER_API_KEY,
        "base_url": OPENROUTER_BASE_URL,
        "extra_body": {
            "frequency_penalty": 0.4,
            "presence_penalty": 0.3,
        },
    }],
    "temperature": 0.9,
    "cache_seed": None,
    "timeout": _TIMEOUT,
}

# speaker selection — gemini-2.5-flash (싸고 빠름)
llm_config_selector = {
    "config_list": [{
        "model": "google/gemini-2.5-flash",
        "api_key": OPENROUTER_API_KEY,
        "base_url": OPENROUTER_BASE_URL,
        # Google 자체 서빙 — 양자화 필터 불필요
    }],
    "temperature": 0.1,
    "cache_seed": None,
    "timeout": _TIMEOUT,
}

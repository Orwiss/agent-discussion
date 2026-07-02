"""Centralized vs Decentralized 실험 — 단일 LLM 통일 (study-design 원칙)

후보 (싼 순):
  1) Gemini 2.5 Flash — function calling 일관성 부족으로 1차 시도 후 탈락
  2) GPT-4o-mini — function calling 안정, parallel call 표준 ★ 현재 채택
  3) Qwen3-235B
  4) Claude Haiku 4.5
"""
import os
import logging
from dotenv import load_dotenv

load_dotenv()
logging.getLogger("autogen.oai.client").setLevel(logging.ERROR)

OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY")
OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"
_TIMEOUT = 120

_BASE_LLM = {
    "model": "google/gemini-2.5-flash",
    "api_key": OPENROUTER_API_KEY,
    "base_url": OPENROUTER_BASE_URL,
}

_BASE_EXTRA = {
    "extra_body": {
        "frequency_penalty": 0.4,
        "presence_penalty": 0.2,
        # Gemini thinking 비활성화 — 추론 토큰이 본문을 잘라먹는 간헐적 truncation 차단
        "reasoning": {"max_tokens": 0},
    },
}

# PM, Designer, Engineer — 모두 동일 모델·동일 temperature
# study-design 페르소나 통일 원칙: 모델은 같고 페르소나만 다름
llm_config_pm = {
    "config_list": [{**_BASE_LLM, **_BASE_EXTRA}],
    "temperature": 0.7,
    "cache_seed": None,
    "timeout": _TIMEOUT,
}

llm_config_designer = {
    "config_list": [{**_BASE_LLM, **_BASE_EXTRA}],
    "temperature": 0.7,
    "cache_seed": None,
    "timeout": _TIMEOUT,
}

llm_config_engineer = {
    "config_list": [{**_BASE_LLM, **_BASE_EXTRA}],
    "temperature": 0.7,
    "cache_seed": None,
    "timeout": _TIMEOUT,
}

# speaker selection (Decentralized round-robin 안전망)
llm_config_selector = {
    "config_list": [_BASE_LLM],
    "temperature": 0.1,
    "cache_seed": None,
    "timeout": _TIMEOUT,
}

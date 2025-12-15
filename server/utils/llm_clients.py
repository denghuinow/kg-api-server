from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional

from langchain_openai import ChatOpenAI, OpenAIEmbeddings

from .config import AppConfig
from .rate_limit import AsyncRateLimiter
from .retry import RetryPolicy


@dataclass(frozen=True)
class LLMResources:
    llm: ChatOpenAI
    embeddings: OpenAIEmbeddings
    llm_limiter: AsyncRateLimiter
    emb_limiter: AsyncRateLimiter
    llm_retry: RetryPolicy
    emb_retry: RetryPolicy


def build_llm_resources(cfg: AppConfig) -> LLMResources:
    llm_extra: dict[str, Any] = {}
    if cfg.llm.repetition_penalty is not None:
        llm_extra["repetition_penalty"] = float(cfg.llm.repetition_penalty)

    llm = ChatOpenAI(
        api_key=cfg.llm.api_key,
        base_url=cfg.llm.api_base_url,
        model=cfg.llm.model,
        temperature=float(cfg.llm.temperature),
        max_retries=int(cfg.llm.max_retries),
        max_tokens=cfg.llm.max_tokens,
        extra_body=llm_extra or None,
    )

    embeddings = OpenAIEmbeddings(
        api_key=cfg.embeddings.api_key,
        base_url=cfg.embeddings.api_base_url,
        model=cfg.embeddings.model,
    )

    llm_limiter = AsyncRateLimiter(rpm=cfg.llm.rate_limit.rpm, tpm=cfg.llm.rate_limit.tpm)
    emb_limiter = AsyncRateLimiter(rpm=cfg.embeddings.rate_limit.rpm, tpm=cfg.embeddings.rate_limit.tpm)

    llm_retry = RetryPolicy(
        max_retries=cfg.llm.retry.max_retries,
        initial_backoff_s=cfg.llm.retry.initial_backoff_s,
        max_backoff_s=cfg.llm.retry.max_backoff_s,
        backoff_multiplier=cfg.llm.retry.backoff_multiplier,
    )
    emb_retry = RetryPolicy(
        max_retries=cfg.embeddings.retry.max_retries,
        initial_backoff_s=cfg.embeddings.retry.initial_backoff_s,
        max_backoff_s=cfg.embeddings.retry.max_backoff_s,
        backoff_multiplier=cfg.embeddings.retry.backoff_multiplier,
    )

    return LLMResources(
        llm=llm,
        embeddings=embeddings,
        llm_limiter=llm_limiter,
        emb_limiter=emb_limiter,
        llm_retry=llm_retry,
        emb_retry=emb_retry,
    )


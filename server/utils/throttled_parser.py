from __future__ import annotations

import asyncio
from typing import Any, List, Optional, Union

import numpy as np

from itext2kg.llm_output_parsing.langchain_output_parser import LangchainOutputParser

from .rate_limit import AsyncRateLimiter
from .retry import RetryPolicy, with_retry


class ThrottledLangchainOutputParser(LangchainOutputParser):
    def __init__(
        self,
        *,
        llm_model: Any,
        embeddings_model: Any,
        llm_limiter: AsyncRateLimiter,
        emb_limiter: AsyncRateLimiter,
        llm_retry: RetryPolicy,
        emb_retry: RetryPolicy,
        llm_max_concurrency: Optional[int],
        emb_max_in_flight: Optional[int],
        sleep_time: int = 5,
        sleep_between_batches: Optional[float] = None,
        max_elements_per_batch: Optional[int] = None,
        max_tokens_per_batch: Optional[int] = None,
    ) -> None:
        super().__init__(
            llm_model=llm_model,
            embeddings_model=embeddings_model,
            sleep_time=sleep_time,
            sleep_between_batches=sleep_between_batches,
            max_concurrency=llm_max_concurrency,
            max_elements_per_batch=max_elements_per_batch,
            max_tokens_per_batch=max_tokens_per_batch,
        )
        self._llm_limiter = llm_limiter
        self._emb_limiter = emb_limiter
        self._llm_retry = llm_retry
        self._emb_retry = emb_retry

        emb_cap = int(emb_max_in_flight) if emb_max_in_flight and emb_max_in_flight > 0 else 0
        self._emb_sem = asyncio.Semaphore(emb_cap) if emb_cap > 0 else None

    async def calculate_embeddings(self, text: Union[str, List[str]]) -> np.ndarray:
        if isinstance(text, list):
            token_est = sum(self.count_tokens(t) for t in text)
            await self._emb_limiter.acquire(requests=1, tokens=token_est)

            async def _call() -> np.ndarray:
                if self._emb_sem is None:
                    embeddings = await self.embeddings_model.aembed_documents(text)
                else:
                    async with self._emb_sem:
                        embeddings = await self.embeddings_model.aembed_documents(text)
                return np.array(embeddings)

            return await with_retry(_call, self._emb_retry)

        if isinstance(text, str):
            token_est = self.count_tokens(text)
            await self._emb_limiter.acquire(requests=1, tokens=token_est)

            async def _call() -> np.ndarray:
                if self._emb_sem is None:
                    emb = await self.embeddings_model.aembed_query(text)
                else:
                    async with self._emb_sem:
                        emb = await self.embeddings_model.aembed_query(text)
                return np.array(emb)

            return await with_retry(_call, self._emb_retry)

        raise TypeError("Invalid text type, please provide a string or a list of strings.")

    async def extract_information_as_json_for_context(
        self,
        output_data_structure: Any,
        contexts: List[str],
        system_query: str = """
        # DIRECTIVES :
        - Act like an experienced information extractor.
        - If you do not find the right information, keep its place empty.
        """,
    ) -> List[Any]:
        if self.config.max_pending_requests and len(contexts) > self.config.max_pending_requests:
            raise ValueError(
                f"Number of contexts ({len(contexts):,}) exceeds {self.config.name}'s "
                f"{self.config.max_pending_requests:,} request limit"
            )

        structured_llm = self.model.with_structured_output(output_data_structure)
        all_prompts = [f"# Context: {context}\n\n# Question: {system_query}\n\nAnswer: " for context in contexts]
        batches = self.split_prompts_into_batches(all_prompts)

        outputs: List[Any] = []
        for i, batch in enumerate(batches):
            token_est = sum(self.count_tokens(p) for p in batch)
            await self._llm_limiter.acquire(requests=len(batch), tokens=token_est)

            async def _call_batch() -> List[Any]:
                runnable_config = {"max_concurrency": self.max_concurrency} if self.max_concurrency else None
                return await structured_llm.abatch(batch, config=runnable_config)

            batch_outputs = await with_retry(_call_batch, self._llm_retry)
            outputs.extend(batch_outputs)

            if i < len(batches) - 1 and self.config.sleep_between_batches and self.config.sleep_between_batches > 0:
                await asyncio.sleep(float(self.config.sleep_between_batches))

        return outputs


# Copyright (c) 2026 OceanBase.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
# http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""HTTP cross-encoder reranker adapter for coarse Memory candidates.

The remote service scores the query against one batch of candidates at a time.
Because a service may cap its batch size, the adapter splits the candidate pool
into bounded batches and then performs a *single* global selection over the
scores of every batch.  Candidates keep their original one-based rank from the
complete input pool, so the selected ranks map straight back onto the coarse
Memory hits without rewriting their identity.
"""

from __future__ import annotations

import math
from typing import Any

import httpx

from powercontext.builtin.artifacts.memory.models import MemoryHit
from powercontext.builtin.artifacts.memory.reranking import (
    MemoryRerankDecision,
    MemoryReranker,
    _validate_bounds,
)
from powercontext.builtin.inference import InferenceUsage

DEFAULT_POLICY_ID = "http-cross-encoder-v1"
_SCORE_FIELDS = frozenset({"rank", "score"})


class HTTPMemoryReranker(MemoryReranker):
    """Score Memory candidates through a caller-provided HTTP client.

    ``batch_size`` is the maximum number of candidates the remote service accepts
    per request.  Requests are independent; the adapter gathers every batch
    before it selects, so a late batch cannot lose to the order it was sent in.
    """

    def __init__(
        self,
        endpoint: str,
        *,
        client: httpx.AsyncClient,
        batch_size: int = 32,
        policy_id: str = DEFAULT_POLICY_ID,
    ) -> None:
        if not isinstance(endpoint, str) or not endpoint.strip():
            raise ValueError("endpoint must be a non-empty string")  # noqa: TRY003
        if type(batch_size) is not int or batch_size < 1:
            raise ValueError("batch_size must be a positive integer")  # noqa: TRY003
        if not isinstance(policy_id, str) or not policy_id.strip():
            raise ValueError("policy_id must be a non-empty string")  # noqa: TRY003
        self._endpoint = endpoint
        self._client = client
        self._batch_size = batch_size
        self.policy_id = policy_id

    async def rerank(
        self,
        query: str,
        candidates: tuple[MemoryHit, ...],
        limit: int,
        /,
    ) -> MemoryRerankDecision:
        """Score every candidate once, then select a globally ordered prefix."""

        _validate_bounds(len(candidates), limit)
        ranked = tuple(enumerate(candidates, start=1))
        batches = tuple(
            ranked[start : start + self._batch_size] for start in range(0, len(ranked), self._batch_size)
        )
        responses = await _gather_batches(self._client, self._endpoint, query, batches)
        scores: dict[int, float] = {}
        for batch, payload in zip(batches, responses, strict=True):
            expected = {rank for rank, _ in batch}
            batch_scores = _parse_scores(payload, expected)
            scores.update(batch_scores)
        ordered = sorted(scores.items(), key=lambda item: (-item[1], item[0]))
        selected_ranks = tuple(rank for rank, _ in ordered[:limit])
        return MemoryRerankDecision(
            selected_ranks=selected_ranks,
            usage=InferenceUsage(requests=len(batches)),
        )


async def _gather_batches(
    client: httpx.AsyncClient,
    endpoint: str,
    query: str,
    batches: tuple[tuple[tuple[int, MemoryHit], ...], ...],
) -> tuple[Any, ...]:
    """POST each batch and return the decoded JSON bodies in batch order."""

    async def send(batch: tuple[tuple[int, MemoryHit], ...]) -> Any:
        response = await client.post(
            endpoint,
            json={
                "query": query,
                "candidates": [{"rank": rank, "text": hit.text} for rank, hit in batch],
            },
        )
        response.raise_for_status()
        return response.json()

    import asyncio

    return await asyncio.gather(*(send(batch) for batch in batches))


def _parse_scores(payload: Any, expected_ranks: set[int]) -> dict[int, float]:
    """Validate one response and return its rank -> finite score mapping."""

    if not isinstance(payload, dict) or set(payload) != {"scores"}:
        raise ValueError("rerank response must contain exactly the 'scores' field")  # noqa: TRY003
    raw_scores = payload["scores"]
    if not isinstance(raw_scores, list) or len(raw_scores) != len(expected_ranks):
        raise ValueError("rerank response must score every candidate in the batch exactly once")  # noqa: TRY003
    scores: dict[int, float] = {}
    for item in raw_scores:
        if not isinstance(item, dict) or set(item) != _SCORE_FIELDS:
            raise ValueError("each rerank score must contain exactly rank and score")  # noqa: TRY003
        rank = item["rank"]
        if type(rank) is not int or rank not in expected_ranks:
            raise ValueError("rerank score rank is outside the submitted batch")  # noqa: TRY003
        if rank in scores:
            raise ValueError("rerank response repeats a candidate rank")  # noqa: TRY003
        score = item["score"]
        if isinstance(score, bool) or not isinstance(score, (int, float)):
            raise ValueError("rerank score must be numeric")  # noqa: TRY003
        numeric = float(score)
        if not math.isfinite(numeric):
            raise ValueError("rerank score must be finite")  # noqa: TRY003
        scores[rank] = numeric
    if set(scores) != expected_ranks:
        raise ValueError("rerank response does not cover the submitted batch")  # noqa: TRY003
    return scores


__all__ = ["DEFAULT_POLICY_ID", "HTTPMemoryReranker"]

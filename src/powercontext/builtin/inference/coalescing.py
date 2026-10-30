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

"""Cross-call embedding request coalescing with a bounded LRU vector cache.

A single coordinator per wrapper owns every underlying provider request. Callers
enqueue the distinct texts they cannot serve from cache, wait on per-text futures,
and the coordinator drains the queue into batches no larger than
``max_batch_size``. Identical texts always map to the same future, so duplicates
within one call, concurrent calls in one coalescing window, and late callers that
arrive while a batch is running share one computation.

The coordinator runs in its own task, so cancelling a caller only detaches that
caller: the shared provider call and the other waiters are unaffected.
"""

from __future__ import annotations

import asyncio
from collections import OrderedDict
from dataclasses import dataclass
from typing import TYPE_CHECKING

from powercontext.builtin.inference.models import EmbeddingResult, EmbeddingVector, InferenceUsage

if TYPE_CHECKING:
    from powercontext.builtin.artifacts.memory.models import EmbeddingProfile
    from powercontext.builtin.inference.protocols import EmbeddingModel

# A cache key is (profile fingerprint, query/document kind, exact text).
_CacheKey = tuple[str, str, str]

_DOCUMENT_KIND = "document"
_QUERY_KIND = "query"


@dataclass(eq=False, slots=True)
class _Batch:
    """One successful provider call shared by all waiters of its texts."""

    keys: tuple[_CacheKey, ...]
    usage: InferenceUsage


@dataclass(slots=True)
class _Outcome:
    """Per-text result slot resolved by the coordinator without raising in a Future."""

    vector: EmbeddingVector | None = None
    batch: _Batch | None = None
    error: BaseException | None = None


@dataclass(slots=True)
class _Item:
    """One distinct text waiting for the coordinator."""

    key: _CacheKey
    text: str
    future: asyncio.Future[_Outcome]


def _sum_usage(usages: list[InferenceUsage]) -> InferenceUsage:
    """Aggregate provider usage, treating an all-``None`` token field as unknown."""

    requests = 0
    input_tokens = 0
    output_tokens = 0
    has_input = False
    has_output = False
    for usage in usages:
        requests += usage.requests
        if usage.input_tokens is not None:
            has_input = True
            input_tokens += usage.input_tokens
        if usage.output_tokens is not None:
            has_output = True
            output_tokens += usage.output_tokens
    return InferenceUsage(
        requests=requests,
        input_tokens=input_tokens if has_input else None,
        output_tokens=output_tokens if has_output else None,
    )


class CoalescingEmbeddingModel:
    """Coalesce concurrent embedding calls and cache successful vectors in LRU order.

    The wrapper is meant to be shared by all concurrent callers of one event loop.
    It exposes the underlying model's ``profile`` and returns the existing
    :class:`EmbeddingResult` contract from :meth:`embed` (and :meth:`embed_query`
    for providers that distinguish retrieval queries from documents).
    """

    def __init__(
        self,
        model: EmbeddingModel,
        *,
        max_batch_size: int = 64,
        coalesce_ms: float = 5.0,
        cache_size: int = 1024,
    ) -> None:
        if max_batch_size < 1:
            raise ValueError("max_batch_size must be positive")  # noqa: TRY003
        if coalesce_ms < 0:
            raise ValueError("coalesce_ms must not be negative")  # noqa: TRY003
        if cache_size < 0:
            raise ValueError("cache_size must not be negative")  # noqa: TRY003
        self._model = model
        self._max_batch_size = max_batch_size
        self._coalesce_seconds = coalesce_ms / 1000.0
        self._cache_size = cache_size
        self._cache: OrderedDict[_CacheKey, EmbeddingVector] = OrderedDict()
        self._pending: OrderedDict[_CacheKey, _Item] = OrderedDict()
        self._inflight: dict[_CacheKey, _Item] = {}
        self._runner: asyncio.Task[None] | None = None
        self._tasks: set[asyncio.Task[None]] = set()

    @property
    def profile(self) -> EmbeddingProfile:
        """Expose the deployment-fixed profile of the underlying model."""

        return self._model.profile

    async def embed(self, texts: tuple[str, ...], /) -> EmbeddingResult:
        """Embed documents, sharing work for identical texts across calls."""

        return await self._embed(texts, kind=_DOCUMENT_KIND)

    async def embed_query(self, texts: tuple[str, ...], /) -> EmbeddingResult:
        """Embed retrieval queries through the provider's query path when it has one.

        Symmetric providers have no separate query prompt, so query texts reuse the
        document path (and its cache) instead of duplicating the same computation.
        """

        if callable(getattr(self._model, "embed_query", None)):
            return await self._embed(texts, kind=_QUERY_KIND)
        return await self._embed(texts, kind=_DOCUMENT_KIND)

    async def _embed(self, texts: tuple[str, ...], *, kind: str) -> EmbeddingResult:
        ordered = tuple(texts)
        if not ordered:
            return EmbeddingResult(vectors=(), usage=InferenceUsage(requests=0))

        fingerprint = self._fingerprint()
        key_by_text = {text: (fingerprint, kind, text) for text in ordered}

        vectors: dict[_CacheKey, EmbeddingVector] = {}
        pending: list[tuple[_CacheKey, _Item]] = []
        for text, key in key_by_text.items():
            cached = self._cache_get(key)
            if cached is not None:
                vectors[key] = cached
            else:
                pending.append((key, self._register(key, text)))

        if pending:
            self._ensure_runner()
            outcomes = await asyncio.gather(*(asyncio.shield(item.future) for _, item in pending))
            batches: dict[int, _Batch] = {}
            for (key, _), outcome in zip(pending, outcomes, strict=True):
                if outcome.error is not None:
                    raise outcome.error
                vector = outcome.vector
                if vector is None:
                    raise RuntimeError("coalescing coordinator produced no vector")  # noqa: TRY003
                vectors[key] = vector
                if outcome.batch is not None:
                    batches[id(outcome.batch)] = outcome.batch
            usage = _sum_usage([batch.usage for batch in batches.values()])
        else:
            usage = InferenceUsage(requests=0)

        ordered_vectors = tuple(vectors[key_by_text[text]] for text in ordered)
        return EmbeddingResult(vectors=ordered_vectors, usage=usage)

    def _fingerprint(self) -> str:
        profile = getattr(self._model, "profile", None)
        dump = getattr(profile, "model_dump_json", None)
        if callable(dump):
            return dump()
        return repr(profile)

    def _cache_get(self, key: _CacheKey) -> EmbeddingVector | None:
        if self._cache_size == 0:
            return None
        vector = self._cache.get(key)
        if vector is not None:
            self._cache.move_to_end(key)
        return vector

    def _cache_put(self, key: _CacheKey, vector: EmbeddingVector) -> None:
        if self._cache_size == 0:
            return
        self._cache[key] = vector
        self._cache.move_to_end(key)
        while len(self._cache) > self._cache_size:
            self._cache.popitem(last=False)

    def _register(self, key: _CacheKey, text: str) -> _Item:
        """Return the existing future for a distinct text, or enqueue a new one."""

        item = self._inflight.get(key)
        if item is not None:
            return item
        item = self._pending.get(key)
        if item is not None:
            return item
        item = _Item(key=key, text=text, future=asyncio.get_running_loop().create_future())
        self._pending[key] = item
        return item

    def _ensure_runner(self) -> None:
        runner = self._runner
        if runner is not None and not runner.done():
            return
        runner = asyncio.get_running_loop().create_task(self._dispatch())
        self._runner = runner
        self._tasks.add(runner)
        runner.add_done_callback(self._runner_done)

    def _runner_done(self, task: asyncio.Task[None]) -> None:
        self._tasks.discard(task)
        if self._runner is task:
            self._runner = None
        if task.cancelled():
            self._fail_all(asyncio.CancelledError())
            return
        error = task.exception()
        if error is not None:
            self._fail_all(error)

    async def _dispatch(self) -> None:
        """Drain pending texts into bounded batches until nothing is left."""

        while self._pending:
            if self._coalesce_seconds > 0:
                # Give concurrent callers a chance to join this window; the sleep also
                # yields the loop so they can register before the batch is claimed.
                await asyncio.sleep(self._coalesce_seconds)
            # A provider request has one input type, so a batch never mixes query and
            # document texts even when both are pending at the same time.
            kind = next(iter(self._pending.values())).key[1]
            items: list[_Item] = []
            for key in list(self._pending):
                if len(items) >= self._max_batch_size:
                    break
                item = self._pending[key]
                if item.key[1] != kind:
                    continue
                del self._pending[key]
                self._inflight[key] = item
                items.append(item)
            if not items:
                continue
            texts = tuple(item.text for item in items)
            embed = self._model.embed_query if kind == _QUERY_KIND else self._model.embed
            try:
                result = await embed(texts)
            except Exception as error:
                self._fail(items, error)
                continue
            vectors = tuple(result.vectors)
            if len(vectors) != len(items):
                self._fail(
                    items,
                    ValueError(f"expected {len(items)} vectors for {len(items)} texts, received {len(vectors)}"),
                )
                continue
            batch = _Batch(keys=tuple(item.key for item in items), usage=result.usage)
            self._succeed(items, vectors, batch)

    def _succeed(self, items: list[_Item], vectors: tuple[EmbeddingVector, ...], batch: _Batch) -> None:
        for item in items:
            self._inflight.pop(item.key, None)
        for item, vector in zip(items, vectors, strict=True):
            self._cache_put(item.key, vector)
        for item, vector in zip(items, vectors, strict=True):
            if not item.future.done():
                item.future.set_result(_Outcome(vector=vector, batch=batch))

    def _fail(self, items: list[_Item], error: BaseException) -> None:
        for item in items:
            self._inflight.pop(item.key, None)
        for item in items:
            if not item.future.done():
                item.future.set_result(_Outcome(error=error))

    def _fail_all(self, error: BaseException) -> None:
        items = list(self._pending.values()) + list(self._inflight.values())
        self._pending.clear()
        self._inflight.clear()
        for item in items:
            if not item.future.done():
                item.future.set_result(_Outcome(error=error))


__all__ = ["CoalescingEmbeddingModel"]

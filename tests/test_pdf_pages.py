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

"""Behavior tests for PDF page evidence retrieval."""

from __future__ import annotations

import asyncio

from powercontext.artifacts import ArtifactRef
from powercontext.builtin.artifacts.memory import CapabilityNotSupportedError
from powercontext.sources.pdf_pages import _preferred_search_mode, _search_stage


class _Capabilities:
    def __init__(self, hybrid: bool) -> None:
        self.hybrid = hybrid


class _Hit:
    def __init__(self, entry_id: str) -> None:
        self.memory_ref = ArtifactRef(family="memory", artifact_id="memory", revision=1)
        self.entry_id = entry_id
        self.entry_version_id = f"{entry_id}-v1"


class _Page:
    def __init__(self, hits: tuple[_Hit, ...]) -> None:
        self.hits = hits


class _FakeMemory:
    def __init__(self, *, hybrid: bool | None, fail_hybrid: bool = False) -> None:
        self._hybrid = hybrid
        self._fail_hybrid = fail_hybrid
        self.modes: list[str] = []

    async def capabilities(self) -> _Capabilities:
        if self._hybrid is None:
            raise RuntimeError("capability probe unavailable")
        return _Capabilities(self._hybrid)

    async def search(self, request) -> _Page:
        self.modes.append(request.mode)
        if request.mode == "hybrid" and self._fail_hybrid:
            raise CapabilityNotSupportedError("hybrid")
        return _Page((_Hit(f"entry-{len(self.modes)}"),))


class _NoCapabilitiesMemory:
    def __init__(self) -> None:
        self.modes: list[str] = []

    async def search(self, request) -> _Page:
        self.modes.append(request.mode)
        return _Page((_Hit("entry"),))


def test_preferred_mode_uses_hybrid_only_when_advertised() -> None:
    assert asyncio.run(_preferred_search_mode(_FakeMemory(hybrid=True))) == "hybrid"
    assert asyncio.run(_preferred_search_mode(_FakeMemory(hybrid=False))) == "fts"


def test_preferred_mode_degrades_when_probe_fails_or_is_absent() -> None:
    assert asyncio.run(_preferred_search_mode(_FakeMemory(hybrid=None))) == "fts"
    assert asyncio.run(_preferred_search_mode(_NoCapabilitiesMemory())) == "fts"


def test_search_stage_falls_back_to_fts_when_hybrid_is_rejected() -> None:
    memory = _FakeMemory(hybrid=True, fail_hybrid=True)
    citations = asyncio.run(_search_stage(memory, "what was net revenue", 10))

    assert citations
    # First attempt advertises hybrid, every retry is lexical; no exception escapes.
    assert memory.modes[0] == "hybrid"
    assert "fts" in memory.modes
    assert set(memory.modes) <= {"hybrid", "fts"}


def test_search_stage_keeps_fts_when_no_hybrid_capability_exists() -> None:
    memory = _FakeMemory(hybrid=False)
    citations = asyncio.run(_search_stage(memory, "what was net revenue", 10))

    assert citations
    assert set(memory.modes) == {"fts"}

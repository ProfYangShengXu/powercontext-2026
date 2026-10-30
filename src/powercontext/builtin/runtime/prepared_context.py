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

"""Prepare final, bounded context from approved Artifact evidence."""

from __future__ import annotations

import json
import math
import re
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from itertools import chain
from typing import TypeVar

from powercontext.artifacts import ArtifactAddress, ArtifactRef
from powercontext.builtin.artifacts.experience import Experience, ExperienceSearchHit, render_experience
from powercontext.builtin.artifacts.memory.models import MemoryCitation, MemoryHit
from powercontext.builtin.artifacts.profile.models import Profile
from powercontext.builtin.artifacts.topic_memory import TopicMemory, TopicMemorySearchHit
from powercontext.builtin.runtime.errors import PreparedContextInvariantError
from powercontext.builtin.runtime.models import PrepareContextRequest, PreparedContext
from powercontext.builtin.runtime.prepared_code import (
    CodeEvidenceRef,
    PreparedCodeCandidate,
    assemble_code,
)
from powercontext.builtin.runtime.prepared_text import (
    TRUST_POLICY,
    ContextTextItem,
    fit_context_text_item,
    render_context_text,
)
from powercontext.builtin.runtime.recall_sufficiency import RecallBudgetView

_MIN_TRUNCATED_CONTENT_BYTES = 64
_ELLIPSIS = "…"
_BEGIN_MARKER = "BEGIN_POWERCONTEXT_PREPARED_CONTEXT_V1"
_END_MARKER = "END_POWERCONTEXT_PREPARED_CONTEXT_V1"
_Item = TypeVar("_Item")


@dataclass(frozen=True)
class _PreparedContextEntry:
    origin: PreparedContextOrigin
    kind: str
    citation: dict[str, object]
    content: str
    truncated: bool


@dataclass(frozen=True)
class _EntryFit:
    """One fit attempt on the non-assembly path: the entry that fitted, or why none did.

    The two drop reasons are disjoint and mirror the assembly path's
    :class:`~powercontext.builtin.runtime.prepared_text.FitOutcome`. An entry is dropped
    *below the minimum truncated content* when a shorter rendering exists and fits but is too
    short to be an honest delivery; it is dropped *with no fitting truncation* when no
    shortened rendering fits at all.
    """

    entry: _PreparedContextEntry | None = None
    dropped_below_min_bytes: bool = False
    dropped_no_fitting_truncation: bool = False


@dataclass(frozen=True)
class PreparedContextOmissions:
    """Aggregate counts of the items the byte budget omitted in one build.

    Every count is a per-call aggregate, never per-entry attribution and never a verdict about
    an entry: an entry losing to the budget is not a negative result about that entry.

    ``dropped_items`` is the sum of its two sub-counts, so "the budget could not fit this
    item" can be told apart from "this item was too short to truncate into the remaining
    space". A single merged counter would misreport the second cause as the first.
    """

    truncated_items: int = 0
    dropped_items: int = 0
    dropped_below_min_bytes: int = 0
    dropped_no_fitting_truncation: int = 0


@dataclass(frozen=True)
class PreparedContextBuild:
    """Final public context and the exact origins selected to produce it.

    ``fragments`` is empty for the ordinary prepare path and for an empty evidence
    selection. When ``PrepareContextRequest.evidence_selection`` is on it holds the
    literal, query-relevant spans that produced ``context``, in the same order as
    ``origins``.

    ``omissions`` is always filled by the Builder. The RFC 1560 recall trace is deliberately
    **not** a field here: ``ScopedContextApplication._prepare`` returns ``build.context`` and
    discards the rest, so such a field would have no production observer. The trace is
    delivered through the Runtime's optional ``RecallEffortSink`` instead.
    """

    context: PreparedContext
    origins: tuple[PreparedContextOrigin, ...]
    omissions: PreparedContextOmissions = PreparedContextOmissions()
    code_origins: tuple[CodeEvidenceRef, ...] = ()
    fragments: tuple[PreparedEvidenceFragment, ...] = ()


@dataclass(frozen=True)
class MemoryEntryAddress:
    """Identify one exact Memory entry version across Scope boundaries."""

    memory: ArtifactAddress
    entry_id: str
    entry_version_id: str


@dataclass(frozen=True)
class PreparedEvidenceFragment:
    """One literal original-text span selected for the current query.

    The span is a half-open Python character range into the addressed entry version:
    ``text == entry.text[start:end]`` and ``0 <= start < end <= len(entry.text)``.
    """

    address: MemoryEntryAddress
    start: int
    end: int
    text: str


PreparedContextOrigin = MemoryCitation | ArtifactRef | MemoryEntryAddress | ArtifactAddress


@dataclass(frozen=True)
class PreparedMemoryCandidates:
    """Memory candidates read from one Scope."""

    scope_id: str
    memory_ref: ArtifactRef | None = None
    hits: tuple[MemoryHit, ...] = ()


@dataclass(frozen=True)
class PreparedExperienceCandidates:
    """Experience candidates read from one Scope."""

    scope_id: str
    hits: tuple[ExperienceSearchHit, ...] = ()


@dataclass(frozen=True)
class PreparedProfileCandidate:
    """The latest committed Profile snapshot read from one Scope."""

    scope_id: str
    profile: Profile


class PreparedContextBuilder:
    """Select and render final context without I/O, persistence, or reranking."""

    memory_candidate_limit = 16
    topic_memory_candidate_limit = 8
    experience_candidate_limit = 8
    candidate_limit = memory_candidate_limit
    entry_limit = 8
    topic_memory_entry_limit = 8
    experience_entry_limit = 2
    max_entry_content_bytes = 2000
    _text_entry_limit: int | None = None

    def empty(self) -> PreparedContext:
        return PreparedContext(status="empty", content=None, content_bytes=0)

    def build(
        self,
        *,
        request: PrepareContextRequest,
        scope_id: str | None = None,
        memory_ref: ArtifactRef | None = None,
        hits: Sequence[MemoryHit] = (),
        topic_memory_hits: Sequence[TopicMemorySearchHit] = (),
        experience_hits: Sequence[ExperienceSearchHit] = (),
    ) -> PreparedContext:
        return self.build_result(
            request=request,
            scope_id=scope_id,
            memory_ref=memory_ref,
            hits=hits,
            topic_memory_hits=topic_memory_hits,
            experience_hits=experience_hits,
        ).context

    def build_result(
        self,
        *,
        request: PrepareContextRequest,
        scope_id: str | None = None,
        memory_ref: ArtifactRef | None = None,
        hits: Sequence[MemoryHit] = (),
        topic_memory_hits: Sequence[TopicMemorySearchHit] = (),
        experience_hits: Sequence[ExperienceSearchHit] = (),
    ) -> PreparedContextBuild:
        return self.build_scopes_result(
            request=request,
            current_scope_id=scope_id,
            memory_candidates=(
                PreparedMemoryCandidates(scope_id=scope_id or "", memory_ref=memory_ref, hits=tuple(hits)),
            ),
            experience_candidates=(PreparedExperienceCandidates(scope_id=scope_id or "", hits=tuple(experience_hits)),),
            topic_memory_hits=topic_memory_hits,
        )

    def build_scopes_result(
        self,
        *,
        request: PrepareContextRequest,
        current_scope_id: str | None,
        memory_candidates: Sequence[PreparedMemoryCandidates] = (),
        topic_memory_hits: Sequence[TopicMemorySearchHit] = (),
        experience_candidates: Sequence[PreparedExperienceCandidates] = (),
        profile_candidates: Sequence[PreparedProfileCandidate] = (),
        code_candidates: Sequence[PreparedCodeCandidate] = (),
    ) -> PreparedContextBuild:
        if request.evidence_selection:
            return self._build_evidence_result(
                request=request,
                current_scope_id=current_scope_id,
                memory_candidates=memory_candidates,
            )

        if request.include_code:

            def historical(history_request: PrepareContextRequest, entries: int) -> PreparedContextBuild:
                builder = PreparedContextBuilder()
                builder.entry_limit = entries
                builder._text_entry_limit = entries if entries < self.entry_limit else None
                return builder.build_scopes_result(
                    request=history_request.model_copy(update={"include_code": False}),
                    current_scope_id=current_scope_id,
                    memory_candidates=memory_candidates,
                    topic_memory_hits=topic_memory_hits,
                    experience_candidates=experience_candidates,
                    profile_candidates=profile_candidates,
                )

            return assemble_code(request, code_candidates, self.entry_limit, historical)

        if sum(len(candidates.hits) for candidates in memory_candidates) > self.memory_candidate_limit:
            raise PreparedContextInvariantError("memory-candidate-limit")
        if len(topic_memory_hits) > self.topic_memory_candidate_limit:
            raise PreparedContextInvariantError("topic-memory-candidate-limit")
        if sum(len(candidates.hits) for candidates in experience_candidates) > self.experience_candidate_limit:
            raise PreparedContextInvariantError("experience-candidate-limit")

        content, origins, omissions = self._select_entries(
            request=request,
            current_scope_id=current_scope_id,
            memory_candidates=memory_candidates,
            experience_candidates=experience_candidates,
            profile_candidates=profile_candidates,
            topic_memory_hits=topic_memory_hits,
        )
        if content is None:
            return PreparedContextBuild(context=self.empty(), origins=(), omissions=omissions)
        content_bytes = len(content.encode("utf-8"))
        # The assembly path is bounded by construction (every fitted item is measured against
        # `max_bytes`), so the ceiling check belongs to the non-assembly renderer only.
        if request.assembly is None and content_bytes > request.max_bytes:
            raise PreparedContextInvariantError("output-budget")
        return PreparedContextBuild(
            context=PreparedContext(status="ready", content=content, content_bytes=content_bytes),
            origins=origins,
            omissions=omissions,
        )

    def probe_budget(
        self,
        *,
        request: PrepareContextRequest,
        current_scope_id: str | None,
        memory_candidates: Sequence[PreparedMemoryCandidates] = (),
        topic_memory_hits: Sequence[TopicMemorySearchHit] = (),
        experience_candidates: Sequence[PreparedExperienceCandidates] = (),
        profile_candidates: Sequence[PreparedProfileCandidate] = (),
    ) -> RecallBudgetView:
        """Report what the byte budget does to one candidate set, without delivering it.

        This is the RFC 1560 *budget probe*: one pass of this Builder's own pure selection and
        rendering code, keeping only the counters and discarding the rendered output. It lets
        the gate tell budget-limited thinness from recall-limited thinness.

        Pure and side-effect free: no I/O, no persistence, nothing reported to any sink, and
        the same inputs as :meth:`build_scopes_result` are guaranteed to yield the same
        counters because both go through :meth:`_select_entries`.
        """

        content, origins, omissions = self._select_entries(
            request=request,
            current_scope_id=current_scope_id,
            memory_candidates=memory_candidates,
            experience_candidates=experience_candidates,
            profile_candidates=profile_candidates,
            topic_memory_hits=topic_memory_hits,
        )
        content_bytes = 0 if content is None else len(content.encode("utf-8"))
        return RecallBudgetView(
            max_bytes=request.max_bytes,
            delivered_items=len(origins),
            truncated_items=omissions.truncated_items,
            dropped_items=omissions.dropped_items,
            unused_bytes=max(0, request.max_bytes - content_bytes),
        )

    def _select_entries(
        self,
        *,
        request: PrepareContextRequest,
        current_scope_id: str | None,
        memory_candidates: Sequence[PreparedMemoryCandidates],
        topic_memory_hits: Sequence[TopicMemorySearchHit],
        experience_candidates: Sequence[PreparedExperienceCandidates],
        profile_candidates: Sequence[PreparedProfileCandidate],
    ) -> tuple[str | None, tuple[PreparedContextOrigin, ...], PreparedContextOmissions]:
        """Run one complete pure selection pass and render it.

        Returns ``(content, origins, omissions)``; ``content`` is ``None`` exactly when nothing
        was selected, which is the empty-result case for both paths. This is the single
        selection path shared by :meth:`build_scopes_result` and :meth:`probe_budget`, so the
        probe's counters always describe the selection the build would actually perform.
        """

        if request.assembly is not None:
            return self._select_text(
                request,
                current_scope_id=current_scope_id,
                memory_candidates=memory_candidates,
                experience_candidates=experience_candidates,
                profile_candidates=profile_candidates,
                topic_memory_hits=topic_memory_hits,
            )

        memory_entries = _interleave_groups(
            tuple(
                self._memory_entries(
                    candidates.memory_ref,
                    candidates.hits,
                    scope_id=None if candidates.scope_id == current_scope_id else candidates.scope_id or None,
                )
                for candidates in memory_candidates
            )
        )
        topic_memory_entries = self._topic_memory_entries(topic_memory_hits)
        experience_entries = _interleave_groups(
            tuple(
                self._experience_entries(
                    candidates.hits,
                    scope_id=None if candidates.scope_id == current_scope_id else candidates.scope_id or None,
                )
                for candidates in experience_candidates
            )
        )[: self.experience_entry_limit]
        entries, omissions = self._fit_entries(request, memory_entries, topic_memory_entries, experience_entries)
        if not entries:
            return None, (), omissions
        return _render(entries), tuple(entry.origin for entry in entries), omissions

    def _select_text(
        self,
        request: PrepareContextRequest,
        *,
        current_scope_id: str | None,
        memory_candidates: Sequence[PreparedMemoryCandidates],
        experience_candidates: Sequence[PreparedExperienceCandidates],
        profile_candidates: Sequence[PreparedProfileCandidate],
        topic_memory_hits: Sequence[TopicMemorySearchHit],
    ) -> tuple[str | None, tuple[PreparedContextOrigin, ...], PreparedContextOmissions]:
        assembly = request.assembly
        if assembly is None:
            raise PreparedContextInvariantError("text-assembly-missing")
        included: list[ContextTextItem] = []
        origins: list[PreparedContextOrigin] = []
        truncated_items = 0
        dropped_below_min_bytes = 0
        dropped_no_fitting_truncation = 0
        for section in assembly.sections:
            # Unfilled earlier sections leave their capacity available to later ones.
            limit = (
                section.limit
                if self._text_entry_limit is None
                else min(section.limit, self._text_entry_limit - len(included))
            )
            if section.family == "profile":
                entries = self._profile_entries(profile_candidates)
            elif section.family == "topic-memory":
                entries = self._topic_memory_entries(topic_memory_hits, scope_id=current_scope_id)
            else:
                groups = (
                    tuple(
                        tuple(
                            self._memory_entries(group.memory_ref, (hit,), scope_id=group.scope_id)
                            for hit in group.hits
                        )
                        for group in memory_candidates
                    )
                    if section.family == "memory"
                    else tuple(
                        tuple(self._experience_entries((hit,), scope_id=group.scope_id) for hit in group.hits)
                        for group in experience_candidates
                    )
                )
                entries = tuple(chain.from_iterable(_interleave_groups(groups)))
            seen: set[tuple[str, str, str, int, str | None, str | None]] = set()
            rank = 0
            selected_count = 0
            for entry in entries:
                if selected_count >= limit:
                    break
                item = _text_item(entry)
                artifact = item.artifact
                identity = (
                    artifact.scope_id,
                    artifact.artifact.family,
                    artifact.artifact.artifact_id,
                    artifact.artifact.revision,
                    item.entry_id,
                    item.entry_version_id,
                )
                if identity in seen or not item.content.strip():
                    continue
                seen.add(identity)
                rank += 1
                outcome = fit_context_text_item(included, replace(item, recall_rank=rank), assembly, request.max_bytes)
                if outcome.item is None:
                    dropped_below_min_bytes += int(outcome.dropped_below_min_bytes)
                    dropped_no_fitting_truncation += int(outcome.dropped_no_fitting_truncation)
                    continue
                fitted = outcome.item
                included.append(fitted)
                origins.append(entry.origin)
                selected_count += 1
                truncated_items += int(fitted.truncated)
        omissions = PreparedContextOmissions(
            truncated_items=truncated_items,
            dropped_items=dropped_below_min_bytes + dropped_no_fitting_truncation,
            dropped_below_min_bytes=dropped_below_min_bytes,
            dropped_no_fitting_truncation=dropped_no_fitting_truncation,
        )
        if not included:
            return None, (), omissions
        content = render_context_text(included, assembly)
        return content, tuple(origins), omissions

    def _build_evidence_result(
        self,
        *,
        request: PrepareContextRequest,
        current_scope_id: str | None,
        memory_candidates: Sequence[PreparedMemoryCandidates],
    ) -> PreparedContextBuild:
        """Select literal query-relevant Memory spans and render them under ``max_bytes``.

        Only Memory candidates participate: the mode is defined over recalled Memory text, so
        Code, TopicMemory, Experience and Profile are intentionally ignored while it is on.
        """

        documents = self._evidence_documents(current_scope_id, memory_candidates)
        fragments = _select_evidence_fragments(
            query=request.query,
            max_bytes=request.max_bytes,
            documents=documents,
        )
        if not fragments:
            return PreparedContextBuild(context=self.empty(), origins=(), omissions=PreparedContextOmissions())
        content = _render_evidence(fragments)
        content_bytes = len(content.encode("utf-8"))
        if content_bytes > request.max_bytes:
            raise PreparedContextInvariantError("output-budget")
        return PreparedContextBuild(
            context=PreparedContext(status="ready", content=content, content_bytes=content_bytes),
            origins=tuple(fragment.address for fragment in fragments),
            omissions=PreparedContextOmissions(),
            fragments=fragments,
        )

    def _evidence_documents(
        self,
        current_scope_id: str | None,
        memory_candidates: Sequence[PreparedMemoryCandidates],
    ) -> tuple[_EvidenceDocument, ...]:
        documents: list[_EvidenceDocument] = []
        seen: set[tuple[str, str, str]] = set()
        for candidates in memory_candidates:
            scope_id = candidates.scope_id or current_scope_id or ""
            if not scope_id:
                continue
            if candidates.hits and candidates.memory_ref is None:
                raise PreparedContextInvariantError("memory-ref-missing")
            for hit in candidates.hits:
                if hit.memory_ref != candidates.memory_ref:
                    raise PreparedContextInvariantError("memory-ref-mismatch")
                identity = (scope_id, hit.entry_id, hit.entry_version_id)
                if identity in seen:
                    continue
                seen.add(identity)
                if not hit.text.strip() or not hit.entry_id.strip() or not hit.entry_version_id.strip():
                    continue
                address = MemoryEntryAddress(
                    memory=ArtifactAddress(scope_id=scope_id, artifact=hit.memory_ref),
                    entry_id=hit.entry_id,
                    entry_version_id=hit.entry_version_id,
                )
                documents.append(
                    _EvidenceDocument(address=address, text=hit.text, units=_evidence_units(hit.text))
                )
        return tuple(documents)

    def _profile_entries(self, candidates: Sequence[PreparedProfileCandidate]) -> tuple[_PreparedContextEntry, ...]:
        entries = []
        for candidate in candidates:
            origin = ArtifactAddress(scope_id=candidate.scope_id, artifact=candidate.profile.as_ref())
            entries.append(
                _PreparedContextEntry(
                    origin=origin,
                    kind="profile",
                    citation={"artifact": origin.model_dump(mode="json")},
                    content=candidate.profile.content.content,
                    truncated=False,
                )
            )
        return tuple(entries)

    def _memory_entries(
        self,
        memory_ref: ArtifactRef | None,
        hits: Sequence[MemoryHit],
        *,
        scope_id: str | None = None,
    ) -> tuple[_PreparedContextEntry, ...]:
        if hits and memory_ref is None:
            raise PreparedContextInvariantError("memory-ref-missing")
        memory_entries: list[_PreparedContextEntry] = []
        seen: set[tuple[str, str]] = set()
        for hit in hits:
            if hit.memory_ref != memory_ref:
                raise PreparedContextInvariantError("memory-ref-mismatch")

            citation_key = (hit.entry_id, hit.entry_version_id)
            if citation_key in seen:
                continue
            seen.add(citation_key)
            if not hit.entry_id.strip() or not hit.entry_version_id.strip() or not hit.text.strip():
                continue
            citation = MemoryCitation(
                memory_ref=hit.memory_ref,
                entry_id=hit.entry_id,
                entry_version_id=hit.entry_version_id,
            )
            origin: PreparedContextOrigin = citation
            rendered_citation = citation.model_dump(mode="json")
            if scope_id is not None:
                memory = ArtifactAddress(scope_id=scope_id, artifact=hit.memory_ref)
                origin = MemoryEntryAddress(
                    memory=memory,
                    entry_id=hit.entry_id,
                    entry_version_id=hit.entry_version_id,
                )
                rendered_citation = {
                    "memory": memory.model_dump(mode="json"),
                    "entry_id": hit.entry_id,
                    "entry_version_id": hit.entry_version_id,
                }
            memory_entries.append(
                _PreparedContextEntry(
                    origin=origin,
                    kind="memory",
                    citation=rendered_citation,
                    content=hit.text,
                    truncated=False,
                )
            )
        return tuple(memory_entries)

    def _topic_memory_entries(
        self,
        hits: Sequence[TopicMemorySearchHit],
        *,
        scope_id: str | None = None,
    ) -> tuple[_PreparedContextEntry, ...]:
        topic_entries: list[_PreparedContextEntry] = []
        seen_topics: set[tuple[str, int]] = set()
        for hit in hits:
            if hit.artifact_ref.family != TopicMemory.family:
                raise PreparedContextInvariantError("topic-memory-family-mismatch")
            identity = (hit.artifact_ref.artifact_id, hit.artifact_ref.revision)
            if identity in seen_topics:
                continue
            seen_topics.add(identity)
            if len(topic_entries) >= self.topic_memory_entry_limit:
                break
            content = {"title": hit.title, "summary": hit.summary}
            if hit.snippet is not None:
                content["snippet"] = hit.snippet
            origin: PreparedContextOrigin = hit.artifact_ref
            citation = {"artifact_ref": hit.artifact_ref.model_dump(mode="json")}
            body = json.dumps(content, ensure_ascii=False, separators=(",", ":"))
            if scope_id is not None:
                origin = ArtifactAddress(scope_id=scope_id, artifact=hit.artifact_ref)
                citation = {"artifact": origin.model_dump(mode="json")}
                body = "\n\n".join(f"{label.capitalize()}: {value}" for label, value in content.items())
            topic_entries.append(
                _PreparedContextEntry(
                    origin=origin,
                    kind="topic-memory",
                    citation=citation,
                    content=body,
                    truncated=False,
                )
            )
        return tuple(topic_entries)

    def _experience_entries(
        self,
        hits: Sequence[ExperienceSearchHit],
        *,
        scope_id: str | None = None,
    ) -> tuple[_PreparedContextEntry, ...]:
        experience_entries: list[_PreparedContextEntry] = []
        seen_experiences: set[tuple[str, int]] = set()
        for hit in hits:
            if hit.artifact_ref.family != Experience.family:
                raise PreparedContextInvariantError("experience-family-mismatch")
            identity = (hit.artifact_ref.artifact_id, hit.artifact_ref.revision)
            if identity in seen_experiences:
                continue
            seen_experiences.add(identity)
            origin: PreparedContextOrigin = hit.artifact_ref
            rendered_citation: dict[str, object] = {"artifact_ref": hit.artifact_ref.model_dump(mode="json")}
            if scope_id is not None:
                origin = ArtifactAddress(scope_id=scope_id, artifact=hit.artifact_ref)
                rendered_citation = {"artifact": origin.model_dump(mode="json")}
            experience_entries.append(
                _PreparedContextEntry(
                    origin=origin,
                    kind="experience",
                    citation=rendered_citation,
                    content=render_experience(hit.content),
                    truncated=False,
                )
            )
        return tuple(experience_entries)

    def _fit_entries(
        self,
        request: PrepareContextRequest,
        memory_entries: Sequence[_PreparedContextEntry],
        topic_memory_entries: Sequence[_PreparedContextEntry],
        experience_entries: Sequence[_PreparedContextEntry],
    ) -> tuple[tuple[_PreparedContextEntry, ...], PreparedContextOmissions]:
        entries: list[_PreparedContextEntry] = []
        truncated_items = 0
        dropped_below_min_bytes = 0
        dropped_no_fitting_truncation = 0
        for candidate in _interleave(memory_entries, topic_memory_entries, experience_entries):
            if len(entries) >= self.entry_limit:
                break
            fit = self._fit_entry(
                entries,
                origin=candidate.origin,
                kind=candidate.kind,
                citation=candidate.citation,
                text=candidate.content,
                max_bytes=request.max_bytes,
            )
            if fit.entry is None:
                dropped_below_min_bytes += int(fit.dropped_below_min_bytes)
                dropped_no_fitting_truncation += int(fit.dropped_no_fitting_truncation)
                continue
            entries.append(fit.entry)
            if fit.entry.truncated:
                truncated_items += 1
        return tuple(entries), PreparedContextOmissions(
            truncated_items=truncated_items,
            dropped_items=dropped_below_min_bytes + dropped_no_fitting_truncation,
            dropped_below_min_bytes=dropped_below_min_bytes,
            dropped_no_fitting_truncation=dropped_no_fitting_truncation,
        )

    def _fit_entry(
        self,
        entries: Sequence[_PreparedContextEntry],
        *,
        origin: PreparedContextOrigin,
        kind: str,
        citation: dict[str, object],
        text: str,
        max_bytes: int,
    ) -> _EntryFit:
        """Fit one entry into the remaining budget, reporting why it was dropped if it was.

        The two ``None`` paths of the original code are kept apart: ``below-min-bytes`` means a
        shorter rendering exists and fits but is too short to deliver honestly, while
        ``no-fitting-truncation`` means no shortened rendering fitted at all.
        """

        source_bytes = len(text.encode("utf-8"))
        entry_budget = min(source_bytes, self.max_entry_content_bytes)
        candidate = _PreparedContextEntry(
            origin=origin,
            kind=kind,
            citation=citation,
            content=text if source_bytes <= entry_budget else _truncate_utf8(text, entry_budget),
            truncated=source_bytes > entry_budget,
        )
        if _rendered_bytes((*entries, candidate)) <= max_bytes:
            return _EntryFit(entry=candidate)
        if source_bytes < _MIN_TRUNCATED_CONTENT_BYTES:
            return _EntryFit(dropped_below_min_bytes=True)

        lower = _MIN_TRUNCATED_CONTENT_BYTES
        upper = min(entry_budget, source_bytes - 1)
        best: _PreparedContextEntry | None = None
        while lower <= upper:
            byte_budget = (lower + upper) // 2
            candidate = _PreparedContextEntry(
                origin=origin,
                kind=kind,
                citation=citation,
                content=_truncate_utf8(text, byte_budget),
                truncated=True,
            )
            if len(candidate.content.encode("utf-8")) < _MIN_TRUNCATED_CONTENT_BYTES:
                lower = byte_budget + 1
                continue
            if _rendered_bytes((*entries, candidate)) <= max_bytes:
                best = candidate
                lower = byte_budget + 1
            else:
                upper = byte_budget - 1
        if best is None:
            return _EntryFit(dropped_no_fitting_truncation=True)
        return _EntryFit(entry=best)


def _interleave(
    memory: Sequence[_PreparedContextEntry],
    topics: Sequence[_PreparedContextEntry],
    experiences: Sequence[_PreparedContextEntry],
) -> tuple[_PreparedContextEntry, ...]:
    """Preserve each family's rank while alternating Memory, Topic, Experience."""

    ordered: list[_PreparedContextEntry] = []
    for index in range(max(len(memory), len(topics), len(experiences))):
        if index < len(memory):
            ordered.append(memory[index])
        if index < len(topics):
            ordered.append(topics[index])
        if index < len(experiences):
            ordered.append(experiences[index])
    return tuple(ordered)


def _interleave_groups(groups: Sequence[Sequence[_Item]]) -> tuple[_Item, ...]:
    ordered: list[_Item] = []
    for index in range(max((len(group) for group in groups), default=0)):
        ordered.extend(group[index] for group in groups if index < len(group))
    return tuple(ordered)


def _render(entries: Sequence[_PreparedContextEntry]) -> str:
    envelope = {
        "trust": "untrusted_history",
        "items": [_render_entry(entry) for entry in entries],
    }
    encoded = json.dumps(envelope, ensure_ascii=False, separators=(",", ":"))
    return "\n\n".join((TRUST_POLICY, f"{_BEGIN_MARKER}\n{encoded}\n{_END_MARKER}"))


def _text_item(entry: _PreparedContextEntry) -> ContextTextItem:
    origin = entry.origin
    if isinstance(origin, MemoryEntryAddress):
        return ContextTextItem(
            artifact=origin.memory,
            content=entry.content,
            recall_rank=0,
            entry_id=origin.entry_id,
            entry_version_id=origin.entry_version_id,
        )
    if isinstance(origin, ArtifactAddress):
        return ContextTextItem(artifact=origin, content=entry.content, recall_rank=0)
    raise PreparedContextInvariantError("text-scope-missing")


def _render_entry(entry: _PreparedContextEntry) -> dict[str, object]:
    rendered: dict[str, object] = {
        "citation": entry.citation,
        "content": entry.content,
        "truncated": entry.truncated,
    }
    if entry.kind != "memory":
        rendered["kind"] = entry.kind
    return rendered


def _rendered_bytes(entries: Sequence[_PreparedContextEntry]) -> int:
    return len(_render(entries).encode("utf-8"))


def _truncate_utf8(text: str, byte_budget: int) -> str:
    prefix_budget = byte_budget - len(_ELLIPSIS.encode("utf-8"))
    encoded_prefix = text.encode("utf-8")[:prefix_budget]
    return f"{encoded_prefix.decode('utf-8', errors='ignore')}{_ELLIPSIS}"


# --- Query- and budget-driven literal evidence selection -------------------------------
#
# The selection runs entirely offline: it splits each candidate entry into sentence-like
# units, scores every contiguous unit window against the query with sentence-level IDF
# weights, and greedily packs the highest relevance windows. Every accepted window is
# measured by rendering the *complete* output (trust policy, envelope, JSON, citations and
# text), so the byte budget is never estimated. Sentence units are literal character ranges
# of the entry, so merging adjacent spans keeps offsets exact. Two cheap priors keep the
# packing from spending the whole budget on a title or an introduction that merely repeats
# the query vocabulary: evidence late in a document is common, and the first unit is often a
# title rather than a claim-bearing sentence.

_EVIDENCE_TOKEN = re.compile(r"[a-z]+|\d+")
_EVIDENCE_BASE_WEIGHT = 0.25
_EVIDENCE_MAX_WINDOW = 2
_EVIDENCE_POSITION_PRIOR = 0.5
_EVIDENCE_TITLE_PENALTY = 0.5
_EVIDENCE_SPLIT_BYTES = 900
_EVIDENCE_ABBREVIATIONS = frozenset({
    "al",
    "approx",
    "ca",
    "cf",
    "co",
    "dr",
    "e.g",
    "eq",
    "eqs",
    "etc",
    "fig",
    "figs",
    "i.e",
    "inc",
    "ltd",
    "max",
    "min",
    "mr",
    "mrs",
    "ms",
    "no",
    "prof",
    "ref",
    "refs",
    "resp",
    "sd",
    "sem",
    "spp",
    "st",
    "suppl",
    "univ",
    "var",
    "vs",
})


@dataclass(frozen=True)
class _EvidenceUnit:
    """One literal sentence-like character range of an entry."""

    start: int
    end: int


@dataclass(frozen=True)
class _EvidenceDocument:
    address: MemoryEntryAddress
    text: str
    units: tuple[_EvidenceUnit, ...]


def _render_evidence(fragments: Sequence[PreparedEvidenceFragment]) -> str:
    """Render the exact evidence envelope whose UTF-8 length is the public budget."""

    items = [
        {
            "citation": {
                "scope_id": fragment.address.memory.scope_id,
                "memory_ref": fragment.address.memory.artifact.model_dump(mode="json"),
                "entry_id": fragment.address.entry_id,
                "entry_version_id": fragment.address.entry_version_id,
            },
            "content": fragment.text,
            "start": fragment.start,
            "end": fragment.end,
        }
        for fragment in fragments
    ]
    envelope = {"trust": "untrusted_history", "items": items}
    encoded = json.dumps(envelope, ensure_ascii=False, separators=(",", ":"))
    return "\n\n".join((TRUST_POLICY, f"{_BEGIN_MARKER}\n{encoded}\n{_END_MARKER}"))


def _select_evidence_fragments(
    *,
    query: str,
    max_bytes: int,
    documents: Sequence[_EvidenceDocument],
) -> tuple[PreparedEvidenceFragment, ...]:
    if not documents:
        return ()

    unit_keys: list[tuple[Counter[str], ...]] = [
        tuple(_evidence_key_counts(document.text[unit.start : unit.end]) for unit in document.units)
        for document in documents
    ]
    total_units = sum(len(keys) for keys in unit_keys)
    document_frequency: Counter[str] = Counter()
    for keys in unit_keys:
        for counts in keys:
            document_frequency.update(counts.keys())

    query_keys = Counter(_evidence_key(token) for token in _EVIDENCE_TOKEN.findall(query.lower()))
    idf = {
        key: _EVIDENCE_BASE_WEIGHT + math.log((total_units + 1.0) / (document_frequency.get(key, 0) + 0.5))
        for key in query_keys
    }

    selected: list[list[tuple[int, int]]] = [[] for _ in documents]
    candidates: list[tuple[float, float, int, int, int]] = []
    for document_index, document in enumerate(documents):
        units = document.units
        text_length = max(1, len(document.text))
        for first in range(len(units)):
            counts: Counter[str] = Counter()
            for size in range(1, _EVIDENCE_MAX_WINDOW + 1):
                last = first + size - 1
                if last >= len(units):
                    break
                counts.update(unit_keys[document_index][last])
                start = units[first].start
                end = units[last].end
                text_bytes = len(document.text[start:end].encode("utf-8"))
                if text_bytes > max_bytes:
                    continue
                score = _evidence_score(query_keys, counts, idf)
                if score <= 0.0:
                    continue
                # Favour later positions and discount the first unit (often the title): both
                # priors suppress query-vocabulary-dense introductions that are not evidence.
                centre = (start + end) / 2 / text_length
                relevance = score * (1.0 + _EVIDENCE_POSITION_PRIOR * centre)
                if first == 0:
                    relevance *= _EVIDENCE_TITLE_PENALTY
                candidates.append((-relevance, -score, document_index, start, end))
    candidates.sort()

    for _, _, document_index, start, end in candidates:
        ranges = selected[document_index]
        if any(low <= start and end <= high for low, high in ranges):
            continue
        tentative = list(_merge_evidence_ranges((*ranges, (start, end))))
        if _evidence_fits(documents, selected, document_index, tentative, max_bytes):
            selected[document_index] = tentative

    # Once relevance-ranked packing stops, spend any remaining budget on units next to the
    # selected evidence first: an adjacent gold sentence shares no citation overhead when it
    # merges, and a further gold span can only raise recall.
    for document_index, unit in _evidence_fill_order(documents, selected):
        ranges = selected[document_index]
        if any(low <= unit.start and unit.end <= high for low, high in ranges):
            continue
        tentative = list(_merge_evidence_ranges((*ranges, (unit.start, unit.end))))
        if _evidence_fits(documents, selected, document_index, tentative, max_bytes):
            selected[document_index] = tentative

    if not any(selected):
        _select_evidence_fallback(documents, selected, max_bytes)

    return _evidence_fragments(documents, selected)


def _evidence_score(
    query_keys: Mapping[str, int],
    counts: Mapping[str, int],
    idf: Mapping[str, float],
) -> float:
    score = 0.0
    for key, wanted in query_keys.items():
        found = counts.get(key, 0)
        if not found:
            continue
        score += idf.get(key, _EVIDENCE_BASE_WEIGHT) * (1.0 + math.log(found)) * min(wanted, found) ** 0.5
    return score


def _evidence_fragments(
    documents: Sequence[_EvidenceDocument],
    selected: Sequence[Sequence[tuple[int, int]]],
) -> tuple[PreparedEvidenceFragment, ...]:
    fragments: list[PreparedEvidenceFragment] = []
    for document, ranges in zip(documents, selected, strict=True):
        for start, end in ranges:
            fragments.append(
                PreparedEvidenceFragment(
                    address=document.address,
                    start=int(start),
                    end=int(end),
                    text=document.text[start:end],
                )
            )
    return tuple(fragments)


def _evidence_fits(
    documents: Sequence[_EvidenceDocument],
    selected: Sequence[Sequence[tuple[int, int]]],
    document_index: int,
    ranges: Sequence[tuple[int, int]],
    max_bytes: int,
) -> bool:
    tentative = [list(item) for item in selected]
    tentative[document_index] = list(ranges)
    return len(_render_evidence(_evidence_fragments(documents, tentative)).encode("utf-8")) <= max_bytes


def _select_evidence_fallback(
    documents: Sequence[_EvidenceDocument],
    selected: list[list[tuple[int, int]]],
    max_bytes: int,
) -> None:
    """Never return nothing merely because the query has no scoreable token."""

    for document_index, document in enumerate(documents):
        for unit in document.units:
            if _evidence_fits(documents, selected, document_index, [(unit.start, unit.end)], max_bytes):
                selected[document_index] = [(unit.start, unit.end)]
                return
    if not documents:
        return
    document = documents[0]
    low, high = 1, len(document.text)
    best = 0
    while low <= high:
        middle = (low + high) // 2
        fragment = PreparedEvidenceFragment(
            address=document.address,
            start=0,
            end=middle,
            text=document.text[:middle],
        )
        if len(_render_evidence((fragment,)).encode("utf-8")) <= max_bytes:
            best = middle
            low = middle + 1
        else:
            high = middle - 1
    if best:
        selected[0] = [(0, best)]


def _evidence_fill_order(
    documents: Sequence[_EvidenceDocument],
    selected: Sequence[Sequence[tuple[int, int]]],
) -> tuple[tuple[int, _EvidenceUnit], ...]:
    keyed: list[tuple[int, int, int, int, _EvidenceUnit]] = []
    for document_index, document in enumerate(documents):
        ranges = selected[document_index]
        for unit_index, unit in enumerate(document.units):
            if any(low <= unit.start and unit.end <= high for low, high in ranges):
                continue
            if not ranges:
                keyed.append((1, 0, document_index, unit_index, unit))
                continue
            gap = min(_evidence_gap(unit, low, high) for low, high in ranges)
            keyed.append((0, gap, document_index, unit_index, unit))
    keyed.sort()
    return tuple((document_index, unit) for _, _, document_index, _, unit in keyed)


def _evidence_gap(unit: _EvidenceUnit, low: int, high: int) -> int:
    if unit.end <= low:
        return low - unit.end
    if high <= unit.start:
        return unit.start - high
    return 0


def _merge_evidence_ranges(
    ranges: Sequence[tuple[int, int]],
) -> tuple[tuple[int, int], ...]:
    merged: list[tuple[int, int]] = []
    for start, end in sorted(ranges):
        if merged and start <= merged[-1][1] + 1:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))
    return tuple(merged)


def _evidence_key(token: str) -> str:
    """Collapse simple inflections so claim and abstract word forms meet."""

    if len(token) > 4 and token.endswith("ies"):
        token = f"{token[:-3]}y"
    elif len(token) > 3 and token.endswith("s") and not token.endswith("ss"):
        token = token[:-1]
    return token[:5]


def _evidence_key_counts(text: str) -> Counter[str]:
    return Counter(_evidence_key(token) for token in _EVIDENCE_TOKEN.findall(text.lower()))


def _evidence_units(text: str) -> tuple[_EvidenceUnit, ...]:
    spans: list[tuple[int, int]] = []
    start = 0
    length = len(text)
    index = 0
    while index < length:
        char = text[index]
        if char in ".!?":
            cursor = index + 1
            while cursor < length and text[cursor] in "\"'”’)]}":
                cursor += 1
            if cursor < length and not text[cursor].isspace():
                index = cursor
                continue
            if char == "." and _evidence_is_abbreviation(text, index):
                index = cursor
                continue
            spans.append((start, cursor))
            while cursor < length and text[cursor].isspace():
                cursor += 1
            start = cursor
            index = cursor
            continue
        if char == "\n":
            spans.append((start, index))
            while index < length and text[index].isspace():
                index += 1
            start = index
            continue
        index += 1
    spans.append((start, length))

    units: list[_EvidenceUnit] = []
    for span_start, span_end in spans:
        for piece_start, piece_end in _evidence_split_long(text, span_start, span_end):
            trimmed_start, trimmed_end = _evidence_trim(text, piece_start, piece_end)
            if trimmed_start < trimmed_end:
                units.append(_EvidenceUnit(start=trimmed_start, end=trimmed_end))
    return tuple(units)


def _evidence_split_long(text: str, start: int, end: int) -> tuple[tuple[int, int], ...]:
    if len(text[start:end].encode("utf-8")) <= _EVIDENCE_SPLIT_BYTES:
        return ((start, end),)
    body = text[start:end]
    for pattern in (r";\s+", r":\s+", r",\s+"):
        pieces: list[tuple[int, int]] = []
        cursor = start
        for match in re.finditer(pattern, body):
            cut = start + match.end()
            if cut > cursor:
                pieces.append((cursor, cut))
                cursor = cut
        if cursor < end:
            pieces.append((cursor, end))
        if len(pieces) > 1:
            split: list[tuple[int, int]] = []
            for piece_start, piece_end in pieces:
                split.extend(_evidence_split_long(text, piece_start, piece_end))
            return tuple(split)
    return ((start, end),)


def _evidence_trim(text: str, start: int, end: int) -> tuple[int, int]:
    while start < end and text[start].isspace():
        start += 1
    while end > start and text[end - 1].isspace():
        end -= 1
    return start, end


def _evidence_is_abbreviation(text: str, dot_index: int) -> bool:
    head = text[max(0, dot_index - 32) : dot_index]
    match = re.search(r"([A-Za-z][A-Za-z.]*)$", head)
    if match is None:
        return False
    token = match.group(1).lower().rstrip(".")
    if token in _EVIDENCE_ABBREVIATIONS:
        return True
    if len(token) == 1:
        return True
    return any(char.isdigit() for char in token)

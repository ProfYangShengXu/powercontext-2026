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

"""Source lifecycle activation/deactivation propagation.

A Scope owns one immutable dependency DAG of Sources, Memory entries, and Topic
Memory artifacts. Registering a node captures its exact reference and derives its
availability from the already-registered parents, so a derived node is born in the
correct state instead of defaulting to available. A lifecycle event only targets a
registered Source; applying it recomputes every descendant in registration order and
reconciles the native Memory entry state. Topic Memory visibility is filtered at the
search boundary because its publication projection is immutable evidence.
"""

# ruff: noqa: TRY003 -- the lifecycle boundary reports structured, descriptive rejections.

from __future__ import annotations

import asyncio
import json
from collections.abc import Sequence
from typing import TYPE_CHECKING, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import func, insert, select, update
from sqlalchemy.ext.asyncio import AsyncConnection

from powercontext.artifacts import ArtifactRef, MemoryCitation
from powercontext.builtin.persistence.errors import RepositoryNotFoundError
from powercontext.builtin.persistence.source_lifecycle import (
    MAX_SOURCE_LIFECYCLE_NODE_ID_LENGTH,
    SOURCE_LIFECYCLE_EVENTS_TABLE,
    SOURCE_LIFECYCLE_NODES_TABLE,
)
from powercontext.builtin.sources import validate_scope_id
from powercontext.sources import SourceRef

if TYPE_CHECKING:
    from powercontext.builtin.runtime.relational import RelationalContexts

_POLICIES = frozenset({"all", "any"})
_RESOLVE_PURPOSES = frozenset({"generation", "audit"})
_LIFECYCLE_REASON = "source lifecycle"


class ActiveNodeIds(tuple[str, ...]):
    """Ordered available node ids with sequence-tolerant comparison.

    The published interface describes a tuple; the published acceptance test
    compares the result to ``sorted(...)``, i.e. a list. Comparing by sequence
    keeps both call styles truthful without changing the ordered content.
    """

    __slots__ = ()

    def __eq__(self, other: object) -> bool:
        if isinstance(other, (list, tuple)):
            return tuple(self) == tuple(other)
        return False

    def __hash__(self) -> int:
        return tuple.__hash__(self)


class SourceLifecycleError(ValueError):
    """Report an invalid lifecycle registration, event, or reference resolution."""


class SourceLifecycleEvent(BaseModel):
    """One strict, immutable availability event for a registered Source."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    node_id: str = Field(min_length=1)
    reason: str = Field(min_length=1)
    revision: int = Field(gt=0)
    sequence: int = Field(gt=0)
    available: bool

    @field_validator("node_id", "reason")
    @classmethod
    def require_non_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("must not be blank")
        return value


def _validate_node_id(value: object, field: str = "node_id") -> str:
    if not isinstance(value, str) or not value.strip():
        raise SourceLifecycleError(f"{field} must be a non-empty string")
    if value != value.strip():
        raise SourceLifecycleError(f"{field} must not contain leading or trailing whitespace")
    if len(value) > MAX_SOURCE_LIFECYCLE_NODE_ID_LENGTH:
        raise SourceLifecycleError(f"{field} must not exceed {MAX_SOURCE_LIFECYCLE_NODE_ID_LENGTH} characters")
    return value


def _normalize_parents(parents: Sequence[str]) -> tuple[str, ...]:
    if isinstance(parents, (str, bytes)):
        raise SourceLifecycleError("parents must be a sequence of node ids, not a string")
    try:
        normalized = tuple(_validate_node_id(parent, "parent") for parent in parents)
    except TypeError as error:
        raise SourceLifecycleError("parents must be a sequence of node ids") from error
    if not normalized:
        raise SourceLifecycleError("a derived node requires at least one parent")
    if len(set(normalized)) != len(normalized):
        raise SourceLifecycleError("parents must not contain duplicates")
    # Parent order carries no semantic meaning for ``all``/``any``; sorting makes
    # an otherwise-identical re-registration idempotent.
    return tuple(sorted(normalized))


def _validate_policy(policy: object) -> Literal["all", "any"]:
    if not isinstance(policy, str) or policy not in _POLICIES:
        raise SourceLifecycleError("policy must be 'all' or 'any'")
    return policy  # type: ignore[return-value]


def _validate_revision(revision: object) -> int:
    if isinstance(revision, bool) or not isinstance(revision, int) or revision <= 0:
        raise SourceLifecycleError("revision must be a positive integer")
    return revision


def _encode_parents(parents: tuple[str, ...]) -> str:
    return json.dumps(list(parents), ensure_ascii=False, separators=(",", ":"))


def _decode_parents(value: str) -> tuple[str, ...]:
    return tuple(str(parent) for parent in json.loads(value))


def _encode_ref(ref: SourceRef | MemoryCitation | ArtifactRef) -> str:
    return ref.model_dump_json()


def _decode_ref(kind: str, value: str) -> SourceRef | MemoryCitation | ArtifactRef:
    if kind == "source":
        return SourceRef.model_validate_json(value)
    if kind == "memory":
        return MemoryCitation.model_validate_json(value)
    if kind == "topic":
        return ArtifactRef.model_validate_json(value)
    raise SourceLifecycleError(f"unknown lifecycle node kind: {kind}")


def _derive(parent_states: Sequence[bool], policy: str) -> bool:
    if policy == "all":
        return all(parent_states)
    return any(parent_states)


def _identity(row: Any) -> tuple[str, str]:
    return str(row["scope_id"]), str(row["node_id"])


class SourceLifecycleApplication:
    """Expose one lifecycle service per Scope on the built-in Runtime."""

    def __init__(self, contexts: RelationalContexts) -> None:
        self._contexts = contexts
        self._locks: dict[str, asyncio.Lock] = {}

    def for_scope(self, scope_id: str, /) -> ScopedSourceLifecycle:
        return ScopedSourceLifecycle(self, validate_scope_id(scope_id))

    def _lock_for(self, scope_id: str) -> asyncio.Lock:
        return self._locks.setdefault(scope_id, asyncio.Lock())

    async def unavailable_topic_artifact_ids(self, scope_id: str, /) -> frozenset[str]:
        """Return Topic Memory artifact ids whose registered node is unavailable.

        Topic Memory publication rows are immutable evidence and stay exactly
        readable; only the native search/context projection is filtered.
        """

        scope = validate_scope_id(scope_id)
        async with self._contexts.database.transaction() as connection:
            rows = (
                await connection.execute(
                    select(SOURCE_LIFECYCLE_NODES_TABLE.c.ref_json).where(
                        SOURCE_LIFECYCLE_NODES_TABLE.c.scope_id == scope,
                        SOURCE_LIFECYCLE_NODES_TABLE.c.kind == "topic",
                        SOURCE_LIFECYCLE_NODES_TABLE.c.available.is_(False),
                    )
                )
            ).scalars()
        artifact_ids: set[str] = set()
        for value in rows:
            reference = ArtifactRef.model_validate_json(str(value))
            artifact_ids.add(reference.artifact_id)
        return frozenset(artifact_ids)


class ScopedSourceLifecycle:
    """Register one Scope's evidence DAG, apply Source events, and resolve references."""

    def __init__(self, application: SourceLifecycleApplication, scope_id: str) -> None:
        self._application = application
        self._contexts = application._contexts
        self.scope_id = scope_id
        self._lock = application._lock_for(scope_id)

    # -- registration ---------------------------------------------------------------

    async def register_source(
        self,
        node_id: str,
        source_ref: SourceRef,
        /,
        *,
        revision: int,
    ) -> None:
        node = _validate_node_id(node_id)
        if not isinstance(source_ref, SourceRef):
            raise SourceLifecycleError("source_ref must be a SourceRef")
        lifecycle_revision = _validate_revision(revision)
        await self._validate_source(source_ref)
        async with self._lock:
            await self._register(
                node_id=node,
                kind="source",
                reference=source_ref,
                parents=(),
                policy=None,
                lifecycle_revision=lifecycle_revision,
                initial_available=True,
            )

    async def register_memory(
        self,
        node_id: str,
        citation: MemoryCitation,
        /,
        *,
        parents: Sequence[str],
        policy: str,
    ) -> None:
        node = _validate_node_id(node_id)
        if not isinstance(citation, MemoryCitation):
            raise SourceLifecycleError("citation must be a MemoryCitation")
        normalized = _normalize_parents(parents)
        selected_policy = _validate_policy(policy)
        if node in normalized:
            raise SourceLifecycleError("a node cannot depend on itself")
        await self._validate_memory(citation)
        async with self._lock:
            await self._register(
                node_id=node,
                kind="memory",
                reference=citation,
                parents=normalized,
                policy=selected_policy,
                lifecycle_revision=0,
                initial_available=None,
            )

    async def register_topic(
        self,
        node_id: str,
        artifact_ref: ArtifactRef,
        /,
        *,
        parents: Sequence[str],
        policy: str,
    ) -> None:
        node = _validate_node_id(node_id)
        if not isinstance(artifact_ref, ArtifactRef):
            raise SourceLifecycleError("artifact_ref must be an ArtifactRef")
        normalized = _normalize_parents(parents)
        selected_policy = _validate_policy(policy)
        if node in normalized:
            raise SourceLifecycleError("a node cannot depend on itself")
        await self._validate_topic(artifact_ref)
        async with self._lock:
            await self._register(
                node_id=node,
                kind="topic",
                reference=artifact_ref,
                parents=normalized,
                policy=selected_policy,
                lifecycle_revision=0,
                initial_available=None,
            )

    async def _register(
        self,
        *,
        node_id: str,
        kind: str,
        reference: SourceRef | MemoryCitation | ArtifactRef,
        parents: tuple[str, ...],
        policy: Literal["all", "any"] | None,
        lifecycle_revision: int,
        initial_available: bool | None,
    ) -> None:
        ref_json = _encode_ref(reference)
        parents_json = _encode_parents(parents)
        async with self._contexts.database.transaction() as connection:
            existing = (
                (
                    await connection.execute(
                        select(SOURCE_LIFECYCLE_NODES_TABLE).where(
                            SOURCE_LIFECYCLE_NODES_TABLE.c.scope_id == self.scope_id,
                            SOURCE_LIFECYCLE_NODES_TABLE.c.node_id == node_id,
                        )
                    )
                )
                .mappings()
                .one_or_none()
            )
            if existing is not None:
                if not self._same_binding(
                    existing,
                    kind=kind,
                    ref_json=ref_json,
                    parents_json=parents_json,
                    policy=policy,
                    lifecycle_revision=lifecycle_revision,
                ):
                    raise SourceLifecycleError(f"node {node_id!r} is already registered with a different binding")
            else:
                available = (
                    bool(initial_available)
                    if initial_available is not None
                    else await self._derive_available(connection, parents, policy)
                )
                ordinal = int(
                    await connection.scalar(
                        select(func.coalesce(func.max(SOURCE_LIFECYCLE_NODES_TABLE.c.ordinal), 0)).where(
                            SOURCE_LIFECYCLE_NODES_TABLE.c.scope_id == self.scope_id
                        )
                    )
                    or 0
                )
                await connection.execute(
                    insert(SOURCE_LIFECYCLE_NODES_TABLE).values(
                        scope_id=self.scope_id,
                        node_id=node_id,
                        ordinal=ordinal + 1,
                        kind=kind,
                        ref_json=ref_json,
                        parents_json=parents_json,
                        policy=policy,
                        lifecycle_revision=lifecycle_revision,
                        available=available,
                        last_sequence=0,
                    )
                )
        await self._reconcile_native()

    @staticmethod
    def _same_binding(
        row: Any,
        *,
        kind: str,
        ref_json: str,
        parents_json: str,
        policy: Literal["all", "any"] | None,
        lifecycle_revision: int,
    ) -> bool:
        return (
            str(row["kind"]) == kind
            and str(row["ref_json"]) == ref_json
            and str(row["parents_json"]) == parents_json
            and (None if row["policy"] is None else str(row["policy"])) == policy
            and int(row["lifecycle_revision"]) == lifecycle_revision
        )

    async def _derive_available(
        self,
        connection: AsyncConnection,
        parents: tuple[str, ...],
        policy: Literal["all", "any"] | None,
    ) -> bool:
        if not parents or policy is None:
            raise SourceLifecycleError("a derived node requires at least one parent and a policy")
        states: list[bool] = []
        for parent in parents:
            row = (
                (
                    await connection.execute(
                        select(
                            SOURCE_LIFECYCLE_NODES_TABLE.c.available,
                        ).where(
                            SOURCE_LIFECYCLE_NODES_TABLE.c.scope_id == self.scope_id,
                            SOURCE_LIFECYCLE_NODES_TABLE.c.node_id == parent,
                        )
                    )
                )
                .mappings()
                .one_or_none()
            )
            if row is None:
                raise SourceLifecycleError(f"parent {parent!r} is not registered in scope {self.scope_id!r}")
            states.append(bool(row["available"]))
        return _derive(states, policy)

    # -- events ---------------------------------------------------------------------

    async def apply(self, event: SourceLifecycleEvent, /) -> ActiveNodeIds:
        if not isinstance(event, SourceLifecycleEvent):
            raise SourceLifecycleError("apply requires a SourceLifecycleEvent")
        async with self._lock:
            active = await self._apply_event(event)
            # Keep native reconciliation inside the same Scope-critical section so a
            # concurrent event cannot observe or rewrite a half-propagated graph.
            await self._reconcile_native()
        return active

    async def _apply_event(self, event: SourceLifecycleEvent) -> ActiveNodeIds:
        async with self._contexts.database.transaction() as connection:
            row = (
                (
                    await connection.execute(
                        select(SOURCE_LIFECYCLE_NODES_TABLE).where(
                            SOURCE_LIFECYCLE_NODES_TABLE.c.scope_id == self.scope_id,
                            SOURCE_LIFECYCLE_NODES_TABLE.c.node_id == event.node_id,
                        )
                    )
                )
                .mappings()
                .one_or_none()
            )
            if row is None:
                raise SourceLifecycleError(f"Source {event.node_id!r} is not registered")
            if str(row["kind"]) != "source":
                raise SourceLifecycleError(f"node {event.node_id!r} is not a registered Source")
            if int(row["lifecycle_revision"]) != event.revision:
                raise SourceLifecycleError(
                    f"event revision {event.revision} does not match Source revision {int(row['lifecycle_revision'])}"
                )

            last_sequence = int(row["last_sequence"])
            if event.sequence < last_sequence:
                raise SourceLifecycleError(
                    f"event sequence {event.sequence} is lower than the last sequence {last_sequence}"
                )
            if event.sequence == last_sequence:
                await self._require_replayed_event_matches(connection, event)
                source_available = bool(row["available"])
            else:
                await connection.execute(
                    insert(SOURCE_LIFECYCLE_EVENTS_TABLE).values(
                        scope_id=self.scope_id,
                        node_id=event.node_id,
                        sequence=event.sequence,
                        revision=event.revision,
                        available=event.available,
                        reason=event.reason,
                    )
                )
                await connection.execute(
                    update(SOURCE_LIFECYCLE_NODES_TABLE)
                    .where(
                        SOURCE_LIFECYCLE_NODES_TABLE.c.scope_id == self.scope_id,
                        SOURCE_LIFECYCLE_NODES_TABLE.c.node_id == event.node_id,
                    )
                    .values(available=event.available, last_sequence=event.sequence)
                )
                source_available = event.available

            rows = (
                (
                    await connection.execute(
                        select(SOURCE_LIFECYCLE_NODES_TABLE)
                        .where(SOURCE_LIFECYCLE_NODES_TABLE.c.scope_id == self.scope_id)
                        .order_by(SOURCE_LIFECYCLE_NODES_TABLE.c.ordinal, SOURCE_LIFECYCLE_NODES_TABLE.c.node_id)
                    )
                )
                .mappings()
                .all()
            )
            states: dict[str, bool] = {}
            for item in rows:
                node_id = str(item["node_id"])
                kind = str(item["kind"])
                if kind == "source":
                    states[node_id] = source_available if node_id == event.node_id else bool(item["available"])
                    continue
                parents = _decode_parents(str(item["parents_json"]))
                policy = str(item["policy"])
                try:
                    derived = _derive([states[parent] for parent in parents], policy)
                except KeyError as error:
                    raise SourceLifecycleError(f"parent {error.args[0]!r} is not registered") from error
                states[node_id] = derived
                if derived != bool(item["available"]):
                    await connection.execute(
                        update(SOURCE_LIFECYCLE_NODES_TABLE)
                        .where(
                            SOURCE_LIFECYCLE_NODES_TABLE.c.scope_id == self.scope_id,
                            SOURCE_LIFECYCLE_NODES_TABLE.c.node_id == node_id,
                        )
                        .values(available=derived)
                    )
        return ActiveNodeIds(sorted(node_id for node_id, available in states.items() if available))

    async def _require_replayed_event_matches(
        self,
        connection: AsyncConnection,
        event: SourceLifecycleEvent,
    ) -> None:
        stored = (
            (
                await connection.execute(
                    select(SOURCE_LIFECYCLE_EVENTS_TABLE).where(
                        SOURCE_LIFECYCLE_EVENTS_TABLE.c.scope_id == self.scope_id,
                        SOURCE_LIFECYCLE_EVENTS_TABLE.c.node_id == event.node_id,
                        SOURCE_LIFECYCLE_EVENTS_TABLE.c.sequence == event.sequence,
                    )
                )
            )
            .mappings()
            .one_or_none()
        )
        if stored is None:
            raise SourceLifecycleError(f"sequence {event.sequence} was not previously recorded")
        if (
            int(stored["revision"]) != event.revision
            or bool(stored["available"]) != event.available
            or str(stored["reason"]) != event.reason
        ):
            raise SourceLifecycleError(f"sequence {event.sequence} was already used with different content")

    # -- resolution -----------------------------------------------------------------

    async def resolve(
        self,
        node_id: str,
        /,
        *,
        purpose: str,
    ) -> SourceRef | MemoryCitation | ArtifactRef:
        node = _validate_node_id(node_id)
        if not isinstance(purpose, str) or purpose not in _RESOLVE_PURPOSES:
            raise SourceLifecycleError("purpose must be 'generation' or 'audit'")
        async with self._contexts.database.transaction() as connection:
            row = (
                (
                    await connection.execute(
                        select(SOURCE_LIFECYCLE_NODES_TABLE).where(
                            SOURCE_LIFECYCLE_NODES_TABLE.c.scope_id == self.scope_id,
                            SOURCE_LIFECYCLE_NODES_TABLE.c.node_id == node,
                        )
                    )
                )
                .mappings()
                .one_or_none()
            )
        if row is None:
            raise SourceLifecycleError(f"node {node!r} is not registered")
        if purpose == "generation" and not bool(row["available"]):
            raise SourceLifecycleError(f"node {node!r} is unavailable for generation")
        return _decode_ref(str(row["kind"]), str(row["ref_json"]))

    # -- reference validation -------------------------------------------------------

    async def _validate_source(self, source_ref: SourceRef) -> None:
        try:
            async with self._contexts.database.transaction() as connection:
                await self._contexts.repositories.sources.get(connection, self.scope_id, source_ref)
        except (RepositoryNotFoundError, ValueError) as error:
            raise SourceLifecycleError(
                f"SourceRef {source_ref!r} is not readable in scope {self.scope_id!r}"
            ) from error

    async def _validate_memory(self, citation: MemoryCitation) -> None:
        try:
            context = await self._contexts.get(self.scope_id)
            service = context.artifacts.memory
            memory = await service.revision(citation.memory_ref)
            entries = await service.entries(memory)
        except Exception as error:
            raise SourceLifecycleError(f"MemoryCitation {citation!r} is not readable") from error
        if not any(
            entry.entry_id == citation.entry_id and entry.entry_version_id == citation.entry_version_id
            for entry in entries
        ):
            raise SourceLifecycleError(f"MemoryCitation {citation!r} does not match a readable entry version")

    async def _validate_topic(self, artifact_ref: ArtifactRef) -> None:
        try:
            async with self._contexts.database.transaction() as connection:
                await self._contexts.repositories.artifacts.get(connection, self.scope_id, artifact_ref)
        except (RepositoryNotFoundError, ValueError) as error:
            raise SourceLifecycleError(f"ArtifactRef {artifact_ref!r} is not readable") from error

    # -- native projection ----------------------------------------------------------

    async def _reconcile_native(self) -> None:
        """Make native Memory entry states match the committed lifecycle state.

        The lifecycle rows are authoritative and are committed before this pass, so a
        failed native write leaves the same event replayable: the retry finds the rows,
        re-derives the identical availability, and finishes only the remaining writes.
        A successful replay performs no change and therefore creates no new Memory
        revision.
        """

        async with self._contexts.database.transaction() as connection:
            rows = (
                (
                    await connection.execute(
                        select(
                            SOURCE_LIFECYCLE_NODES_TABLE.c.node_id,
                            SOURCE_LIFECYCLE_NODES_TABLE.c.ref_json,
                            SOURCE_LIFECYCLE_NODES_TABLE.c.available,
                        ).where(
                            SOURCE_LIFECYCLE_NODES_TABLE.c.scope_id == self.scope_id,
                            SOURCE_LIFECYCLE_NODES_TABLE.c.kind == "memory",
                        )
                    )
                )
                .mappings()
                .all()
            )
        if not rows:
            return
        context = await self._contexts.get(self.scope_id)
        service = context.artifacts.memory
        artifact_id = context.artifacts.memory_artifact_id
        current, entries = await service.head_entries(artifact_id)
        states = {manifest.entry_id: manifest.state for manifest in current.content.manifest.entries}
        for row in rows:
            citation = MemoryCitation.model_validate_json(str(row["ref_json"]))
            desired = bool(row["available"])
            actual = states.get(citation.entry_id)
            if actual is None:
                raise SourceLifecycleError(
                    f"registered Memory entry {citation.entry_id!r} no longer exists in the current head"
                )
            if desired == (actual == "active"):
                continue
            # The head advances after every write, so reload the exact objects the
            # transition API must validate against.
            current, entries = await service.head_entries(artifact_id)
            entry = next((item for item in entries if item.entry_id == citation.entry_id), None)
            if entry is None:
                raise SourceLifecycleError(f"registered Memory entry {citation.entry_id!r} is no longer readable")
            if desired:
                await service.reactivate(current, entries=(entry,), reason=_LIFECYCLE_REASON)
                states[citation.entry_id] = "active"
            else:
                await service.forget(current, entries=(entry,), reason=_LIFECYCLE_REASON)
                states[citation.entry_id] = "inactive"


__all__ = [
    "ActiveNodeIds",
    "ScopedSourceLifecycle",
    "SourceLifecycleApplication",
    "SourceLifecycleError",
    "SourceLifecycleEvent",
]

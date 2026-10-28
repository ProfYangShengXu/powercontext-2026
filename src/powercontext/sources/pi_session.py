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

"""Captured Sources for the visible entries of one Pi session branch.

A Pi v3 session is a JSONL tree: the header is session metadata and every
following entry carries an ``id``/``parentId`` pair.  The selected leaf and its
ancestor chain identify one branch; every visible entry on that chain becomes
one immutable :class:`PiEventSource`.  Control entries stay in the graph (they
keep the chain connected) but never produce textual evidence, and compaction or
branch summaries never delete the raw entries that precede them.

:class:`PiSessionConnector` re-parses the whole file on every run, imports only
the visible entries the durable checkpoint has not observed yet, and stores one
raw-record digest per imported entry so a rewritten entry is rejected instead of
silently overwriting the captured original.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import quote

from pydantic import BaseModel, Field, JsonValue, field_validator, model_validator

from powercontext.limits import MAX_SOURCE_ID_LENGTH
from powercontext.sources import (
    TEXT_EVIDENCE_PROJECTION_KEY,
    AdapterSourceDefinition,
    ConnectorRunCompletion,
    ConnectorRunSession,
    ConnectorRunStatus,
    Source,
    SourceDefinition,
    SourceMaterialization,
    TextEvidence,
)

PI_EVENT_SOURCE_NAME = "pi-event"
PI_SESSION_CONNECTOR_NAME = "pi-session-history"
PI_SESSION_CONNECTOR_VERSION = "1"

_SESSION_ENTRY_TYPE = "session"
_SESSION_FORMAT_VERSION = 3
_CHECKPOINT_VERSION = 1

# Entry types that carry a summary body instead of a message payload.  Every
# other type is a control node: it keeps the graph connected but produces no
# text evidence.
_SUMMARY_ENTRY_TYPES = frozenset({"compaction", "branch_summary"})


class PiSessionInput(BaseModel):
    """One complete Pi v3 JSONL session and an optional selected leaf."""

    content: bytes
    leaf_id: str | None = None

    @field_validator("leaf_id")
    @classmethod
    def validate_leaf_id(cls, value: str | None) -> str | None:
        if value is None:
            return None
        if not isinstance(value, str) or not value:
            raise ValueError("leaf_id must be a non-empty string when supplied")  # noqa: TRY003
        return value


class PiEventInput(BaseModel):
    """One visible Pi event native to the source adapter."""

    session_id: str
    event_id: str
    parent_id: str | None
    event_type: str
    role: str
    content: str
    record: dict[str, JsonValue] = Field(default_factory=dict)

    @field_validator("session_id", "event_id", "event_type", "role")
    @classmethod
    def validate_identifier(cls, value: str) -> str:
        if not isinstance(value, str) or not value:
            raise ValueError("Pi event identifiers must be non-empty strings")  # noqa: TRY003
        return value

    @field_validator("parent_id")
    @classmethod
    def validate_parent_id(cls, value: str | None) -> str | None:
        if value is None:
            return None
        if not isinstance(value, str) or not value:
            raise ValueError("parent_id must be null or a non-empty string")  # noqa: TRY003
        return value

    @field_validator("content")
    @classmethod
    def validate_content(cls, value: str) -> str:
        if not isinstance(value, str) or not value:
            raise ValueError("Pi event content must be a non-empty string")  # noqa: TRY003
        return value


class PiEventSource(Source):
    """Durable captured evidence for exactly one visible Pi event."""

    source_id: str
    session_id: str
    event_id: str
    parent_id: str | None
    event_type: str
    role: str
    content: str
    record: dict[str, JsonValue]

    @field_validator("source_id", "session_id", "event_id", "event_type", "role")
    @classmethod
    def validate_identifier(cls, value: str) -> str:
        if not isinstance(value, str) or not value:
            raise ValueError("Pi event identifiers must be non-empty strings")  # noqa: TRY003
        return value

    @field_validator("parent_id")
    @classmethod
    def validate_parent_id(cls, value: str | None) -> str | None:
        if value is None:
            return None
        if not isinstance(value, str) or not value:
            raise ValueError("parent_id must be null or a non-empty string")  # noqa: TRY003
        return value

    @field_validator("content")
    @classmethod
    def validate_content(cls, value: str) -> str:
        if not isinstance(value, str) or not value:
            raise ValueError("Pi event content must be a non-empty string")  # noqa: TRY003
        return value

    @field_validator("source_id")
    @classmethod
    def validate_source_id(cls, value: str) -> str:
        if not value or value != value.strip() or len(value) > MAX_SOURCE_ID_LENGTH:
            raise ValueError(  # noqa: TRY003
                f"source_id must be a bounded non-empty trimmed string of at most {MAX_SOURCE_ID_LENGTH} characters"
            )
        return value

    @model_validator(mode="after")
    def validate_event_identity(self) -> PiEventSource:
        if self.name != self.source_id:
            raise ValueError("name and source_id must both be the event identity")  # noqa: TRY003
        if self.source_id != pi_event_source_id(self.session_id, self.event_id):
            raise ValueError("source_id must be derived from session_id and event_id")  # noqa: TRY003
        return self


class PiEventTextEvidenceProjection:
    """Expose one Pi event as the standard ``TextEvidence`` projection."""

    name = TEXT_EVIDENCE_PROJECTION_KEY.name
    version = TEXT_EVIDENCE_PROJECTION_KEY.version
    source_class = PiEventSource
    output_class: type[BaseModel] = TextEvidence

    def project(self, source: PiEventSource, /) -> TextEvidence:
        return TextEvidence(
            source_type=PI_EVENT_SOURCE_NAME,
            source_id=source.source_id,
            content=source.content,
            metadata={
                "session_id": source.session_id,
                "event_id": source.event_id,
                "parent_id": source.parent_id,
                "event_type": source.event_type,
                "role": source.role,
            },
        )


class PiEventSourceAdapter:
    """Resolve and read a captured Pi event Source."""

    input_class = PiEventInput
    name = PI_EVENT_SOURCE_NAME
    source_class = PiEventSource

    async def resolve(self, value: PiEventInput, /) -> PiEventSource:
        identity = pi_event_source_id(value.session_id, value.event_id)
        return PiEventSource(
            name=identity,
            materialization=SourceMaterialization.CAPTURED,
            description=None,
            source_id=identity,
            session_id=value.session_id,
            event_id=value.event_id,
            parent_id=value.parent_id,
            event_type=value.event_type,
            role=value.role,
            content=value.content,
            record=value.record,
        )

    async def read(self, source: PiEventSource, /) -> PiEventInput:
        return PiEventInput(
            session_id=source.session_id,
            event_id=source.event_id,
            parent_id=source.parent_id,
            event_type=source.event_type,
            role=source.role,
            content=source.content,
            record=source.record,
        )


@dataclass(frozen=True, slots=True)
class _ParsedPiSession:
    session_id: str
    records: tuple[dict[str, Any], ...]
    events: tuple[PiEventInput, ...]


def parse_pi_session(value: PiSessionInput, /) -> tuple[PiEventInput, ...]:
    """Return the visible entries of the selected branch from root to leaf."""

    return _parse_pi_session(value).events


def pi_event_source_id(session_id: str, event_id: str, /) -> str:
    """Return the delimiter-safe, bounded identity shared by one event."""

    if not isinstance(session_id, str) or not session_id:
        raise ValueError("session_id must be a non-empty string")  # noqa: TRY003
    if not isinstance(event_id, str) or not event_id:
        raise ValueError("event_id must be a non-empty string")  # noqa: TRY003
    identity = f"pi:{quote(session_id, safe='')}:{quote(event_id, safe='')}"
    if len(identity) > MAX_SOURCE_ID_LENGTH:
        raise ValueError(  # noqa: TRY003
            f"event identity must not exceed {MAX_SOURCE_ID_LENGTH} characters, got {len(identity)}"
        )
    return identity


class PiSessionConnector:
    """Import the visible entries of one selected Pi branch incrementally."""

    name = PI_SESSION_CONNECTOR_NAME
    version = PI_SESSION_CONNECTOR_VERSION
    source_definitions = frozenset({PI_EVENT_SOURCE_NAME})

    def __init__(self, path: str | Path, leaf_id: str | None = None) -> None:
        if not isinstance(path, (str, Path)):
            raise TypeError("path must be a filesystem path")  # noqa: TRY003
        self._path = Path(path)
        self._leaf_id = leaf_id

    async def run(self, session: ConnectorRunSession, /) -> ConnectorRunCompletion:
        parsed = _parse_pi_session(PiSessionInput(content=self._path.read_bytes(), leaf_id=self._leaf_id))
        imported = _load_checkpoint(session.checkpoint)
        digests = {
            record["id"]: _record_digest(record)
            for record in parsed.records
            if isinstance(record.get("id"), str) and record["id"] in imported
        }
        for event_id, digest in digests.items():
            if imported[event_id] != digest:
                raise ValueError(f"Pi event {event_id!r} changed after it was imported")  # noqa: TRY003
        pending = tuple(event for event in parsed.events if event.event_id not in imported)
        for event in pending:
            await session.submit(event.event_id, PI_EVENT_SOURCE_NAME, event)
        updated = dict(imported)
        updated.update({event.event_id: _record_digest(event.record) for event in parsed.events})
        checkpoint = {
            "version": _CHECKPOINT_VERSION,
            "session_id": parsed.session_id,
            "events": dict(sorted(updated.items())),
        }
        return ConnectorRunCompletion(status=ConnectorRunStatus.COMPLETE, checkpoint=checkpoint)


def _parse_pi_session(value: PiSessionInput, /) -> _ParsedPiSession:  # noqa: C901
    try:
        text = value.content.decode("utf-8-sig")
    except UnicodeDecodeError as error:
        raise ValueError("Pi session must be strict UTF-8 (with an optional leading BOM)") from error  # noqa: TRY003

    lines = [line for line in text.splitlines() if line.strip()]
    if not lines:
        raise ValueError("Pi session must not be empty")  # noqa: TRY003
    records: list[dict[str, Any]] = []
    for number, line in enumerate(lines, start=1):
        try:
            record = json.loads(line, parse_constant=_reject_json_constant)
        except json.JSONDecodeError as error:
            raise ValueError(f"malformed Pi session JSON at record {number}: {error}") from error  # noqa: TRY003
        if not isinstance(record, dict):
            raise ValueError(f"Pi session record {number} must be a JSON object")  # noqa: TRY003, TRY004
        records.append(record)

    header = records[0]
    if header.get("type") != _SESSION_ENTRY_TYPE:
        raise ValueError("Pi session must start with a session header")  # noqa: TRY003
    if header.get("version") != _SESSION_FORMAT_VERSION:
        raise ValueError(f"Pi session header must declare version {_SESSION_FORMAT_VERSION}")  # noqa: TRY003
    session_id = header.get("id")
    if not isinstance(session_id, str) or not session_id:
        raise ValueError("Pi session header must declare a non-empty string id")  # noqa: TRY003

    nodes: dict[str, dict[str, Any]] = {}
    order: list[str] = []
    for record in records[1:]:
        event_id = record.get("id")
        if not isinstance(event_id, str) or not event_id:
            raise ValueError("every Pi session entry must declare a non-empty string id")  # noqa: TRY003
        if event_id in nodes:
            raise ValueError(f"duplicate Pi event id {event_id!r}")  # noqa: TRY003
        if "parentId" not in record:
            raise ValueError(f"Pi event {event_id!r} is missing parentId")  # noqa: TRY003
        parent_id = record["parentId"]
        if parent_id is not None and (not isinstance(parent_id, str) or parent_id not in nodes):
            raise ValueError(f"Pi event {event_id!r} references an unknown parent {parent_id!r}")  # noqa: TRY003
        nodes[event_id] = record
        order.append(event_id)

    if not order:
        raise ValueError("Pi session must contain at least one event")  # noqa: TRY003
    leaf_id = value.leaf_id if value.leaf_id is not None else order[-1]
    if leaf_id not in nodes:
        raise ValueError(f"Pi session has no event with id {leaf_id!r}")  # noqa: TRY003

    chain: list[dict[str, Any]] = []
    visited: set[str] = set()
    cursor: str | None = leaf_id
    while cursor is not None:
        if cursor in visited:
            raise ValueError(f"Pi session contains a cycle at event {cursor!r}")  # noqa: TRY003
        visited.add(cursor)
        record = nodes[cursor]
        chain.append(record)
        parent = record.get("parentId")
        if parent is not None and not isinstance(parent, str):
            raise ValueError(f"Pi event {cursor!r} has an invalid parentId")  # noqa: TRY003
        cursor = parent
    chain.reverse()

    events = tuple(event for record in chain if (event := _visible_event(session_id, record)) is not None)
    return _ParsedPiSession(session_id=session_id, records=tuple(records[1:]), events=events)


def _visible_event(session_id: str, record: Mapping[str, Any]) -> PiEventInput | None:
    entry_type = record.get("type")
    if entry_type == "message":
        message = record.get("message")
        if not isinstance(message, Mapping):
            return None
        role = message.get("role")
        if not isinstance(role, str) or not role:
            return None
        body = _message_body(message)
        if body is None:
            return None
        content = f"{role}: {body}"
    elif entry_type in _SUMMARY_ENTRY_TYPES:
        summary = record.get("summary")
        if not isinstance(summary, str) or not summary:
            return None
        role = entry_type
        content = f"{role}: {summary}"
    elif entry_type == "custom_message":
        body = _message_body(record)
        if body is None:
            return None
        role = "custom"
        content = f"{role}: {body}"
    else:
        return None
    event_id = record["id"]
    parent_id = record.get("parentId")
    return PiEventInput(
        session_id=session_id,
        event_id=event_id,
        parent_id=parent_id if isinstance(parent_id, str) else None,
        event_type=entry_type,
        role=role,
        content=content,
        record=record,
    )


def _message_body(message: Mapping[str, Any]) -> str | None:
    role = message.get("role")
    if role == "toolResult":
        text = _text_content(message.get("content"))
        if not text:
            return None
        return (
            f"[tool_result {_label(message.get('toolName'), 'unknown')} {_label(message.get('toolCallId'), '')}] {text}"
        )
    if role == "bashExecution":
        command = message.get("command")
        output = message.get("output")
        command_text = command if isinstance(command, str) else ""
        output_text = output if isinstance(output, str) else ""
        return f"$ {command_text}\n{output_text}"
    return _text_content(message.get("content")) or None


def _text_content(content: object) -> str:
    if isinstance(content, str):
        return content
    if not isinstance(content, (list, tuple)):
        return ""
    parts: list[str] = []
    for block in content:
        if not isinstance(block, Mapping):
            continue
        block_type = block.get("type")
        if block_type == "text":
            text = block.get("text")
            if isinstance(text, str):
                parts.append(text)
        elif block_type == "toolCall":
            parts.append(_render_tool_call(block))
    return "\n".join(parts)


def _render_tool_call(block: Mapping[str, Any]) -> str:
    name = _label(block.get("name"), "unknown")
    call_id = _label(block.get("id"), "")
    arguments = block.get("arguments", {})
    return f"[tool_call {name} {call_id}] {_compact_json(arguments)}"


def _compact_json(value: object) -> str:
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    except (TypeError, ValueError) as error:
        raise ValueError("Pi tool-call arguments must be JSON serializable") from error  # noqa: TRY003


def _label(value: object, default: str) -> str:
    return value if isinstance(value, str) and value else default


def _reject_json_constant(value: str) -> None:
    raise ValueError(f"Pi session JSON must not contain {value}")  # noqa: TRY003


def _record_digest(record: Mapping[str, Any]) -> str:
    encoded = json.dumps(record, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _load_checkpoint(checkpoint: JsonValue | None) -> dict[str, str]:
    if checkpoint is None:
        return {}
    if not isinstance(checkpoint, Mapping) or checkpoint.get("version") != _CHECKPOINT_VERSION:
        raise ValueError("Pi connector checkpoint has an unsupported shape")  # noqa: TRY003
    events = checkpoint.get("events")
    if not isinstance(events, Mapping):
        raise ValueError("Pi connector checkpoint must contain an events mapping")  # noqa: TRY003, TRY004
    imported: dict[str, str] = {}
    for event_id, digest in events.items():
        if not isinstance(event_id, str) or not event_id or not isinstance(digest, str) or len(digest) != 64:
            raise ValueError("Pi connector checkpoint entries must be id/digest pairs")  # noqa: TRY003
        imported[event_id] = digest
    return imported


PI_EVENT_SOURCE_DEFINITION: SourceDefinition[PiEventInput, PiEventSource, PiEventInput] = AdapterSourceDefinition(
    PiEventSourceAdapter(),
    projections=(PiEventTextEvidenceProjection(),),
)


__all__ = [
    "PI_EVENT_SOURCE_DEFINITION",
    "PI_EVENT_SOURCE_NAME",
    "PI_SESSION_CONNECTOR_NAME",
    "PI_SESSION_CONNECTOR_VERSION",
    "PiEventInput",
    "PiEventSource",
    "PiEventSourceAdapter",
    "PiEventTextEvidenceProjection",
    "PiSessionConnector",
    "PiSessionInput",
    "parse_pi_session",
    "pi_event_source_id",
]

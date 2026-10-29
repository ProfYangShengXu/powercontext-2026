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

"""Wire contract for full Context Packs and prefix-reusing deltas.

A :class:`ContextPack` is the complete prepared context cut into bounded,
individually valid UTF-8 chunks.  A :class:`ContextPackDelta` keeps the longest
common chunk prefix of a base pack and carries only the target suffix, so a
receiver holding the base pack can rebuild the target without any sender-side
state.  Both wire objects are self-describing JSON: schema, Base64 chunk arrays,
SHA-256 digests, and the exact serialized byte count.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
import re
from dataclasses import dataclass
from typing import Any

from powercontext.errors import PowerContextError

PACK_SCHEMA = "powercontext.context-pack.v1"
DELTA_SCHEMA = "powercontext.context-pack-delta.v1"
DEFAULT_CHUNK_BYTES = 512

_PACK_FIELDS = frozenset({"schema", "items", "digest", "byte_count"})
_DELTA_FIELDS = frozenset({"schema", "base_digest", "target_digest", "prefix_items", "appended"})
_DIGEST_PATTERN = re.compile(r"^sha256:[0-9a-f]{64}$")


class ContextPackError(PowerContextError):
    """Protocol, chunking, or verification failure for a Context Pack or delta."""


@dataclass(frozen=True, slots=True)
class ContextPack:
    """One complete prepared context as ordered, UTF-8 valid byte chunks."""

    items: tuple[bytes, ...]
    digest: str
    byte_count: int

    def __post_init__(self) -> None:
        items = _normalize_bytes_tuple(self.items, "items")
        object.__setattr__(self, "items", items)
        expected_digest = _pack_digest(items)
        if self.digest != expected_digest:
            raise ContextPackError("Context Pack digest does not match its items")  # noqa: TRY003
        expected_count = _pack_byte_count(items)
        if self.byte_count != expected_count:
            raise ContextPackError("Context Pack byte_count does not match its encoding")  # noqa: TRY003

    @classmethod
    def build(cls, items: Any, /) -> ContextPack:
        """Build a complete pack from an ordered tuple of byte chunks."""

        normalized = _normalize_bytes_tuple(items, "items")
        return cls(items=normalized, digest=_pack_digest(normalized), byte_count=_pack_byte_count(normalized))

    @classmethod
    def from_content(cls, content: str | bytes, /, *, chunk_bytes: int = DEFAULT_CHUNK_BYTES) -> ContextPack:
        """Cut one context body into deterministic, bounded, valid UTF-8 chunks."""

        if not _is_positive_int(chunk_bytes):
            raise ContextPackError("chunk_bytes must be a positive integer")  # noqa: TRY003
        try:
            raw = content.encode("utf-8") if isinstance(content, str) else bytes(content)
        except (AttributeError, TypeError, UnicodeEncodeError) as error:
            raise ContextPackError("context content must be valid UTF-8 text") from error  # noqa: TRY003
        normalized = _valid_utf8_bytes(raw)
        return cls.build(_split_utf8(normalized, chunk_bytes))

    def to_bytes(self) -> bytes:
        """Serialize the only legal wire representation of this pack."""

        payload = {
            "schema": PACK_SCHEMA,
            "items": [_encode_item(item) for item in self.items],
            "digest": self.digest,
            "byte_count": self.byte_count,
        }
        return _canonical_json(payload)

    @classmethod
    def from_bytes(cls, value: bytes, /) -> ContextPack:
        """Parse and verify one complete pack, including its byte count."""

        payload = _decode_object(value, _PACK_FIELDS, "Context Pack")
        if payload["schema"] != PACK_SCHEMA:
            raise ContextPackError("Context Pack schema is not supported")  # noqa: TRY003
        items = _decode_items(payload["items"], "Context Pack items")
        digest = payload["digest"]
        if not isinstance(digest, str) or not _DIGEST_PATTERN.fullmatch(digest):
            raise ContextPackError("Context Pack digest must be sha256:<hex>")  # noqa: TRY003
        byte_count = payload["byte_count"]
        if not _is_plain_int(byte_count) or byte_count < 0:
            raise ContextPackError("Context Pack byte_count must be a non-negative integer")  # noqa: TRY003
        if byte_count != len(value):
            raise ContextPackError("Context Pack byte_count does not match the received bytes")  # noqa: TRY003
        pack = cls.build(items)
        if pack.digest != digest:
            raise ContextPackError("Context Pack digest does not match its items")  # noqa: TRY003
        return pack


@dataclass(frozen=True, slots=True)
class ContextPackDelta:
    """One prefix-reusing delta from a base pack to a target pack."""

    base_digest: str
    target_digest: str
    prefix_items: int
    appended: tuple[bytes, ...] = ()

    def __post_init__(self) -> None:
        for field, value in (("base_digest", self.base_digest), ("target_digest", self.target_digest)):
            if not isinstance(value, str) or not _DIGEST_PATTERN.fullmatch(value):
                raise ContextPackError(f"{field} must be sha256:<hex>")  # noqa: TRY003
        if not _is_plain_int(self.prefix_items) or self.prefix_items < 0:
            raise ContextPackError("prefix_items must be a non-negative integer")  # noqa: TRY003
        object.__setattr__(self, "appended", _normalize_bytes_tuple(self.appended, "appended"))

    @classmethod
    def between(cls, base: ContextPack, target: ContextPack, /) -> ContextPackDelta:
        """Keep the longest equal chunk prefix of ``base`` and append the target suffix."""

        if not isinstance(base, ContextPack) or not isinstance(target, ContextPack):
            raise ContextPackError("between requires two ContextPack values")  # noqa: TRY003
        prefix = 0
        for left, right in zip(base.items, target.items, strict=False):
            if left != right:
                break
            prefix += 1
        return cls(
            base_digest=base.digest,
            target_digest=target.digest,
            prefix_items=prefix,
            appended=target.items[prefix:],
        )

    @property
    def byte_count(self) -> int:
        """The exact serialized size of this delta; never written as a field."""

        return len(self.to_bytes())

    def to_bytes(self) -> bytes:
        """Serialize the only legal wire representation of this delta."""

        payload = {
            "schema": DELTA_SCHEMA,
            "base_digest": self.base_digest,
            "target_digest": self.target_digest,
            "prefix_items": self.prefix_items,
            "appended": [_encode_item(item) for item in self.appended],
        }
        return _canonical_json(payload)

    @classmethod
    def from_bytes(cls, value: bytes, /) -> ContextPackDelta:
        """Parse and verify one delta envelope."""

        payload = _decode_object(value, _DELTA_FIELDS, "Context Pack delta")
        if payload["schema"] != DELTA_SCHEMA:
            raise ContextPackError("Context Pack delta schema is not supported")  # noqa: TRY003
        prefix = payload["prefix_items"]
        if not _is_plain_int(prefix) or prefix < 0:
            raise ContextPackError("prefix_items must be a non-negative integer")  # noqa: TRY003
        appended = _decode_items(payload["appended"], "Context Pack delta appended")
        return cls(
            base_digest=payload["base_digest"],
            target_digest=payload["target_digest"],
            prefix_items=prefix,
            appended=appended,
        )


def apply_context_pack_delta(base: ContextPack, delta: ContextPackDelta, /) -> ContextPack:
    """Rebuild the target pack from a held base pack and one verified delta."""

    if not isinstance(base, ContextPack) or not isinstance(delta, ContextPackDelta):
        raise ContextPackError("apply requires a ContextPack and a ContextPackDelta")  # noqa: TRY003
    if base.digest != delta.base_digest:
        raise ContextPackError("delta base_digest does not match the held base pack")  # noqa: TRY003
    if delta.prefix_items > len(base.items):
        raise ContextPackError("delta prefix_items exceeds the base pack")  # noqa: TRY003
    target = ContextPack.build((*base.items[: delta.prefix_items], *delta.appended))
    if target.digest != delta.target_digest:
        raise ContextPackError("delta target_digest does not match the rebuilt pack")  # noqa: TRY003
    return target


def _split_utf8(value: bytes, chunk_bytes: int) -> tuple[bytes, ...]:
    """Greedily cut at UTF-8 boundaries without exceeding ``chunk_bytes``."""

    if not value:
        return ()
    chunks: list[bytes] = []
    start = 0
    size = len(value)
    while start < size:
        end = min(start + chunk_bytes, size)
        if end < size:
            while end > start and (value[end] & 0xC0) == 0x80:
                end -= 1
            if end == start:
                raise ContextPackError("chunk_bytes is too small to hold one UTF-8 character")  # noqa: TRY003
        chunks.append(value[start:end])
        start = end
    return tuple(chunks)


def _valid_utf8_bytes(value: bytes) -> bytes:
    try:
        value.decode("utf-8")
    except UnicodeDecodeError as error:
        raise ContextPackError("content must be valid UTF-8") from error  # noqa: TRY003
    return value


def _normalize_bytes_tuple(value: Any, field: str) -> tuple[bytes, ...]:
    if isinstance(value, (bytes, bytearray, memoryview, str)) or not isinstance(value, (tuple, list)):
        raise ContextPackError(f"{field} must be a tuple of bytes")  # noqa: TRY003
    normalized: list[bytes] = []
    for item in value:
        if not isinstance(item, (bytes, bytearray, memoryview)):
            raise ContextPackError(f"{field} must contain only bytes")  # noqa: TRY003
        normalized.append(bytes(item))
    return tuple(normalized)


def _decode_object(value: bytes, expected: frozenset[str], label: str) -> dict[str, Any]:
    if not isinstance(value, (bytes, bytearray, memoryview)):
        raise ContextPackError(f"{label} must be UTF-8 encoded bytes")  # noqa: TRY003
    try:
        payload = json.loads(bytes(value).decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ContextPackError(f"{label} is not valid UTF-8 JSON") from error  # noqa: TRY003
    if not isinstance(payload, dict):
        raise ContextPackError(f"{label} must be a JSON object")  # noqa: TRY003
    if set(payload) != expected:
        raise ContextPackError(f"{label} has missing or extra fields")  # noqa: TRY003
    return payload


def _decode_items(value: Any, field: str) -> tuple[bytes, ...]:
    if not isinstance(value, list):
        raise ContextPackError(f"{field} must be a JSON array")  # noqa: TRY003
    items: list[bytes] = []
    for encoded in value:
        if not isinstance(encoded, str):
            raise ContextPackError(f"{field} must contain Base64 strings")  # noqa: TRY003
        try:
            item = base64.b64decode(encoded, validate=True)
        except (binascii.Error, ValueError) as error:
            raise ContextPackError(f"{field} contains invalid Base64") from error  # noqa: TRY003
        if _encode_item(item) != encoded:
            raise ContextPackError(f"{field} contains non-canonical Base64")  # noqa: TRY003
        items.append(item)
    return tuple(items)


def _encode_item(value: bytes) -> str:
    return base64.b64encode(value).decode("ascii")


def _canonical_json(payload: dict[str, Any]) -> bytes:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def _pack_digest(items: tuple[bytes, ...]) -> str:
    unsigned = {"schema": PACK_SCHEMA, "items": [_encode_item(item) for item in items]}
    return f"sha256:{hashlib.sha256(_canonical_json(unsigned)).hexdigest()}"


def _pack_byte_count(items: tuple[bytes, ...]) -> int:
    digest = _pack_digest(items)
    encoded_items = [_encode_item(item) for item in items]
    byte_count = 0
    for _ in range(16):
        payload = {"schema": PACK_SCHEMA, "items": encoded_items, "digest": digest, "byte_count": byte_count}
        size = len(_canonical_json(payload))
        if size == byte_count:
            return byte_count
        byte_count = size
    raise ContextPackError("Context Pack byte_count did not converge")  # noqa: TRY003


def _is_plain_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _is_positive_int(value: Any) -> bool:
    return _is_plain_int(value) and value > 0


__all__ = [
    "DEFAULT_CHUNK_BYTES",
    "DELTA_SCHEMA",
    "PACK_SCHEMA",
    "ContextPack",
    "ContextPackDelta",
    "ContextPackError",
    "apply_context_pack_delta",
]

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

"""Row-level captured Sources for offline CSV documents.

Every data record becomes one :class:`CSVRowSource` whose identity is stable
across reordering of the file and only depends on the document identity and the
primary-key value.  The standard ``powercontext.text-evidence`` projection keeps
the original column order so a retrieval citation can be read back as the full
original record.
"""

from __future__ import annotations

import csv
import io
import json
from typing import Any
from urllib.parse import quote

from pydantic import BaseModel, field_validator, model_validator

from powercontext.limits import MAX_SOURCE_ID_LENGTH
from powercontext.sources import (
    TEXT_EVIDENCE_PROJECTION_KEY,
    AdapterSourceDefinition,
    Source,
    SourceDefinition,
    SourceMaterialization,
    TextEvidence,
)

CSV_ROW_SOURCE_NAME = "csv-row"


class CSVDocumentInput(BaseModel):
    """One complete offline CSV document supplied for row-level capture."""

    document_id: str
    primary_key: str
    content: bytes


class CSVRowInput(BaseModel):
    """One data record selected from a CSV document by its primary-key value."""

    document_id: str
    primary_key: str
    primary_key_value: str
    columns: tuple[str, ...]
    values: tuple[str, ...]


class CSVRowSource(Source):
    """Durable captured evidence for exactly one CSV data record."""

    source_id: str
    document_id: str
    primary_key: str
    primary_key_value: str
    columns: tuple[str, ...]
    values: tuple[str, ...]

    @field_validator("source_id")
    @classmethod
    def validate_source_id(cls, value: str) -> str:
        if not value or value != value.strip() or len(value) > MAX_SOURCE_ID_LENGTH:
            raise ValueError(  # noqa: TRY003
                f"source_id must be a bounded non-empty trimmed string of at most {MAX_SOURCE_ID_LENGTH} characters"
            )
        return value

    @model_validator(mode="after")
    def validate_row_material(self) -> CSVRowSource:
        _validate_document_id(self.document_id)
        _validate_primary_key(self.primary_key)
        _validate_record(
            primary_key=self.primary_key,
            primary_key_value=self.primary_key_value,
            columns=self.columns,
            values=self.values,
        )
        if self.name != self.source_id:
            raise ValueError("name and source_id must both be the row identity")  # noqa: TRY003
        return self


class CSVRowTextEvidenceProjection:
    """Expose one CSV row as the standard ``TextEvidence`` projection."""

    name = TEXT_EVIDENCE_PROJECTION_KEY.name
    version = TEXT_EVIDENCE_PROJECTION_KEY.version
    source_class = CSVRowSource
    output_class: type[BaseModel] = TextEvidence

    def project(self, source: CSVRowSource, /) -> TextEvidence:
        row = _row_mapping(source.columns, source.values)
        return TextEvidence(
            source_type=CSV_ROW_SOURCE_NAME,
            source_id=source.source_id,
            content=_dump_row(row),
            metadata={
                "document_id": source.document_id,
                "primary_key": source.primary_key,
                "primary_key_value": source.primary_key_value,
                "columns": list(source.columns),
                "row": row,
            },
        )


class CSVRowSourceAdapter:
    """Resolve and read the row-level captured Source for one CSV record."""

    input_class = CSVRowInput
    name = CSV_ROW_SOURCE_NAME
    source_class = CSVRowSource

    async def resolve(self, value: CSVRowInput, /) -> CSVRowSource:
        _validate_document_id(value.document_id)
        _validate_primary_key(value.primary_key)
        _validate_record(
            primary_key=value.primary_key,
            primary_key_value=value.primary_key_value,
            columns=value.columns,
            values=value.values,
        )
        identity = csv_row_source_id(value.document_id, value.primary_key_value)
        return CSVRowSource(
            name=identity,
            materialization=SourceMaterialization.CAPTURED,
            source_id=identity,
            document_id=value.document_id,
            primary_key=value.primary_key,
            primary_key_value=value.primary_key_value,
            columns=value.columns,
            values=value.values,
        )

    async def read(self, source: CSVRowSource, /) -> CSVRowInput:
        return CSVRowInput(
            document_id=source.document_id,
            primary_key=source.primary_key,
            primary_key_value=source.primary_key_value,
            columns=source.columns,
            values=source.values,
        )


def parse_csv_document(value: CSVDocumentInput, /) -> tuple[CSVRowInput, ...]:  # noqa: C901
    """Parse and fully validate one CSV document, then return its row inputs.

    The whole document is decoded and validated before any row is returned, so a
    failure never exposes a partial result.
    """

    _validate_document_id(value.document_id)
    _validate_primary_key(value.primary_key)
    try:
        text = value.content.decode("utf-8-sig")
    except UnicodeDecodeError as error:
        raise ValueError("CSV document must be strict UTF-8 (with an optional leading BOM)") from error  # noqa: TRY003

    reader = csv.reader(
        io.StringIO(text, newline=""),
        delimiter=",",
        quotechar='"',
        doublequote=True,
        skipinitialspace=False,
        strict=True,
    )
    try:
        header = next(reader)
    except StopIteration:
        raise ValueError("CSV document must not be empty") from None  # noqa: TRY003
    except csv.Error as error:
        raise ValueError(f"malformed CSV header: {error}") from error  # noqa: TRY003

    if not header:
        raise ValueError("CSV header must contain at least one column")  # noqa: TRY003
    for column in header:
        if not column or not column.strip():
            raise ValueError("CSV header columns must be non-empty and not whitespace-only")  # noqa: TRY003
    if len(set(header)) != len(header):
        raise ValueError("CSV header columns must be unique")  # noqa: TRY003
    if value.primary_key not in header:
        raise ValueError(  # noqa: TRY003
            f"primary_key {value.primary_key!r} does not exactly match a CSV header column"
        )
    key_index = header.index(value.primary_key)

    columns = tuple(header)
    rows: list[CSVRowInput] = []
    seen: set[str] = set()
    try:
        for record in reader:
            # ``csv.reader`` yields ``[]`` only for a completely empty physical record.
            if not record:
                continue
            if len(record) != len(columns):
                raise ValueError(  # noqa: TRY003
                    f"CSV record at line {reader.line_num} has {len(record)} fields, expected {len(columns)}"
                )
            primary_key_value = record[key_index]
            if not primary_key_value.strip():
                raise ValueError(f"CSV record at line {reader.line_num} has an empty primary-key value")  # noqa: TRY003
            if primary_key_value in seen:
                raise ValueError(  # noqa: TRY003
                    f"CSV record at line {reader.line_num} duplicates primary-key value {primary_key_value!r}"
                )
            seen.add(primary_key_value)
            rows.append(
                CSVRowInput(
                    document_id=value.document_id,
                    primary_key=value.primary_key,
                    primary_key_value=primary_key_value,
                    columns=columns,
                    values=tuple(record),
                )
            )
    except csv.Error as error:
        raise ValueError(f"malformed CSV record at line {reader.line_num}: {error}") from error  # noqa: TRY003

    return tuple(rows)


def csv_row_source_id(document_id: str, primary_key_value: str, /) -> str:
    """Return the delimiter-safe, bounded row identity used by the definition."""

    _validate_document_id(document_id)
    if not isinstance(primary_key_value, str) or not primary_key_value.strip():
        raise ValueError("primary_key_value must be a non-empty, non-whitespace string")  # noqa: TRY003
    identity = f"csv:{quote(document_id, safe='')}:{quote(primary_key_value, safe='')}"
    if len(identity) > MAX_SOURCE_ID_LENGTH:
        raise ValueError(  # noqa: TRY003
            f"row identity must not exceed {MAX_SOURCE_ID_LENGTH} characters, got {len(identity)}"
        )
    return identity


def _validate_document_id(document_id: object) -> str:
    if not isinstance(document_id, str) or not document_id or document_id != document_id.strip():
        raise ValueError(  # noqa: TRY003
            "document_id must be a non-empty string without leading or trailing whitespace"
        )
    return document_id


def _validate_primary_key(primary_key: object) -> str:
    if not isinstance(primary_key, str) or not primary_key:
        raise ValueError("primary_key must be a non-empty string")  # noqa: TRY003
    return primary_key


def _validate_record(
    *,
    primary_key: str,
    primary_key_value: str,
    columns: tuple[str, ...],
    values: tuple[str, ...],
) -> None:
    if not columns:
        raise ValueError("columns must contain at least one column")  # noqa: TRY003
    for column in columns:
        if not isinstance(column, str) or not column or not column.strip():
            raise ValueError("columns must be non-empty and must not be whitespace-only")  # noqa: TRY003
    if len(set(columns)) != len(columns):
        raise ValueError("columns must be unique")  # noqa: TRY003
    if len(values) != len(columns):
        raise ValueError("values must contain exactly one entry per column")  # noqa: TRY003
    if not primary_key_value or not primary_key_value.strip():
        raise ValueError("primary_key_value must be a non-empty, non-whitespace string")  # noqa: TRY003
    if primary_key not in columns:
        raise ValueError("primary_key must exactly match one of the columns")  # noqa: TRY003
    if values[columns.index(primary_key)] != primary_key_value:
        raise ValueError("primary_key_value must equal the value of the primary-key column")  # noqa: TRY003


def _row_mapping(columns: tuple[str, ...], values: tuple[str, ...]) -> dict[str, Any]:
    return dict(zip(columns, values, strict=True))


def _dump_row(row: dict[str, Any]) -> str:
    return json.dumps(row, ensure_ascii=False, separators=(",", ":"))


CSV_ROW_SOURCE_DEFINITION: SourceDefinition[CSVRowInput, CSVRowSource, CSVRowInput] = AdapterSourceDefinition(
    CSVRowSourceAdapter(),
    projections=(CSVRowTextEvidenceProjection(),),
)


__all__ = [
    "CSV_ROW_SOURCE_DEFINITION",
    "CSV_ROW_SOURCE_NAME",
    "CSVDocumentInput",
    "CSVRowInput",
    "CSVRowSource",
    "CSVRowSourceAdapter",
    "CSVRowTextEvidenceProjection",
    "csv_row_source_id",
    "parse_csv_document",
]

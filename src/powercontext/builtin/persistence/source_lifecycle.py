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

"""Persistent Source lifecycle registrations, events, and derived state.

The lifecycle graph is written once per Scope. Nodes are immutable bindings;
only a registered Source's availability changes, and a derived node's state is
recomputed from its parents in registration order. Keeping both the registration
rows and the event log relational makes a restart observe exactly the state the
previous process committed.
"""

from __future__ import annotations

from sqlalchemy import Boolean, Column, Index, Integer, String, Table, Text

from powercontext.builtin.persistence.tables import SHARED_METADATA, identity_string
from powercontext.limits import MAX_SCOPE_ID_LENGTH

MAX_SOURCE_LIFECYCLE_NODE_ID_LENGTH = 512
MAX_SOURCE_LIFECYCLE_KIND_LENGTH = 16
MAX_SOURCE_LIFECYCLE_POLICY_LENGTH = 8

SOURCE_LIFECYCLE_NODES_TABLE = Table(
    "pc_source_lifecycle_nodes",
    SHARED_METADATA,
    Column("scope_id", identity_string(MAX_SCOPE_ID_LENGTH), primary_key=True),
    Column(
        "node_id",
        identity_string(MAX_SOURCE_LIFECYCLE_NODE_ID_LENGTH),
        primary_key=True,
    ),
    Column("ordinal", Integer, nullable=False),
    Column("kind", String(MAX_SOURCE_LIFECYCLE_KIND_LENGTH), nullable=False),
    Column("ref_json", Text, nullable=False),
    Column("parents_json", Text, nullable=False),
    Column("policy", String(MAX_SOURCE_LIFECYCLE_POLICY_LENGTH)),
    Column("lifecycle_revision", Integer, nullable=False),
    Column("available", Boolean, nullable=False),
    Column("last_sequence", Integer, nullable=False),
    Index(
        "ix_pc_source_lifecycle_nodes_scope_kind",
        "scope_id",
        "kind",
        "available",
    ),
)

SOURCE_LIFECYCLE_EVENTS_TABLE = Table(
    "pc_source_lifecycle_events",
    SHARED_METADATA,
    Column("scope_id", identity_string(MAX_SCOPE_ID_LENGTH), primary_key=True),
    Column(
        "node_id",
        identity_string(MAX_SOURCE_LIFECYCLE_NODE_ID_LENGTH),
        primary_key=True,
    ),
    Column("sequence", Integer, primary_key=True),
    Column("revision", Integer, nullable=False),
    Column("available", Boolean, nullable=False),
    Column("reason", Text, nullable=False),
)

SOURCE_LIFECYCLE_TABLES = (SOURCE_LIFECYCLE_NODES_TABLE, SOURCE_LIFECYCLE_EVENTS_TABLE)

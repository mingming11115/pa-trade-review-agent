from __future__ import annotations

from dataclasses import dataclass
from collections.abc import Mapping
from hashlib import sha256
import json
from types import MappingProxyType
from datetime import datetime
from typing import Any, Literal
from uuid import UUID
from pydantic import BaseModel, Field, field_validator


@dataclass(frozen=True)
class ImportResult:
    created: tuple[str, ...]
    skipped: tuple[str, ...]
    different: tuple[str, ...]


@dataclass(frozen=True)
class SnapshotEntry:
    content: str
    scope_key: str
    revision: int
    content_hash: str


@dataclass(frozen=True)
class PromptSnapshot:
    scope_key: str
    entries: Mapping[str, SnapshotEntry]

    def __post_init__(self) -> None:
        object.__setattr__(self, 'entries', MappingProxyType(dict(self.entries)))

    @property
    def contents(self) -> Mapping[str, str]:
        return MappingProxyType({name: entry.content for name, entry in self.entries.items()})

    @property
    def manifest_hash(self) -> str:
        manifest = [[name, entry.scope_key, entry.revision, entry.content_hash]
                    for name, entry in sorted(self.entries.items())]
        return sha256(json.dumps(manifest, ensure_ascii=False, separators=(',', ':')).encode('utf-8')).hexdigest()

    def to_payload(self) -> dict[str, dict[str, Any]]:
        return {name: {'content': entry.content, 'scope_key': entry.scope_key,
                       'revision': entry.revision, 'content_hash': entry.content_hash}
                for name, entry in self.entries.items()}

    def metadata(self, filenames: list[str] | tuple[str, ...]) -> dict[str, Any]:
        return {'manifest_hash': self.manifest_hash,
                'files': [{'filename': name, 'scope_key': self.entries[name].scope_key,
                           'revision': self.entries[name].revision, 'content_hash': self.entries[name].content_hash}
                          for name in dict.fromkeys(filenames)]}



class KnowledgeDocument(BaseModel):
    filename: str
    content: str
    version: str
    size: int
    source: Literal['default', 'personal']
    revision: int


class KnowledgeVersion(BaseModel):
    id: UUID
    revision: int
    actor: str
    action: str
    created_at: datetime
    can_rollback: bool


class KnowledgeVersionPage(BaseModel):
    items: list[KnowledgeVersion]
    next_before_revision: int | None = None


class KnowledgeRollback(BaseModel):
    expected_version: str = Field(min_length=1, max_length=100)


class KnowledgeUpdate(KnowledgeRollback):
    content: str = Field(min_length=1, max_length=1_000_000)

    @field_validator('content')
    @classmethod
    def nonblank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError('提示词不能为空')
        return value


class KnowledgeSummary(BaseModel):
    filename: str
    source: Literal['default', 'personal']
    revision: int
    version: str
    size: int


class KnowledgePromptRef(BaseModel):
    stage_id: str
    placement: Literal['system', 'user']
    condition: str


class KnowledgeSummaryPublic(KnowledgeSummary):
    stages: list[KnowledgePromptRef]

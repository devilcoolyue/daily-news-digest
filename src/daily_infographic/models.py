from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any


@dataclass
class SourceConfig:
    id: str
    name: str
    type: str
    enabled: bool
    tier: str
    priority: float
    url: str | None = None
    tags: list[str] = field(default_factory=list)
    dev_only: bool = False
    params: dict[str, Any] = field(default_factory=dict)


@dataclass
class DomainConfig:
    domain: str
    display_name: str
    timezone: str
    window_hours: int
    top_k: int
    title_max_len: int
    summary_max_len: int
    tags_max_count: int
    diversity_per_entity_limit: int
    dedupe_title_similarity_threshold: float
    scoring_weights: dict[str, float]
    source_reliability: dict[str, float]
    keyword_impact: dict[str, float]
    entity_keywords: list[str]
    tag_keywords: dict[str, str]
    llm: dict[str, Any]
    render: dict[str, Any]
    sources: list[SourceConfig]


@dataclass
class NewsItem:
    source_id: str
    source_name: str
    source_tier: str
    title: str
    summary: str
    url: str
    published_at: datetime
    tags: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class Event:
    event_id: str
    canonical_title: str
    summary: str
    published_at: datetime
    urls: list[str]
    source_ids: list[str]
    source_names: list[str]
    primary_source_name: str
    entity: str
    tags: list[str]
    score_breakdown: dict[str, float]
    score: float
    items: list[NewsItem] = field(default_factory=list)


@dataclass
class Card:
    title: str
    tags: list[str]
    summary: str
    source_label: str
    date_label: str
    score: float
    url: str
    entity: str
    icon_kind: str = ""
    icon_url: str = ""


@dataclass
class RunResult:
    image_path: str
    manifest_path: str
    fetched_count: int
    event_count: int
    selected_count: int
    skipped_sources: list[str]

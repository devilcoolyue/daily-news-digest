from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from .models import DomainConfig, SourceConfig


def _as_float_map(value: dict[str, Any] | None) -> dict[str, float]:
    if not value:
        return {}
    return {str(k): float(v) for k, v in value.items()}


def load_domain_config(path: str | Path) -> DomainConfig:
    cfg_path = Path(path)
    with cfg_path.open("r", encoding="utf-8") as f:
        raw = yaml.safe_load(f)

    diversity = raw.get("diversity", {})
    dedupe = raw.get("dedupe", {})

    sources = []
    for item in raw.get("sources", []):
        known_keys = {
            "id",
            "name",
            "type",
            "enabled",
            "tier",
            "priority",
            "url",
            "tags",
            "dev_only",
        }
        params = {k: v for k, v in item.items() if k not in known_keys}
        sources.append(
            SourceConfig(
                id=item["id"],
                name=item.get("name", item["id"]),
                type=item["type"],
                enabled=bool(item.get("enabled", True)),
                tier=item.get("tier", "media"),
                priority=float(item.get("priority", 0.5)),
                url=item.get("url"),
                tags=list(item.get("tags", [])),
                dev_only=bool(item.get("dev_only", False)),
                params=params,
            )
        )

    return DomainConfig(
        domain=raw["domain"],
        display_name=raw.get("display_name", raw["domain"]),
        timezone=raw.get("timezone", "Asia/Shanghai"),
        window_hours=int(raw.get("window_hours", 24)),
        top_k=int(raw.get("top_k", 12)),
        title_max_len=int(raw.get("title_max_len", 20)),
        summary_max_len=int(raw.get("summary_max_len", 60)),
        tags_max_count=int(raw.get("tags_max_count", 3)),
        diversity_per_entity_limit=int(diversity.get("per_entity_limit", 2)),
        dedupe_title_similarity_threshold=float(
            dedupe.get("title_similarity_threshold", 0.82)
        ),
        scoring_weights=_as_float_map(raw.get("scoring")),
        source_reliability=_as_float_map(raw.get("source_reliability")),
        keyword_impact=_as_float_map(raw.get("keyword_impact")),
        entity_keywords=[str(v) for v in raw.get("entity_keywords", [])],
        tag_keywords={str(k): str(v) for k, v in raw.get("tag_keywords", {}).items()},
        llm=dict(raw.get("llm", {})),
        render=dict(raw.get("render", {})),
        sources=sources,
    )

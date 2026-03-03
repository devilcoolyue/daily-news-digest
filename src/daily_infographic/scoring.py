from __future__ import annotations

from collections import Counter
from datetime import datetime

from .models import DomainConfig, NewsItem
from .utils import clamp01


def infer_entity(text: str, entity_keywords: list[str]) -> str:
    lowered = text.lower()
    for entity in entity_keywords:
        if entity.lower() in lowered:
            return entity
    return "其他"


def infer_tags(text: str, tag_keywords: dict[str, str], max_count: int) -> list[str]:
    lowered = text.lower()
    out: list[str] = []
    for needle, tag in tag_keywords.items():
        if needle.lower() in lowered and tag not in out:
            out.append(tag)
        if len(out) >= max_count:
            return out
    return out[:max_count]


def compute_event_score(
    items: list[NewsItem],
    config: DomainConfig,
    source_priority: dict[str, float],
    run_until_utc: datetime,
) -> dict[str, float]:
    if not items:
        return {
            "impact": 0.0,
            "reliability": 0.0,
            "recency": 0.0,
            "corroboration": 0.0,
            "buzz": 0.0,
            "total": 0.0,
        }

    latest = max(items, key=lambda x: x.published_at)
    joined_text = " ".join([i.title + " " + i.summary for i in items])
    lowered = joined_text.lower()

    # Impact: keyword hit strength.
    impact_hits = 0.0
    for keyword, weight in config.keyword_impact.items():
        if keyword.lower() in lowered:
            impact_hits += float(weight)
    impact = 0.35 if impact_hits == 0 else clamp01(impact_hits / 2.0)

    # Reliability: source tier reliability + source priority.
    rel_values = []
    for item in items:
        tier_rel = config.source_reliability.get(item.source_tier, 0.60)
        pri = source_priority.get(item.source_id, 0.50)
        rel_values.append(clamp01(tier_rel * 0.7 + pri * 0.3))
    reliability = sum(rel_values) / len(rel_values)

    # Recency: linear decay in configured window.
    age_hours = max(0.0, (run_until_utc - latest.published_at).total_seconds() / 3600.0)
    recency = clamp01(1.0 - (age_hours / float(config.window_hours)))

    # Corroboration: multiple unique sources increase confidence.
    unique_sources = len({i.source_name for i in items})
    corroboration = clamp01((unique_sources - 1) / 3.0)

    # Buzz: very lightweight topical pulse.
    buzz_terms = ["发布", "首发", "爆", "热", "trend", "viral", "benchmark"]
    buzz_count = sum(lowered.count(term) for term in buzz_terms)
    buzz = clamp01(0.25 + buzz_count * 0.10)

    weights = config.scoring_weights
    total = (
        impact * weights.get("impact", 0.35)
        + reliability * weights.get("reliability", 0.25)
        + recency * weights.get("recency", 0.20)
        + corroboration * weights.get("corroboration", 0.15)
        + buzz * weights.get("buzz", 0.05)
    )

    return {
        "impact": round(impact, 4),
        "reliability": round(reliability, 4),
        "recency": round(recency, 4),
        "corroboration": round(corroboration, 4),
        "buzz": round(buzz, 4),
        "total": round(total, 4),
    }


def choose_primary_item(items: list[NewsItem], source_priority: dict[str, float]) -> NewsItem:
    def score(item: NewsItem) -> float:
        return source_priority.get(item.source_id, 0.50)

    return sorted(items, key=score, reverse=True)[0]


def source_distribution(items: list[NewsItem]) -> dict[str, int]:
    counter = Counter([i.source_name for i in items])
    return dict(counter)

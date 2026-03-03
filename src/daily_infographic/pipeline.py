from __future__ import annotations

import html
import json
import re
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from typing import Sequence
from zoneinfo import ZoneInfo

from .config import load_domain_config
from .dedupe import cluster_news_items
from .llm_refiner import enrich_events
from .models import Card, DomainConfig, Event, NewsItem, RunResult
from .render import plan_card_layout, render_infographic
from .scoring import choose_primary_item, compute_event_score, infer_entity, infer_tags
from .selection import select_top_events
from .sources import MockSource, NewsApiSource, RssSource, SourceAdapter
from .utils import floor_sentence, now_utc, stable_id, to_local_date_label


ADAPTERS: dict[str, type[SourceAdapter]] = {
    "rss": RssSource,
    "newsapi": NewsApiSource,
    "mock": MockSource,
}


def _build_time_window(config: DomainConfig, run_date: date) -> tuple[datetime, datetime]:
    tz = ZoneInfo(config.timezone)
    now_local = now_utc().astimezone(tz)
    today_local = now_local.date()

    if run_date >= today_local:
        until_local = now_local
    else:
        until_local = datetime.combine(run_date, time(23, 59, 59), tzinfo=tz)

    since_local = until_local - timedelta(hours=config.window_hours)
    return since_local.astimezone(timezone.utc), until_local.astimezone(timezone.utc)


def _build_sources(config: DomainConfig, mock_only: bool) -> tuple[list[SourceAdapter], list[str]]:
    adapters: list[SourceAdapter] = []
    skipped: list[str] = []

    for src in config.sources:
        if not src.enabled:
            continue
        if src.dev_only and not mock_only:
            continue
        if mock_only and src.type != "mock":
            continue

        adapter_cls = ADAPTERS.get(src.type)
        if adapter_cls is None:
            skipped.append(f"{src.id}(unknown:{src.type})")
            continue
        adapters.append(adapter_cls(src))

    return adapters, skipped


def _collect_items(
    config: DomainConfig,
    since_utc: datetime,
    until_utc: datetime,
    mock_only: bool,
) -> tuple[list[NewsItem], list[str]]:
    adapters, skipped = _build_sources(config, mock_only)
    all_items: list[NewsItem] = []

    for adapter in adapters:
        try:
            rows = adapter.fetch(since_utc, until_utc)
            all_items.extend(rows)
        except Exception:
            skipped.append(adapter.cfg.id)

    all_items = [
        i
        for i in all_items
        if i.title.strip() and i.url.strip() and i.published_at.tzinfo is not None
    ]

    if all_items or mock_only:
        return all_items, skipped

    # Fallback: no online data fetched, activate mock source if configured.
    for src in config.sources:
        if src.type == "mock" and src.enabled:
            rows = MockSource(src).fetch(since_utc, until_utc)
            all_items.extend(rows)
            skipped.append("online_sources_empty_fallback_to_mock")
            break

    return all_items, skipped


def _unique_in_order(values: list[str]) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for value in values:
        if value not in seen:
            out.append(value)
            seen.add(value)
    return out


def _build_events(
    config: DomainConfig,
    clusters: list[list[NewsItem]],
    run_until_utc: datetime,
) -> list[Event]:
    source_priority = {s.id: s.priority for s in config.sources}
    events: list[Event] = []

    for cluster in clusters:
        if not cluster:
            continue

        primary = choose_primary_item(cluster, source_priority)
        latest_time = max(item.published_at for item in cluster)

        canonical_title = html.unescape(primary.title).strip()
        canonical_summary = html.unescape(primary.summary).strip()
        summary = floor_sentence(canonical_summary) or canonical_title
        merged_text = " ".join([canonical_title, summary])
        entity = infer_entity(merged_text, config.entity_keywords)

        tags = infer_tags(merged_text, config.tag_keywords, config.tags_max_count)
        for raw_tag in primary.tags:
            if len(tags) >= config.tags_max_count:
                break
            if raw_tag not in tags:
                tags.append(raw_tag)
        if not tags:
            tags = [entity if entity != "其他" else config.domain.upper()]

        score_breakdown = compute_event_score(cluster, config, source_priority, run_until_utc)

        event = Event(
            event_id=stable_id(canonical_title, str(latest_time.date())),
            canonical_title=canonical_title,
            summary=summary,
            published_at=latest_time,
            urls=_unique_in_order([item.url for item in cluster]),
            source_ids=_unique_in_order([item.source_id for item in cluster]),
            source_names=_unique_in_order([item.source_name for item in cluster]),
            primary_source_name=primary.source_name,
            entity=entity,
            tags=tags,
            score_breakdown=score_breakdown,
            score=score_breakdown["total"],
            items=cluster,
        )
        events.append(event)

    return events


def _clip_text_soft(text: str, max_len: int) -> str:
    clean = " ".join(text.split())
    if len(clean) <= max_len:
        return clean
    if max_len <= 1:
        return clean[:max_len]
    return clean[: max_len - 1].rstrip("，,。；;：:!！?？ ") + "…"


def _ensure_summary_period(text: str) -> str:
    clean = " ".join((text or "").split()).strip()
    if not clean:
        return ""

    if clean.endswith(("。", "！", "？")):
        return clean[:-1].rstrip() + "。"
    if clean.endswith(("!", "?", ".", "；", ";", "：", ":")):
        return clean[:-1].rstrip() + "。"
    if clean.endswith("…"):
        return clean + "。"
    return clean + "。"


def _estimate_layout_text_weight(
    title: str,
    summary: str,
    tags: list[str],
) -> float:
    blob = " ".join(
        part
        for part in [
            (title or "").strip(),
            (summary or "").strip(),
            " ".join([str(tag).strip() for tag in tags if str(tag).strip()]),
        ]
        if part
    )
    if not blob:
        return 0.0

    cjk_chars = len(re.findall(r"[\u4e00-\u9fff]", blob))
    latin_tokens = len(re.findall(r"[A-Za-z0-9]+", blob))
    punctuation = len(re.findall(r"[，。！？,.!?;；:：]", blob))
    return float(cjk_chars) + float(latin_tokens) * 2.2 + float(punctuation) * 0.35


def _build_layout_hints(events: Sequence[Event]) -> list[dict[str, float]]:
    hints: list[dict[str, float]] = []
    for event in events:
        hints.append(
            {
                "score": float(event.score),
                "text_weight": _estimate_layout_text_weight(
                    event.canonical_title,
                    event.summary,
                    event.tags,
                ),
            }
        )
    return hints


def _estimate_card_budget(card_w: int, card_h: int) -> dict[str, int]:
    inner_pad = max(12, int(min(card_w, card_h) * 0.07))
    scale = min(card_w / 320.0, card_h / 350.0)
    scale = max(0.70, min(1.45, scale))

    title_font = max(20, int(34 * scale))
    body_font = max(17, int(26 * scale))
    title_max_lines = 4 if card_h > card_w * 1.30 else 3
    summary_max_lines = 6 if card_h > card_w * 1.35 else 5 if card_h > card_w * 1.10 else 4
    if card_w < 280:
        summary_max_lines = min(7, summary_max_lines + 1)

    title_width = max(90, card_w - inner_pad * 2 - int(64 * scale))
    body_width = max(90, card_w - inner_pad * 2)

    # Approximate Chinese-char capacity from font size and line count.
    title_chars_per_line = max(4, int(title_width / max(1, int(title_font * 0.96))))
    summary_chars_per_line = max(6, int(body_width / max(1, int(body_font * 0.96))))

    return {
        # Keep a readable floor to avoid over-truncating on compact cards.
        "title_max_chars": max(22, title_chars_per_line * title_max_lines),
        "summary_max_chars": max(52, summary_chars_per_line * summary_max_lines),
    }


def _build_card_budgets(
    config: DomainConfig,
    run_date: date,
    card_count: int,
    layout_hints: Sequence[dict[str, float]] | None = None,
) -> list[dict[str, int]]:
    rects = plan_card_layout(config, run_date, card_count, layout_hints=layout_hints)
    budgets: list[dict[str, int]] = []
    for _, _, w, h in rects:
        budgets.append(_estimate_card_budget(w, h))
    return budgets


def _events_to_cards(
    config: DomainConfig,
    events: list[Event],
    enriched: dict[str, dict[str, str]],
    budgets: list[dict[str, int]],
) -> list[Card]:
    cards: list[Card] = []
    for idx, event in enumerate(events):
        payload = enriched.get(event.event_id, {})
        budget = budgets[idx] if idx < len(budgets) else {}
        title_budget = max(config.title_max_len, int(budget.get("title_max_chars", config.title_max_len)))
        summary_budget = max(config.summary_max_len, int(budget.get("summary_max_chars", config.summary_max_len)))

        title = payload.get("title", "") or event.canonical_title
        summary = payload.get("summary", "") or event.summary
        title = title.replace("…", "").replace("...", "")
        summary = summary.replace("…", "").replace("...", "")
        icon_kind = payload.get("icon_kind", "")
        icon_url = payload.get("icon_url", "")
        cards.append(
            Card(
                title=_clip_text_soft(title, max(40, title_budget)),
                tags=event.tags[: config.tags_max_count],
                summary=_ensure_summary_period(_clip_text_soft(summary, max(120, summary_budget * 2))),
                source_label=event.primary_source_name,
                date_label=to_local_date_label(event.published_at, config.timezone),
                score=event.score,
                url=event.urls[0] if event.urls else "",
                entity=event.entity,
                icon_kind=icon_kind,
                icon_url=icon_url,
            )
        )
    return cards


def _write_manifest(
    config: DomainConfig,
    run_date: date,
    since_utc: datetime,
    until_utc: datetime,
    events: list[Event],
    selected: list[Event],
    enriched: dict[str, dict[str, str]],
    output_dir: str | Path,
) -> str:
    out_root = Path(output_dir)
    out_root.mkdir(parents=True, exist_ok=True)

    payload = {
        "domain": config.domain,
        "display_name": config.display_name,
        "run_date": run_date.isoformat(),
        "window": {
            "since_utc": since_utc.isoformat(),
            "until_utc": until_utc.isoformat(),
        },
        "event_count": len(events),
        "selected_count": len(selected),
        "cards": [
            {
                "event_id": event.event_id,
                "title": event.canonical_title,
                "summary": event.summary,
                "entity": event.entity,
                "tags": event.tags,
                "score": event.score,
                "score_breakdown": event.score_breakdown,
                "published_at": event.published_at.isoformat(),
                "primary_source": event.primary_source_name,
                "sources": event.source_names,
                "urls": event.urls,
                "icon_kind": str(enriched.get(event.event_id, {}).get("icon_kind", "")),
                "icon_url": str(enriched.get(event.event_id, {}).get("icon_url", "")),
            }
            for event in selected
        ],
    }

    path = out_root / f"{run_date.isoformat()}-{config.domain}-manifest.json"
    with path.open("w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    return str(path)


def run_pipeline(
    config_path: str,
    run_date: date,
    mock_only: bool = False,
    output_dir: str | None = None,
) -> RunResult:
    config = load_domain_config(config_path)
    since_utc, until_utc = _build_time_window(config, run_date)

    items, skipped_sources = _collect_items(config, since_utc, until_utc, mock_only)
    clusters = cluster_news_items(items, config.dedupe_title_similarity_threshold)
    events = _build_events(config, clusters, until_utc)

    selected = select_top_events(
        events,
        top_k=config.top_k,
        per_entity_limit=config.diversity_per_entity_limit,
    )
    # Always make the hottest event the primary card.
    selected = sorted(selected, key=lambda event: event.score, reverse=True)
    layout_hints = _build_layout_hints(selected)

    card_budgets = _build_card_budgets(config, run_date, len(selected), layout_hints=layout_hints)
    enriched = enrich_events(selected, config, card_budgets)
    cards = _events_to_cards(config, selected, enriched, card_budgets)
    out_dir = output_dir or str(config.render.get("output_dir", "output"))

    image_path = render_infographic(
        cards,
        config,
        run_date,
        output_dir=out_dir,
        layout_hints=layout_hints,
    )
    manifest_path = _write_manifest(
        config,
        run_date,
        since_utc,
        until_utc,
        events,
        selected,
        enriched,
        output_dir=out_dir,
    )

    return RunResult(
        image_path=image_path,
        manifest_path=manifest_path,
        fetched_count=len(items),
        event_count=len(events),
        selected_count=len(selected),
        skipped_sources=skipped_sources,
    )

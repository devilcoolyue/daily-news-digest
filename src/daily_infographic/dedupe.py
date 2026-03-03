from __future__ import annotations

from difflib import SequenceMatcher

from .models import NewsItem
from .utils import normalize_title


class _Cluster:
    def __init__(self, seed: NewsItem):
        self.items = [seed]
        self.norm_title = normalize_title(seed.title)

    def add(self, item: NewsItem) -> None:
        self.items.append(item)


def _similarity(a: str, b: str) -> float:
    if not a or not b:
        return 0.0
    if a == b:
        return 1.0
    return SequenceMatcher(None, a, b).ratio()


def cluster_news_items(items: list[NewsItem], threshold: float) -> list[list[NewsItem]]:
    clusters: list[_Cluster] = []

    for item in sorted(items, key=lambda x: x.published_at, reverse=True):
        item_norm = normalize_title(item.title)

        # Fast path: same URL belongs to same event.
        matched_cluster = None
        for cluster in clusters:
            if any(existing.url == item.url for existing in cluster.items):
                matched_cluster = cluster
                break

        if matched_cluster is None:
            best_ratio = 0.0
            best_cluster: _Cluster | None = None
            for cluster in clusters:
                ratio = _similarity(item_norm, cluster.norm_title)
                if ratio > best_ratio:
                    best_ratio = ratio
                    best_cluster = cluster
            if best_cluster is not None and best_ratio >= threshold:
                matched_cluster = best_cluster

        if matched_cluster is None:
            clusters.append(_Cluster(item))
        else:
            matched_cluster.add(item)

    return [c.items for c in clusters]

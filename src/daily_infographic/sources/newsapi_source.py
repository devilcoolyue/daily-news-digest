from __future__ import annotations

import json
import os
from datetime import datetime
import urllib.parse
import urllib.request

from ..models import NewsItem
from ..utils import in_window, now_utc, parse_datetime
from .base import SourceAdapter


class NewsApiSource(SourceAdapter):
    """Optional adapter. Requires NEWSAPI_KEY."""

    def fetch(self, since: datetime, until: datetime) -> list[NewsItem]:
        api_key = os.getenv("NEWSAPI_KEY", "").strip()
        if not api_key:
            return []

        query = self.cfg.params.get("query", "AI OR artificial intelligence")
        params = {
            "q": query,
            "from": since.isoformat(),
            "to": until.isoformat(),
            "language": self.cfg.params.get("language", "en"),
            "sortBy": "publishedAt",
            "pageSize": int(self.cfg.params.get("page_size", 50)),
            "apiKey": api_key,
        }
        url = "https://newsapi.org/v2/everything?" + urllib.parse.urlencode(params)

        try:
            with urllib.request.urlopen(url, timeout=20) as resp:
                payload = json.loads(resp.read().decode("utf-8"))
        except Exception:
            return []

        out: list[NewsItem] = []
        for article in payload.get("articles", []):
            title = (article.get("title") or "").strip()
            link = (article.get("url") or "").strip()
            summary = (article.get("description") or "").strip()
            published_at = parse_datetime(article.get("publishedAt")) or now_utc()

            if not title or not link:
                continue
            if not in_window(published_at, since, until):
                continue

            out.append(
                NewsItem(
                    source_id=self.cfg.id,
                    source_name=self.cfg.name,
                    source_tier=self.cfg.tier,
                    title=title,
                    summary=summary,
                    url=link,
                    published_at=published_at,
                    tags=list(self.cfg.tags),
                )
            )
        return out

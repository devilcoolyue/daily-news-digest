from __future__ import annotations

from datetime import datetime
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET

from ..models import NewsItem
from ..utils import in_window, now_utc, parse_datetime
from .base import SourceAdapter


REQUEST_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0 Safari/537.36"
    ),
    "Accept": "application/rss+xml, application/xml, text/xml, */*",
}


class RssSource(SourceAdapter):
    def fetch(self, since: datetime, until: datetime) -> list[NewsItem]:
        if not self.cfg.url:
            return []

        try:
            req = urllib.request.Request(self.cfg.url, headers=REQUEST_HEADERS)
            with urllib.request.urlopen(req, timeout=15) as resp:
                payload = resp.read()
        except (urllib.error.URLError, TimeoutError):
            return []

        try:
            root = ET.fromstring(payload)
        except ET.ParseError:
            return []

        if root.tag.endswith("rss"):
            return self._parse_rss(root, since, until)
        return self._parse_atom(root, since, until)

    def _parse_rss(self, root: ET.Element, since: datetime, until: datetime) -> list[NewsItem]:
        out: list[NewsItem] = []
        channel = root.find("channel")
        if channel is None:
            return out

        for item in channel.findall("item"):
            title = (item.findtext("title") or "").strip()
            link = (item.findtext("link") or "").strip()
            summary = (item.findtext("description") or "").strip()
            published_at = parse_datetime(item.findtext("pubDate")) or now_utc()
            if not in_window(published_at, since, until):
                continue
            if not title or not link:
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

    def _parse_atom(self, root: ET.Element, since: datetime, until: datetime) -> list[NewsItem]:
        out: list[NewsItem] = []
        ns = "{http://www.w3.org/2005/Atom}"

        for entry in root.findall(f"{ns}entry"):
            title = (entry.findtext(f"{ns}title") or "").strip()
            summary = (entry.findtext(f"{ns}summary") or entry.findtext(f"{ns}content") or "").strip()
            published_raw = entry.findtext(f"{ns}published") or entry.findtext(f"{ns}updated")
            published_at = parse_datetime(published_raw) or now_utc()
            if not in_window(published_at, since, until):
                continue

            link = ""
            for link_node in entry.findall(f"{ns}link"):
                rel = link_node.attrib.get("rel", "alternate")
                if rel == "alternate":
                    link = link_node.attrib.get("href", "")
                    break
            if not link and entry.find(f"{ns}id") is not None:
                link = entry.findtext(f"{ns}id", "")

            if not title or not link:
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

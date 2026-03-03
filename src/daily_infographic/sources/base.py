from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime

from ..models import NewsItem, SourceConfig


class SourceAdapter(ABC):
    def __init__(self, source_config: SourceConfig):
        self.cfg = source_config

    @abstractmethod
    def fetch(self, since: datetime, until: datetime) -> list[NewsItem]:
        raise NotImplementedError

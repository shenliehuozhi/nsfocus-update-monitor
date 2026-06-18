"""Base collector abstract class."""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class UnifiedContentItem:
    """Unified content item produced by any collector."""
    source_id: int
    source_type: str               # 'nsfocus', 'rss', 'wechat_mp'
    product_name: str
    version_branch: str            # e.g., 'V6.0.9'
    package_type: str              # e.g., 'sys', 'rule', 'nti'
    file_name: str
    package_version: str = ''
    md5_hash: str = ''
    file_size: int = 0
    description_raw: str = ''
    description_parsed: dict = field(default_factory=dict)
    min_sys_version: str = ''
    restart_required: bool = False
    urgency: str = 'normal'        # 'normal', 'high', 'critical'
    download_id: int = 0
    published_at: str = ''
    page_hash: str = ''
    source_url: str = ''           # detail page URL for quick-mode HEAD check
    path_id: str = ''              # MD5(source_url + JSON(chain))[:12], unique per (url, chain)

    def to_snapshot_dict(self) -> dict:
        return {
            'source_id': self.source_id,
            'product_name': self.product_name,
            'version_branch': self.version_branch,
            'package_type': self.package_type,
            'file_name': self.file_name,
            'package_version': self.package_version,
            'md5_hash': self.md5_hash,
            'file_size': self.file_size,
            'description_raw': self.description_raw,
            'description_parsed': self.description_parsed,
            'min_sys_version': self.min_sys_version,
            'restart_required': self.restart_required,
            'urgency': self.urgency,
            'download_id': self.download_id,
            'published_at': self.published_at,
            'page_hash': self.page_hash,
            'source_url': self.source_url,
            'path_id': self.path_id,
        }


@dataclass
class CollectorHealth:
    healthy: bool
    items_collected: int = 0
    errors: list = field(default_factory=list)
    duration_ms: int = 0


class BaseCollector(ABC):
    """Abstract collector. Subclass and implement collect()."""

    source_type: str = 'unknown'

    @abstractmethod
    def collect(self, source_id: int, session_cookie: str) -> list[UnifiedContentItem]:
        """Collect content items. Returns list of UnifiedContentItem."""
        ...

    def check_health(self, session_cookie: str) -> CollectorHealth:
        """Quick health check — try one request, verify structure."""
        return CollectorHealth(healthy=True)

"""单图下载的候选顺序与失败决策。"""

from dataclasses import dataclass
from enum import Enum, auto
from typing import Literal
from urllib.parse import SplitResult, urlsplit

DEFAULT_CDN_HOSTS = (
    "img1.doubanio.com",
    "img2.doubanio.com",
    "img3.doubanio.com",
    "img9.doubanio.com",
)
DEFAULT_REQUEST_INTERVAL_SECONDS = 3.0


@dataclass(frozen=True)
class DownloadCandidate:
    url: str
    source: Literal["raw", "raw_cdn", "normal"]


class FailureAction(Enum):
    NEXT_CANDIDATE = auto()
    STOP_IMAGE = auto()
    STOP_DOWNLOADS = auto()


def _http_url(url: str | None) -> SplitResult | None:
    if not url or any(character.isspace() for character in url):
        return None
    try:
        parts = urlsplit(url)
        if parts.scheme in ("http", "https") and parts.hostname:
            # 读取 port 可检测非法端口。
            if parts.port is None or 0 < parts.port <= 65535:
                return parts
    except ValueError:
        pass
    return None


@dataclass(frozen=True)
class DownloadPolicy:
    cdn_hosts: tuple[str, ...] = DEFAULT_CDN_HOSTS
    retry_interval_seconds: float = DEFAULT_REQUEST_INTERVAL_SECONDS

    def candidates(self, url_raw: str | None, url_normal: str | None) -> tuple[DownloadCandidate, ...]:
        candidates = {}
        raw = _http_url(url_raw)
        if raw is not None:
            candidates[url_raw] = DownloadCandidate(url_raw, "raw")
            if raw.hostname in self.cdn_hosts:
                port = f":{raw.port}" if raw.port is not None else ""
                for host in self.cdn_hosts:
                    if host == raw.hostname:
                        continue
                    url = raw._replace(netloc=host + port).geturl()
                    candidates.setdefault(url, DownloadCandidate(url, "raw_cdn"))
        if _http_url(url_normal) is not None:
            candidates.setdefault(url_normal, DownloadCandidate(url_normal, "normal"))
        return tuple(candidates.values())

    def failure_action(self, status_code: int | None, *, connection_failed=False,
                       invalid_image=False, access_challenge=False) -> FailureAction:
        if status_code == 429:
            return FailureAction.STOP_DOWNLOADS
        if access_challenge:
            return FailureAction.STOP_IMAGE
        if connection_failed or invalid_image or status_code in (404, 410):
            return FailureAction.NEXT_CANDIDATE
        if status_code is not None and 500 <= status_code <= 599:
            return FailureAction.NEXT_CANDIDATE
        return FailureAction.STOP_IMAGE

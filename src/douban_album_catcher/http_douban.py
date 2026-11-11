"""豆瓣页面解析与单图下载；不负责分页等待或数据库事务编排。"""

import os
import re
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urljoin, urlparse, urlsplit

import requests
from bs4 import BeautifulSoup

from .data_model import Album, Image
from .download_policy import DownloadCandidate, DownloadPolicy, FailureAction

headers = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/108.0.0.0 Safari/537.36"
}


class DoubanResponseError(Exception):
    """网络响应或页面结构不符合预期。"""


    def __init__(self, message, *, status_code=None):
        super().__init__(message)
        self.status_code = status_code


@dataclass(frozen=True)
class DownloadAttempt:
    candidate: DownloadCandidate
    status_code: int | None
    reason: str


class ImageDownloadError(DoubanResponseError):
    def __init__(self, message, attempts=()):
        self.attempts = tuple(attempts)
        status_code = self.attempts[-1].status_code if self.attempts else None
        details = "；".join(
            f"{attempt.candidate.source} {_log_url(attempt.candidate.url)}（{attempt.reason}）"
            for attempt in self.attempts
        )
        super().__init__(f"{message}：{details}" if details else message, status_code=status_code)


class DownloadRateLimited(ImageDownloadError):
    """服务端限流，停止本次任务的图片下载阶段。"""


def _get(url, *, referer=None):
    request_headers = headers.copy()
    if referer is not None:
        request_headers["Referer"] = referer
    response = requests.get(url, headers=request_headers, timeout=30)
    if response.status_code != 200:
        raise DoubanResponseError(
            f"请求 {url} 返回 HTTP {response.status_code}",
            status_code=response.status_code,
        )
    return response


def _page(url):
    response = _get(url)
    # 登录/验证跳转即使最终返回 200，也不能当作成功页面。
    final_url = getattr(response, "url", url)
    path = urlparse(final_url).path.lower()
    if any(part in path for part in ("captcha", "login", "passport")):
        raise DoubanResponseError(f"页面要求登录或验证：{final_url}")
    soup = BeautifulSoup(response.text, "lxml")
    if soup.select_one('input[name="captcha-solution"], input[name="captcha"]'):
        raise DoubanResponseError(f"页面要求验证码：{url}")
    return soup


def catch_album_info(url):
    url = urljoin(url, urlparse(url).path)
    soup = _page(url)
    count_element = soup.select_one("div.photitle span")
    if soup.title is None or count_element is None:
        raise DoubanResponseError("缺少相册标题或图片数量，可能是拦截页面")
    count_match = re.search(r"\d+", count_element.get_text())
    if count_match is None:
        raise DoubanResponseError("无法解析相册图片数量")
    description = soup.select_one("p.description")
    paginator = soup.select_one("div.paginator")
    count = int(count_match.group())
    page_total = 1 if count else 0
    if paginator is not None:
        current = paginator.select_one("span.thispage[data-total-page]")
        if current is None:
            raise DoubanResponseError("缺少分页总数")
        try:
            page_total = int(current["data-total-page"])
        except (TypeError, ValueError) as exc:
            raise DoubanResponseError("分页总数格式错误") from exc
    return Album(title=soup.title.get_text(strip=True), url=url,
                 description=description.get_text() if description else "",
                 count=count, page_size=18, page_total=page_total)


def catch_album_page(album, start):
    soup = _page(album.url + "?m_start=" + str(start))
    box = soup.select_one("div.photolst")
    if box is None:
        raise DoubanResponseError("缺少相册图片列表，可能是验证码或登录页面")
    images = []
    for index, item in enumerate(box.select("a.photolst_photo")):
        photo = item.find("img")
        if photo is None or not photo.get("src") or not item.get("href"):
            raise DoubanResponseError("分页图片缺少地址或详情链接")
        image_url = urljoin(album.url, photo["src"])
        print(f"采集图片信息：{image_url} - {photo["src"]}")
        name = urlparse(image_url).path.rsplit("/", 1)[-1]
        if not name:
            raise DoubanResponseError("图片文件名为空")
        note = catch_image_note(urljoin(album.url, item["href"]))
        images.append(Image(album_id=album.id, order_index=start + index + 1,
                            name=name, note=note, url_normal=image_url,
                            url_large=image_url.replace("/m/", "/l/"),
                            url_raw=image_url.replace("/m/", "/r/")))
    return images


def catch_image_note(image_url):
    soup = _page(image_url)
    if soup.select_one("#content") is None or soup.select_one(".photo-show, .photo-wp, #image") is None:
        raise DoubanResponseError("缺少图片详情结构，可能是拦截页面")
    description = soup.select_one("div.photo_descri")
    return description.get_text(strip=True) if description else ""


def _log_url(url):
    parts = urlsplit(url)
    return f"{parts.scheme}://{parts.hostname}{parts.path}"


def _is_access_url(url):
    parts = urlsplit(url)
    path = parts.path.lower()
    return parts.hostname == "accounts.douban.com" or any(
        part in path for part in ("captcha", "login", "passport")
    )


def _has_image_signature(content):
    return (
        content.startswith((b"\xff\xd8\xff", b"\x89PNG\r\n\x1a\n", b"GIF87a", b"GIF89a"))
        or (content.startswith(b"RIFF") and content[8:12] == b"WEBP")
    )


def _fetch_image(image, policy, wait):
    candidates = policy.candidates(image.url_raw, image.url_normal)
    if not candidates:
        raise ImageDownloadError("原图和普通图均没有有效的下载地址")

    attempts = []
    for index, candidate in enumerate(candidates):
        if index and policy.retry_interval_seconds > 0:
            wait(policy.retry_interval_seconds)
        try:
            response = _get(candidate.url, referer="https://www.douban.com/")
        except DoubanResponseError as exc:
            status = exc.status_code
            reason = f"HTTP {status}"
            action = policy.failure_action(status)
        except requests.RequestException as exc:
            status = None
            reason = type(exc).__name__
            action = policy.failure_action(
                status, connection_failed=isinstance(exc, (requests.ConnectionError, requests.Timeout))
            )
        else:
            status = response.status_code
            content = response.content
            content_type = response.headers.get("Content-Type", "").lower()
            is_html = "text/html" in content_type or content.lstrip()[:1] == b"<"
            challenge = _is_access_url(getattr(response, "url", None) or candidate.url)
            if is_html and not challenge:
                soup = BeautifulSoup(content, "lxml")
                challenge = soup.select_one(
                    'input[name="captcha-solution"], input[name="captcha"], '
                    'input[type="password"], form[action*="login"]'
                ) is not None
            if not challenge and not is_html and _has_image_signature(content):
                return candidate, content
            if challenge:
                reason = "要求登录或验证码"
            elif not content:
                reason = "响应为空"
            elif is_html:
                reason = "响应为 HTML"
            else:
                reason = "无法识别图片格式"
            action = policy.failure_action(status, invalid_image=True, access_challenge=challenge)

        attempts.append(DownloadAttempt(candidate, status, reason))
        if action is FailureAction.STOP_DOWNLOADS:
            raise DownloadRateLimited("图片下载遇到限流", attempts)
        if action is FailureAction.STOP_IMAGE:
            raise ImageDownloadError("停止下载当前图片", attempts)

    raise ImageDownloadError("图片下载候选已用尽", attempts)


def _update_file_size(image, size):
    updated = Image.update(file_size=size).where(Image.id == image.id).execute()
    if updated != 1:
        raise ImageDownloadError(f"无法更新图片大小记录：{image.name}")
    image.file_size = size


def _save_image(image, file_path, content):
    file_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=file_path.parent, suffix=".tmp", delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(content)
        if image.file_size is not None:
            _update_file_size(image, None)
        os.replace(temporary, file_path)
        temporary = None
        _update_file_size(image, len(content))
    finally:
        if temporary is not None:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                # 保留原始保存异常，便于定位失败原因。
                pass


def download_image(image, directory, *, policy=None, wait=None) -> bool:
    """下载成功返回 True，已有相同大小文件返回 False，失败抛出异常。"""
    file_index = f"{image.order_index:04d}"
    file_path = Path(directory) / (file_index + "-" + image.name)
    if image.file_size is not None and file_path.is_file():
        if image.file_size == file_path.stat().st_size:
            print(f"图片已存在，跳过下载：{file_index} - {image.name}")
            return False

    if policy is None:
        policy = DownloadPolicy()
    if wait is None:
        wait = time.sleep
    candidate, content = _fetch_image(image, policy, wait)
    _save_image(image, file_path, content)
    source = "normal，普通图回退" if candidate.source == "normal" else candidate.source
    print(f"图片下载成功：{file_path.name}；来源：{source}；地址：{_log_url(candidate.url)}；{len(content)} 字节")
    return True

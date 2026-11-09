"""豆瓣页面解析与单图下载；不负责分页等待或数据库事务编排。"""

import re
from pathlib import Path
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup

from .data_model import Album, Image

headers = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/108.0.0.0 Safari/537.36"
}


class DoubanResponseError(Exception):
    """网络响应或页面结构不符合预期。"""


def _get(url, *, referer=None):
    request_headers = headers.copy()
    if referer is not None:
        request_headers["Referer"] = referer
    response = requests.get(url, headers=request_headers, timeout=30)
    if response.status_code != 200:
        raise DoubanResponseError(f"请求 {url} 返回 HTTP {response.status_code}")
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


def download_image(image, directory) -> bool:
    """下载成功返回 True，已有相同大小文件返回 False，失败抛出异常。"""
    directory = Path(directory)
    file_index = f"{image.order_index:04d}"
    file_path = directory / (file_index + "-" + image.name)
    if image.file_size is not None and file_path.is_file():
        if image.file_size == file_path.stat().st_size:
            print(f"图片已存在，跳过下载：{file_index} - {image.name}")
            return False
    # 图片 CDN 会拒绝没有豆瓣来源信息的直链请求（HTTP 418）。
    response = _get(image.url_raw, referer="https://www.douban.com/")
    if "text/html" in response.headers.get("Content-Type", "").lower():
        raise DoubanResponseError(f"图片地址返回了 HTML：{image.url_raw}")
    if not response.content:
        raise DoubanResponseError(f"图片响应为空：{image.url_raw}")
    directory.mkdir(parents=True, exist_ok=True)
    file_path.write_bytes(response.content)
    # requests 可能解压响应，使用实际写入长度而非传输 Content-Length。
    size = len(response.content)
    updated = Image.update(file_size=size).where(Image.id == image.id).execute()
    if updated != 1:
        raise DoubanResponseError(f"图片已写入，但数据库更新失败：{image.name}")
    print(f"图片下载成功：{file_index} - {image.name}")
    return True

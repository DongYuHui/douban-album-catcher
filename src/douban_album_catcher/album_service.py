"""相册任务：分页采集全部成功后，下载本次采集的图片。"""

import datetime
import re
import time
from dataclasses import dataclass

from .config import AppSettings
from .data_model import Album, Image, db
from .download_policy import DownloadPolicy
from .http_douban import DownloadRateLimited, catch_album_info, catch_album_page, download_image


class AlbumTaskError(Exception):
    """任务未能完成，已提交的分页仍保留。"""


class AlbumTaskInterrupted(AlbumTaskError):
    """用户中断了任务。"""


@dataclass(frozen=True)
class DownloadResult:
    collected: int
    downloaded: int
    skipped: int
    failures: tuple[tuple[str, str], ...]


def _validate_album(album):
    for field in ("count", "page_total", "page_size"):
        if type(getattr(album, field)) is not int:
            raise AlbumTaskError(f"相册 {field} 必须是整数")
    if album.count < 0 or album.page_total < 0 or album.page_size <= 0:
        raise AlbumTaskError("相册数量或分页参数非法")
    expected = (album.count + album.page_size - 1) // album.page_size
    if album.count == 0:
        if album.page_total not in (0, 1):
            raise AlbumTaskError("空相册分页信息不一致")
    elif album.page_total != expected:
        raise AlbumTaskError("相册图片数与分页总数不一致，请重新采集")


def _save_page(album, images):
    ids = set()
    with db.atomic():
        for image in images:
            existing = Image.get_or_none(Image.name == image.name)
            if existing is not None and existing.album_id != album.id:
                raise AlbumTaskError(f"图片 {image.name} 已属于其他相册，无法覆盖")
            values = {
                field: getattr(image, field)
                for field in ("order_index", "url_normal", "url_large", "url_raw")
            }
            values.update(album_id=album.id, note=image.note or "",
                          modify_time=datetime.datetime.now())
            if existing is None:
                saved = Image.create(name=image.name, **values)
            else:
                if existing.url_raw != image.url_raw or existing.url_normal != image.url_normal:
                    values["file_size"] = None
                Image.update(**values).where(Image.id == existing.id).execute()
                saved = existing
            ids.add(saved.id)
    return ids


def _album_directory_name(album):
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', '', album.title).rstrip(" .")
    if not name:
        return f"相册-{album.id}"

    # Windows 也会将带扩展名的设备名视为保留名称。
    stem = name.split(".", 1)[0].rstrip(" ").upper()
    if re.fullmatch(r"(CON|PRN|AUX|NUL|CONIN\$|CONOUT\$|(?:COM|LPT)[1-9¹²³])", stem):
        return "_" + name
    return name


def run_album_task(url: str, settings: AppSettings, *, wait=None) -> DownloadResult:
    """同步执行一次任务；wait 可注入，避免测试真实等待。"""
    if wait is None:
        wait = time.sleep
    policy = DownloadPolicy(retry_interval_seconds=settings.request_interval_seconds)
    stage = "获取并保存相册信息"
    try:
        album = catch_album_info(url)
        if album is None:
            raise AlbumTaskError("未获取到相册信息")
        _validate_album(album)
        with db.atomic():
            existing = Album.get_or_none(Album.url == album.url)
            if existing is not None:
                album.id = existing.id
                album.create_time = existing.create_time
            album.modify_time = datetime.datetime.now()
            album.save()

        ids = set()
        print(f"开始采集分页，共 {album.page_total if album.count else 0} 页")
        if album.count:
            for index in range(album.page_total):
                stage = f"采集并保存第 {index + 1}/{album.page_total} 页"
                images = catch_album_page(album, index * album.page_size)
                if not images:
                    raise AlbumTaskError("分页没有返回应有的图片信息")
                ids.update(_save_page(album, images))
                print(f"第 {index + 1}/{album.page_total} 页已保存")
                if index + 1 < album.page_total and settings.page_query_interval_seconds > 0:
                    stage = f"第 {index + 1} 页后等待下一页"
                    print(f"等待 {settings.page_query_interval_seconds:g} 秒")
                    wait(settings.page_query_interval_seconds)
        stage = "检查采集完整性"
        if len(ids) != album.count:
            raise AlbumTaskError(f"本次采集 {len(ids)} 张，声明 {album.count} 张，数据不完整，请重新采集")

        if not ids:
            print("相册没有图片可下载")
            return DownloadResult(0, 0, 0, ())

        stage = "创建相册下载目录"
        directory = settings.download_directory / _album_directory_name(album)
        directory.mkdir(parents=True, exist_ok=True)

        # 不使用无界 IN 参数列表；按相册顺序流式扫描，过滤历史残留记录。
        print(f"全部分页已保存，开始下载 {len(ids)} 张图片")
        downloaded = skipped = 0
        failures = []
        images = Image.select().where(Image.album_id == album.id).order_by(Image.order_index, Image.id)
        for image in images.iterator():
            if image.id not in ids:
                continue
            stage = f"下载图片 {image.name}"
            try:
                if download_image(image, directory, policy=policy, wait=wait):
                    downloaded += 1
                else:
                    skipped += 1
            except DownloadRateLimited as exc:
                failures.append((image.name, str(exc)))
                remaining = len(ids) - downloaded - skipped - len(failures)
                print(f"下载失败：{image.name}；原因：{exc}；因限流未处理 {remaining} 张")
                break
            except Exception as exc:
                failures.append((image.name, str(exc)))
                print(f"下载失败：{image.name}；原因：{exc}")
        result = DownloadResult(len(ids), downloaded, skipped, tuple(failures))
        print(f"采集 {result.collected} 张；下载 {downloaded} 张；跳过 {skipped} 张；失败 {len(failures)} 张")
        return result
    except KeyboardInterrupt as exc:
        raise AlbumTaskInterrupted(f"{stage}：用户已中断任务") from exc
    except Exception as exc:
        raise AlbumTaskError(f"{stage}：{exc}") from exc

import argparse
import sys
from pathlib import Path

import peewee

from .config import ConfigError, ConfigStore, default_config_path
from .startup import load_startup_settings

from .data_model import Album, Image, init_database
from .http_douban import catch_album_info, catch_album_page, download_image

def main(argv=None) -> None:
    parser = argparse.ArgumentParser(description="豆瓣相册下载助手")
    parser.add_argument("--config", type=Path, help="指定配置文件路径（默认使用用户配置目录）")
    args = parser.parse_args(argv)
    store = ConfigStore(args.config if args.config is not None else default_config_path())
    try:
        settings = load_startup_settings(store)
        settings.database_path.parent.mkdir(parents=True, exist_ok=True)
        init_database(str(settings.database_path))
    except (ConfigError, OSError, peewee.PeeweeException) as exc:
        print(f"启动失败：{exc}\n配置文件：{store.path}", file=sys.stderr)
        raise SystemExit(1) from exc
    print(f"配置文件：{store.path}")
    print("提示：request_interval_seconds 已保存，但本阶段尚未接入请求限速。")

    # 获取相册
    # album_url = "https://www.douban.com/photos/album/1874833760"
    album_url = "https://www.douban.com/photos/album/1887978860"
    album_info = catch_album_info(album_url)
    if album_info == None:
        print("获取相册信息失败")
        sys.exit()
    album_query = Album.select().where(Album.url == album_info.url).first()
    if album_query != None:
        album_info.id = album_query.id
    album_info.save()

    # 获取图片
    for index in range(album_info.page_total):

        # 获取分页
        album_page = catch_album_page(album_info, index * 18)
        if album_page == None:
            print("获取相册分页失败")
            sys.exit()
        for image_item in album_page:
            image_query = Image.select().where(Image.name == image_item.name).first()
            if image_query == None:
                image_item.save()
            else:
                image_item.id = image_query.id
                image_item.file_size = image_query.file_size

        # 下载图片
        for image_item in album_page:
            try:
                download_image(image_item, settings.download_directory)
            except Exception as exc:
                print(f"下载图片异常：{image_item}；原因：{exc}", file=sys.stderr)


if __name__ == "__main__":
    main()

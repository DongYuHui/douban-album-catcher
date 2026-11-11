import argparse
import sys
from pathlib import Path

import peewee

from .album_service import AlbumTaskError, AlbumTaskInterrupted, run_album_task
from .config import ConfigError, ConfigStore, default_config_path
from .data_model import db, init_database
from .startup import load_startup_settings


def main(argv=None) -> None:
    parser = argparse.ArgumentParser(description="豆瓣相册下载助手")
    parser.add_argument("--config", type=Path, help="指定配置文件路径（默认使用用户配置目录）")
    args = parser.parse_args(argv)
    store = ConfigStore(args.config if args.config is not None else default_config_path())
    try:
        settings = load_startup_settings(store)
        settings.database_path.parent.mkdir(parents=True, exist_ok=True)
        init_database(str(settings.database_path))
        print(f"配置文件：{store.path}")
        print("提示：request_interval_seconds 控制同一张图的备用地址请求间隔；page_query_interval_seconds 控制分页间隔。")
        # album_url = "https://www.douban.com/photos/album/1887978860"
        album_url = "https://www.douban.com/photos/album/1691221449"
        result = run_album_task(album_url, settings)
        if result.failures:
            raise SystemExit(1)
    except (KeyboardInterrupt, AlbumTaskInterrupted) as exc:
        print(f"任务中断：{exc}", file=sys.stderr)
        raise SystemExit(130) from exc
    except (ConfigError, OSError, peewee.PeeweeException, AlbumTaskError) as exc:
        print(f"任务失败：{exc}\n配置文件：{store.path}", file=sys.stderr)
        raise SystemExit(1) from exc
    finally:
        if not db.is_closed():
            db.close()


if __name__ == "__main__":
    main()

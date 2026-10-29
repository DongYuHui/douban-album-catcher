"""用户配置的默认值、校验及持久化，不依赖界面或下载服务。"""

import json
import math
import os
import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path

from platformdirs import user_config_path, user_data_path, user_pictures_path

APP_NAME = "Douban Album Catcher"
SCHEMA_VERSION = 1


class ConfigError(Exception):
    """配置无法读取、校验或保存。"""


@dataclass(frozen=True)
class AppSettings:
    schema_version: int
    database_path: Path
    download_directory: Path
    request_interval_seconds: float


def default_config_path() -> Path:
    return user_config_path(APP_NAME, appauthor=False) / "config.json"


def default_settings() -> AppSettings:
    return AppSettings(
        schema_version=SCHEMA_VERSION,
        database_path=user_data_path(APP_NAME, appauthor=False) / "album.db",
        download_directory=user_pictures_path() / "DoubanAlbums",
        request_interval_seconds=3.0,
    )


def _path(value: object, field: str, *, directory: bool) -> Path:
    if not isinstance(value, str) or not value.strip() or "\0" in value:
        raise ConfigError(f"{field} 必须是非空且不含空字符的路径字符串")
    try:
        path = Path(value).expanduser()
        if not path.is_absolute():
            raise ConfigError(f"{field} 必须是绝对路径：{value}")
        if path.exists() and (not path.is_dir() if directory else not path.is_file()):
            kind = "目录" if directory else "文件"
            raise ConfigError(f"{field} 必须指向{kind}：{path}")
        # 检查最近的已存在父级，避免把文件当作目录使用。
        for parent in path.parents:
            if parent.exists():
                if not parent.is_dir():
                    raise ConfigError(f"{field} 的父路径不是目录：{parent}")
                break
        return path
    except (OSError, RuntimeError, ValueError) as exc:
        raise ConfigError(f"{field} 路径不可用：{exc}") from exc


def _settings(values: object) -> AppSettings:
    if not isinstance(values, dict):
        raise ConfigError("配置 JSON 顶层必须是对象")
    fields = set(AppSettings.__dataclass_fields__)
    unknown = values.keys() - fields
    if unknown:
        raise ConfigError(f"未知配置字段：{', '.join(sorted(unknown))}")
    defaults = asdict(default_settings())
    defaults.update(values)
    version = defaults["schema_version"]
    if type(version) is not int or version != SCHEMA_VERSION:
        raise ConfigError(f"不支持的 schema_version：{version}，当前仅支持 {SCHEMA_VERSION}")
    interval = defaults["request_interval_seconds"]
    try:
        valid_interval = type(interval) in (int, float) and math.isfinite(interval) and interval >= 0
    except OverflowError:
        valid_interval = False
    if not valid_interval:
        raise ConfigError("request_interval_seconds 必须是有限的非负数（秒）")
    # 默认值中的 Path 与 JSON 中的字符串走同一套校验。
    return AppSettings(
        schema_version=version,
        database_path=_path(values.get("database_path", str(defaults["database_path"])), "database_path", directory=False),
        download_directory=_path(values.get("download_directory", str(defaults["download_directory"])), "download_directory", directory=True),
        request_interval_seconds=float(interval),
    )


def _serialize(settings: AppSettings) -> dict:
    values = asdict(settings)
    for field in ("database_path", "download_directory"):
        value = values[field]
        if not isinstance(value, Path):
            raise ConfigError(f"{field} 在配置对象中必须是 Path")
        values[field] = str(value)
    return values


class ConfigStore:
    def __init__(self, path: Path):
        self.path = Path(path).expanduser().absolute()

    def load(self) -> AppSettings:
        try:
            with self.path.open(encoding="utf-8") as stream:
                values = json.load(stream)
        except FileNotFoundError:
            # 悬空链接不是首次运行，应保留并报告。
            if self.path.is_symlink():
                raise ConfigError(f"配置文件是无效的符号链接：{self.path}")
            settings = _settings({})
            self.save(settings)
            return settings
        except (OSError, ValueError) as exc:
            raise ConfigError(f"无法读取配置 {self.path}：{exc}；请修正文件，原文件未被覆盖") from exc
        return _settings(values)

    def save(self, settings: AppSettings) -> None:
        values = _serialize(settings)
        checked = _settings(values)
        temporary = None
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with tempfile.NamedTemporaryFile(
                mode="w", encoding="utf-8", dir=self.path.parent,
                prefix=f".{self.path.name}.", suffix=".tmp", delete=False,
            ) as stream:
                temporary = Path(stream.name)
                json.dump(_serialize(checked), stream, ensure_ascii=False, indent=2, allow_nan=False)
                stream.write("\n")
            os.replace(temporary, self.path)
        except (OSError, ValueError) as exc:
            raise ConfigError(f"无法保存配置 {self.path}：{exc}") from exc
        finally:
            if temporary is not None:
                try:
                    temporary.unlink(missing_ok=True)
                except OSError:
                    # 清理失败不应遮蔽原始的写入/替换错误。
                    pass

"""首次启动的旧资源选择；界面交互与配置存储分离。"""

import sys
from dataclasses import replace
from pathlib import Path

from .config import AppSettings, ConfigError, ConfigStore, default_settings


def legacy_resources(roots: list[Path]) -> tuple[list[Path], list[Path]]:
    databases, directories = [], []
    seen = set()
    for root in roots:
        root = root.resolve()
        if root in seen:
            continue
        seen.add(root)
        database, directory = root / "album.db", root / "image"
        if database.is_file():
            databases.append(database)
        if directory.is_dir():
            directories.append(directory)
    return databases, directories


def _choose(label: str, candidates: list[Path], default: Path) -> Path:
    print(f"请选择{label}（不会搬迁已有数据）：")
    print(f"  0：使用默认位置 {default}")
    for index, path in enumerate(candidates, start=1):
        print(f"  {index}：继续使用 {path}")
    while True:
        try:
            answer = input("请输入编号（不输入则取消）：").strip()
        except EOFError as exc:
            raise ConfigError("输入已结束，首次配置未保存") from exc
        if not answer:
            raise ConfigError("已取消，首次配置未保存")
        if answer.isdecimal() and 0 <= int(answer) <= len(candidates):
            return default if int(answer) == 0 else candidates[int(answer) - 1]
        print("编号无效，请重新输入。")


def load_startup_settings(store: ConfigStore) -> AppSettings:
    if store.path.exists() or store.path.is_symlink():
        return store.load()
    roots = [Path.cwd()]
    # 只有源码检出目录才作为旧项目目录检查，不检查安装环境的 site-packages。
    project = Path(__file__).resolve().parents[2]
    if (project / "pyproject.toml").is_file():
        roots.append(project)
    databases, directories = legacy_resources(roots)
    if not databases and not directories:
        return store.load()
    print("发现旧版资源：")
    for path in databases + directories:
        print(f"  {path}")
    if not sys.stdin.isatty():
        raise ConfigError(
            f"非交互运行不会自动选择数据位置。请在终端启动并选择，"
            f"或手动创建 {store.path}，填写绝对路径 database_path 和 download_directory。"
        )
    settings = default_settings()
    if databases:
        settings = replace(settings, database_path=_choose("数据库", databases, settings.database_path))
    if directories:
        settings = replace(settings, download_directory=_choose("下载目录", directories, settings.download_directory))
    store.save(settings)
    return settings

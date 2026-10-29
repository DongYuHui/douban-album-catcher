# 豆瓣相册下载助手

用于下载豆瓣相册内容。当前提供脚本下载功能，后续计划使用 tkinter 实现 macOS、Windows 图形界面。

## 开发环境

使用 [uv](https://docs.astral.sh/uv/getting-started/installation/) 管理 Python、虚拟环境和依赖，开发版本固定为 Python 3.14。

在项目根目录执行：

```sh
uv sync --locked
```

uv 会根据 `.python-version` 选择 Python，并按 `uv.lock` 安装依赖到 `.venv`。如果缺少指定 Python，可先执行 `uv python install 3.14`。

## 运行

```sh
uv run douban-album-catcher
```

也可以使用模块入口：

```sh
uv run python -m douban_album_catcher
```

运行会访问豆瓣并下载图片。当前相册地址仍在 `src/douban_album_catcher/main.py` 中设置；数据库 `album.db` 和图片目录 `image/` 仍相对于运行时的工作目录。建议始终在项目根目录运行，以继续使用原有数据。

原根目录下的 `main.py`、`data_model.py` 和 `http_douban.py` 已迁入包中。原 `http_douban.py` 的重复下载入口统一到上述命令；模块导入本身不会启动下载。

## 项目结构

```text
.
├── .python-version
├── pyproject.toml
├── uv.lock
├── src/
│   └── douban_album_catcher/
│       ├── __init__.py
│       ├── __main__.py
│       ├── main.py
│       ├── data_model.py
│       └── http_douban.py
└── docsLocal/
    └── 规划/
```

## 依赖管理与构建

依赖声明统一放在 `pyproject.toml`，精确版本由 `uv.lock` 记录，不再维护 `requirements.txt`。

```sh
# 添加运行依赖
uv add 包名

# 添加开发依赖
uv add --dev 包名

# 按声明更新锁定版本并同步环境
uv lock --upgrade
uv sync --locked

# 构建 Python wheel 和源码包
uv build
```

依赖变更后应同时提交 `pyproject.toml` 和 `uv.lock`。当前依赖已更新至 2026-10-29 查询到的最新稳定版本，并限制主要版本；精确版本见锁文件。Python 最低版本为 3.14。

| 直接依赖 | 当前锁定版本 |
| --- | --- |
| requests | 2.34.2 |
| beautifulsoup4 | 4.15.0 |
| lxml | 6.1.3 |
| peewee | 4.5.3 |

Peewee 已从 3.x 升级到 4.x。升级验证使用临时 SQLite 数据库检查建表、增查改、重新打开及模拟图片下载，不会修改用户数据库。

`uv build` 生成的 Python 包尚不是独立的 macOS/Windows 桌面安装程序。配置管理与 GUI 仍处于规划阶段，见 `docsLocal/规划/`；规划中的源码文件现位于 `src/douban_album_catcher/`，依赖清单应改为 `pyproject.toml`。

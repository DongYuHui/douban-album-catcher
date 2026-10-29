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

运行会访问豆瓣并下载图片。当前相册地址仍在 `src/douban_album_catcher/main.py` 中设置；数据库和下载目录通过配置文件指定。

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
│       ├── config.py
│       ├── startup.py
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
| platformdirs | 4.14.0 |

Peewee 已从 3.x 升级到 4.x。升级验证使用临时 SQLite 数据库检查建表、增查改、重新打开及模拟图片下载，不会修改用户数据库。

`uv build` 生成的 Python 包尚不是独立的 macOS/Windows 桌面安装程序。GUI 仍处于规划阶段，配置模块已实现。

## 配置文件

首次运行自动创建 JSON 配置，路径通过 [platformdirs](https://platformdirs.readthedocs.io/en/stable/explanation.html) 获取。典型位置：

| 平台 | 配置文件 |
| --- | --- |
| macOS | `~/Library/Application Support/DoubanAlbumCatcher/config.json` |
| Windows | `%LOCALAPPDATA%\DoubanAlbumCatcher\config.json` |

启动时会打印实际配置位置。也可以指定其他配置文件：

```sh
uv run douban-album-catcher --config /absolute/path/config.json
```

以下为 macOS 配置示例，请替换为自己电脑上的绝对路径；Windows JSON 中的反斜杠需写为 `\\`，也可以使用 `/`。

```json
{
  "schema_version": 1,
  "database_path": "/Users/example/Library/Application Support/DoubanAlbumCatcher/album.db",
  "download_directory": "/Users/example/Pictures/DoubanAlbums",
  "request_interval_seconds": 3.0
}
```

- `database_path`：数据库文件路径，默认位于用户应用数据目录。
- `download_directory`：下载目录，默认是用户图片目录下的 `DoubanAlbums`。
- `request_interval_seconds`：有限的非负数，单位为秒。**当前仅保存和校验，尚未接入网络限速**，不能靠该值防止触发反爬限制。
- `schema_version`：当前为整数 `1`，暂不支持其他版本。

关闭程序后修改 JSON，重新启动生效。路径支持 `~`，展开后必须为绝对路径。缺失字段使用默认值；拼错的未知字段、非法值和损坏 JSON 会导致启动失败，原文件保留，不会自动覆盖或切换到新数据库。修正文件即可重试。若需要恢复默认配置，请先备份原文件，再移走它并重新启动。

配置保存使用同目录临时文件写入后替换。数据库父目录在启动时创建，下载目录在实际下载时创建；权限和磁盘不可用等错误会提示原因。

### 旧数据与切换路径

首次运行会检查当前工作目录和源码项目目录中的 `album.db`、`image/`。若发现旧资源，会显示绝对路径并要求逐项选择继续使用旧位置或使用默认位置；输入为空则取消。非交互环境会停止，需先在终端选择，或手动创建配置文件填写路径。其他位置的旧资源需要手动指定。

已有配置时直接遵从配置，不再探测旧目录。切换路径不会搬迁数据库或图片；如果选择新的数据库文件，程序会创建空库。保留旧路径即可继续使用旧数据。

## 验证

```sh
uv run python -m unittest discover -s tests -v
```

测试使用临时配置、数据库和模拟响应，不访问豆瓣，不修改真实用户配置。已在 macOS / Python 3.14 验证；Windows 尚未实测。

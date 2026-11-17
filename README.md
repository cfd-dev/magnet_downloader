# 磁力下载器 Magnet Downloader

基于 [libtorrent](https://libtorrent.org/) 的通用 BitTorrent 下载工具，提供**命令行**和**图形界面**两种用法。图形界面版支持多任务并发、系统托盘、断点续传与任务状态恢复，并可打包为**单文件绿色 exe**。

> 本工具不内置任何具体资源，下载内容的合法性与版权风险由使用者自行判断和承担。

## 功能特性

- 同时添加多个磁力链接 / `.torrent` 文件并行下载，每个任务独立线程、共享一个 libtorrent 会话
- 磁力链接优先从公共种子缓存（itorrents.org）按 infohash 拉取元数据，**校验 infohash 一致**后才使用，失败自动回退 DHT / tracker
- 缓存命中的 `.torrent` 落盘保存，恢复任务时**优先读本地种子**，减少网络依赖
- 任务实时显示进度、下载/上传速度、大小、节点/种子数与保存目录
- 停止、重启、移除、全部停止；停止/退出后重开可**断点续传**
- 关闭窗口时可选择「最小化到托盘」或「退出程序」；托盘支持双击恢复、右键菜单
- 退出时自动保存进行中的任务，下次启动自动恢复并继续下载
- 自定义专属图标，应用于 exe 文件、窗口标题栏和系统托盘
- 单文件绿色 exe，无需安装 Python 或任何附加文件

## 快速开始

### 方式一：GUI 版（推荐）

从 Releases 下载 `磁力下载器.exe`，双击运行，然后：

1. 粘贴磁力链接，或点「浏览…」选择 `.torrent` 文件
2. 选择保存目录（默认 `downloads`）
3. 点「添加任务」，可继续添加多个任务并行下载
4. 关闭窗口时可选择最小化到托盘，双击托盘图标恢复

### 方式二：源码运行

需要 Python 3.9+（GUI 版需要自带 tkinter 的发行版，例如 [python.org 官方安装包](https://www.python.org/downloads/)）：

```bash
pip install libtorrent

# 图形界面
python magnet_download_gui.py

# 命令行：磁力链接或 .torrent 文件，第二个参数可选保存目录
python magnet_download.py "magnet:?xt=urn:btih:..." ./downloads
python magnet_download.py "example.torrent" ./downloads
```


## 打包单文件 exe

构建环境：Windows + Python 3.9（需含 tkinter 与 PyInstaller）。注意：部分精简版/内置 Python 发行版不带 tkinter，会导致 PyInstaller 报 `tkinter installation is broken`，请使用 python.org 官方发行版。

```bash
pip install libtorrent pyinstaller

# 可选：下载 UPX 并解压，可显著减小体积（libtorrent.pyd 约 13 MB → 3.7 MB）
# https://github.com/upx/upx/releases

python -m PyInstaller --noconfirm --onefile --windowed --clean ^
  --icon app_icon.ico --add-data "app_icon.ico;." ^
  --upx-dir "path\to\upx-5.0.2-win64" ^
  --name "磁力下载器" magnet_download_gui.py
```

产物在 `dist/磁力下载器.exe`，约 12.7 MB，单文件即可运行。

图标源文件在 `app_icon.png` / `app_icon.ico`，由 `make_icon.py` 用 Pillow 绘制生成（1024px 超采样后缩至 256px，含 16～256 多尺寸）。想改配色或形状可修改脚本后重跑：

```bash
pip install pillow
python make_icon.py
```

## 文件与数据目录

| 路径 | 说明 |
| --- | --- |
| `magnet_download.py` | 命令行下载核心：种子缓存、infohash 校验、DHT 回退、会话与下载循环 |
| `magnet_download_gui.py` | tkinter 图形界面：多任务调度、托盘、持久化、图标加载 |
| `make_icon.py` | 图标生成脚本 |
| `磁力下载器_torrents/` | 缓存命中的 `.torrent` 文件，按 infohash 命名 |
| `磁力下载器_tasks.json` | 进行中的任务列表（id、链接、保存目录） |

后两项默认保存在 **exe 所在目录**；目录不可写时自动退到 `%APPDATA%\磁力下载器\`。备份时把种子缓存目录和任务 JSON 一起带上，即可完整恢复任务。删除任务 JSON 则下次启动不再自动恢复。

## 使用提示

- 磁力链接首次获取元数据可能较慢：优先查缓存，未命中则走 DHT，缓存请求超时为 90 秒，期间界面显示「准备中」属正常现象。
- 移除任务**不会删除已下载的文件**；停止后点「重启选中」可从原目录校验并续传。
- 添加两个完全相同 infohash 的任务，libtorrent 会报重复，该任务会显示「出错」——这是预期行为。
- 下载速度取决于做种节点数量与网络环境；公共 tracker/DHT 在部分网络下可能受限。

## 许可证

[MIT](LICENSE) © 2026 nianhuawong

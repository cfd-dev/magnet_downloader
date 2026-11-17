#!/usr/bin/env python3
"""
通用磁力链接 / .torrent 下载脚本（基于 libtorrent）

用法:
    python magnet_download.py "magnet:?xt=urn:btih:...." [保存目录]
    python magnet_download.py "xxx.torrent"             [保存目录]

依赖:
    pip install libtorrent

说明:
    - 这是通用下载工具，不内置任何具体资源；下载内容的合法性由使用者自行负责。
    - 磁力链接会优先从公共种子缓存 (itorrents.org) 按 infohash 拉取 .torrent，
      以跳过慢速的 DHT 元数据阶段；失败则自动回退到 DHT / tracker 发现。
    - 从缓存拿到的种子会校验 infohash 一致后才使用，不匹配则丢弃。
    - 支持断点续传：中断后重新运行同一个链接/种子即可继续。
    - Ctrl+C 可随时暂停。
"""
import os
import sys
import time
import warnings
import urllib.request

# 该 libtorrent 构建只提供旧式 Boost.Python API（无 settings_pack 等新版接口），
# 这些 API 会抛 DeprecationWarning 但仍可正常工作、且没有新版替代，
# 因此在导入库前统一屏蔽，避免控制台刷屏。
warnings.filterwarnings("ignore", category=DeprecationWarning)

import libtorrent as lt

# 公共 tracker，用于辅助发现做种节点（磁力链接首次获取元数据时尤其有用）
TRACKERS = [
    "udp://tracker.opentrackr.org:1337/announce",
    "udp://open.stealth.si:80/announce",
    "udp://tracker.torrent.eu.org:451/announce",
    "udp://tracker.theoks.net:6969/announce",
    "udp://tracker-udp.gbitt.info:80/announce",
    "udp://tracker.leechers-paradise.org:6969/announce",
    "udp://9.rarbg.to:2710/announce",
    "udp://exodus.desync.com:6969/announce",
    "http://tracker.openbittorrent.com:80/announce",
    "udp://tracker.internetwarriors.net:1337/announce",
    "udp://ipv4.tracker.harry.lu:80/announce",
]

# DHT 引导节点
DHT_ROUTERS = [
    "router.bittorrent.com:6881",
    "router.utorrent.com:6881",
    "dht.transmissionbt.com:6881",
    "dht.aelitis.com:6881",
    "dht.libtorrent.org:25401",
]

# 公共种子缓存：按 infohash 拉取 .torrent，跳过 DHT 元数据阶段。
# {hash} 会被替换为 40 位十六进制 infohash。可按需增删其他镜像。
TORRENT_CACHES = [
    "https://itorrents.org/torrent/{hash}.torrent",
]


def human(n):
    """把字节数转成易读字符串"""
    n = float(n)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024:
            return f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} PB"


def make_session():
    session = lt.session()
    session.listen_on(6881, 6891)
    for router in DHT_ROUTERS:
        host, port = router.split(":")
        try:
            session.add_dht_router(host, int(port))
        except Exception:
            pass
    session.start_dht()
    return session


def magnet_to_hash(magnet):
    """从磁力链接解析出 40 位十六进制 infohash（兼容 hex / base32 两种 btih）。"""
    try:
        return str(lt.parse_magnet_uri(magnet).info_hash).lower()
    except Exception:
        return None


def fetch_from_cache(infohash, save_dir=None):
    """
    从公共种子缓存拉取 .torrent；infohash 校验通过则返回 torrent_info，否则 None。
    save_dir 不为空时，把校验通过的种子文件存为 {infohash}.torrent（保存失败不影响下载）。
    """
    for tpl in TORRENT_CACHES:
        url = tpl.format(hash=infohash)
        print(f"缓存：尝试 {url} ...")
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=90) as resp:
                data = resp.read()
            if not data.startswith(b"d"):  # bencode 字典应以 'd' 开头
                print("缓存：返回内容不是有效种子文件，跳过。")
                continue
            info = lt.torrent_info(data)
            if str(info.info_hash()).lower() != infohash.lower():
                print("缓存：infohash 不匹配，已丢弃（防止拿到错误资源）。")
                continue
            print("缓存：获取成功且 infohash 校验通过。")
            if save_dir:
                try:
                    os.makedirs(save_dir, exist_ok=True)
                    path = os.path.join(save_dir, f"{infohash}.torrent")
                    with open(path, "wb") as f:
                        f.write(data)
                    print(f"缓存：种子文件已保存到 {path}")
                except Exception as e:
                    print(f"缓存：种子文件保存失败（{e}），不影响下载。")
            return info
        except Exception as e:
            print(f"缓存：获取失败（{e}）。")
    return None


def add_target(session, target, save_path):
    """返回 (handle, torrent_info)。磁力链接优先走缓存，失败回退 DHT。"""
    if target.startswith("magnet:"):
        infohash = magnet_to_hash(target)
        if infohash:
            info = fetch_from_cache(infohash)
            if info is not None:
                handle = session.add_torrent(
                    {"ti": info, "save_path": save_path, "trackers": TRACKERS}
                )
                return handle, info

        # 回退：从 DHT / tracker 获取元数据
        print("磁力链接：缓存未命中，改为从 DHT / tracker 获取元数据（可能需几分钟）...")
        handle = lt.add_magnet_uri(
            session, target, {"save_path": save_path, "trackers": TRACKERS}
        )
        deadline = time.time() + 420
        while not handle.has_metadata():
            if time.time() > deadline:
                print("超时：未找到可用的做种节点（该资源可能无种子或网络/DHT 受限）。")
                sys.exit(2)
            st = handle.status()
            print(
                f"  DHT 节点 {session.status().dht_nodes} | peers {st.num_peers} | 等待元数据 ..."
            )
            time.sleep(10)
        return handle, handle.get_torrent_info()

    if os.path.isfile(target) and target.lower().endswith(".torrent"):
        info = lt.torrent_info(target)
        handle = session.add_torrent({"ti": info, "save_path": save_path, "trackers": TRACKERS})
        return handle, info

    print("无法识别的目标：请传入 magnet: 链接或 .torrent 文件路径。")
    sys.exit(1)


def main():
    if len(sys.argv) < 2:
        print("用法: python magnet_download.py <magnet链接 或 .torrent文件> [保存目录]")
        sys.exit(1)

    target = sys.argv[1]
    save_path = sys.argv[2] if len(sys.argv) > 2 else os.path.join(os.getcwd(), "downloads")
    os.makedirs(save_path, exist_ok=True)

    session = make_session()
    handle, info = add_target(session, target, save_path)

    print(f"名称   : {info.name()}")
    print(f"大小   : {human(info.total_size())}")
    print(f"文件数 : {info.num_files()}")
    print(f"保存至 : {save_path}")
    print("开始下载 ...（Ctrl+C 暂停，重跑同链接可续传）")

    last_pct = -1
    try:
        while not handle.is_finished():
            st = handle.status()
            pct = int(st.progress * 100)
            if pct != last_pct:
                try:
                    seeds = st.list_seeds
                except Exception:
                    seeds = "?"
                print(
                    f"  {pct:3d}% | 下载 {human(st.download_rate)}/s | "
                    f"上传 {human(st.upload_rate)}/s | peers {st.num_peers} | seeds {seeds}"
                )
                last_pct = pct
            time.sleep(10)
    except KeyboardInterrupt:
        print("\n已暂停，进度已保存；重新运行同一链接即可续传。")
        sys.exit(0)

    print("下载完成！")
    print(f"文件保存在: {save_path}")


if __name__ == "__main__":
    main()

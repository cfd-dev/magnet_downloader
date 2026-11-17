#!/usr/bin/env python3
"""
磁力链接 / .torrent 下载器 —— 多任务图形界面版
基于 tkinter（Python 内置）+ libtorrent，下载核心逻辑复用 magnet_download.py。

特点：
    - 可同时添加多个任务并行下载，每个任务独立线程、共享一个 DHT 会话。
    - 任务列表实时显示状态、进度、速度、节点/种子、大小与保存目录。
    - 支持单独停止/移除/重启任一任务，也支持全部停止；停止后可一键重启续传。
    - 磁力链接优先从公共种子缓存按 infohash 拉取元数据，失败回退 DHT。
"""
import os
import sys
import time
import json
import ctypes
import threading
import queue
import tkinter as tk
from ctypes import wintypes
from tkinter import ttk, filedialog, messagebox

import libtorrent as lt

import magnet_download as core


APP_TITLE = "磁力下载器（多任务）"
DEFAULT_SAVE = os.path.join(os.getcwd(), "downloads")

# libtorrent 会话的添加/删除需要串行化；状态查询是线程安全的。
SESSION_LOCK = threading.Lock()

PHASE_TEXT = {
    "preparing": "准备中",
    "downloading": "下载中",
    "done": "已完成",
    "stopped": "已停止",
    "error": "出错",
}


def get_data_dir():
    """任务状态与本地种子统一放在 exe（或脚本）旁边；不可写时退回 %APPDATA%。"""
    if getattr(sys, "frozen", False):
        base = os.path.dirname(os.path.abspath(sys.executable))
    else:
        base = os.path.dirname(os.path.abspath(__file__))
    if os.access(base, os.W_OK):
        return base
    folder = os.path.join(os.environ.get("APPDATA") or os.path.expanduser("~"), "磁力下载器")
    os.makedirs(folder, exist_ok=True)
    return folder


def torrent_dir():
    """按 infohash 命名的本地 .torrent 缓存目录。"""
    return os.path.join(get_data_dir(), "磁力下载器_torrents")


def resource_path(name):
    """打包后从临时目录取内置资源，源码运行时从脚本目录取。"""
    if getattr(sys, "frozen", False):
        base = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(sys.executable)))
    else:
        base = os.path.dirname(os.path.abspath(__file__))
    return os.path.join(base, name)


class QueueWriter:
    """把一行行日志放进队列，并加上任务前缀。"""

    def __init__(self, q, prefix=""):
        self.q = q
        self.prefix = prefix
        self.buf = ""

    def write(self, s):
        if not s:
            return
        self.buf += s
        while "\n" in self.buf:
            line, self.buf = self.buf.split("\n", 1)
            self.q.put(f"{self.prefix} {line.rstrip()}".strip())

    def flush(self):
        if self.buf:
            self.q.put(f"{self.prefix} {self.buf.rstrip()}".strip())
            self.buf = ""


class ThreadStdout:
    """
    全局 sys.stdout 替代品：按当前线程把 print 分发给对应任务的日志队列。
    多个下载线程同时调用 core 里的 print 也不会串台。
    """

    def __init__(self, logq):
        self.logq = logq
        self.writers = {}
        self.lock = threading.Lock()
        self.default = QueueWriter(logq, "[系统]")

    def register(self, prefix):
        writer = QueueWriter(self.logq, prefix)
        with self.lock:
            self.writers[threading.get_ident()] = writer
        return writer

    def unregister(self):
        with self.lock:
            self.writers.pop(threading.get_ident(), None)

    def _writer(self):
        with self.lock:
            return self.writers.get(threading.get_ident(), self.default)

    def write(self, s):
        self._writer().write(s)

    def flush(self):
        self._writer().flush()

    def isatty(self):
        return False

    @property
    def encoding(self):
        return "utf-8"


# ---------- 系统托盘（Windows 纯 ctypes 实现，不增加任何依赖） ----------
if sys.platform == "win32":
    _user32 = ctypes.WinDLL("user32", use_last_error=True)
    _shell32 = ctypes.WinDLL("shell32", use_last_error=True)
    _kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

    # 显式用指针宽度定义 WPARAM/LPARAM，避免 32/64 位截断。
    _LPARAM = ctypes.c_ssize_t
    _WPARAM = ctypes.c_size_t
    _WNDPROC = ctypes.WINFUNCTYPE(
        ctypes.c_ssize_t, wintypes.HWND, wintypes.UINT, _WPARAM, _LPARAM
    )

    class _WNDCLASSW(ctypes.Structure):
        _fields_ = [
            ("style", wintypes.UINT),
            ("lpfnWndProc", _WNDPROC),
            ("cbClsExtra", ctypes.c_int),
            ("cbWndExtra", ctypes.c_int),
            ("hInstance", ctypes.c_void_p),
            ("hIcon", ctypes.c_void_p),
            ("hCursor", ctypes.c_void_p),
            ("hbrBackground", ctypes.c_void_p),
            ("lpszMenuName", wintypes.LPCWSTR),
            ("lpszClassName", wintypes.LPCWSTR),
        ]

    class _NOTIFYICONDATAW(ctypes.Structure):
        _fields_ = [
            ("cbSize", wintypes.DWORD),
            ("hWnd", wintypes.HWND),
            ("uID", wintypes.UINT),
            ("uFlags", wintypes.UINT),
            ("uCallbackMessage", wintypes.UINT),
            ("hIcon", ctypes.c_void_p),
            ("szTip", wintypes.WCHAR * 128),
            ("dwState", wintypes.DWORD),
            ("dwStateMask", wintypes.DWORD),
            ("szInfo", wintypes.WCHAR * 256),
            ("uVersion", wintypes.UINT),
            ("szInfoTitle", wintypes.WCHAR * 64),
            ("dwInfoFlags", wintypes.DWORD),
            ("guidItem", ctypes.c_byte * 16),
            ("hBalloonIcon", ctypes.c_void_p),
        ]

    _NIM_ADD, _NIM_DELETE = 0, 2
    _NIF_MESSAGE, _NIF_ICON, _NIF_TIP = 1, 2, 4
    _WM_LBUTTONDBLCLK, _WM_RBUTTONUP, _WM_QUIT = 0x0203, 0x0205, 0x0012
    _TRAY_MSG = 0x0400 + 100

    _user32.RegisterClassW.argtypes = [ctypes.POINTER(_WNDCLASSW)]
    _user32.RegisterClassW.restype = ctypes.c_ushort  # ATOM
    _user32.CreateWindowExW.argtypes = [
        wintypes.DWORD, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD,
        ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
        wintypes.HWND, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p,
    ]
    _user32.CreateWindowExW.restype = wintypes.HWND
    _user32.LoadIconW.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
    _user32.LoadIconW.restype = ctypes.c_void_p
    _user32.LoadImageW.argtypes = [
        ctypes.c_void_p, wintypes.LPCWSTR, wintypes.UINT,
        ctypes.c_int, ctypes.c_int, wintypes.UINT,
    ]
    _user32.LoadImageW.restype = ctypes.c_void_p
    _user32.DestroyIcon.argtypes = [ctypes.c_void_p]
    _user32.DefWindowProcW.argtypes = [
        wintypes.HWND, wintypes.UINT, _WPARAM, _LPARAM
    ]
    _user32.DefWindowProcW.restype = ctypes.c_ssize_t
    _user32.GetCursorPos.argtypes = [ctypes.POINTER(wintypes.POINT)]
    _user32.GetMessageW.argtypes = [
        ctypes.POINTER(wintypes.MSG), wintypes.HWND, wintypes.UINT, wintypes.UINT
    ]
    _user32.TranslateMessage.argtypes = [ctypes.POINTER(wintypes.MSG)]
    _user32.DispatchMessageW.argtypes = [ctypes.POINTER(wintypes.MSG)]
    _user32.PostThreadMessageW.argtypes = [
        wintypes.DWORD, wintypes.UINT, _WPARAM, _LPARAM
    ]
    _kernel32.GetCurrentThreadId.restype = wintypes.DWORD
    _user32.UnregisterClassW.argtypes = [wintypes.LPCWSTR, ctypes.c_void_p]
    _shell32.Shell_NotifyIconW.argtypes = [wintypes.DWORD, ctypes.POINTER(_NOTIFYICONDATAW)]
    _shell32.Shell_NotifyIconW.restype = wintypes.BOOL


class TrayController:
    """系统托盘图标：双击恢复窗口，右键弹出菜单（恢复 / 退出）。"""

    def __init__(self, cmds_q, tip, icon_path=None):
        self.q = cmds_q
        self.tip = tip
        self.icon_path = icon_path
        self.hicon = None
        self.icon_owned = False
        self.ready = threading.Event()
        self.failed = False
        self.hwnd = None
        self.tid = None
        self.nid = None
        self.class_name = f"MagnetTray_{os.getpid()}_{id(self)}"
        self.wndproc_ref = _WNDPROC(self._wndproc)

    def start(self):
        threading.Thread(target=self._run, daemon=True).start()
        self.ready.wait(5)
        return not self.failed

    def stop(self):
        if self.tid:
            try:
                _user32.PostThreadMessageW(self.tid, _WM_QUIT, 0, 0)
            except Exception:
                pass

    def _wndproc(self, hwnd, msg, wparam, lparam):
        if msg == _TRAY_MSG:
            event = lparam & 0xFFFF
            if event == _WM_LBUTTONDBLCLK:
                self.q.put(("restore",))
            elif event == _WM_RBUTTONUP:
                pt = wintypes.POINT()
                _user32.GetCursorPos(ctypes.byref(pt))
                self.q.put(("menu", pt.x, pt.y))
            return 0
        return _user32.DefWindowProcW(hwnd, msg, wparam, lparam)

    def _run(self):
        self.tid = _kernel32.GetCurrentThreadId()
        wc = _WNDCLASSW()
        wc.style = 0
        wc.lpfnWndProc = self.wndproc_ref
        wc.lpszClassName = self.class_name
        try:
            if not _user32.RegisterClassW(ctypes.byref(wc)):
                self.failed = True
                return
            self.hwnd = _user32.CreateWindowExW(
                0, self.class_name, self.tip, 0, 0, 0, 0, 0,
                None, None, None, None
            )
            if not self.hwnd:
                self.failed = True
                return

            nid = _NOTIFYICONDATAW()
            nid.cbSize = ctypes.sizeof(nid)
            nid.hWnd = self.hwnd
            nid.uID = 1
            nid.uFlags = _NIF_MESSAGE | _NIF_ICON | _NIF_TIP
            nid.uCallbackMessage = _TRAY_MSG
            if self.icon_path and os.path.isfile(self.icon_path):
                try:
                    # IMAGE_ICON=1, LR_LOADFROMFILE=0x10
                    hicon = _user32.LoadImageW(None, self.icon_path, 1, 16, 16, 0x10)
                    if hicon:
                        self.hicon = hicon
                        self.icon_owned = True
                except Exception:
                    pass
            nid.hIcon = self.hicon or _user32.LoadIconW(None, ctypes.c_void_p(32512))
            nid.szTip = self.tip[:127]
            self.nid = nid
            if not _shell32.Shell_NotifyIconW(_NIM_ADD, ctypes.byref(nid)):
                self.failed = True
                return
            self.ready.set()

            msg = wintypes.MSG()
            while _user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
                _user32.TranslateMessage(ctypes.byref(msg))
                _user32.DispatchMessageW(ctypes.byref(msg))
        except Exception:
            self.failed = True
        finally:
            if self.nid:
                try:
                    _shell32.Shell_NotifyIconW(_NIM_DELETE, ctypes.byref(self.nid))
                except Exception:
                    pass
            if self.hwnd:
                try:
                    _user32.DestroyWindow(self.hwnd)
                except Exception:
                    pass
            if self.icon_owned and self.hicon:
                try:
                    _user32.DestroyIcon(self.hicon)
                except Exception:
                    pass
            try:
                _user32.UnregisterClassW(self.class_name, None)
            except Exception:
                pass
            self.ready.set()


class Task:
    """单个下载任务的状态容器。GUI 线程读，worker 线程通过队列更新。"""

    def __init__(self, tid, target, save_path):
        self.id = tid
        self.target = target
        self.save_path = save_path
        self.stop_requested = False
        self.removed = False
        self.phase = "preparing"
        self.progress = 0.0
        self.name = os.path.basename(target) or target
        self.total = 0
        self.num_files = 0
        self.down = 0
        self.up = 0
        self.peers = 0
        self.seeds = "?"
        self.dht = 0
        self.error = ""
        self.thread = None
        self.handle = None

    def stop(self, remove=False):
        self.stop_requested = True
        if remove:
            self.removed = True


def emit(uiq, tid, kind, *data):
    uiq.put((tid, kind) + data)


def add_target_safe(session, target, save_path, task):
    """与 core.add_target 一致，但可响应停止/移除，且不调用 sys.exit。"""
    if target.startswith("magnet:"):
        infohash = core.magnet_to_hash(target)
        if infohash:
            cache_dir = torrent_dir()
            local_path = os.path.join(cache_dir, f"{infohash}.torrent")
            info = None
            if os.path.isfile(local_path):
                try:
                    candidate = lt.torrent_info(local_path)
                    if str(candidate.info_hash()).lower() == infohash.lower():
                        info = candidate
                        print(f"使用本地种子文件：{local_path}")
                    else:
                        print("本地种子文件 infohash 不匹配，已忽略。")
                except Exception as exc:  # noqa: BLE001
                    print(f"本地种子文件读取失败（{exc}），改为查询在线缓存。")
            if info is None:
                info = core.fetch_from_cache(infohash, cache_dir)
            if info is not None:
                try:
                    with SESSION_LOCK:
                        handle = session.add_torrent(
                            {"ti": info, "save_path": save_path, "trackers": core.TRACKERS}
                        )
                    return handle, info
                except Exception as exc:  # noqa: BLE001
                    raise RuntimeError(f"添加任务失败（可能已存在相同任务）：{exc}") from exc

        print("缓存未命中，改为从 DHT / tracker 获取元数据（可能需几分钟）...")
        try:
            with SESSION_LOCK:
                handle = lt.add_magnet_uri(
                    session, target, {"save_path": save_path, "trackers": core.TRACKERS}
                )
        except Exception as exc:  # noqa: BLE001
            raise RuntimeError(f"添加任务失败（可能已存在相同任务）：{exc}") from exc

        deadline = time.time() + 420
        while not handle.has_metadata():
            if task.stop_requested or task.removed:
                return handle, None
            if time.time() > deadline:
                raise RuntimeError("等待元数据超时：未找到做种节点（资源可能无种子或网络受限）")
            st = handle.status()
            dht = getattr(session.status(), "dht_nodes", 0)
            print(f"  DHT 节点 {dht} | peers {st.num_peers} | 等待元数据 ...")
            time.sleep(2)
        return handle, handle.get_torrent_info()

    if os.path.isfile(target) and target.lower().endswith(".torrent"):
        info = lt.torrent_info(target)
        try:
            with SESSION_LOCK:
                handle = session.add_torrent(
                    {"ti": info, "save_path": save_path, "trackers": core.TRACKERS}
                )
            return handle, info
        except Exception as exc:  # noqa: BLE001
            raise RuntimeError(f"添加任务失败（可能已存在相同任务）：{exc}") from exc

    raise RuntimeError("无法识别的目标：请填写 magnet: 链接或选择 .torrent 文件")


def worker(session, task, uiq, dispatcher):
    """单个任务的下载线程。"""
    dispatcher.register(f"[任务{task.id}]")
    handle = None
    try:
        emit(uiq, task.id, "phase", "preparing")
        print(f"保存目录：{task.save_path}")

        handle, info = add_target_safe(session, task.target, task.save_path, task)
        task.handle = handle

        if handle is not None and (task.stop_requested or task.removed or info is None):
            with SESSION_LOCK:
                try:
                    session.remove_torrent(handle)
                except Exception:
                    pass
            task.handle = None
            emit(uiq, task.id, "phase", "stopped")
            return

        task.name = info.name()
        task.total = info.total_size()
        task.num_files = info.num_files()
        emit(uiq, task.id, "info", task.name, task.total, task.num_files)
        emit(uiq, task.id, "phase", "downloading")

        while not handle.is_finished():
            if task.stop_requested or task.removed:
                with SESSION_LOCK:
                    try:
                        session.remove_torrent(handle)
                    except Exception:
                        pass
                task.handle = None
                emit(uiq, task.id, "phase", "stopped")
                print("已停止，进度已保存；重新添加同一任务可续传。")
                return

            st = handle.status()
            try:
                seeds = st.list_seeds
            except Exception:
                seeds = "?"
            dht = getattr(session.status(), "dht_nodes", 0)
            emit(uiq, task.id, "stat", st.progress, st.download_rate,
                 st.upload_rate, st.num_peers, seeds, dht)
            time.sleep(0.5)

        emit(uiq, task.id, "stat", 1.0, 0, 0, 0, "?",
             getattr(session.status(), "dht_nodes", 0))
        emit(uiq, task.id, "phase", "done")
        print("下载完成！")
    except Exception as exc:  # noqa: BLE001 - 线程内捕获一切并反馈到界面
        emit(uiq, task.id, "error", str(exc))
        print(f"错误：{exc}")
    finally:
        dispatcher.unregister()


class App:
    def __init__(self, root, dispatcher):
        self.root = root
        self.root.title(APP_TITLE)
        self.root.geometry("980x620")
        self.root.minsize(820, 500)

        self.dispatcher = dispatcher
        self.uiq = queue.Queue()
        self.tray_q = queue.Queue()
        self.tasks = {}
        self.next_id = 1
        self.session = None
        self.tray = None
        self.tray_menu = None
        self.last_save = time.time()

        self._build_ui()
        self._restore_tasks()
        self.root.after(150, self._tick)
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

    # ---------- 界面 ----------
    def _build_ui(self):
        frm = ttk.Frame(self.root, padding=10)
        frm.pack(fill=tk.BOTH, expand=True)
        frm.columnconfigure(1, weight=1)

        # 添加任务区
        top = ttk.LabelFrame(frm, text="添加下载任务", padding=8)
        top.grid(row=0, column=0, columnspan=2, sticky="ew", pady=(0, 8))
        top.columnconfigure(1, weight=1)

        ttk.Label(top, text="磁力链接 / .torrent 文件：").grid(row=0, column=0, sticky="w")
        self.var_target = tk.StringVar()
        ttk.Entry(top, textvariable=self.var_target).grid(row=0, column=1, sticky="ew", padx=4)
        ttk.Button(top, text="浏览…", width=9, command=self._browse_torrent).grid(row=0, column=2)

        ttk.Label(top, text="保存目录：").grid(row=1, column=0, sticky="w", pady=(6, 0))
        self.var_save = tk.StringVar(value=DEFAULT_SAVE)
        ttk.Entry(top, textvariable=self.var_save).grid(row=1, column=1, sticky="ew", padx=4, pady=(6, 0))
        ttk.Button(top, text="选择…", width=9, command=self._browse_save).grid(row=1, column=2, pady=(6, 0))
        ttk.Button(top, text="添加任务", command=self._add_task).grid(
            row=0, column=3, rowspan=2, sticky="ns", padx=(10, 0)
        )

        # 任务列表区
        mid = ttk.Frame(frm)
        mid.grid(row=1, column=0, columnspan=2, sticky="nsew")
        frm.rowconfigure(1, weight=1)

        columns = ("id", "state", "name", "progress", "down", "up", "size", "peers", "path")
        self.tree = ttk.Treeview(mid, columns=columns, show="headings", height=9, selectmode="extended")
        headings = {
            "id": ("#", 38), "state": ("状态", 70), "name": ("名称", 220),
            "progress": ("进度", 60), "down": ("下载速度", 105), "up": ("上传速度", 105),
            "size": ("大小", 100), "peers": ("节点/种子", 90), "path": ("保存目录", 180),
        }
        for col in columns:
            text, width = headings[col]
            self.tree.heading(col, text=text)
            self.tree.column(col, width=width, anchor="w", stretch=(col in ("name", "path")))
        self.tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        vsb = ttk.Scrollbar(mid, orient=tk.VERTICAL, command=self.tree.yview)
        vsb.pack(side=tk.RIGHT, fill=tk.Y)
        self.tree.configure(yscrollcommand=vsb.set)
        self.tree.bind("<<TreeviewSelect>>", lambda _e: self._refresh_tree())

        # 操作按钮
        bar = ttk.Frame(frm)
        bar.grid(row=2, column=0, columnspan=2, sticky="ew", pady=8)
        self.btn_restart = ttk.Button(bar, text="重启选中", command=self._restart_selected)
        self.btn_restart.pack(side=tk.LEFT)
        ttk.Button(bar, text="停止选中", command=self._stop_selected).pack(side=tk.LEFT, padx=6)
        ttk.Button(bar, text="移除选中", command=self._remove_selected).pack(side=tk.LEFT)
        ttk.Button(bar, text="全部停止", command=self._stop_all).pack(side=tk.LEFT, padx=6)
        ttk.Button(bar, text="打开选中目录", command=self._open_folder).pack(side=tk.LEFT)
        self.var_selected_progress = tk.StringVar(value="未选中任务")
        ttk.Label(bar, textvariable=self.var_selected_progress).pack(side=tk.RIGHT)

        # 日志区
        ttk.Label(frm, text="运行日志：").grid(row=3, column=0, columnspan=2, sticky="w")
        logf = ttk.Frame(frm)
        logf.grid(row=4, column=0, columnspan=2, sticky="nsew")
        frm.rowconfigure(4, weight=1)
        self.log = tk.Text(logf, height=9, wrap=tk.WORD, state=tk.DISABLED, font=("Consolas", 9))
        sb = ttk.Scrollbar(logf, orient=tk.VERTICAL, command=self.log.yview)
        self.log.configure(yscrollcommand=sb.set)
        self.log.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        sb.pack(side=tk.RIGHT, fill=tk.Y)

    # ---------- 会话与任务 ----------
    def _get_session(self):
        if self.session is None:
            self.session = core.make_session()
        return self.session

    # ---------- 任务保存 / 恢复 ----------
    @staticmethod
    def _state_path():
        """状态文件放在 exe（或脚本）旁边；不可写时退回 %APPDATA%。"""
        return os.path.join(get_data_dir(), "磁力下载器_tasks.json")

    def _save_tasks(self):
        """只保存仍在进行中的任务，下次启动会自动重新添加并续传。"""
        active = [t for t in self.tasks.values()
                  if t.phase in ("preparing", "downloading")]
        data = {
            "version": 1,
            "tasks": [
                {"id": t.id, "target": t.target, "save_path": t.save_path}
                for t in active
            ],
        }
        try:
            with open(self._state_path(), "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
        except Exception as exc:  # noqa: BLE001
            print(f"保存任务列表失败：{exc}")
        self.last_save = time.time()

    def _start_task(self, tid, target, save_path):
        """创建并启动一个任务（新添加和启动时恢复共用）。"""
        task = Task(tid, target, save_path)
        self.tasks[tid] = task
        if self.tree.exists(str(tid)):  # 重启任务时复用原行，避免重复条目
            self.tree.item(str(tid), values=self._row_values(task))
        else:
            self.tree.insert("", tk.END, iid=str(tid), values=self._row_values(task))
        session = self._get_session()
        task.thread = threading.Thread(
            target=worker, args=(session, task, self.uiq, self.dispatcher), daemon=True
        )
        task.thread.start()
        return task

    def _restore_tasks(self):
        path = self._state_path()
        if not os.path.isfile(path):
            return
        try:
            with open(path, encoding="utf-8") as f:
                entries = json.load(f).get("tasks", [])
        except Exception as exc:  # noqa: BLE001
            print(f"读取任务列表失败：{exc}")
            return
        if not entries:
            return
        restored = 0
        for item in entries:
            try:
                tid = int(item.get("id", 0))
                target = str(item.get("target", "")).strip()
                save = str(item.get("save_path", "")).strip() or DEFAULT_SAVE
            except Exception:  # noqa: BLE001
                continue
            if not target:
                continue
            if tid >= self.next_id:
                self.next_id = tid + 1
            else:
                tid = self.next_id
                self.next_id += 1
            self._start_task(tid, target, save)
            restored += 1
        if restored:
            print(f"已恢复 {restored} 个任务，正在继续下载。")

    def _browse_torrent(self):
        path = filedialog.askopenfilename(
            title="选择 .torrent 文件",
            filetypes=[("种子文件", "*.torrent"), ("所有文件", "*.*")],
        )
        if path:
            self.var_target.set(path)

    def _browse_save(self):
        path = filedialog.askdirectory(title="选择保存目录")
        if path:
            self.var_save.set(path)

    def _add_task(self):
        target = self.var_target.get().strip()
        save = self.var_save.get().strip() or DEFAULT_SAVE
        if not target:
            messagebox.showwarning(APP_TITLE, "请填写磁力链接或选择 .torrent 文件。")
            return
        try:
            os.makedirs(save, exist_ok=True)
        except Exception as exc:  # noqa: BLE001
            messagebox.showerror(APP_TITLE, f"无法创建保存目录：{exc}")
            return

        tid = self.next_id
        self.next_id += 1
        self._start_task(tid, target, save)
        self.var_target.set("")
        self._refresh_tree()

    def _selected_ids(self):
        result = []
        for iid in self.tree.selection():
            try:
                result.append(int(iid))
            except ValueError:
                pass
        return result

    def _stop_selected(self):
        ids = self._selected_ids()
        if not ids:
            messagebox.showinfo(APP_TITLE, "请先在列表中选择任务。")
            return
        for tid in ids:
            self.tasks[tid].stop()
        self._refresh_tree()

    def _stop_all(self):
        for task in self.tasks.values():
            task.stop()
        self._refresh_tree()

    def _restart_selected(self):
        """重启选中的已停止/出错任务：文件仍在原目录，libtorrent 校验后续传。"""
        ids = self._selected_ids()
        pending = []
        for tid in ids:
            task = self.tasks.get(tid)
            if task is None or task.phase not in ("stopped", "error"):
                continue
            old_thread = task.thread
            if old_thread is not None and old_thread.is_alive():
                task.stop(remove=True)
                old_thread.join(timeout=3)
                if old_thread.is_alive():
                    print(f"任务 #{tid} 尚未完全停止（可能正在请求网络），请稍后再试。")
                    continue
            pending.append((tid, task.target, task.save_path))

        if not pending:
            messagebox.showinfo(APP_TITLE, "请先选择已停止或出错的任务。")
            return

        for tid, target, save_path in pending:
            print(f"任务 #{tid} 正在重新启动，将从原保存目录校验并续传。")
            self._start_task(tid, target, save_path)
        self._refresh_tree()

    def _remove_selected(self):
        ids = self._selected_ids()
        if not ids:
            messagebox.showinfo(APP_TITLE, "请先在列表中选择任务。")
            return
        if not messagebox.askyesno(APP_TITLE, f"移除选中的 {len(ids)} 个任务？\n（不会删除已下载的文件）"):
            return
        for tid in ids:
            task = self.tasks[tid]
            task.stop(remove=True)
            self.tree.delete(str(tid))
            del self.tasks[tid]
        self._refresh_tree()

    def _open_folder(self):
        ids = self._selected_ids()
        paths = [self.tasks[tid].save_path for tid in ids] if ids else [DEFAULT_SAVE]
        for path in dict.fromkeys(paths):
            try:
                os.makedirs(path, exist_ok=True)
                os.startfile(path)
            except Exception as exc:  # noqa: BLE001
                messagebox.showwarning(APP_TITLE, f"无法打开目录 {path}：{exc}")

    def _on_close(self):
        active = sum(1 for t in self.tasks.values()
                     if t.phase in ("preparing", "downloading"))
        dlg = tk.Toplevel(self.root)
        dlg.title(APP_TITLE)
        dlg.resizable(False, False)
        dlg.transient(self.root)
        dlg.grab_set()

        hint = f"当前有 {active} 个任务正在进行。\n\n" if active else ""
        tk.Label(
            dlg,
            text=hint + "关闭窗口时希望怎么做？\n\n"
                 "最小化到托盘：任务继续在后台下载，可从系统托盘恢复。\n"
                 "退出程序：停止所有任务并关闭程序。",
            justify=tk.LEFT, wraplength=400,
        ).pack(padx=18, pady=(16, 10))

        btnf = ttk.Frame(dlg)
        btnf.pack(pady=(0, 16))
        result = {"choice": None}

        def choose(value):
            result["choice"] = value
            dlg.destroy()

        ttk.Button(btnf, text="最小化到托盘", width=14,
                   command=lambda: choose("minimize")).pack(side=tk.LEFT, padx=4)
        ttk.Button(btnf, text="退出程序", width=12,
                   command=lambda: choose("quit")).pack(side=tk.LEFT, padx=4)
        ttk.Button(btnf, text="取消", width=10,
                   command=lambda: choose("cancel")).pack(side=tk.LEFT, padx=4)
        dlg.protocol("WM_DELETE_WINDOW", lambda: choose("cancel"))

        dlg.update_idletasks()
        x = self.root.winfo_rootx() + max(0, (self.root.winfo_width() - dlg.winfo_width()) // 2)
        y = self.root.winfo_rooty() + max(0, (self.root.winfo_height() - dlg.winfo_height()) // 2)
        dlg.geometry(f"+{x}+{y}")
        self.root.wait_window(dlg)

        if result["choice"] == "quit":
            self._quit_app()
        elif result["choice"] == "minimize":
            self._minimize_to_tray()

    def _quit_app(self):
        self._save_tasks()
        for task in self.tasks.values():
            task.stop(remove=True)
        if self.tray:
            self.tray.stop()
        self.root.destroy()

    def _minimize_to_tray(self):
        if self.tray is None:
            self.tray = TrayController(self.tray_q, APP_TITLE, resource_path("app_icon.ico"))
            if not self.tray.start():
                self.tray = None
                messagebox.showwarning(APP_TITLE, "系统托盘不可用，改为最小化到任务栏。")
                self.root.iconify()
                return
        self.root.withdraw()

    def _restore_window(self):
        self.root.deiconify()
        self.root.lift()
        self.root.attributes("-topmost", True)
        self.root.after(300, lambda: self.root.attributes("-topmost", False))

    def _show_tray_menu(self, x, y):
        menu = tk.Menu(self.root, tearoff=0)
        menu.add_command(label="恢复窗口", command=self._restore_window)
        menu.add_separator()
        menu.add_command(label="退出程序", command=self._quit_app)
        self.tray_menu = menu  # 保持引用，防止菜单被回收
        menu.tk_popup(x, y)

    # ---------- 刷新 ----------
    @staticmethod
    def _row_values(task):
        size = core.human(task.total) if task.total else "-"
        return (
            task.id,
            PHASE_TEXT.get(task.phase, task.phase),
            task.name,
            f"{int(task.progress * 100)}%",
            f"{core.human(task.down)}/s",
            f"{core.human(task.up)}/s",
            size,
            f"{task.peers}/{task.seeds}",
            task.save_path,
        )

    def _refresh_tree(self):
        for tid, task in self.tasks.items():
            if self.tree.exists(str(tid)):
                self.tree.item(str(tid), values=self._row_values(task))
        ids = self._selected_ids()
        can_restart = any(
            tid in self.tasks and self.tasks[tid].phase in ("stopped", "error")
            for tid in ids
        )
        self.btn_restart.configure(state=tk.NORMAL if can_restart else tk.DISABLED)
        if len(ids) == 1:
            task = self.tasks.get(ids[0])
            if task:
                self.var_selected_progress.set(
                    f"选中 #{task.id}：{int(task.progress * 100)}% · "
                    f"{PHASE_TEXT.get(task.phase, task.phase)}"
                )
        elif len(ids) > 1:
            self.var_selected_progress.set(f"已选中 {len(ids)} 个任务")
        else:
            self.var_selected_progress.set("未选中任务")

    def _tick(self):
        try:
            while True:
                tid, kind, *data = self.uiq.get_nowait()
                task = self.tasks.get(tid)
                if task is None:
                    continue
                if kind == "phase":
                    task.phase = data[0]
                elif kind == "info":
                    task.name, task.total, task.num_files = data
                elif kind == "stat":
                    (task.progress, task.down, task.up,
                     task.peers, task.seeds, task.dht) = data
                elif kind == "error":
                    task.phase = "error"
                    task.error = data[0]
        except queue.Empty:
            pass

        try:
            while True:
                cmd = self.tray_q.get_nowait()
                if cmd[0] == "restore":
                    self._restore_window()
                elif cmd[0] == "menu":
                    self._show_tray_menu(cmd[1], cmd[2])
        except queue.Empty:
            pass

        lines = []
        try:
            while True:
                lines.append(self.dispatcher.logq.get_nowait())
        except queue.Empty:
            pass
        if lines:
            self.log.configure(state=tk.NORMAL)
            self.log.insert(tk.END, "\n".join(lines) + "\n")
            self.log.see(tk.END)
            self.log.configure(state=tk.DISABLED)

        if time.time() - self.last_save > 20:
            self._save_tasks()

        self._refresh_tree()
        self.root.after(150, self._tick)


def main():
    old_stdout = sys.stdout
    old_stderr = sys.stderr
    dispatcher = ThreadStdout(queue.Queue())
    # --windowed 模式下 stdout/stderr 可能为 None，统一接管避免库写入时报错。
    sys.stdout = dispatcher
    sys.stderr = dispatcher
    try:
        root = tk.Tk()
        try:
            root.iconbitmap(resource_path("app_icon.ico"))
        except Exception:
            pass
        App(root, dispatcher)
        root.mainloop()
    finally:
        sys.stdout = old_stdout
        sys.stderr = old_stderr


if __name__ == "__main__":
    main()

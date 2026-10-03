import sys
import os
import re
import html
import json
import shutil
import sqlite3
import subprocess
import time
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QHBoxLayout, QVBoxLayout,
    QLabel, QPushButton, QTreeWidget, QTreeWidgetItem, QFrame,
    QMenu, QMessageBox, QFileDialog, QHeaderView, QPlainTextEdit,
    QGraphicsOpacityEffect, QDialog, QLineEdit, QCheckBox, QProgressBar,
    QListWidget, QListWidgetItem, QComboBox
)
from PySide6.QtCore import (
    Qt, QTimer, QPropertyAnimation, QEasingCurve, QPoint, QThread, Signal,
    QRectF
)
from PySide6.QtGui import (
    QFont, QColor, QPainter, QPen, QPixmap, QPainterPath, QIcon
)


APP_NAME = "Cherry 会话管理器"
APP_VERSION = "v1.4.0"
APP_AUTHOR = "mougehongshifen"
APP_GITHUB = "https://github.com/mougehongshifen/cherry-session-manager"


def resource_path(filename):
    if hasattr(sys, '_MEIPASS'):
        return os.path.join(sys._MEIPASS, filename)
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), filename)


def app_dir():
    """主程序实际所在目录（打包成 exe 时是 exe 所在目录，不是临时解包目录）。"""
    if getattr(sys, "frozen", False):
        return os.path.dirname(os.path.abspath(sys.executable))
    return os.path.dirname(os.path.abspath(__file__))


def bundled_dir():
    """打包后随程序一起分发的资源目录（PyInstaller 临时解包目录），否则同脚本目录。"""
    if hasattr(sys, "_MEIPASS"):
        return sys._MEIPASS
    return os.path.dirname(os.path.abspath(__file__))


CONFIG_FILE = os.path.join(os.path.expanduser("~"), ".cherry_extra_config.json")


def load_config():
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except:
            return {}
    return {}


def save_config(cfg):
    """写配置。失败也不该让程序崩掉（例如家目录只读）。"""
    try:
        with open(CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump(cfg, f, ensure_ascii=False, indent=2)
        return True
    except Exception:
        return False


# ==========================================================
# 运行时目录名编解码
#
# Cherry Studio 把「运行时会话记录」按工作目录分文件夹存放，
# 两种运行时的文件夹命名规则不同，以下均在真机上验证过：
#
#   claude-code : <Data>\Agents\.claude\projects\<消毒工作目录>\<运行时会话id>.jsonl
#                 消毒 = 把非 [A-Za-z0-9] 的字符全换成 '-'
#                 例  C:\My Projects\my_app  ->  C--My-Projects-my-app
#
#   dsh         : <Data>\Agents\.dsh\sessions\<projectKey(工作目录)>\<encodeSegment(会话id)>\
#                 / \ : 的连续段折叠成一个 '-'，其余非法字符变成 ~XXXX
#                 例  C:\My Projects\my_app  ->  --C-My~0020Projects-my_app--
#
# 注意：运行时会话 id ≠ Cherry 的 agent_session.id。
#       claude-code 的运行时 id 存在 agent_session_message.runtime_resume_token；
#       dsh 的运行时 id 恰好等于 agent_session.id。
#
# ------------------------------------------------------------------
# 出处声明 / Attribution
#   project_key() 与 encode_segment() 的编码规则来自 DeepSeek Harness (DSH)
#   的 session-persistence-jsonl 包，该项目以 MIT 许可证发布：
#       Copyright (c) 2026 DeepSeek  —  https://github.com/deepseek-ai
#   这里的 Python 版本是照该规则重写的实现。详见仓库根目录的 NOTICE 文件。
#   claude_project_dir_name() 的规则来自 Claude Code 的 projects 目录约定。
# ------------------------------------------------------------------
# ==========================================================

_SAFE_SEG_CHARS = set("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789._-")


def _utf16_units(s):
    """按 UTF-16 码元拆分，与 JS 的 charCodeAt 语义一致（emoji 等星平面字符占 2 个码元）。"""
    b = s.encode("utf-16-le", "surrogatepass")
    return [b[i] | (b[i + 1] << 8) for i in range(0, len(b), 2)]


def encode_segment(raw):
    """DSH：把一个字符串编码成单个安全的路径段（可逆）。"""
    if raw is None or raw == "":
        raise ValueError("cannot encode an empty path segment")
    if raw == ".":
        return "~002E"
    if raw == "..":
        return "~002E~002E"
    out = []
    for code in _utf16_units(raw):
        ch = chr(code)
        if ch != "~" and ch in _SAFE_SEG_CHARS:
            out.append(ch)
        else:
            out.append("~" + format(code, "04X"))
    return "".join(out)


def decode_segment(seg):
    """encode_segment 的逆运算（含代理对还原）。"""
    units = []
    i = 0
    while i < len(seg):
        if seg[i] == "~" and i + 5 <= len(seg):
            try:
                units.append(int(seg[i + 1:i + 5], 16))
                i += 5
                continue
            except ValueError:
                pass
        units.append(ord(seg[i]))
        i += 1
    try:
        raw = b"".join(u.to_bytes(2, "little") for u in units)
        return raw.decode("utf-16-le", "surrogatepass")
    except Exception:
        return "".join(chr(u) for u in units)


def project_key(cwd):
    """DSH：把工作目录编码成它在 .dsh\\sessions 下的项目文件夹名。"""
    if not cwd:
        raise ValueError("cannot encode an empty project path")
    out = []
    sep_run = False
    for code in _utf16_units(cwd):
        ch = chr(code)
        if ch in ("/", "\\", ":"):
            if not sep_run:
                out.append("-")
            sep_run = True
        elif ch != "~" and ch in _SAFE_SEG_CHARS:
            out.append(ch)
            sep_run = False
        else:
            out.append("~" + format(code, "04X"))
            sep_run = False
    readable = re.sub(r"^-+", "", "".join(out)) or "root"
    return "--" + readable[:251] + "--"


def claude_project_dir_name(workspace_path):
    """claude-code：把工作目录编码成它在 .claude\\projects 下的文件夹名。"""
    return re.sub(r"[^A-Za-z0-9]", "-", workspace_path or "")


# ==========================================================
# 路径扫描
# ==========================================================

CHERRY_HOME = os.path.join(os.path.expanduser("~"), ".cherrystudio")
CHERRY_BOOT_CONFIG = os.path.join(CHERRY_HOME, "boot-config.json")
CHERRY_LEGACY_CONFIG = os.path.join(CHERRY_HOME, "config", "config.json")


def _read_json(path):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def cherry_declared_userdata():
    """Cherry Studio 自己记录的数据根目录（最权威，优先于任何扫描）。

    boot-config.json  : {"app.user_data_path": {"<exe路径>": "<userData>"}}
    config.json(旧版) : {"appDataPath": [{"executablePath":..., "dataPath":...}]}
    真正放 DB 的是 <userData>\\Data。
    """
    out = []
    seen = set()

    def add(ud, src):
        if isinstance(ud, str) and ud and ud not in seen:
            seen.add(ud)
            out.append((ud, src))

    j = _read_json(CHERRY_BOOT_CONFIG)
    if isinstance(j, dict):
        m = j.get("app.user_data_path")
        if isinstance(m, dict):
            for exe, ud in sorted(m.items(), key=lambda kv: 0 if os.path.exists(kv[0]) else 1):
                add(ud, "boot-config.json")

    j = _read_json(CHERRY_LEGACY_CONFIG)
    if isinstance(j, dict):
        arr = j.get("appDataPath")
        if isinstance(arr, list):
            for e in arr:
                if isinstance(e, dict):
                    add(e.get("dataPath"), "config.json(旧版)")
    return out


def data_dir_of_userdata(userdata):
    """userData -> 真正含 cherrystudio.sqlite 的目录（通常是 userData\\Data）。"""
    if not userdata:
        return None
    for c in (os.path.join(userdata, "Data"), userdata):
        if os.path.exists(os.path.join(c, "cherrystudio.sqlite")):
            return c
    return None


def list_roots():
    """返回需要遍历的根目录：Windows 用真实盘符，macOS/Linux 用根与挂载点。

    Windows 上只取「固定盘」和「可移动盘」，**跳过网络盘和光驱** ——
    对断开的映射盘做 os.path.exists 可能卡住好几秒，
    那会让启动和「全盘深度扫描」卡死。
    （数据目录在网络盘上时，靠 boot-config.json 这种权威来源定位，不依赖遍历。）
    """
    if os.name == "nt":
        try:
            import ctypes
            k32 = ctypes.windll.kernel32
            mask = k32.GetLogicalDrives()
            out = []
            for i in range(26):
                if not (mask & (1 << i)):
                    continue
                root = f"{chr(65 + i)}:\\"
                try:
                    # DRIVE_REMOVABLE=2, DRIVE_FIXED=3, DRIVE_REMOTE=4, DRIVE_CDROM=5
                    dtype = k32.GetDriveTypeW(ctypes.c_wchar_p(root))
                except Exception:
                    dtype = 3
                if dtype in (2, 3):
                    out.append(root)
            if out:
                return out
        except Exception:
            pass
        return [f"{c}:\\" for c in "CDEFGH" if os.path.exists(f"{c}:\\")] or ["C:\\"]
    out = ["/"]
    if sys.platform == "darwin":
        out.append("/Volumes")
    else:
        out += ["/mnt", "/media", "/opt", "/srv"]
    return [p for p in out if os.path.isdir(p)]


def platform_default_bases():
    """Cherry Studio 在各平台上的默认 userData 位置。

    环境变量可能为空（某些启动方式/精简环境），所以同时用 ~ 推导一份兜底。
    """
    home = os.path.expanduser("~")
    out = []
    if os.name == "nt":
        for env, fallback in (("APPDATA", os.path.join(home, "AppData", "Roaming")),
                              ("LOCALAPPDATA", os.path.join(home, "AppData", "Local"))):
            bases = []
            v = os.environ.get(env)
            if v:
                bases.append(v)
            bases.append(fallback)
            for b in bases:
                out.append(os.path.join(b, "CherryStudio"))
                out.append(os.path.join(b, "Cherry Studio"))
    elif sys.platform == "darwin":
        out.append(os.path.join(home, "Library", "Application Support", "CherryStudio"))
    else:
        xdg = os.environ.get("XDG_CONFIG_HOME") or os.path.join(home, ".config")
        out.append(os.path.join(xdg, "CherryStudio"))
    # 去重但保序
    return list(dict.fromkeys(out))


def _wellknown_paths():
    """常见的自定义数据目录摆放位置（相对每个根目录）。"""
    out = []
    if os.name == "nt":
        for root in list_roots():
            for rel in (r"Cherry\memory\Data", r"Cherry\memory",
                        r"CherryStudio\Data", r"CherryStudio",
                        r"Cherry Studio\Data", r"Cherry Studio",
                        r"Cherry\Data", r"Cherry"):
                out.append(os.path.join(root, rel))
    else:
        out += ["/opt/CherryStudio", "/opt/cherrystudio",
                "/usr/local/share/CherryStudio"]
    return out


def _walk_for_candidates(roots):
    """逐级兜底遍历（只在便宜的位置都找不到时才用）。"""
    skip = {"windows", "program files", "program files (x86)", "programdata",
            "$recycle.bin", "system volume information", "perflogs", "recovery",
            "msocache", "config.msi", "$windows.~ws", "node_modules", "__pycache__",
            "proc", "sys", "dev", "run", "boot", "lost+found", ".trash", ".git"}
    hint = ("cherry", "cherrystudio", "cherry studio", "memory", "data")
    out = []
    for root in roots:
        try:
            level1 = os.listdir(root)
        except OSError:
            continue
        for l1 in level1:
            if l1.lower() in skip or l1.startswith("$"):
                continue
            p1 = os.path.join(root, l1)
            if not os.path.isdir(p1):
                continue
            out.append(p1)
            out.append(os.path.join(p1, "Data"))
            # 名称可疑的一级目录再往下探一层（例：<盘符>:\<某目录>\Data）
            if any(h in l1.lower() for h in hint):
                try:
                    for l2 in os.listdir(p1):
                        p2 = os.path.join(p1, l2)
                        if not os.path.isdir(p2):
                            continue
                        out.append(p2)
                        out.append(os.path.join(p2, "Data"))
                        if any(h in l2.lower() for h in hint):
                            try:
                                for l3 in os.listdir(p2):
                                    p3 = os.path.join(p2, l3)
                                    if os.path.isdir(p3):
                                        out.append(p3)
                            except OSError:
                                pass
                except OSError:
                    pass
    return out


def collect_data_candidates(deep=False):
    """收集所有含 cherrystudio.sqlite 的目录。

    返回按「打分从高到低」排序的 [(path, 来源说明), ...]
    """
    found = {}

    def add(path, source):
        if not path:
            return
        try:
            path = os.path.normpath(path)
        except Exception:
            return
        if path in found:
            return
        if os.path.isfile(os.path.join(path, "cherrystudio.sqlite")):
            found[path] = source

    # 1) Cherry 自己声明的（最可信）
    for ud, src in cherry_declared_userdata():
        add(data_dir_of_userdata(ud), f"{src} 声明 userData={ud}")

    # 2) 各平台默认位置
    for base in platform_default_bases():
        add(os.path.join(base, "Data"), "平台默认位置")
        add(base, "平台默认位置")

    # 3) 常见盘位 / 常见摆放
    for p in _wellknown_paths():
        add(p, "常见位置")

    # 4) 逐级兜底
    if deep:
        for p in _walk_for_candidates(list_roots()):
            add(p, "全盘遍历")

    return sorted(found.items(), key=lambda kv: _score_data_dir(kv[0]), reverse=True)


def _score_data_dir(d):
    """给数据目录打分：优先「最近被写过」的库（活库一直在写），其次看体积。"""
    try:
        st = os.stat(os.path.join(d, "cherrystudio.sqlite"))
        mt, sz = st.st_mtime, st.st_size
    except OSError:
        mt, sz = 0, 0
    has_agents = 1 if os.path.isdir(os.path.join(d, "Agents")) else 0
    return (mt, sz, has_agents)


def probe_db_counts(db_path):
    """轻量读取该库里有多少 Agent / 会话，供选择界面显示。失败返回 (None, None)。

    注意：只能用普通的 `mode=ro`。**不能用 `immutable=1`** —— 那等于告诉 SQLite
    「这个文件不会变」，它会跳过 WAL 与加锁；而 Cherry 的库是 WAL 模式且可能
    正在被写入，按 immutable 读会拿到不一致的页甚至崩溃。
    """
    try:
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=5)
        cur = conn.cursor()
        cur.execute("SELECT COUNT(*) FROM agent")
        a = cur.fetchone()[0]
        cur.execute("SELECT COUNT(*) FROM agent_session")
        s = cur.fetchone()[0]
        conn.close()
        return a, s
    except Exception:
        return None, None


def describe_data_dir(path, with_counts=False):
    """把候选目录整理成便于展示的一行信息。"""
    info = {"path": path, "mtime": "?", "size_mb": 0, "agents": None, "sessions": None,
            "source": "", "valid": False}
    db = os.path.join(path, "cherrystudio.sqlite")
    try:
        st = os.stat(db)
        info["mtime"] = time.strftime("%Y-%m-%d %H:%M", time.localtime(st.st_mtime))
        info["size_mb"] = round(st.st_size / 1048576, 1)
        info["valid"] = True
    except OSError:
        return info
    if with_counts:
        a, s = probe_db_counts(db)
        info["agents"], info["sessions"] = a, s
    return info


class DataDirPickerDialog(QDialog):
    """发现多个 Cherry 数据目录时，让用户选一个。

    一台电脑上存在多个 cherrystudio.sqlite 很常见：换过安装位置、多个 Cherry 用户、
    旧的 AppData 残留等。自动打分只是猜测，最终由用户确认。
    """

    def __init__(self, parent=None, deep=False):
        super().__init__(parent)
        self.setWindowTitle("选择 Cherry 数据目录")
        self.setModal(True)
        self.resize(760, 460)
        self.selected = None
        self._deep = deep
        self.setStyleSheet(f"""
            QDialog {{ background-color: #fafafa; }}
            QLabel {{ color: #333333; font-family: "微软雅黑"; font-size: 13px; }}
            QListWidget {{ background-color: #ffffff; border: 1px solid #dddddd; border-radius: 8px;
                           padding: 4px; font-family: "微软雅黑"; font-size: 12px; outline: none; }}
            QListWidget::item {{ padding: 8px 10px; border-radius: 6px; }}
            QListWidget::item:selected {{ background-color: {CHERRY_SOFT}; color: #222222; }}
            QListWidget::item:hover {{ background-color: #fff6f4; }}
            QPushButton {{ background-color: #ffffff; border: 1px solid {CHERRY}; border-radius: 6px;
                           padding: 7px 16px; color: {CHERRY}; font-size: 13px; min-width: 84px; }}
            QPushButton:hover {{ background-color: #fff6f4; }}
        """)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(24, 20, 24, 18)
        lay.setSpacing(10)

        t = QLabel("🔍  检测到多个 Cherry 数据目录")
        t.setStyleSheet(f"color: {CHERRY}; font-size: 16px; font-weight: bold;")
        lay.addWidget(t)
        hint = QLabel("请选择你要管理的那一个（通常是「会话数最多、最近修改」的那个）。")
        hint.setWordWrap(True)
        hint.setStyleSheet("color: #777777; font-size: 12px;")
        lay.addWidget(hint)

        self.list = QListWidget()
        self.list.setWordWrap(True)
        self.list.setSpacing(2)
        self.list.itemDoubleClicked.connect(lambda _i: self.on_ok())
        lay.addWidget(self.list, stretch=1)

        self.status = QLabel("")
        self.status.setWordWrap(True)
        self.status.setStyleSheet("color: #888888; font-size: 11px; font-family: 'Consolas';")
        lay.addWidget(self.status)

        row = QHBoxLayout()
        self.btn_deep = QPushButton("🐢 全盘深度扫描")
        self.btn_deep.setToolTip("在所有磁盘上逐级查找 cherrystudio.sqlite，较慢")
        self.btn_deep.clicked.connect(self.on_deep)
        row.addWidget(self.btn_deep)
        row.addStretch()
        self.btn_rescan = QPushButton("🔄 重新扫描")
        self.btn_rescan.clicked.connect(lambda: self.reload(deep=self._deep))
        row.addWidget(self.btn_rescan)
        cb = QPushButton("取消")
        cb.clicked.connect(self.reject)
        row.addWidget(cb)
        ok = QPushButton("使用选中的")
        ok.setStyleSheet(f"QPushButton {{ background-color: {CHERRY}; border: 1px solid {CHERRY};"
                         f" border-radius: 6px; padding: 7px 20px; color: #ffffff; font-size: 13px;"
                         f" min-width: 84px; }} QPushButton:hover {{ background-color: {CHERRY_HOVER}; }}")
        ok.clicked.connect(self.on_ok)
        row.addWidget(ok)
        lay.addLayout(row)

        self.reload(deep=deep)

    def reload(self, deep=False):
        self._deep = deep
        self.status.setText("正在扫描..." if not deep else "正在全盘扫描，可能需要几十秒...")
        QApplication.processEvents()

        result = scan_all_paths(deep=True if deep else None, with_counts=True)
        cands = result.get("data_candidates", [])
        active = result.get("data_dir")

        self.list.clear()
        for c in cands:
            mark = "●" if c["path"] == active else "○"
            parts = []
            if c.get("sessions") is not None:
                parts.append(f"{c['agents']} 个 Agent · {c['sessions']} 个会话")
            parts.append(f"{c['size_mb']} MB")
            parts.append(c["mtime"])
            line1 = f"{mark}  {c['path']}"
            line2 = "      " + "  ·  ".join(parts)
            line3 = f"      来源：{c.get('source') or '未知'}"
            it = QListWidgetItem("\n".join([line1, line2, line3]))
            it.setData(Qt.UserRole, c["path"])
            self.list.addItem(it)
            if c["path"] == active:
                self.list.setCurrentItem(it)

        if not cands:
            self.status.setText("❌ 没有找到任何含 cherrystudio.sqlite 的目录。"
                                "请点「全盘深度扫描」，或在设置里手动指定。")
        else:
            self.status.setText(f"共找到 {len(cands)} 个候选（● 为当前使用）。"
                                f"数据目录来源：{result.get('data_dir_source') or '未知'}")

        self._cands = cands

    def on_deep(self):
        self.reload(deep=True)

    def on_ok(self):
        it = self.list.currentItem()
        if it is None:
            QMessageBox.information(self, "请选择", "请先在列表里选一个目录。")
            return
        self.selected = it.data(Qt.UserRole)
        self.accept()


def scan_all_paths(deep=None, with_counts=False):
    """定位数据目录 / 运行时目录 / bun / 工具目录。

    参数：
      deep        None = 自动（便宜位置找不到候选时才全盘遍历）；True/False 强制
      with_counts 是否为候选目录统计 Agent / 会话数（较慢，只在选择界面用）

    返回 dict：
      data_dir          DB 与 Agents 所在的「数据目录」
      data_dir_source   该目录是从哪找到的（便于排查）
      data_candidates   所有候选目录（已排序，含时间/体积/来源）
      agents_dir / db_path / dsh_sessions / claude_projects / bun_path / tools_dir
    """
    result = {"data_dir": None, "data_dir_source": None, "data_candidates": [],
              "agents_dir": None, "db_path": None,
              "dsh_sessions": None, "claude_projects": None,
              "bun_path": None, "tools_dir": None}

    # ---- 1) 数据目录：先便宜地找；找不到再全盘遍历 ----
    candidates = collect_data_candidates(deep=False)
    if not candidates and deep is not False:
        candidates = collect_data_candidates(deep=True)
    elif deep:
        candidates = collect_data_candidates(deep=True)

    for path, source in candidates:
        c = describe_data_dir(path, with_counts=with_counts)
        c["source"] = source
        result["data_candidates"].append(c)

    if candidates:
        chosen, source = candidates[0]
        result["data_dir"] = chosen
        result["data_dir_source"] = source
    elif deep is not False:
        # 记录一句，便于排查「明明装了却找不到」
        result["data_dir_source"] = "未找到任何含 cherrystudio.sqlite 的目录"

    # ---- 2) 由数据目录推导运行时目录 ----
    if result["data_dir"]:
        chosen = result["data_dir"]
        result["db_path"] = os.path.join(chosen, "cherrystudio.sqlite")
        agents = os.path.join(chosen, "Agents")
        result["agents_dir"] = agents if os.path.isdir(agents) else None
        dsh = os.path.join(agents, ".dsh", "sessions")
        if os.path.isdir(dsh):
            result["dsh_sessions"] = dsh
        cld = os.path.join(agents, ".claude", "projects")
        if os.path.isdir(cld):
            result["claude_projects"] = cld

    # ---- 3) bun.exe ----
    bun_cands = []
    bw = shutil.which("bun")
    if bw:
        bun_cands.append(bw)
    # 打包时随程序一起带的 bun（双击即用，不用用户自己装）
    for d in (app_dir(), bundled_dir()):
        for rel in ("bun.exe", os.path.join("bin", "bun.exe"), "bun",
                    os.path.join("bin", "bun")):
            bun_cands.append(os.path.join(d, rel))
    for name in ("bun.exe", "bun"):
        bun_cands.append(os.path.join(CHERRY_HOME, "bin", name))
    for root in list_roots():
        for name in ("CherryStudio", "Cherry Studio"):
            if os.name == "nt":
                bun_cands.append(os.path.join(root, "Program Files", name, "resources", "bin", "bun.exe"))
                bun_cands.append(os.path.join(root, "Program Files (x86)", name, "resources", "bin", "bun.exe"))
                bun_cands.append(os.path.join(root, name, "resources", "bin", "bun.exe"))
            else:
                bun_cands.append(os.path.join(root, name, "resources", "bin", "bun"))
                bun_cands.append(os.path.join("/Applications", name + ".app",
                                              "Contents", "Resources", "bin", "bun"))
    for c in bun_cands:
        if c and os.path.exists(c):
            result["bun_path"] = c
            break

    # ---- 4) session-tools ----
    tools_cands = []
    for d in (app_dir(), bundled_dir()):
        tools_cands.append(os.path.join(d, "session-tools"))
        tools_cands.append(os.path.join(d, "tools"))
    for root in list_roots():
        tools_cands.append(os.path.join(root, "Cherry", "script", "session-tools"))
        tools_cands.append(os.path.join(root, "cherry", "script", "session-tools"))
    for c in tools_cands:
        if os.path.exists(os.path.join(c, "duplicate-session.js")):
            result["tools_dir"] = c
            break

    return result


# ==========================================================
# 全局路径状态
# ==========================================================

BUN_PATH = None
TOOLS_DIR = None
SCRIPT_PATH = None
DATA_DIR = None          # <data_dir>
DATA_DIR_SOURCE = None   # 数据目录的来源说明
DATA_CANDIDATES = []     # 扫描到的所有候选数据目录
AGENTS_DIR = None        # <data_dir>\Agents
DSH_BASE_PATH = None     # <agents_dir>\.dsh\sessions
CLAUDE_PROJECTS = None   # <agents_dir>\.claude\projects

# ---- 从数据库载入的映射 ----
WS = {}                  # workspace_id -> {name, path, type, resolved, status}
AGENTS = {}              # agent_id -> {name, type, avatar, dir, exists}
AGENT_BY_NAME = {}       # agent_name -> [agent_id, ...]
SESS = {}                # session_id -> {name, agent_id, ws_id, deleted}
AGENT_SESSIONS = {}      # agent_id -> [session_id, ...]
AGENT_WORKSPACES = {}    # agent_id -> [(ws_id, count), ...] 按会话数降序
DELETED_IDS = set()
_TOKEN_CACHE = {}        # session_id -> [runtime token, ...]


def refresh_global_paths():
    global BUN_PATH, TOOLS_DIR, SCRIPT_PATH, DATA_DIR, DATA_DIR_SOURCE, DATA_CANDIDATES
    global AGENTS_DIR, DSH_BASE_PATH, CLAUDE_PROJECTS
    cfg = load_config()

    # 始终扫描一遍，这样选择界面/设置界面总有完整的候选列表
    scanned = scan_all_paths()
    DATA_CANDIDATES = list(scanned.get("data_candidates", []))

    # 数据目录：已保存且仍然有效就用它，否则用扫描打分最高的
    DATA_DIR = None
    DATA_DIR_SOURCE = None
    saved = cfg.get("data_dir")
    if saved and os.path.isfile(os.path.join(saved, "cherrystudio.sqlite")):
        DATA_DIR = saved
        if cfg.get("data_dir_locked"):
            DATA_DIR_SOURCE = "设置中手动指定"
        elif cfg.get("data_dir_asked"):
            DATA_DIR_SOURCE = "上次选择的目录"
        else:
            DATA_DIR_SOURCE = "上次自动选定"
        if DATA_DIR not in [c["path"] for c in DATA_CANDIDATES]:
            info = describe_data_dir(DATA_DIR)
            info["source"] = DATA_DIR_SOURCE
            DATA_CANDIDATES.insert(0, info)

    if not DATA_DIR:
        DATA_DIR = scanned.get("data_dir")
        DATA_DIR_SOURCE = scanned.get("data_dir_source")
        if DATA_DIR and DATA_DIR not in [c["path"] for c in DATA_CANDIDATES]:
            info = describe_data_dir(DATA_DIR)
            info["source"] = DATA_DIR_SOURCE or ""
            DATA_CANDIDATES.insert(0, info)

    # bun
    bun = cfg.get("bun_path")
    BUN_PATH = bun if (bun and os.path.exists(bun)) else None
    if not BUN_PATH:
        BUN_PATH = scanned.get("bun_path")

    # session-tools：优先用「跟本程序放一起的」那份，
    # 这样把整个文件夹拷到任何地方（或别人下载解压）都能直接跑
    TOOLS_DIR = None
    for d in (app_dir(), bundled_dir()):
        cand = os.path.join(d, "session-tools")
        if os.path.exists(os.path.join(cand, "duplicate-session.js")):
            TOOLS_DIR = cand
            break
    if not TOOLS_DIR:
        tools = cfg.get("tools_dir")
        TOOLS_DIR = tools if (tools and os.path.exists(os.path.join(tools, "duplicate-session.js"))) else None
        if not TOOLS_DIR:
            TOOLS_DIR = scanned.get("tools_dir")
    SCRIPT_PATH = os.path.join(TOOLS_DIR, "duplicate-session.js") if TOOLS_DIR else None

    # 运行时目录一律从「选定的数据目录」推导，避免两处不一致
    if DATA_DIR:
        AGENTS_DIR = os.path.join(DATA_DIR, "Agents")
        d = os.path.join(AGENTS_DIR, ".dsh", "sessions")
        DSH_BASE_PATH = d if os.path.isdir(d) else None
        c = os.path.join(AGENTS_DIR, ".claude", "projects")
        CLAUDE_PROJECTS = c if os.path.isdir(c) else None
    else:
        AGENTS_DIR = DSH_BASE_PATH = CLAUDE_PROJECTS = None


def get_db_path():
    if not DATA_DIR:
        return None
    db = os.path.join(DATA_DIR, "cherrystudio.sqlite")
    return db if os.path.exists(db) else None


# ==========================================================
# 工作目录解析
# ==========================================================

_RE_AGENTS_TAIL = re.compile(r"[\\/]Agents[\\/](.+)$", re.IGNORECASE)


def resolve_workspace_path(path, data_dir=None):
    """把数据库里的工作目录路径解析成本机真实存在的路径。

    数据库里存的是「历史快照」：Cherry 的内置默认工作区路径规则是
        <数据目录>\\Agents\\<名字>
    换机器 / 搬数据目录时这些行不会被重写，所以会留下这种过期路径：
        C:\\Users\\someone\\AppData\\Roaming\\CherryStudio\\Data\\Agents\\t-default
    变化的是整个数据目录前缀，不是用户名 —— 所以按
    「\\Agents\\ 之后的部分」重定位到当前数据目录。

    返回 (真实路径 或 None, 状态说明)
    """
    if not path:
        return None, "无路径"
    if os.path.isdir(path):
        return path, "有效"
    if os.path.exists(path):
        return path, "有效(文件)"

    # 1) 旧数据目录前缀 -> 当前数据目录
    if data_dir:
        m = _RE_AGENTS_TAIL.search(path)
        if m:
            tail = m.group(1).replace("/", os.sep)
            cand = os.path.join(data_dir, "Agents", tail)
            if os.path.isdir(cand):
                return cand, "已重定位"
    return None, "不存在"


# ==========================================================
# 数据库载入
# ==========================================================

def _table_columns(cur, table):
    """取某张表实际有哪些列。"""
    try:
        cur.execute(f"PRAGMA table_info({table})")
        return {r[1] for r in cur.fetchall()}
    except Exception:
        return set()


def _select_rows(cur, table, wanted, where="", params=()):
    """只 SELECT 该表「确实存在」的列，返回 (行字典列表, 缺失的列集合)。

    为什么不能直接写死列名：Cherry Studio 升级时会加列（例如 `agent_session.deleted_at`
    是后来才有的）。对着旧版本的数据库查一个不存在的列会直接抛
    `no such column`，整个会话列表就读不出来了。
    """
    have = _table_columns(cur, table)
    if not have:
        return [], set(wanted)
    missing = {c for c in wanted if c not in have}
    cols = [c for c in wanted if c in have]
    if not cols:
        return [], missing
    sql = f"SELECT {', '.join(cols)} FROM {table}"
    if where:
        sql += " WHERE " + where
    try:
        cur.execute(sql, params)
    except Exception:
        return [], missing
    return [dict(zip(cols, r)) for r in cur.fetchall()], missing


def load_cherry_data(log_func=None):
    """一次性把 workspace / agent / session 的关联全部读出来。"""
    global WS, AGENTS, AGENT_BY_NAME, SESS, AGENT_SESSIONS, AGENT_WORKSPACES, DELETED_IDS, _TOKEN_CACHE
    WS.clear(); AGENTS.clear(); AGENT_BY_NAME.clear(); SESS.clear()
    AGENT_SESSIONS.clear(); AGENT_WORKSPACES.clear(); DELETED_IDS.clear(); _TOKEN_CACHE.clear()

    db = get_db_path()
    if not db:
        if log_func:
            log_func("❌ 找不到 cherrystudio.sqlite，无法读取工作目录信息")
        return False

    try:
        conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=15)
        cur = conn.cursor()
        problems = []

        # 1) 工作区
        rows, miss = _select_rows(cur, "agent_workspace", ["id", "name", "path", "type"])
        if miss:
            problems.append(f"agent_workspace 缺列 {sorted(miss)}")
        for r in rows:
            wid = r.get("id")
            path = r.get("path") or ""
            resolved, status = resolve_workspace_path(path, DATA_DIR)
            WS[wid] = {"name": r.get("name") or "(未命名)", "path": path,
                       "type": r.get("type") or "?", "resolved": resolved, "status": status}

        # 2) Agent
        rows, miss = _select_rows(cur, "agent", ["id", "name", "type", "configuration"])
        if miss:
            problems.append(f"agent 缺列 {sorted(miss)}")
        for r in rows:
            aid = r.get("id")
            name = r.get("name") or "(未命名)"
            avatar = "🤖"
            cfg = r.get("configuration")
            if cfg:
                try:
                    a = (json.loads(cfg) or {}).get("avatar")
                    if isinstance(a, str) and a:
                        avatar = a
                except Exception:
                    pass
            adir = os.path.join(AGENTS_DIR, aid) if (AGENTS_DIR and aid) else None
            AGENTS[aid] = {"name": name, "type": r.get("type") or "?", "avatar": avatar,
                           "dir": adir, "exists": bool(adir and os.path.isdir(adir))}
            AGENT_BY_NAME.setdefault(name, []).append(aid)

        # 3) 会话（deleted_at / type 是较新版本才有的列）
        rows, miss = _select_rows(cur, "agent_session",
                                  ["id", "name", "agent_id", "workspace_id", "deleted_at"])
        if "deleted_at" in miss:
            problems.append("这个数据库比较旧，没有 deleted_at 列（无法标出「已删除」会话）")
        for r in rows:
            sid = r.get("id")
            del_at = r.get("deleted_at")
            aid = r.get("agent_id")
            SESS[sid] = {"name": r.get("name") or "(未命名)", "agent_id": aid,
                         "ws_id": r.get("workspace_id"), "deleted": del_at is not None}
            if del_at is not None:
                DELETED_IDS.add(sid)
            if aid:
                AGENT_SESSIONS.setdefault(aid, []).append(sid)

        # 4) agent -> 用过的工作区（按会话数降序）
        for aid, sids in AGENT_SESSIONS.items():
            counter = {}
            for sid in sids:
                wid = SESS[sid]["ws_id"]
                if wid:
                    counter[wid] = counter.get(wid, 0) + 1
            AGENT_WORKSPACES[aid] = sorted(counter.items(), key=lambda kv: -kv[1])

        conn.close()

        relocated = sum(1 for w in WS.values() if w["status"] == "已重定位")
        missing = sum(1 for w in WS.values() if w["resolved"] is None)
        if log_func:
            log_func(f"📊 载入：{len(AGENTS)} 个 Agent · {len(WS)} 个工作区 · {len(SESS)} 个会话")
            if relocated:
                log_func(f"   · {relocated} 个工作区路径已按当前数据目录自动重定位")
            if missing:
                log_func(f"   · {missing} 个工作区路径确实不存在")
            for m in problems:
                log_func(f"   ⚠ {m}")
        if not SESS:
            if log_func:
                log_func("❌ 这个数据库里读不到任何会话。可能："
                         "① 数据目录选错了；② Cherry 版本过旧、表结构不兼容")
            return False
        return True
    except Exception as e:
        if log_func:
            log_func(f"❌ 读取数据库失败：{type(e).__name__}: {e}")
        return False


def get_deleted_session_ids():
    return DELETED_IDS


def get_agent_avatars():
    return {a["name"]: a["avatar"] for a in AGENTS.values()}


_SHARED_CONN = None
_SHARED_CONN_DB = None
_TOKEN_SCHEMA_OK = {}


def _shared_conn():
    """复用一个只读连接。

    为什么不每次新建：206 个会话各建一次连接光建连就要 1.8 秒。
    为什么不做全表扫描：`SELECT ... FROM agent_session_message`（不带 session_id 条件）
    是全表扫描 —— 作者机器上 141MB 的库要 0.5 秒，别人几个 GB 的库会卡住界面。
    只按 session_id 查就能用上索引 agent_session_message_session_created_id_idx。
    """
    global _SHARED_CONN, _SHARED_CONN_DB
    db = get_db_path()
    if not db:
        return None
    if _SHARED_CONN is not None and _SHARED_CONN_DB == db:
        return _SHARED_CONN
    try:
        if _SHARED_CONN is not None:
            try:
                _SHARED_CONN.close()
            except Exception:
                pass
        _SHARED_CONN = sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=10)
        _SHARED_CONN_DB = db
    except Exception:
        _SHARED_CONN = None
        _SHARED_CONN_DB = None
    return _SHARED_CONN


def _reset_shared_conn():
    global _SHARED_CONN, _SHARED_CONN_DB
    if _SHARED_CONN is not None:
        try:
            _SHARED_CONN.close()
        except Exception:
            pass
    _SHARED_CONN = None
    _SHARED_CONN_DB = None


def _has_token_column():
    """旧版本的库里可能没有 runtime_resume_token 这一列。"""
    db = get_db_path()
    if db in _TOKEN_SCHEMA_OK:
        return _TOKEN_SCHEMA_OK[db]
    ok = False
    conn = _shared_conn()
    if conn is not None:
        try:
            cur = conn.cursor()
            cur.execute("PRAGMA table_info(agent_session_message)")
            ok = any(r[1] == "runtime_resume_token" for r in cur.fetchall())
        except Exception:
            ok = False
    _TOKEN_SCHEMA_OK[db] = ok
    return ok


def tokens_of(session_id):
    """取该会话用过的运行时会话 id（可能有多个）。带缓存。

    运行时会话 id ≠ Cherry 的会话 id（只有 dsh 恰好相同），
    它决定了 .claude\\projects\\<项目>\\<id>.jsonl 里的文件名。
    """
    if session_id in _TOKEN_CACHE:
        return _TOKEN_CACHE[session_id]
    out = []
    if _has_token_column():
        conn = _shared_conn()
        if conn is not None:
            try:
                cur = conn.cursor()
                cur.execute("""SELECT runtime_resume_token FROM agent_session_message
                               WHERE session_id = ? AND runtime_resume_token IS NOT NULL
                                 AND runtime_resume_token != ''
                               LIMIT 50""", (session_id,))
                out = list(dict.fromkeys(r[0] for r in cur.fetchall() if r[0]))
            except Exception:
                _reset_shared_conn()
                out = []
    _TOKEN_CACHE[session_id] = out
    return out


# ==========================================================
# 运行时目录定位
# ==========================================================

def claude_project_dir(workspace_path):
    if not CLAUDE_PROJECTS or not workspace_path:
        return None
    return os.path.join(CLAUDE_PROJECTS, claude_project_dir_name(workspace_path))


def dsh_project_dir(workspace_path):
    if not DSH_BASE_PATH or not workspace_path:
        return None
    try:
        return os.path.join(DSH_BASE_PATH, project_key(workspace_path))
    except ValueError:
        return None


def claude_session_files(workspace_path, ids):
    """claude-code 的会话记录文件（.jsonl）。"""
    out = []
    proj = claude_project_dir(workspace_path)
    if not proj or not os.path.isdir(proj):
        return out
    for i in ids:
        if not i:
            continue
        for name in (i + ".jsonl", encode_segment(i) + ".jsonl"):
            f = os.path.join(proj, name)
            if os.path.isfile(f):
                out.append(f)
        d = os.path.join(proj, i)
        if os.path.isdir(d):
            out.append(d)
    return list(dict.fromkeys(out))


def dsh_session_dirs(workspace_path, ids):
    """dsh 的会话目录。"""
    out = []
    proj = dsh_project_dir(workspace_path)
    if not proj or not os.path.isdir(proj):
        return out
    for i in ids:
        if not i:
            continue
        for name in (encode_segment(i), i):
            d = os.path.join(proj, name)
            if os.path.isdir(d):
                out.append(d)
    return list(dict.fromkeys(out))


def scan_runtime_for_ids(ids):
    """兜底：在 .dsh 和 .claude 下直接按 id 找（Cherry 升级改了命名规则时仍可用）。"""
    out = []
    ids = {i for i in ids if i}
    if not ids:
        return out
    if DSH_BASE_PATH and os.path.isdir(DSH_BASE_PATH):
        try:
            for proj in os.listdir(DSH_BASE_PATH):
                pp = os.path.join(DSH_BASE_PATH, proj)
                if not os.path.isdir(pp):
                    continue
                for i in ids:
                    d = os.path.join(pp, i)
                    if os.path.isdir(d):
                        out.append(d)
        except OSError:
            pass
    if CLAUDE_PROJECTS and os.path.isdir(CLAUDE_PROJECTS):
        try:
            for proj in os.listdir(CLAUDE_PROJECTS):
                pp = os.path.join(CLAUDE_PROJECTS, proj)
                if not os.path.isdir(pp):
                    continue
                for i in ids:
                    f = os.path.join(pp, i + ".jsonl")
                    if os.path.isfile(f):
                        out.append(f)
        except OSError:
            pass
    return list(dict.fromkeys(out))


def runtime_targets_for_session(session_id):
    """该会话的运行时会话记录：返回 [(显示名, 路径, 是否文件), ...]"""
    info = SESS.get(session_id) or {}
    ws_id = info.get("ws_id")
    ws_path = WS.get(ws_id, {}).get("resolved") if ws_id else None
    ids = [session_id] + tokens_of(session_id)

    out = []
    if ws_path:
        for f in claude_session_files(ws_path, ids):
            out.append(("claude", f, os.path.isfile(f)))
        for d in dsh_session_dirs(ws_path, ids):
            out.append(("dsh", d, False))
    if not out:
        for p in scan_runtime_for_ids(ids):
            out.append(("扫描命中", p, os.path.isfile(p)))
    return out


# ==========================================================
# 删除会话前的只读检查
#
# 删除是破坏性的，所以界面在动手前要先把「会失去什么」摆出来：
# 消息多少条、有没有会被一起删掉的运行时历史目录、有没有被置顶、
# 有没有被 IM 渠道绑定。
# 下面的查询全部带 session_id 条件（走索引），不做全表扫描 ——
# 别人几个 GB 的库也不会卡。
# ==========================================================

COPY_MARKERS = ("(副本)", "（副本）", "(copy)", "(Copy)", "(COPY)")


def is_copy_name(name):
    n = name or ""
    return any(m in n for m in COPY_MARKERS)


def dsh_dirs_for_id(session_id):
    """会被一起删掉的 DSH 历史目录。

    和 session-lib.js 的 findDshSessionDirs 保持一致的规则：
    <数据目录>/Agents/.dsh/sessions/<工作目录编码>/<会话id>/
    """
    out = []
    if not DSH_BASE_PATH or not session_id or not os.path.isdir(DSH_BASE_PATH):
        return out
    try:
        for proj in os.listdir(DSH_BASE_PATH):
            d = os.path.join(DSH_BASE_PATH, proj, session_id)
            if os.path.isdir(d):
                out.append(d)
    except OSError:
        pass
    return out


def session_msg_totals(ids, chunk=400):
    """一次查出这些会话各自的消息总条数（含失败/中间消息 —— 删除时是全部删掉）。

    分块是为了避开 SQLite 的 SQL 变量个数上限；查询本身走
    agent_session_message_session_created_id_idx 索引。
    """
    out = {}
    ids = [i for i in dict.fromkeys(ids) if i]
    conn = _shared_conn()
    if conn is None or not ids:
        return out
    cur = conn.cursor()
    for i in range(0, len(ids), chunk):
        part = ids[i:i + chunk]
        try:
            rows = cur.execute(
                "SELECT session_id, COUNT(*) FROM agent_session_message "
                "WHERE session_id IN (%s) GROUP BY session_id" % ",".join("?" * len(part)),
                part).fetchall()
            for sid, n in rows:
                out[sid] = n
        except Exception:
            pass
    return out


def inspect_session_for_delete(session_id):
    """只读地收集「删掉这个会话会失去什么」，给确认框用。绝不写入。"""
    info = {"msg_total": None, "msg_copyable": None, "pinned": False, "channel": False,
            "dsh_dirs": dsh_dirs_for_id(session_id), "claude_files": 0}
    conn = _shared_conn()
    if conn is not None:
        cur = conn.cursor()
        probes = (
            ("msg_total", "SELECT COUNT(*) FROM agent_session_message WHERE session_id = ?", 0),
            ("msg_copyable", "SELECT COUNT(*) FROM agent_session_message WHERE session_id = ? "
                             "AND status != 'error' AND delivery_sender_session_id IS NULL", 0),
            ("pinned", "SELECT 1 FROM pin WHERE entity_type = 'session' AND entity_id = ? LIMIT 1", False),
            ("channel", "SELECT 1 FROM agent_channel WHERE session_id = ? LIMIT 1", False),
        )
        for key, sql, dflt in probes:
            try:
                row = cur.execute(sql, (session_id,)).fetchone()
                info[key] = bool(row[0]) if dflt is False else (row[0] if row else 0)
            except Exception:
                pass  # 老版本数据库里可能没有 pin / agent_channel 表
    # claude-code 的记录文件是按「运行时 id」命名的，引擎不会删它们
    # （同一个运行时 id 可能被别的会话接着用，删文件有风险）—— 这里只数一下，如实告知
    try:
        info["claude_files"] = len([p for _n, p, isf in runtime_targets_for_session(session_id) if isf])
    except Exception:
        info["claude_files"] = 0
    return info


# ==========================================================
# 自动备份的管理
#
# 每次写数据库（复制、删除）之前都会用 VACUUM INTO 生成一份**整个**数据库的
# 快照。这是好事，但一份就是一百多 MB，用久了能在数据目录里堆出好几个 GB
# （作者机器上实测堆到 63 份 / 5.9 GB）。所以：
#   · 启动时把份数和体积写进日志，让人看得见；
#   · 提供「清理旧备份」，只保留最新的几份。
# 安全阀：只认「数据目录正下方、名字符合 <库名>.bak-sessioncopy- 前缀」的文件。
# ==========================================================

BACKUP_PREFIX = "cherrystudio.sqlite.bak-sessioncopy-"


def human_size(n):
    n = float(n or 0)
    for unit, div in (("GB", 1024 ** 3), ("MB", 1024 ** 2), ("KB", 1024)):
        if n >= div:
            return f"{n / div:.2f} {unit}" if unit == "GB" else f"{n / div:.1f} {unit}"
    return f"{int(n)} B"


def list_local_backups():
    """数据目录里的自动备份（新的在前）。读不到就返回空列表。"""
    out = []
    if not DATA_DIR:
        return out
    try:
        names = os.listdir(DATA_DIR)
    except OSError:
        return out
    for name in names:
        if not name.startswith(BACKUP_PREFIX):
            continue
        p = os.path.join(DATA_DIR, name)
        try:
            st = os.stat(p)
        except OSError:
            continue
        if not os.path.isfile(p):
            continue
        out.append({"path": p, "name": name, "size": st.st_size, "mtime": st.st_mtime})
    out.sort(key=lambda x: -x["mtime"])
    return out


# ==========================================================
# 删除留底（不是整库备份）
#
# 删会话时会在这里留一份：<_数据目录>\_deleted_sessions\<时间戳>-<n>sessions\
#   journal.json —— 被删的那些行的原样拷贝（几十 KB ~ 几 MB）
#   dsh\...      —— 被删的运行时历史目录（改名搬进来，不复制）
# 用户随时可以清理它们，所以这里只做「列目录 + 数大小」，不解析 json。
# ==========================================================

JOURNAL_DIR_NAME = "_deleted_sessions"


def list_local_journals():
    """删除留底（新的在前）。"""
    out = []
    if not DATA_DIR:
        return out
    root = os.path.join(DATA_DIR, JOURNAL_DIR_NAME)
    if not os.path.isdir(root):
        return out
    try:
        names = os.listdir(root)
    except OSError:
        return out
    for name in names:
        d = os.path.join(root, name)
        f = os.path.join(d, "journal.json")
        if not os.path.isfile(f):
            continue
        size = 0
        for base, _dirs, files in os.walk(d):
            for fn in files:
                try:
                    size += os.path.getsize(os.path.join(base, fn))
                except OSError:
                    pass
        try:
            mt = os.path.getmtime(f)
        except OSError:
            mt = 0
        # 目录名形如 2026-10-04T00-52-11-123Z-3sessions，末尾那个数字是会话个数
        n = None
        m = re.search(r"-(\d+)sessions$", name)
        if m:
            n = int(m.group(1))
        out.append({"dir": d, "file": f, "name": name, "size": size, "mtime": mt, "sessions": n})
    out.sort(key=lambda x: -x["mtime"])
    return out


# ==========================================================
# 打开路径
# ==========================================================

def open_path(target, select=False):
    """在系统文件管理器里打开目录（或定位到文件）。跨平台。"""
    try:
        t = os.path.normpath(target)
        if not os.path.exists(t):
            return False, "路径不存在"
        if os.name == "nt":
            if select and os.path.isfile(t):
                subprocess.Popen(["explorer", "/select,", t])
            else:
                os.startfile(t)
        elif sys.platform == "darwin":
            subprocess.Popen(["open", "-R", t] if select else ["open", t])
        else:
            # Linux 没有统一的「定位到文件」，退化成打开所在目录
            subprocess.Popen(["xdg-open",
                              os.path.dirname(t) if (select and os.path.isfile(t)) else t])
        return True, None
    except Exception as e:
        return False, str(e)


# ==========================================================
# 扫描对话框
# ==========================================================

class StartupScanDialog(QDialog):
    STEPS = [
        ("db_path", "查找数据目录（cherrystudio.sqlite）"),
        ("agents_dir", "查找 Agents 目录"),
        ("dsh_sessions", "查找 DSH 会话记录目录（.dsh\\sessions）"),
        ("claude_projects", "查找 Claude 会话记录目录（.claude\\projects）"),
        ("bun_path", "查找 bun.exe 运行时"),
        ("tools_dir", "查找会话复制工具（session-tools）"),
    ]

    def __init__(self):
        super().__init__()
        self.setWindowTitle("正在初始化...")
        self.setFixedSize(520, 400)
        self.setStyleSheet(f"""
            QDialog {{ background-color: #fafafa; }}
            QLabel {{ color: #333333; font-family: "微软雅黑"; font-size: 13px; }}
            QProgressBar {{ border: 1px solid {CHERRY}; border-radius: 6px; background: #ffffff; height: 12px; }}
            QProgressBar::chunk {{ background-color: {CHERRY}; border-radius: 5px; }}
        """)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(28, 24, 28, 20)
        lay.setSpacing(12)
        t = QLabel("🔍  首次启动 · 正在扫描电脑")
        t.setStyleSheet(f"color: {CHERRY}; font-size: 16px; font-weight: bold;")
        lay.addWidget(t)
        hint = QLabel("正在查找 Cherry Studio 的数据目录、运行时目录和工具...")
        hint.setWordWrap(True)
        hint.setStyleSheet("color: #888888; font-size: 12px;")
        lay.addWidget(hint)
        lay.addSpacing(4)
        self.step_labels = []
        for _, n in self.STEPS:
            l = QLabel(f"◯  {n}")
            l.setStyleSheet("color: #888888; font-size: 12px; padding: 2px 0;")
            lay.addWidget(l)
            self.step_labels.append(l)
        lay.addStretch()
        self.progress = QProgressBar()
        self.progress.setRange(0, len(self.STEPS))
        self.progress.setValue(0)
        lay.addWidget(self.progress)
        self.result = None

    def start_scan(self):
        QApplication.processEvents()
        self.result = scan_all_paths()
        keys = [k for k, _ in self.STEPS]
        for i, k in enumerate(keys):
            QTimer.singleShot(110 * (i + 1), lambda idx=i, key=k: self.mark_step(idx, key))
        QTimer.singleShot(110 * len(keys) + 200, self.finish_scan)

    def mark_step(self, idx, key):
        l = self.step_labels[idx]
        val = self.result.get(key)
        if val:
            l.setText(f"✅  {l.text()[3:]}")
            l.setStyleSheet("color: #22a06b; font-size: 12px; padding: 2px 0;")
        else:
            l.setText(f"⚠️  {l.text()[3:]}  （未找到）")
            l.setStyleSheet("color: #e67e22; font-size: 12px; padding: 2px 0;")
        self.progress.setValue(idx + 1)
        QApplication.processEvents()

    def finish_scan(self):
        if self.result:
            cfg = load_config()
            cfg["data_dir"] = self.result.get("data_dir")
            cfg["data_dir_locked"] = False      # 自动选定，换机器后可重新识别
            cfg["data_dir_asked"] = False       # 若发现多个候选，启动时让用户确认
            if self.result.get("bun_path"):
                cfg["bun_path"] = self.result["bun_path"]
            if self.result.get("tools_dir"):
                cfg["tools_dir"] = self.result["tools_dir"]
            cfg["scan_completed"] = True
            cfg.pop("dsh_base", None)
            cfg.pop("data_dir_manual", None)
            save_config({k: v for k, v in cfg.items() if v is not None})
        self.accept()


CHERRY = "#FF6B5C"
CHERRY_HOVER = "#FF8A7A"
CHERRY_SOFT = "#FFE8E4"
PANEL_BG = "rgba(255, 255, 255, 235)"


class RootFrame(QFrame):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("rootFrame")
        self._bg_pixmap = None
        bg = resource_path("Background.png")
        if os.path.exists(bg):
            pix = QPixmap(bg)
            if not pix.isNull():
                self._bg_pixmap = pix

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setRenderHint(QPainter.SmoothPixmapTransform)
        rect = self.rect().adjusted(1, 1, -1, -1)
        path = QPainterPath()
        path.addRoundedRect(QRectF(rect), 13, 13)
        if self._bg_pixmap and not self._bg_pixmap.isNull():
            p.setClipPath(path)
            sc = self._bg_pixmap.scaled(self.size(), Qt.KeepAspectRatioByExpanding, Qt.SmoothTransformation)
            p.drawPixmap((self.width() - sc.width()) // 2, (self.height() - sc.height()) // 2, sc)
            p.fillPath(path, QColor(255, 255, 255, 150))
        else:
            p.fillPath(path, QColor("#fafafa"))
        p.setClipping(False)
        p.setPen(QPen(QColor(CHERRY), 2))
        p.setBrush(Qt.NoBrush)
        p.drawRoundedRect(rect, 13, 13)


def build_list_cmd(bun, script, data_dir=None):
    """构造「列出会话」命令。

    一定要把数据目录传给 JS —— 脚本以前写死了作者的电脑路径，
    不传的话在别人机器上会去读写一个不存在的目录。
    """
    cmd = [bun, script, "--list"]
    if data_dir:
        cmd += ["--data-dir", data_dir]
    return cmd


def build_copy_cmd(bun, script, session_id, data_dir=None, with_backup=False):
    """构造「复制会话」命令。

    默认不带 --with-backup：不再每次都备份一整个数据库（一份 ≈130 MB）。
    界面设置里勾了「写入前备份整个数据库」才会加上。
    """
    cmd = [bun, script, "--session", session_id]
    if with_backup:
        cmd.append("--with-backup")
    if data_dir:
        cmd += ["--data-dir", data_dir]
    return cmd


def build_delete_cmd(bun, script, session_ids, data_dir=None, with_backup=False):
    """构造「删除会话」命令（可以一次删多个）。

    ⚠️ id 是拼在参数里的，Windows 命令行总长约 3 万字符封顶，
       所以调用方要分批（见下面 DELETE_BATCH）。
    """
    cmd = [bun, script, "--delete-many", ",".join(session_ids)]
    if with_backup:
        cmd.append("--with-backup")
    if data_dir:
        cmd += ["--data-dir", data_dir]
    return cmd


def build_prune_deleted_cmd(bun, script, keep, data_dir=None):
    """构造「清理删除留底」命令。"""
    cmd = [bun, script, "--prune-deleted", "--keep", str(int(keep))]
    if data_dir:
        cmd += ["--data-dir", data_dir]
    return cmd


def want_full_backup():
    """设置里的「写入前备份整个数据库」。默认关 —— 一份 ≈130 MB，堆起来很占地方。"""
    return bool(load_config().get("backup_before_write"))


class CopyWorker(QThread):
    done = Signal(int, str, str)

    def __init__(self, bun, script, sid, data_dir=None, with_backup=False):
        super().__init__()
        self.bun, self.script, self.sid, self.data_dir = bun, script, sid, data_dir
        self.with_backup = with_backup

    def run(self):
        try:
            r = subprocess.run(build_copy_cmd(self.bun, self.script, self.sid,
                                              self.data_dir, self.with_backup),
                               capture_output=True, text=True, encoding="utf-8",
                               errors="replace", timeout=900)
            self.done.emit(r.returncode, r.stdout or "", r.stderr or "")
        except subprocess.TimeoutExpired:
            self.done.emit(-1, "", "复制超时（15 分钟没有任何响应）。\n"
                                   "数据库可能被占用，建议关掉 Cherry Studio 后重试。")
        except Exception as e:
            self.done.emit(-1, "", str(e))


DELETE_BATCH = 300


class DeleteWorker(QThread):
    """在后台线程里执行删除。

    为什么要放线程里：整个界面只有一个线程，删除（尤其是先备份一个大库）
    可能要好几十秒，直接跑会把界面冻住。
    为什么要分批：id 要拼进命令行参数，Windows 上限约 3 万字符。
    """
    done = Signal(int, str, str)

    def __init__(self, bun, script, session_ids, data_dir=None, with_backup=False):
        super().__init__()
        self.bun, self.script = bun, script
        self.ids = [i for i in session_ids if i]
        self.data_dir = data_dir
        self.with_backup = with_backup

    def run(self):
        outs = []
        for i in range(0, len(self.ids), DELETE_BATCH):
            batch = self.ids[i:i + DELETE_BATCH]
            try:
                r = subprocess.run(build_delete_cmd(self.bun, self.script, batch,
                                                    self.data_dir, self.with_backup),
                                   capture_output=True, text=True, encoding="utf-8",
                                   errors="replace", timeout=900)
            except subprocess.TimeoutExpired:
                self.done.emit(-1, "\n".join(outs),
                               "删除超时（15 分钟没有任何响应），可能只完成了一部分。\n"
                               "数据库可能被占用，建议关掉 Cherry Studio 后重试。")
                return
            except Exception as e:
                self.done.emit(-1, "\n".join(outs), str(e))
                return
            outs.append(r.stdout or "")
            if r.returncode != 0:
                self.done.emit(r.returncode, "\n".join(outs), r.stderr or "")
                return
        self.done.emit(0, "\n".join(outs), "")


class PruneBackupsWorker(QThread):
    """后台清理旧备份 / 旧留底。

    删文件本身很快，但一次几十个大文件（几百 MB 到几 GB）在慢盘上
    也能卡住界面好几秒，所以照样丢到线程里。
    kind: "backup" = 整库备份；"journal" = 删除留底
    """
    done = Signal(int, str, str)

    def __init__(self, bun, script, keep, data_dir=None, kind="backup"):
        super().__init__()
        self.bun, self.script, self.keep, self.data_dir, self.kind = bun, script, keep, data_dir, kind

    def cmd(self):
        flag = "--prune-deleted" if self.kind == "journal" else "--prune-backups"
        cmd = [self.bun, self.script, flag, "--keep", str(self.keep)]
        if self.data_dir:
            cmd += ["--data-dir", self.data_dir]
        return cmd

    def run(self):
        try:
            r = subprocess.run(self.cmd(), capture_output=True, text=True, encoding="utf-8",
                               errors="replace", timeout=300)
            self.done.emit(r.returncode, r.stdout or "", r.stderr or "")
        except subprocess.TimeoutExpired:
            self.done.emit(-1, "", "清理超时（5 分钟没有响应）。")
        except Exception as e:
            self.done.emit(-1, "", str(e))


class StickyAgentBar(QWidget):
    def __init__(self, parent, tree):
        super().__init__(parent)
        self.tree = tree
        self.current_agent_item = None
        self.setFixedHeight(32)
        self.setObjectName("stickyBar")
        self.setStyleSheet(f"QWidget#stickyBar {{ background-color: {CHERRY_SOFT}; border: 1px solid {CHERRY}; border-radius: 6px; }}")
        lay = QHBoxLayout(self)
        lay.setContentsMargins(10, 0, 6, 0)
        lay.setSpacing(8)
        self.label = QLabel("")
        self.label.setStyleSheet(f"color: {CHERRY}; font-family: '微软雅黑'; font-size: 12px; font-weight: bold; background: transparent; border: none;")
        lay.addWidget(self.label)
        lay.addStretch()
        self.cb = QPushButton("▲  收起")
        self.cb.setFixedHeight(22)
        self.cb.setCursor(Qt.PointingHandCursor)
        self.cb.setStyleSheet(f"QPushButton {{ background-color: #ffffff; border: 1px solid {CHERRY}; border-radius: 4px; color: {CHERRY}; padding: 0 10px; font-size: 11px; font-family: '微软雅黑'; }} QPushButton:hover {{ background-color: #fff6f4; }}")
        self.cb.clicked.connect(self.collapse)
        lay.addWidget(self.cb)
        self.hide()

    def refresh(self):
        if self.tree.topLevelItemCount() == 0:
            self.hide(); return
        top = None
        for y in range(0, 40, 4):
            top = self.tree.itemAt(0, y)
            if top is not None:
                break
        if top is None:
            self.hide(); return
        parent = top.parent()
        if parent is None:
            self.hide(); return
        data = parent.data(0, Qt.UserRole)
        if not data or data.get("type") != "agent":
            self.hide(); return
        self.current_agent_item = parent
        self.label.setText(f"📌  {parent.text(0)}")
        self.show()

    def collapse(self):
        if self.current_agent_item:
            self.current_agent_item.setExpanded(False)
        self.hide()


class FirstUseWarning(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("使用须知")
        self.setModal(True)
        self.resize(520, 420)
        self.setStyleSheet(f"""
            QDialog {{ background-color: #fafafa; }}
            QLabel {{ color: #333333; font-family: "微软雅黑"; font-size: 13px; }}
            QCheckBox {{ color: #666666; font-size: 12px; }}
            QPushButton {{ background-color: {CHERRY}; border: 1px solid {CHERRY}; border-radius: 6px; padding: 8px 24px; color: #ffffff; font-size: 13px; min-width: 80px; }}
            QPushButton:hover {{ background-color: {CHERRY_HOVER}; }}
        """)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(28, 24, 28, 20)
        lay.setSpacing(12)
        t = QLabel("⚠️  使用须知")
        t.setStyleSheet(f"color: {CHERRY}; font-size: 17px; font-weight: bold;")
        lay.addWidget(t)
        txt = QLabel("本工具为个人项目，直接读写 Cherry Studio 的本地数据库。\n\n"
                     "· 写入是一个事务：要么全成功，要么一点都不改\n"
                     "· 默认不会每次备份整个数据库（一份有一百多 MB）；"
                     "想要额外保险可以在「设置」里打开「写入前备份整个数据库」\n"
                     "· 「删除会话」是永久删除：确认框里会写清楚删掉什么；"
                     "工具只留一份很小的「删除留底」，删掉留底就真的找不回来了\n"
                     "· 建议首次使用前先手动备份 Cherry Studio 数据\n"
                     "· 本工具按“现状”提供，作者不对数据丢失负责")
        txt.setWordWrap(True)
        txt.setStyleSheet("color: #555555; line-height: 22px; font-size: 13px;")
        lay.addWidget(txt)
        lay.addStretch()
        b = QHBoxLayout()
        self.checkbox = QCheckBox("不再提示")
        b.addWidget(self.checkbox)
        b.addStretch()
        ok = QPushButton("我知道了")
        ok.clicked.connect(self.on_ok)
        b.addWidget(ok)
        lay.addLayout(b)

    def on_ok(self):
        if self.checkbox.isChecked():
            cfg = load_config()
            cfg["hide_first_warning"] = True
            save_config(cfg)
        self.accept()


class AboutDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle(f"关于 {APP_NAME}")
        self.setModal(True)
        self.resize(520, 460)
        self.setStyleSheet(f"""
            QDialog {{ background-color: #fafafa; }}
            QLabel {{ color: #333333; font-family: "微软雅黑"; font-size: 13px; }}
            QPushButton {{ background-color: #ffffff; border: 1px solid {CHERRY}; border-radius: 6px; padding: 7px 16px; color: {CHERRY}; font-size: 13px; min-width: 80px; }}
            QPushButton:hover {{ background-color: #fff6f4; }}
        """)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(28, 24, 28, 20)
        lay.setSpacing(10)
        n = QLabel(APP_NAME)
        n.setStyleSheet(f"color: {CHERRY}; font-size: 20px; font-weight: bold;")
        n.setAlignment(Qt.AlignCenter)
        lay.addWidget(n)
        v = QLabel(APP_VERSION)
        v.setStyleSheet("color: #888888; font-size: 13px;")
        v.setAlignment(Qt.AlignCenter)
        lay.addWidget(v)
        lay.addSpacing(8)
        al = QLabel(f'<span style="color:#666666;">by.</span><a href="{APP_GITHUB}" style="color:{CHERRY}; text-decoration:none; margin-left:2px;">{APP_AUTHOR}</a>')
        al.setOpenExternalLinks(True)
        al.setStyleSheet("font-size: 13px;")
        al.setAlignment(Qt.AlignCenter)
        lay.addWidget(al)
        lay.addSpacing(12)
        line = QFrame()
        line.setFrameShape(QFrame.HLine)
        line.setStyleSheet("background-color: #eeeeee; max-height: 1px;")
        lay.addWidget(line)
        lay.addSpacing(6)
        d = QLabel("为 Cherry Studio 提供会话复制（分支）与删除清理的小工具。\n通过调用本地脚本，实现会话的复制、删除与备份管理。\n100% 纯 AI 无天然制造")
        d.setWordWrap(True)
        d.setStyleSheet("color: #666666; line-height: 20px;")
        lay.addWidget(d)
        lay.addSpacing(6)
        dt = QLabel("第三方依赖")
        dt.setStyleSheet(f"color: {CHERRY}; font-weight: bold; font-size: 13px;")
        lay.addWidget(dt)
        dp = QLabel('· PySide6 (LGPL-3.0) — 界面框架<br>· Bun (MIT) — 运行会话复制脚本<br>· Cherry Studio (AGPL-3.0) — 数据来源')
        dp.setWordWrap(True)
        dp.setStyleSheet("color: #666666; font-size: 12px; line-height: 20px;")
        lay.addWidget(dp)
        lay.addStretch()
        row = QHBoxLayout()
        row.addStretch()
        cb = QPushButton("关闭")
        cb.clicked.connect(self.accept)
        row.addWidget(cb)
        lay.addLayout(row)


class SettingsDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("设置")
        self.setModal(True)
        self.resize(700, 420)
        self.setStyleSheet(f"""
            QDialog {{ background-color: #fafafa; }}
            QLabel {{ color: #333333; font-family: "微软雅黑"; font-size: 13px; }}
            QLineEdit {{ background-color: #ffffff; border: 1px solid #dddddd; border-radius: 6px; padding: 7px 10px; color: #333333; font-family: "Consolas", "微软雅黑"; font-size: 12px; }}
            QLineEdit:focus {{ border-color: {CHERRY}; }}
            QPushButton {{ background-color: #ffffff; border: 1px solid {CHERRY}; border-radius: 6px; padding: 7px 16px; color: {CHERRY}; font-size: 13px; min-width: 70px; }}
            QPushButton:hover {{ background-color: #fff6f4; }}
        """)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(24, 20, 24, 20)
        lay.setSpacing(10)
        r = QHBoxLayout()
        t = QLabel("路径配置")
        t.setStyleSheet(f"color: {CHERRY}; font-size: 15px; font-weight: bold;")
        r.addWidget(t)
        r.addStretch()
        ab = QPushButton("🔄 重新扫描")
        ab.clicked.connect(self.on_scan)
        r.addWidget(ab)
        lay.addLayout(r)

        lay.addWidget(QLabel("数据目录（含 cherrystudio.sqlite 与 Agents\\）"))
        r0 = QHBoxLayout()
        self.data_edit = QLineEdit(DATA_DIR or "")
        self.data_edit.setPlaceholderText("留空 = 自动识别")
        r0.addWidget(self.data_edit)
        b0 = QPushButton("选择...")
        b0.setToolTip("从扫描到的候选目录里选一个")
        b0.clicked.connect(self.pick_data_dir)
        r0.addWidget(b0)
        b0b = QPushButton("浏览...")
        b0b.clicked.connect(lambda: self.pick_dir(self.data_edit))
        r0.addWidget(b0b)
        lay.addLayout(r0)

        lay.addWidget(QLabel("bun.exe 路径"))
        r1 = QHBoxLayout()
        self.bun_edit = QLineEdit(BUN_PATH or "")
        self.bun_edit.setPlaceholderText("留空 = 自动识别")
        r1.addWidget(self.bun_edit)
        b1 = QPushButton("浏览...")
        b1.clicked.connect(self.pick_bun)
        r1.addWidget(b1)
        lay.addLayout(r1)

        lay.addWidget(QLabel("工具目录（含 duplicate-session.js）"))
        r2 = QHBoxLayout()
        self.tools_edit = QLineEdit(TOOLS_DIR or "")
        self.tools_edit.setPlaceholderText("留空 = 自动识别")
        r2.addWidget(self.tools_edit)
        b2 = QPushButton("浏览...")
        b2.clicked.connect(lambda: self.pick_dir(self.tools_edit))
        r2.addWidget(b2)
        lay.addLayout(r2)

        self.info = QLabel(self._info_text())
        self.info.setWordWrap(True)
        self.info.setStyleSheet("color: #777777; font-size: 11px; font-family: 'Consolas';")
        lay.addWidget(self.info)

        # 写入前是否做整库快照。默认**关**：一份 ≈130 MB，堆起来很占地方
        # （作者机器上就堆过 6 GB）。勾上就是老行为。
        self.backup_check = QCheckBox("写入前备份整个数据库（更保险，但每写一次就多占一份整库大小）")
        self.backup_check.setChecked(want_full_backup())
        self.backup_check.setStyleSheet("color: #444444; font-size: 12px;")
        lay.addWidget(self.backup_check)

        lay.addStretch()
        brow = QHBoxLayout()
        brow.addStretch()
        cb = QPushButton("取消")
        cb.clicked.connect(self.reject)
        brow.addWidget(cb)
        sb = QPushButton("保存")
        sb.setStyleSheet(f"QPushButton {{ background-color: {CHERRY}; border: 1px solid {CHERRY}; border-radius: 6px; padding: 7px 20px; color: #ffffff; font-size: 13px; min-width: 70px; }} QPushButton:hover {{ background-color: {CHERRY_HOVER}; }}")
        sb.clicked.connect(self.on_save)
        brow.addWidget(sb)
        lay.addLayout(brow)

    def _info_text(self):
        def mark(p):
            return "✅" if p and os.path.exists(p) else "❌"
        lines = [f"{mark(DSH_BASE_PATH)} DSH 会话记录  {DSH_BASE_PATH or '(未找到)'}",
                 f"{mark(CLAUDE_PROJECTS)} Claude 会话记录  {CLAUDE_PROJECTS or '(未找到)'}"]
        if DATA_DIR_SOURCE:
            lines.append(f"数据目录来源：{DATA_DIR_SOURCE}")
        if DATA_CANDIDATES:
            lines.append("检测到的候选数据目录（最近写入的在前）：")
            for c in DATA_CANDIDATES:
                flag = "✔" if c["path"] == DATA_DIR else " "
                lines.append(f"   {flag} {c['path']}   [{c['mtime']} · {c['size_mb']}MB]")
        return "\n".join(lines)

    def pick_dir(self, edit):
        p = QFileDialog.getExistingDirectory(self, "选择文件夹")
        if p:
            edit.setText(p)

    def pick_data_dir(self):
        dlg = DataDirPickerDialog(self)
        if dlg.exec() == QDialog.Accepted and dlg.selected:
            self.data_edit.setText(dlg.selected)

    def pick_bun(self):
        p, _ = QFileDialog.getOpenFileName(self, "选择 bun.exe", "", "可执行文件 (*.exe);;所有文件 (*)")
        if p:
            self.bun_edit.setText(p)

    def on_scan(self):
        r = scan_all_paths(with_counts=True)
        self.data_edit.setText(r.get("data_dir") or "")
        self.bun_edit.setText(r.get("bun_path") or "")
        self.tools_edit.setText(r.get("tools_dir") or "")
        lines = [f"扫描结果（数据目录来源：{r.get('data_dir_source') or '未知'}）：",
                 f"  DSH 会话记录  {r.get('dsh_sessions') or '(未找到)'}",
                 f"  Claude 会话记录  {r.get('claude_projects') or '(未找到)'}"]
        for c in r.get("data_candidates", []):
            flag = "✔" if c["path"] == r.get("data_dir") else " "
            cnt = ""
            if c.get("sessions") is not None:
                cnt = f" · {c['agents']} Agent/{c['sessions']} 会话"
            lines.append(f"  {flag} {c['path']}   [{c['mtime']} · {c['size_mb']}MB{cnt}]")
        self.info.setText("\n".join(lines))

    def on_save(self):
        data = self.data_edit.text().strip()
        bun = self.bun_edit.text().strip()
        tools = self.tools_edit.text().strip()
        if data and not os.path.exists(os.path.join(data, "cherrystudio.sqlite")):
            QMessageBox.warning(self, "路径无效", f"该目录下找不到 cherrystudio.sqlite：\n{data}"); return
        if bun and not os.path.exists(bun):
            QMessageBox.warning(self, "路径无效", f"bun.exe 不存在：\n{bun}"); return
        if tools and not os.path.exists(os.path.join(tools, "duplicate-session.js")):
            QMessageBox.warning(self, "路径无效", "找不到 duplicate-session.js"); return
        cfg = load_config()
        cfg.pop("dsh_base", None)
        cfg.pop("data_dir_manual", None)
        if data:
            cfg["data_dir"] = data
            # 与自动探测结果一致就不锁定，这样换机器后仍能自动跟随
            cfg["data_dir_locked"] = (data != (scan_all_paths().get("data_dir") or ""))
        else:
            cfg["data_dir"] = None
            cfg["data_dir_locked"] = False
        cfg["data_dir_asked"] = True
        cfg["bun_path"] = bun or None
        cfg["tools_dir"] = tools or None
        if self.backup_check.isChecked():
            cfg["backup_before_write"] = True
        else:
            cfg.pop("backup_before_write", None)
        save_config({k: v for k, v in cfg.items() if v is not None})
        self.accept()


class AgentInfoDialog(QDialog):
    def __init__(self, parent, agent_id):
        super().__init__(parent)
        self.setWindowTitle("Agent 详情")
        self.setModal(True)
        self.resize(660, 420)
        self.setStyleSheet(f"""
            QDialog {{ background-color: #fafafa; }}
            QLabel {{ color: #333333; font-family: "微软雅黑"; font-size: 12px; }}
            QPushButton {{ background-color: #ffffff; border: 1px solid {CHERRY}; border-radius: 6px; padding: 7px 16px; color: {CHERRY}; font-size: 13px; min-width: 70px; }}
            QPushButton:hover {{ background-color: #fff6f4; }}
        """)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(22, 18, 22, 18)
        lay.setSpacing(8)
        a = AGENTS.get(agent_id, {})
        t = QLabel(f"{a.get('avatar', '🤖')}  {a.get('name', '?')}")
        t.setStyleSheet(f"color: {CHERRY}; font-size: 16px; font-weight: bold;")
        lay.addWidget(t)

        lines = [f"Agent ID : {agent_id}", f"类型     : {a.get('type', '?')}"]
        lines.append(f"Agent 目录 : {a.get('dir') or '(无)'}   {'✅' if a.get('exists') else '❌'}")
        lines.append("")
        lines.append(f"会话数   : {len(AGENT_SESSIONS.get(agent_id, []))}")
        lines.append("工作目录 :")
        for wid, cnt in AGENT_WORKSPACES.get(agent_id, []):
            w = WS.get(wid, {})
            flag = "✅" if w.get("resolved") else "❌"
            opt = "" if w.get("status") == "有效" else f"  [{w.get('status')}]"
            lines.append(f"  {flag} {w.get('name')}  ({cnt} 个会话){opt}")
            lines.append(f"      {w.get('path')}")
            if w.get("resolved") and w["resolved"] != w.get("path"):
                lines.append(f"      → 实际: {w['resolved']}")
        if not AGENT_WORKSPACES.get(agent_id):
            lines.append("  (无)")

        body = QLabel("\n".join(lines))
        body.setWordWrap(True)
        body.setTextInteractionFlags(Qt.TextSelectableByMouse)
        body.setStyleSheet("color: #444444; font-family: 'Consolas','微软雅黑'; font-size: 12px; line-height: 19px;")
        lay.addWidget(body)
        lay.addStretch()
        row = QHBoxLayout()
        row.addStretch()
        cb = QPushButton("关闭")
        cb.clicked.connect(self.accept)
        row.addWidget(cb)
        lay.addLayout(row)


class ConfirmDeleteDialog(QDialog):
    """删除单个会话的确认框。

    破坏性操作的三条纪律（「使用须知」里承诺过要让人看得见后果）：
      1) 把「会失去什么」写清楚：名字、消息条数、跟着一起删掉的运行时目录；
      2) 默认按钮是「取消」—— 回车和 Esc 都不会误删；
      3) 必须先手动勾一下确认，删除按钮才可用。
    """

    def __init__(self, parent, name, session_id, info, is_deleted=False):
        super().__init__(parent)
        self.setWindowTitle("删除会话")
        self.setModal(True)
        self.setMinimumWidth(580)
        self.setStyleSheet(f"""
            QDialog {{ background-color: #fafafa; }}
            QLabel {{ color: #333333; font-family: "微软雅黑"; font-size: 13px; }}
            QCheckBox {{ color: #444444; font-size: 12px; }}
            QPushButton {{ background-color: #ffffff; border: 1px solid {CHERRY}; border-radius: 6px;
                           padding: 7px 18px; color: {CHERRY}; font-size: 13px; min-width: 84px; }}
            QPushButton:hover {{ background-color: #fff6f4; }}
            QPushButton#dangerBtn {{ background-color: #e0574a; border: 1px solid #e0574a; color: #ffffff; font-weight: bold; }}
            QPushButton#dangerBtn:hover {{ background-color: #cc4636; }}
            QPushButton#dangerBtn:disabled {{ background-color: #f0f0f0; border-color: #e6e6e6; color: #bbbbbb; }}
        """)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(26, 22, 26, 20)
        lay.setSpacing(12)

        t = QLabel("🗑  删除会话")
        t.setStyleSheet(f"color: {CHERRY}; font-size: 17px; font-weight: bold;")
        lay.addWidget(t)

        # 名字可能含 < > &，用 RichText 拼表格就必须转义
        safe_name = html.escape(name or "(未命名)")
        msg = info.get("msg_total")
        msg_txt = f"{msg} 条" if isinstance(msg, int) else "（读不出来）"
        rows = [
            ("名字", f"<b>{safe_name}</b>"),
            ("会话 ID", f'<span style="font-family:Consolas,monospace;">{html.escape(str(session_id))}</span>'),
            ("消息", f"{msg_txt} —— 会一起删掉"),
        ]
        dsh_n = len(info.get("dsh_dirs") or [])
        rows.append(("运行时记录", f"{dsh_n} 个 DSH 历史目录 —— 会一起删掉" if dsh_n else "无"))
        if info.get("claude_files"):
            rows.append(("", f'<span style="color:#999999;">另有 {info["claude_files"]} 个 claude-code '
                             '记录文件不会被删除（那是运行时自己的文件，留着不影响使用）</span>'))
        table = "".join(
            f'<tr><td style="color:#999999;padding:2px 12px 2px 0;white-space:nowrap;">{k}</td>'
            f'<td style="padding:2px 0;">{v}</td></tr>' for k, v in rows)
        body = QLabel(f"<table cellspacing='0' cellpadding='0'>{table}</table>")
        body.setTextFormat(Qt.RichText)
        body.setWordWrap(True)
        body.setTextInteractionFlags(Qt.TextSelectableByMouse)
        lay.addWidget(body)

        warn = QLabel("⚠️  删了就没了：Cherry Studio 里没法恢复，也没有「撤销」。\n"
                      "工具只会把这次删掉的那几行记在一份很小的「删除留底」里"
                      "（几十 KB ~ 几 MB，不是整个数据库），万一需要还能还原。\n"
                      "Cherry Studio 如果正开着，删完请重启它 —— 否则它可能还显示在列表里。")
        warn.setWordWrap(True)
        warn.setStyleSheet("color: #b3402f; background-color: #fdf1ee; border: 1px solid #f6d5cd;"
                           "border-radius: 8px; padding: 10px 12px; font-size: 12px; line-height: 20px;")
        lay.addWidget(warn)

        if want_full_backup():
            bk = QLabel("（设置里打开了「写入前备份整个数据库」，所以这次还会另存一份整库快照。）")
            bk.setWordWrap(True)
            bk.setStyleSheet("color: #666666; font-size: 11px;")
            lay.addWidget(bk)

        extra = []
        if info.get("pinned"):
            extra.append("· 这个会话在 Cherry Studio 里是「置顶」的，置顶记录也会一起清掉。")
        if info.get("channel"):
            extra.append("· 这个会话正被一个 IM 渠道绑定（比如 Telegram / 微信），删除后该渠道会解除绑定。")
        if is_deleted:
            extra.append("· 这个会话之前已经被删掉了（在 Cherry Studio 里看不到），"
                         "这次是把它的记录从数据库里彻底清掉。")
        if extra:
            e = QLabel("\n".join(extra))
            e.setWordWrap(True)
            e.setStyleSheet("color: #7a5a12; background-color: #fff8e6; border: 1px solid #f2e2b8;"
                            "border-radius: 8px; padding: 8px 12px; font-size: 12px; line-height: 19px;")
            lay.addWidget(e)

        lay.addStretch()

        self.check = QCheckBox("我确认要删除这个会话（不可恢复）")
        self.check.toggled.connect(lambda on: self.del_btn.setEnabled(on))
        lay.addWidget(self.check)

        row = QHBoxLayout()
        row.addStretch()
        self.cancel_btn = QPushButton("取消")
        self.cancel_btn.setDefault(True)
        self.cancel_btn.clicked.connect(self.reject)
        row.addWidget(self.cancel_btn)
        self.del_btn = QPushButton("🗑  删除会话")
        self.del_btn.setObjectName("dangerBtn")
        self.del_btn.setAutoDefault(False)
        self.del_btn.setEnabled(False)
        self.del_btn.clicked.connect(self.accept)
        row.addWidget(self.del_btn)
        lay.addLayout(row)

        # 焦点和默认按钮都给「取消」：手一抖按回车是取消，不是删除
        self.cancel_btn.setFocus()
        self.adjustSize()


class CleanupCopiesDialog(QDialog):
    """批量清理「副本」。

    候选 = 名字里带「(副本)」的会话（引擎复制出来的默认名字就是这个）。
    默认一个都不勾 —— 想删哪个自己勾，避免手一抖清掉一堆。
    """

    def __init__(self, parent, candidates, scope_text=""):
        super().__init__(parent)
        self.candidates = list(candidates)
        self.selected_ids = []
        self.setWindowTitle("清理副本")
        self.setModal(True)
        self.resize(720, 480)
        self.setStyleSheet(f"""
            QDialog {{ background-color: #fafafa; }}
            QLabel {{ color: #333333; font-family: "微软雅黑"; font-size: 13px; }}
            QCheckBox {{ color: #444444; font-size: 12px; }}
            QListWidget {{ background-color: #ffffff; border: 1px solid #e8e8e8; border-radius: 8px;
                           color: #333333; font-size: 12px; outline: none; }}
            QListWidget::item {{ height: 26px; padding-left: 4px; }}
            QListWidget::item:hover {{ background-color: #fff6f4; }}
            QPushButton {{ background-color: #ffffff; border: 1px solid {CHERRY}; border-radius: 6px;
                           padding: 6px 14px; color: {CHERRY}; font-size: 12px; }}
            QPushButton:hover {{ background-color: #fff6f4; }}
            QPushButton:disabled {{ color: #bbbbbb; border-color: #e6e6e6; background-color: #fafafa; }}
            QPushButton#dangerBtn {{ background-color: #e0574a; border: 1px solid #e0574a; color: #ffffff; font-weight: bold; }}
            QPushButton#dangerBtn:hover {{ background-color: #cc4636; }}
            QPushButton#dangerBtn:disabled {{ background-color: #f0f0f0; border-color: #e6e6e6; color: #bbbbbb; }}
        """)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(24, 20, 24, 18)
        lay.setSpacing(10)

        t = QLabel(f"🧹  清理副本（{len(self.candidates)} 个）")
        t.setStyleSheet(f"color: {CHERRY}; font-size: 17px; font-weight: bold;")
        lay.addWidget(t)

        hint = QLabel(f"下面这些会话的名字里带「(副本)」，是复制出来的{scope_text}。\n"
                      "默认一个都不勾：勾上哪些，就删哪些。"
                      "想删别的会话，右键那个会话选「删除此会话」就行。")
        hint.setWordWrap(True)
        hint.setStyleSheet("color: #666666; font-size: 12px; line-height: 19px;")
        lay.addWidget(hint)

        self.list = QListWidget()
        for c in self.candidates:
            label = f"{c['name']}    ·  {c['msg']} 条消息  ·  {c['agent']}"
            if c.get("deleted"):
                label += "  ·  已删除"
            it = QListWidgetItem(label)
            it.setFlags(it.flags() | Qt.ItemIsUserCheckable)
            it.setCheckState(Qt.Unchecked)
            it.setToolTip(f"{c['name']}\n会话 ID: {c['id']}\n消息: {c['msg']} 条\nAgent: {c['agent']}")
            self.list.addItem(it)
        self.list.itemChanged.connect(lambda _it: self._update_state())
        lay.addWidget(self.list, stretch=1)

        row = QHBoxLayout()
        self.all_btn = QPushButton("全选")
        self.all_btn.clicked.connect(lambda: self._set_all(Qt.Checked))
        self.none_btn = QPushButton("全不选")
        self.none_btn.clicked.connect(lambda: self._set_all(Qt.Unchecked))
        row.addWidget(self.all_btn)
        row.addWidget(self.none_btn)
        self.sum_label = QLabel("")
        self.sum_label.setStyleSheet("color: #888888; font-size: 12px;")
        row.addWidget(self.sum_label)
        row.addStretch()
        lay.addLayout(row)

        self.check = QCheckBox("我确认删除选中的会话（不可恢复）")
        self.check.setEnabled(False)
        self.check.toggled.connect(lambda on: self.del_btn.setEnabled(on))
        lay.addWidget(self.check)

        btns = QHBoxLayout()
        btns.addStretch()
        self.cancel_btn = QPushButton("取消")
        self.cancel_btn.clicked.connect(self.reject)
        btns.addWidget(self.cancel_btn)
        self.del_btn = QPushButton("删除选中的")
        self.del_btn.setObjectName("dangerBtn")
        self.del_btn.setEnabled(False)
        self.del_btn.clicked.connect(self.on_ok)
        btns.addWidget(self.del_btn)
        lay.addLayout(btns)

        self._update_state()

    def _checked_ids(self):
        out = []
        for i in range(self.list.count()):
            it = self.list.item(i)
            if it.checkState() == Qt.Checked and i < len(self.candidates):
                out.append(self.candidates[i]["id"])
        return out

    def _set_all(self, state):
        self.list.blockSignals(True)
        for i in range(self.list.count()):
            self.list.item(i).setCheckState(state)
        self.list.blockSignals(False)
        self._update_state()

    def _update_state(self):
        ids = self._checked_ids()
        n = len(ids)
        msgs = sum(c["msg"] for c in self.candidates if c["id"] in set(ids))
        self.sum_label.setText(f"已勾选 {n} 个 · 共 {msgs} 条消息")
        self.del_btn.setText("删除选中的" if n == 0 else f"删除选中的 {n} 个")
        self.del_btn.setEnabled(n > 0 and self.check.isChecked())
        if n == 0:
            self.check.setChecked(False)
            self.check.setEnabled(False)
        else:
            self.check.setEnabled(True)

    def on_ok(self):
        self.selected_ids = self._checked_ids()
        if not self.selected_ids:
            return
        self.accept()


class PruneStorageDialog(QDialog):
    """清理「整库备份」或「删除留底」——两者只是名字和提示不同，逻辑一样。

    默认「保留最新的 5 份」，因为最新的那几份就够用了（它们含最新的数据）；
    更老的那些只是占地方。
    """

    KEEP_CHOICES = [1, 3, 5, 10, 20, 50]
    KINDS = {
        "backup": {
            "title": "🗄  清理旧备份",
            "hint": "以前每次复制/删除都会顺手备份一份**整个**数据库（一份 ≈130 MB）。",
            "warn": "⚠️  删掉的是备份文件本身：万一以后要从某个旧备份里找东西，那就找不回来了。\n"
                    "拿不准就别删。当前数据库（cherrystudio.sqlite）和会话数据完全不受影响。",
            "confirm": "我确认要删除列出的这些旧备份",
            "action": "删除旧备份",
        },
        "journal": {
            "title": "♻️  清理删除留底",
            "hint": "删会话时留下的「留底」：只装被删的那几行，用来还原。",
            "warn": "⚠️  删掉留底之后，那些会话就真的无法还原了（数据库里本来就已经没有了）。\n"
                    "如果删掉的东西你确定不要了，清掉它们能省点空间。",
            "confirm": "我确认要删除列出的这些留底",
            "action": "删除留底",
        },
    }

    def __init__(self, parent, items, kind="backup"):
        super().__init__(parent)
        self.items = list(items)          # 新的在前
        self.kind = kind
        self.cfg = self.KINDS.get(kind, self.KINDS["backup"])
        self.keep = 5
        self.setWindowTitle(self.cfg["title"])
        self.setModal(True)
        self.resize(780, 520)
        self.setStyleSheet(f"""
            QDialog {{ background-color: #fafafa; }}
            QLabel {{ color: #333333; font-family: "微软雅黑"; font-size: 13px; }}
            QCheckBox {{ color: #444444; font-size: 12px; }}
            QComboBox {{ background-color: #ffffff; border: 1px solid #dddddd; border-radius: 6px;
                         padding: 5px 8px; color: #333333; font-size: 12px; }}
            QComboBox:focus {{ border-color: {CHERRY}; }}
            QListWidget {{ background-color: #ffffff; border: 1px solid #e8e8e8; border-radius: 8px;
                           color: #333333; font-family: "Consolas", "微软雅黑"; font-size: 12px; outline: none; }}
            QListWidget::item {{ height: 24px; }}
            QPushButton {{ background-color: #ffffff; border: 1px solid {CHERRY}; border-radius: 6px;
                           padding: 6px 14px; color: {CHERRY}; font-size: 12px; }}
            QPushButton:hover {{ background-color: #fff6f4; }}
            QPushButton#dangerBtn {{ background-color: #e0574a; border: 1px solid #e0574a; color: #ffffff; font-weight: bold; }}
            QPushButton#dangerBtn:hover {{ background-color: #cc4636; }}
            QPushButton#dangerBtn:disabled {{ background-color: #f0f0f0; border-color: #e6e6e6; color: #bbbbbb; }}
        """)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(24, 20, 24, 18)
        lay.setSpacing(10)

        total = sum(b["size"] for b in self.items)
        t = QLabel(self.cfg["title"])
        t.setStyleSheet(f"color: {CHERRY}; font-size: 17px; font-weight: bold;")
        lay.addWidget(t)
        info = QLabel(f"{self.cfg['hint']}\n"
                      f"现在有 <b>{len(self.items)}</b> 份，一共 <b>{human_size(total)}</b>。\n"
                      f"最新的几份就够用了，更老的只是占地方。")
        info.setWordWrap(True)
        info.setStyleSheet("color: #666666; font-size: 12px; line-height: 20px;")
        lay.addWidget(info)

        row = QHBoxLayout()
        row.addWidget(QLabel("保留最新的"))
        self.combo = QComboBox()
        for k in self.KEEP_CHOICES:
            self.combo.addItem(f"{k} 份", k)
        self.combo.addItem("全部保留（不删）", 10 ** 9)
        self.combo.setCurrentIndex(self.KEEP_CHOICES.index(5))
        self.combo.currentIndexChanged.connect(lambda _i: self._update_state())
        row.addWidget(self.combo)
        self.sum_label = QLabel("")
        self.sum_label.setStyleSheet("color: #888888; font-size: 12px;")
        row.addWidget(self.sum_label)
        row.addStretch()
        lay.addLayout(row)

        self.list = QListWidget()
        lay.addWidget(self.list, stretch=1)

        warn = QLabel(self.cfg["warn"])
        warn.setWordWrap(True)
        warn.setStyleSheet("color: #b3402f; background-color: #fdf1ee; border: 1px solid #f6d5cd;"
                           "border-radius: 8px; padding: 9px 12px; font-size: 12px; line-height: 19px;")
        lay.addWidget(warn)

        self.check = QCheckBox(self.cfg["confirm"])
        self.check.toggled.connect(lambda on: self.del_btn.setEnabled(on))
        lay.addWidget(self.check)

        btns = QHBoxLayout()
        btns.addStretch()
        self.cancel_btn = QPushButton("取消")
        self.cancel_btn.setDefault(True)
        self.cancel_btn.clicked.connect(self.reject)
        btns.addWidget(self.cancel_btn)
        self.del_btn = QPushButton(self.cfg["action"])
        self.del_btn.setObjectName("dangerBtn")
        self.del_btn.setAutoDefault(False)
        self.del_btn.clicked.connect(self.accept)
        btns.addWidget(self.del_btn)
        lay.addLayout(btns)

        self._update_state()
        self.cancel_btn.setFocus()

    def _label(self, item):
        when = time.strftime("%Y-%m-%d %H:%M", time.localtime(item["mtime"]))
        if self.kind == "journal":
            n = item.get("sessions")
            extra = f"{n} 个会话" if isinstance(n, int) else ""
            return f"{when}   {human_size(item['size']):>9}   {extra:<8}   {item['name']}"
        return f"{when}   {human_size(item['size']):>9}   {item['name']}"

    def _update_state(self):
        self.keep = self.combo.currentData()
        doomed = self.items[self.keep:]
        freed = sum(b["size"] for b in doomed)
        self.list.clear()
        for i, b in enumerate(self.items):
            mark = "删除" if i >= self.keep else "保留"
            it = QListWidgetItem(f"[{mark}]  {self._label(b)}")
            it.setForeground(QColor("#c0392b" if i >= self.keep else "#666666"))
            self.list.addItem(it)
        if doomed:
            self.sum_label.setText(f"→ 将删除 {len(doomed)} 份，释放 {human_size(freed)}")
            self.check.setEnabled(True)
            self.del_btn.setText(f"{self.cfg['action']}（{len(doomed)} 份）")
        else:
            self.sum_label.setText("→ 不会删除任何东西")
            self.check.setChecked(False)
            self.check.setEnabled(False)
            self.del_btn.setText(self.cfg["action"])
        # 必须每次都按「有东西要删 且 已勾确认」重新算一遍，
        # 否则控件会保留上一次的可用状态（这里就踩过一次：一进来删除按钮就是可点的）
        self.del_btn.setEnabled(bool(doomed) and self.check.isChecked())

    def on_ok(self):
        self.keep = self.combo.currentData()
        if len(self.items) <= self.keep:
            return
        self.accept()

class ToastItem(QWidget):
    def __init__(self, parent, on_finished):
        super().__init__(parent)
        self.setAttribute(Qt.WA_TransparentForMouseEvents)
        self.on_finished = on_finished
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        self.label = QLabel("")
        self.label.setStyleSheet(f"QLabel {{ background-color: #ffffff; color: #333333; border: 2px solid {CHERRY}; border-radius: 10px; padding: 12px 20px; font-family: '微软雅黑'; font-size: 13px; }}")
        self.label.setMinimumWidth(240)
        self.label.setMaximumWidth(380)
        self.label.setWordWrap(True)
        lay.addWidget(self.label)
        self.op = QGraphicsOpacityEffect(self)
        self.op.setOpacity(0.0)
        self.setGraphicsEffect(self.op)
        self.fi = QPropertyAnimation(self.op, b"opacity")
        self.fi.setDuration(180); self.fi.setStartValue(0.0); self.fi.setEndValue(1.0)
        self.fi.setEasingCurve(QEasingCurve.OutCubic)
        self.fo = QPropertyAnimation(self.op, b"opacity")
        self.fo.setDuration(400); self.fo.setStartValue(1.0); self.fo.setEndValue(0.0)
        self.fo.setEasingCurve(QEasingCurve.InCubic)
        self.fo.finished.connect(self._done)
        self.pa = QPropertyAnimation(self, b"pos")
        self.pa.setDuration(220); self.pa.setEasingCurve(QEasingCurve.OutCubic)
        self.ht = QTimer(self); self.ht.setSingleShot(True); self.ht.timeout.connect(self.fade)
        self.hide()

    def fade(self):
        self.fo.stop(); self.fo.setStartValue(self.op.opacity()); self.fo.setEndValue(0.0); self.fo.start()

    def _done(self):
        self.hide(); self.on_finished(self)

    def move_to(self, x, y, animated=True):
        if animated and self.isVisible():
            self.pa.stop(); self.pa.setStartValue(self.pos()); self.pa.setEndValue(QPoint(int(x), int(y))); self.pa.start()
        else:
            self.pa.stop(); self.move(int(x), int(y))


class ToastManager:
    def __init__(self, parent):
        self.parent = parent
        self.toasts = []
        self.margin = 20; self.gap = 8

    def show(self, text):
        t = ToastItem(self.parent, on_finished=self._remove)
        t.label.setText(text); t.adjustSize()
        self.toasts.insert(0, t)
        pos = self._calc()
        x, y = pos[0]
        t.move(int(x), int(y)); t.show(); t.raise_()
        t.op.setOpacity(0.0); t.fi.start(); t.ht.start(4000)
        for i in range(1, len(self.toasts)):
            x, y = pos[i]
            self.toasts[i].move_to(x, y, True)

    def _calc(self):
        pw = self.parent.width(); ph = self.parent.height()
        res = []; y = ph - self.margin
        for t in self.toasts:
            y -= t.height()
            x = pw - t.width() - self.margin
            res.append((x, y)); y -= self.gap
        return res

    def _remove(self, t):
        if t not in self.toasts:
            return
        self.toasts.remove(t); t.deleteLater()
        pos = self._calc()
        for i, tt in enumerate(self.toasts):
            x, y = pos[i]; tt.move_to(x, y, True)

    def reposition_all(self):
        pos = self._calc()
        for i, t in enumerate(self.toasts):
            x, y = pos[i]; t.move(int(x), int(y))


class TitleBar(QWidget):
    def __init__(self, parent):
        super().__init__(parent)
        self.parent_window = parent
        self.setFixedHeight(44)
        self.setStyleSheet("background: transparent;")
        self.drag_position = None
        lay = QHBoxLayout(self)
        lay.setContentsMargins(18, 0, 10, 0)
        lay.setSpacing(4)
        self.tl = QLabel(APP_NAME)
        self.tl.setStyleSheet(f"color: {CHERRY}; font-size: 13px; font-weight: bold; background: transparent;")
        lay.addWidget(self.tl)
        lay.addStretch()
        for name, obj, cb, tip in [
            ("?", "winAbout", parent.open_about, "关于"),
            ("⚙", "winSettings", parent.open_settings, "设置"),
            ("—", "winMin", parent.showMinimized, "最小化"),
        ]:
            b = QPushButton(name)
            b.setObjectName(obj)
            b.setFixedSize(32, 32)
            b.setCursor(Qt.PointingHandCursor)
            b.setToolTip(tip)
            b.clicked.connect(cb)
            lay.addWidget(b)
        self.cb = QPushButton("✕")
        self.cb.setObjectName("winClose")
        self.cb.setFixedSize(32, 32)
        self.cb.setCursor(Qt.PointingHandCursor)
        self.cb.setToolTip("关闭")
        self.cb.clicked.connect(parent.close)
        lay.addWidget(self.cb)

    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton:
            self.drag_position = e.globalPosition().toPoint() - self.parent_window.frameGeometry().topLeft()
            e.accept()

    def mouseMoveEvent(self, e):
        if e.buttons() == Qt.LeftButton and self.drag_position:
            self.parent_window.move(e.globalPosition().toPoint() - self.drag_position)
            e.accept()

    def mouseReleaseEvent(self, e):
        self.drag_position = None


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(APP_NAME)
        self.resize(1100, 680)
        self.setWindowFlags(Qt.FramelessWindowHint)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self._copy_worker = None
        self._delete_worker = None
        self._prune_worker = None
        self.setStyleSheet(f"""
            QLabel {{ color: #333333; background: transparent; }}
            #leftPanel, #rightPanel {{ background-color: {PANEL_BG}; border: 2px solid {CHERRY}; border-radius: 12px; }}
            #titleText {{ color: {CHERRY}; font-size: 14px; font-weight: bold; background: transparent; }}
            #statusText {{ color: #666666; font-size: 12px; background: transparent; }}
            #treeWidget {{ background-color: {PANEL_BG}; alternate-background-color: rgba(252, 245, 243, 235); border: none; outline: none; color: #333333; font-size: 13px; }}
            #treeWidget::item {{ height: 28px; }}
            #treeWidget::item:selected {{ background-color: {CHERRY_SOFT}; color: #222222; }}
            #treeWidget::item:hover {{ background-color: #fff6f4; }}
            QHeaderView {{ background-color: transparent; }}
            QHeaderView::section {{ background-color: rgba(255, 240, 235, 235); color: {CHERRY}; padding: 6px; border: none; font-weight: bold; }}
            QPlainTextEdit#logView {{ background-color: {PANEL_BG}; border: none; color: #222222; font-family: "Consolas", "微软雅黑"; font-size: 12px; padding: 4px; selection-background-color: {CHERRY}; selection-color: #ffffff; }}
            #actionBtn {{ background-color: #ffffff; border: 2px solid {CHERRY}; border-radius: 6px; padding: 8px 16px; color: {CHERRY}; font-size: 13px; font-weight: bold; outline: none; }}
            #actionBtn:hover {{ background-color: #fff6f4; border-color: {CHERRY_HOVER}; }}
            #actionBtn:pressed {{ background-color: {CHERRY_SOFT}; }}
            #actionBtn:disabled {{ color: #cccccc; border-color: #eeeeee; }}
            #winMin, #winSettings, #winAbout {{ background-color: #ffffff; border: 1px solid {CHERRY}; color: {CHERRY}; font-size: 14px; font-weight: bold; outline: none; border-radius: 6px; }}
            #winMin:hover, #winSettings:hover, #winAbout:hover {{ background-color: {CHERRY}; border-color: {CHERRY}; color: #ffffff; }}
            #winClose {{ background-color: #ffffff; border: 1px solid #cccccc; color: #666666; font-size: 14px; font-weight: bold; outline: none; border-radius: 6px; }}
            #winClose:hover {{ background-color: {CHERRY}; border-color: {CHERRY}; color: #ffffff; }}
            QMenu {{ background-color: #ffffff; border: 2px solid {CHERRY}; padding: 4px; color: #333333; }}
            QMenu::item {{ padding: 6px 22px; border-radius: 4px; }}
            QMenu::item:selected {{ background-color: {CHERRY_SOFT}; color: {CHERRY}; }}
            QMenu::item:disabled {{ color: #b8b8b8; }}
            QMenu::separator {{ height: 1px; background: #eeeeee; margin: 4px 8px; }}
            QScrollBar:vertical {{ background: rgba(255, 255, 255, 150); width: 10px; margin: 2px; border-radius: 5px; }}
            QScrollBar::handle:vertical {{ background: {CHERRY}; border-radius: 5px; min-height: 30px; }}
            QScrollBar::handle:vertical:hover {{ background: {CHERRY_HOVER}; }}
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0px; }}
            QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {{ background: transparent; }}
            QScrollBar:horizontal {{ height: 0px; background: transparent; }}
        """)
        self.root_frame = RootFrame()
        self.setCentralWidget(self.root_frame)
        rl = QVBoxLayout(self.root_frame)
        rl.setContentsMargins(0, 0, 0, 0)
        rl.setSpacing(0)
        self.title_bar = TitleBar(self)
        rl.addWidget(self.title_bar)
        content = QWidget()
        content.setStyleSheet("background: transparent;")
        cl = QHBoxLayout(content)
        cl.setContentsMargins(14, 4, 14, 14)
        cl.setSpacing(12)
        # 左
        lp = QWidget()
        lp.setObjectName("leftPanel")
        lp.setFixedWidth(400)
        ll = QVBoxLayout(lp)
        ll.setContentsMargins(8, 12, 8, 8)
        ll.setSpacing(6)
        t = QLabel("📋  Agent / 会话列表")
        t.setObjectName("titleText")
        t.setFont(QFont("微软雅黑", 13, QFont.Bold))
        ll.addWidget(t)
        self.status_label = QLabel("正在加载...")
        self.status_label.setObjectName("statusText")
        ll.addWidget(self.status_label)
        self.tree = QTreeWidget()
        self.tree.setObjectName("treeWidget")
        self.tree.setColumnCount(2)
        self.tree.setHeaderLabels(["名称 / Agent", "消息数"])
        self.tree.setColumnWidth(0, 290)
        self.tree.header().setStretchLastSection(True)
        self.tree.header().setSectionResizeMode(1, QHeaderView.Stretch)
        self.tree.setIndentation(14)
        self.tree.setRootIsDecorated(True)
        self.tree.setContextMenuPolicy(Qt.CustomContextMenu)
        self.tree.customContextMenuRequested.connect(self.show_context_menu)
        self.tree.itemDoubleClicked.connect(self.on_item_double_clicked)
        self.tree.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        ll.addWidget(self.tree)
        self.sticky_bar = StickyAgentBar(lp, self.tree)
        ll.addWidget(self.sticky_bar)
        self.tree.verticalScrollBar().valueChanged.connect(lambda v: self.sticky_bar.refresh())
        self.refresh_btn = QPushButton("🔄  刷新列表")
        self.refresh_btn.setObjectName("actionBtn")
        self.refresh_btn.clicked.connect(self.on_refresh_clicked)
        ll.addWidget(self.refresh_btn)
        # 右
        rp = QWidget()
        rp.setObjectName("rightPanel")
        rl2 = QVBoxLayout(rp)
        rl2.setContentsMargins(8, 12, 8, 8)
        rl2.setSpacing(6)
        lt = QLabel("📜  运行日志")
        lt.setObjectName("titleText")
        lt.setFont(QFont("微软雅黑", 13, QFont.Bold))
        rl2.addWidget(lt)
        self.log_view = QPlainTextEdit()
        self.log_view.setObjectName("logView")
        self.log_view.setReadOnly(True)
        self.log_view.setFont(QFont("Consolas", 10))
        self.log_view.setMaximumBlockCount(500)
        self.log_view.setPlaceholderText("等待操作...")
        rl2.addWidget(self.log_view)
        cl.addWidget(lp)
        cl.addWidget(rp, stretch=1)
        rl.addWidget(content)
        self.toast_manager = ToastManager(self.root_frame)

        self.log_paths()
        load_cherry_data(log_func=self.log)
        self.load_sessions(write_log=True)

    # ---------------- 基础 ----------------

    def log_paths(self):
        self.log("🔍 环境自检")
        self.log(f"   数据目录      {DATA_DIR or '❌ 未找到'}")
        if DATA_DIR_SOURCE:
            self.log(f"                 （来源：{DATA_DIR_SOURCE}）")
        if len(DATA_CANDIDATES) > 1:
            self.log("   ⚠ 检测到多个候选数据目录，已选择最近写入的那个：")
            for c in DATA_CANDIDATES:
                flag = "✔" if c["path"] == DATA_DIR else " "
                self.log(f"      {flag} {c['path']}   [{c['mtime']} · {c['size_mb']}MB]")
        self.log(f"   Agents        {AGENTS_DIR or '❌ 未找到'}")
        self.log(f"   DSH 会话记录  {DSH_BASE_PATH or '❌ 未找到'}")
        self.log(f"   Claude 记录   {CLAUDE_PROJECTS or '❌ 未找到'}")
        self.log(f"   bun.exe       {BUN_PATH or '❌ 未找到'}")
        self.log(f"   复制工具      {TOOLS_DIR or '❌ 未找到'}")
        # 备份/留底会一直堆着，主动报出来，别等磁盘满了才发现
        # （作者机器上旧行为堆过 63 份 / 5.9 GB）
        baks = list_local_backups()
        if baks:
            total = sum(b["size"] for b in baks)
            self.log(f"   旧备份        {len(baks)} 份 · 共 {human_size(total)}"
                     f"（以前每次写入都留一份，现在不这么干了；"
                     f"右键列表空白处 →「清理旧备份」可以清）")
        jr = list_local_journals()
        if jr:
            jtotal = sum(x["size"] for x in jr)
            self.log(f"   删除留底      {len(jr)} 份 · 共 {human_size(jtotal)}"
                     f"（删会话时留的，只装被删的那几行；空白处右键 →「清理删除留底」）")

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self.toast_manager.reposition_all()

    def open_about(self):
        AboutDialog(self).exec()

    def open_settings(self):
        dlg = SettingsDialog(self)
        if dlg.exec() == QDialog.Accepted:
            refresh_global_paths()
            load_cherry_data(log_func=self.log)
            self.log("⚙️ 设置已保存")
            self.log_paths()
            self.load_sessions(write_log=True)

    def log(self, msg):
        ts = time.strftime("%H:%M:%S")
        self.log_view.appendPlainText(f"[{ts}] {msg}")
        sb = self.log_view.verticalScrollBar()
        sb.setValue(sb.maximum())

    def on_refresh_clicked(self):
        self.refresh_btn.setText("⏳  刷新中...")
        self.refresh_btn.setEnabled(False)
        QApplication.processEvents()
        refresh_global_paths()
        load_cherry_data(log_func=self.log)
        self.load_sessions(write_log=True)
        self.refresh_btn.setText("🔄  刷新列表")
        self.refresh_btn.setEnabled(True)

    def on_item_double_clicked(self, item, col):
        d = item.data(0, Qt.UserRole)
        if d and d.get("type") == "agent":
            item.setExpanded(not item.isExpanded())

    # ---------------- 列表 ----------------

    def load_sessions(self, write_log=False):
        if not BUN_PATH:
            self.status_label.setText("❌ 未找到 bun.exe，请点右上角 ⚙ 配置")
            if write_log:
                self.log("❌ 未找到 bun.exe（复制会话需要一个 JavaScript 运行时）")
            return
        if not SCRIPT_PATH or not os.path.exists(SCRIPT_PATH):
            self.status_label.setText("❌ 未找到 session-tools/duplicate-session.js")
            if write_log:
                self.log("❌ 未找到会话复制脚本 duplicate-session.js，"
                         "请把它（连同 session-lib.js）放到程序旁边的 session-tools\\ 里")
            return
        agent_avatars = get_agent_avatars()
        try:
            # 一定要设超时：万一 bun 卡住（被占用、被杀、环境异常），
            # 没有超时的话界面会永久假死，用户只能强制结束进程。
            try:
                r = subprocess.run(build_list_cmd(BUN_PATH, SCRIPT_PATH, DATA_DIR),
                                   capture_output=True, text=True, encoding="utf-8",
                                   errors="replace", timeout=120)
            except subprocess.TimeoutExpired:
                self.status_label.setText("❌ 读取超时（bun 无响应）")
                if write_log:
                    self.log("❌ 调用 bun 超过 120 秒没有响应。")
                    self.log("   可能原因：数据库被别的程序锁住、bun 环境异常。")
                    self.log("   可以先关掉 Cherry Studio 再试，或在设置里换一个 bun.exe。")
                return
            if r.returncode != 0:
                self.status_label.setText("❌ 调用失败")
                if write_log:
                    self.log("❌ 调用 bun 失败")
                    for ln in ((r.stderr or r.stdout or "").strip().split("\n"))[:4]:
                        self.log("   " + ln)
                return
            self.tree.clear()
            lines = r.stdout.strip().split("\n")
            agents_dict = {}
            for line in lines:
                if not line.strip():
                    continue
                # 从右往左切：会话名字里万一含有 "  |  " 也不会把这一行拆坏
                parts = line.rsplit("  |  ", 3)
                if len(parts) != 4:
                    continue
                sn, an, mc, sid = [p.strip() for p in parts]
                if not an or an.lower() == "null":
                    an = "(无 Agent)"
                agents_dict.setdefault(an, []).append((sn, mc, sid))
            total = 0
            deleted = 0
            for an, sessions in agents_dict.items():
                # 每个会话自己带着 agent_id，以它为准（同一个名字可能有多个 Agent）
                first_aid = None
                for _sn, _mc, sid in sessions:
                    got = SESS.get(sid, {}).get("agent_id")
                    if got:
                        first_aid = got
                        break
                if first_aid is None:
                    aids = AGENT_BY_NAME.get(an, [])
                    first_aid = aids[0] if aids else None
                avatar = agent_avatars.get(an, "🤖")
                parent = QTreeWidgetItem(self.tree)
                parent.setText(0, f"{avatar}  {an}")
                parent.setText(1, "")
                parent.setForeground(0, QColor(CHERRY))
                parent.setFont(0, QFont("微软雅黑", 12, QFont.Bold))
                parent.setData(0, Qt.UserRole, {"type": "agent", "agent_id": first_aid, "agent_name": an})
                tip = self._agent_tooltip(first_aid)
                if tip:
                    parent.setToolTip(0, tip)
                for sn, mc, sid in sessions:
                    sid_aid = SESS.get(sid, {}).get("agent_id") or first_aid
                    is_del = sid in DELETED_IDS
                    if is_del:
                        child = QTreeWidgetItem(parent)
                        child.setText(0, f"  📄  （已删除）{sn}")
                        child.setText(1, mc)
                        f = QFont("微软雅黑", 11); f.setStrikeOut(True)
                        child.setFont(0, f)
                        child.setForeground(0, QColor("#aaaaaa"))
                        deleted += 1
                    else:
                        child = QTreeWidgetItem(parent)
                        child.setText(0, f"  📄  {sn}")
                        child.setText(1, mc)
                        child.setForeground(0, QColor("#444444"))
                    sdata = {"type": "session", "session_id": sid, "session_name": sn,
                             "agent_id": sid_aid, "agent_name": an, "is_deleted": is_del}
                    child.setData(0, Qt.UserRole, sdata)
                    child.setToolTip(0, self._session_tooltip(sid))
                    total += 1
                parent.setExpanded(False)
            if deleted > 0:
                self.status_label.setText(f"✅ {len(agents_dict)} 个 Agent · {total} 个会话（{deleted} 个已删除）")
            else:
                self.status_label.setText(f"✅ {len(agents_dict)} 个 Agent · {total} 个会话")
            if write_log:
                m = f"🔍 已识别：{len(agents_dict)} 个 Agent，共 {total} 个会话"
                if deleted > 0:
                    m += f"（其中 {deleted} 个已删除）"
                self.log(m)
            self.sticky_bar.refresh()
        except Exception as e:
            self.status_label.setText(f"❌ 异常：{e}")
            if write_log:
                self.log(f"❌ 异常：{e}")

    def _agent_tooltip(self, agent_id):
        if not agent_id:
            return ""
        a = AGENTS.get(agent_id, {})
        lines = [f"ID: {agent_id}"]
        d = a.get("dir")
        if d:
            lines.append(f"目录: {d}{'' if a.get('exists') else '  (不存在)'}")
        ws = AGENT_WORKSPACES.get(agent_id, [])
        if ws:
            lines.append("工作目录:")
            for wid, cnt in ws[:4]:
                w = WS.get(wid, {})
                flag = "✓" if w.get("resolved") else "✗"
                lines.append(f"  {flag} {w.get('name')} ({cnt})")
            if len(ws) > 4:
                lines.append(f"  … 共 {len(ws)} 个")
        return "\n".join(lines)

    def _session_tooltip(self, session_id):
        # 刻意不查运行时会话 id：那需要访问数据库，而构造列表时会给每个会话都调一次。
        # 运行时记录在右键菜单里就能看到。
        info = SESS.get(session_id, {})
        w = WS.get(info.get("ws_id"), {})
        # 会话名放第一行：左栏是固定宽度且关掉了横向滚动条，
        # 长名字（比如反复复制出来的「xxx (副本) (副本)…」）在列表里会被裁掉，
        # 这里至少能完整看到。
        lines = [f"会话名: {info.get('name') or session_id}"]
        if w:
            lines.append(f"工作目录: {w.get('name')}")
            lines.append(f"  {w.get('path')}")
            if w.get("resolved") and w["resolved"] != w.get("path"):
                lines.append(f"  实际: {w['resolved']}")
            elif not w.get("resolved"):
                lines.append("  ⚠ 该路径不存在")
        lines.append(f"会话 ID: {session_id}")
        return "\n".join(lines)

    # ---------------- 右键菜单 ----------------

    def show_context_menu(self, pos):
        item = self.tree.itemAt(pos)
        d = item.data(0, Qt.UserRole) if item else None
        menu = QMenu(self)
        if not d:
            # 右键点在空白处：给几个「对整个列表」的操作
            a = menu.addAction("🔄  刷新列表")
            a.triggered.connect(self.on_refresh_clicked)
            cands = self._copy_candidates()
            act = menu.addAction(f"🧹  清理副本…（全部 {len(cands)} 个）")
            act.setEnabled(len(cands) > 0)
            act.triggered.connect(lambda: self.cleanup_copies(None))
            jr = list_local_journals()
            if jr:
                j_act = menu.addAction(
                    f"♻️  清理删除留底…（{len(jr)} 份 · {human_size(sum(x['size'] for x in jr))}）")
            else:
                j_act = menu.addAction("♻️  清理删除留底…（没有留底）")
                j_act.setEnabled(False)
            j_act.triggered.connect(lambda: self.prune_storage("journal"))
            baks = list_local_backups()
            if baks:
                b_act = menu.addAction(
                    f"🗄  清理旧备份…（{len(baks)} 份 · {human_size(sum(b['size'] for b in baks))}）")
            else:
                b_act = menu.addAction("🗄  清理旧备份…（没有自动备份）")
                b_act.setEnabled(False)
            b_act.triggered.connect(lambda: self.prune_storage("backup"))
            menu.exec(self.tree.viewport().mapToGlobal(pos))
            return
        if d.get("type") == "session":
            self._build_session_menu(menu, d)
        else:
            self._build_agent_menu(menu, d)
        menu.exec(self.tree.viewport().mapToGlobal(pos))

    # ---- 会话菜单 ----

    def _build_session_menu(self, menu, d):
        sid = d.get("session_id")
        if d.get("is_deleted"):
            a = menu.addAction("📋  复制此会话（恢复为可见）")
        else:
            a = menu.addAction("📋  复制此会话")
        a.triggered.connect(lambda: self.duplicate_session(d))
        menu.addSeparator()

        info = SESS.get(sid, {})
        w = WS.get(info.get("ws_id"), {})

        # 打开工作目录
        if w:
            if w.get("resolved"):
                act = menu.addAction(f"📂  打开工作目录（{w.get('name')}）")
                act.setToolTip(w["resolved"])
                target = w["resolved"]
                act.triggered.connect(lambda _=False, t=target: self._open(t))
            else:
                act = menu.addAction(f"⚠️  工作目录不存在（{w.get('name')}）")
                act.setEnabled(False)
                act.setToolTip(w.get("path") or "")
        else:
            act = menu.addAction("⚠️  该会话没有关联工作目录")
            act.setEnabled(False)

        # 打开运行时会话记录
        targets = runtime_targets_for_session(sid)
        if len(targets) == 1:
            name, path, isfile = targets[0]
            act = menu.addAction(f"📜  打开会话记录（{name}）")
            act.triggered.connect(lambda _=False, t=path, f=isfile: self._open(t, select=f))
        elif len(targets) > 1:
            sub = menu.addMenu(f"📜  打开会话记录（{len(targets)} 项）")
            for name, path, isfile in targets:
                it = sub.addAction(name)
                it.setToolTip(path)
                it.triggered.connect(lambda _=False, t=path, f=isfile: self._open(t, select=f))
        else:
            act = menu.addAction("📜  未找到会话记录（.dsh / .claude）")
            act.setEnabled(False)

        menu.addSeparator()
        cp = menu.addAction("🔗  复制工作目录路径")
        cp.setEnabled(bool(w))
        cp.triggered.connect(lambda _=False, t=(w.get("resolved") or w.get("path")) if w else None:
                             self._copy_text(t))

        # 破坏性操作单独放在最后，和上面的「看一眼」类操作隔开
        menu.addSeparator()
        if d.get("is_deleted"):
            del_act = menu.addAction("🗑  彻底删除（已删除的会话，不可恢复）…")
        else:
            del_act = menu.addAction("🗑  删除此会话（不可恢复）…")
        del_act.triggered.connect(lambda _=False, x=d: self.confirm_delete_session(x))

    # ---- Agent 菜单 ----

    def _build_agent_menu(self, menu, d):
        aid = d.get("agent_id")
        an = d.get("agent_name", "?")
        a = AGENTS.get(aid, {})

        # 1) Agent 自己的目录
        adir = a.get("dir")
        if adir and a.get("exists"):
            act = menu.addAction("📂  打开 Agent 目录")
            act.triggered.connect(lambda _=False, t=adir: self._open(t))
        else:
            act = menu.addAction("⚠️  Agent 目录不存在")
            act.setEnabled(False)
            act.setToolTip(adir or "")

        menu.addSeparator()

        # 2) 工作目录（可能多个）
        ws_list = AGENT_WORKSPACES.get(aid, [])
        if len(ws_list) == 1:
            wid, cnt = ws_list[0]
            w = WS.get(wid, {})
            if w.get("resolved"):
                act = menu.addAction(f"📂  打开工作目录（{w.get('name')}）")
                act.setToolTip(w["resolved"])
                act.triggered.connect(lambda _=False, t=w["resolved"]: self._open(t))
            else:
                act = menu.addAction(f"⚠️  工作目录不存在（{w.get('name')}）")
                act.setEnabled(False)
                act.setToolTip(w.get("path") or "")
        elif len(ws_list) > 1:
            sub = menu.addMenu(f"📂  打开工作目录（{len(ws_list)} 个）")
            for wid, cnt in ws_list:
                w = WS.get(wid, {})
                if w.get("resolved"):
                    label = f"{w.get('name')}   · {cnt} 个会话"
                    it = sub.addAction(label)
                    it.setToolTip(w["resolved"])
                    it.triggered.connect(lambda _=False, t=w["resolved"]: self._open(t))
                else:
                    it = sub.addAction(f"{w.get('name')}   (路径失效)")
                    it.setEnabled(False)
                    it.setToolTip(w.get("path") or "")
        else:
            act = menu.addAction("📂  该 Agent 没有会话 / 工作目录")
            act.setEnabled(False)

        # 3) 运行时会话记录目录
        self._add_agent_runtime_menu(menu, aid, ws_list)

        # 4) 清理这个 Agent 的副本（名字里带「(副本)」的会话）
        menu.addSeparator()
        copies = self._copy_candidates(aid)
        if copies:
            act = menu.addAction(f"🧹  清理副本…（这个 Agent 有 {len(copies)} 个）")
            act.triggered.connect(lambda _=False, a=aid: self.cleanup_copies(a))
        else:
            act = menu.addAction("🧹  清理副本…（这个 Agent 没有副本）")
            act.setEnabled(False)

        menu.addSeparator()
        ai = menu.addAction("ℹ️  Agent 详情")
        ai.setEnabled(aid is not None)
        ai.triggered.connect(lambda _=False, x=aid: self._show_agent_info(x))
        cp = menu.addAction("🔗  复制 Agent ID")
        cp.setEnabled(aid is not None)
        cp.triggered.connect(lambda _=False, t=aid: self._copy_text(t))
        if adir:
            cp2 = menu.addAction("🔗  复制 Agent 目录路径")
            cp2.triggered.connect(lambda _=False, t=adir: self._copy_text(t))

    def _add_agent_runtime_menu(self, menu, aid, ws_list):
        """列出该 Agent 各工作目录对应的 .claude / .dsh 记录目录。"""
        entries = []
        for wid, cnt in ws_list:
            w = WS.get(wid, {})
            path = w.get("resolved")
            if not path:
                continue
            cp = claude_project_dir(path)
            if cp and os.path.isdir(cp):
                entries.append((f"claude · {w.get('name')}", cp))
            dp = dsh_project_dir(path)
            if dp and os.path.isdir(dp):
                entries.append((f"dsh · {w.get('name')}", dp))

        if not entries:
            act = menu.addAction("📜  未找到会话记录目录")
            act.setEnabled(False)
            return

        if len(entries) == 1:
            name, path = entries[0]
            act = menu.addAction(f"📜  打开会话记录目录（{name}）")
            act.triggered.connect(lambda _=False, t=path: self._open(t))
        else:
            sub = menu.addMenu(f"📜  打开会话记录目录（{len(entries)} 个）")
            for name, path in entries:
                it = sub.addAction(name)
                it.setToolTip(path)
                it.triggered.connect(lambda _=False, t=path: self._open(t))

    # ---------------- 动作 ----------------

    def _open(self, target, select=False):
        ok, err = open_path(target, select=select)
        if ok:
            self.log(f"📂 已打开：{target}")
        else:
            self.log(f"❌ 打开失败（{err}）：{target}")
            self.toast_manager.show(f"❌  {err}")

    def _copy_text(self, text):
        if not text:
            self.toast_manager.show("⚠️  没有可复制的内容")
            return
        QApplication.clipboard().setText(text)
        self.log(f"🔗 已复制：{text}")
        self.toast_manager.show("🔗  已复制到剪贴板")

    def _show_agent_info(self, aid):
        if not aid:
            return
        AgentInfoDialog(self, aid).exec()

    def duplicate_session(self, data):
        sid = data.get("session_id")
        sn = data.get("session_name", "未命名")
        is_del = data.get("is_deleted", False)
        if not BUN_PATH or not SCRIPT_PATH:
            QMessageBox.warning(self, "错误", "未找到 bun.exe")
            self.log("❌ 复制失败：未找到 bun.exe")
            return
        if is_del:
            self.log(f"📋 开始复制（恢复已删除会话）：{sn}")
            self.toast_manager.show(f"⏳  正在恢复：{sn}")
        else:
            self.log(f"📋 开始复制：{sn}")
            self.toast_manager.show(f"⏳  正在复制：{sn}")
        QApplication.processEvents()
        self._copy_worker = CopyWorker(BUN_PATH, SCRIPT_PATH, sid, DATA_DIR, want_full_backup())
        self._copy_worker.done.connect(lambda rc, out, err, n=sn, dd=is_del: self._on_copy_done(rc, out, err, n, dd))
        self._copy_worker.start()

    def _on_copy_done(self, rc, out, err, sn, was_del):
        if rc != 0:
            m = (err or out or "").strip()
            self.log(f"❌ 复制失败：{m[:100]}")
            self.toast_manager.show(f"❌  复制失败：{m[:50]}")
            return
        if was_del:
            self.log(f"✅ 已恢复：{sn} → {sn} (副本)")
            self.toast_manager.show(f"✅  已恢复：{sn} (副本)")
        else:
            self.log(f"✅ 已复制：{sn} → {sn} (副本)")
            self.toast_manager.show(f"✅  已复制：{sn} (副本)")
        _TOKEN_CACHE.clear()
        load_cherry_data(log_func=None)
        self.load_sessions(write_log=False)

    # ---------------- 删除 ----------------

    def prune_storage(self, kind="backup"):
        """清理整库备份 / 删除留底。"""
        if kind == "journal":
            items = list_local_journals()
            empty_msg = "♻️  没有删除留底"
            empty_log = "♻️ 数据目录里没有删除留底。"
        else:
            items = list_local_backups()
            empty_msg = "🗄  没有自动备份"
            empty_log = "🗄 数据目录里没有自动备份文件。"
        if not items:
            self.toast_manager.show(empty_msg)
            self.log(empty_log)
            return
        dlg = PruneStorageDialog(self, items, kind)
        if dlg.exec() != QDialog.Accepted:
            self.log(f"已取消清理（{kind}）。")
            return
        keep = dlg.keep
        if not BUN_PATH or not SCRIPT_PATH:
            QMessageBox.warning(self, "错误", "未找到 bun.exe")
            return
        label = "删除留底" if kind == "journal" else "旧备份"
        self.log(f"🧹 开始清理{label}（保留最新的 {keep} 份）……")
        self.toast_manager.show(f"⏳  正在清理{label}……")
        QApplication.processEvents()
        self._prune_worker = PruneBackupsWorker(BUN_PATH, SCRIPT_PATH, keep, DATA_DIR, kind)
        self._prune_worker.done.connect(lambda rc, out, err, k=kind: self._on_prune_done(rc, out, err, k))
        self._prune_worker.start()

    def _on_prune_done(self, rc, out, err, kind="backup"):
        label = "删除留底" if kind == "journal" else "旧备份"
        if rc != 0:
            m = (err or out or "").strip()
            self.log(f"❌ 清理{label}失败：{m[:160]}")
            self.toast_manager.show(f"❌  清理{label}失败")
            return
        lines = [l.strip() for l in (out or "").splitlines() if l.strip()]
        for ln in lines[:2]:
            self.log("   " + ln)
        freed = ""
        for ln in lines:
            if "释放" in ln:
                freed = ln.split("释放")[-1].strip()
        left = len(list_local_journals()) if kind == "journal" else len(list_local_backups())
        self.log(f"✅ 清理完成：现在还剩 {left} 份{label}" + (f"，释放了 {freed}" if freed else ""))
        self.toast_manager.show("✅  已清理" + label + (f"（{freed}）" if freed else ""))

    def _copy_candidates(self, agent_id=None):
        """列出「副本」候选（名字里带「(副本)」的会话）。

        按列表里的显示顺序走（也就是 Agent 分组 + 创建时间倒序），
        这样弹出的清理框和左边的树是同一个顺序，找起来不费劲。
        消息条数单独一条 SQL 批量查（走索引），而不是每个会话查一次。
        """
        rows = []
        for i in range(self.tree.topLevelItemCount()):
            top = self.tree.topLevelItem(i)
            ad = top.data(0, Qt.UserRole) or {}
            if agent_id is not None and (ad.get("agent_id") or None) != agent_id:
                continue
            for j in range(top.childCount()):
                ch = top.child(j)
                sd = ch.data(0, Qt.UserRole) or {}
                if sd.get("type") != "session":
                    continue
                sid = sd.get("session_id")
                name = sd.get("session_name") or (SESS.get(sid) or {}).get("name") or "(未命名)"
                if not is_copy_name(name):
                    continue
                rows.append((sid, name, ad.get("agent_name") or "(无 Agent)",
                             bool(sd.get("is_deleted")), ch.text(1)))
        totals = session_msg_totals([r[0] for r in rows])
        out = []
        for sid, name, agent_name, is_deleted, col1 in rows:
            msg = totals.get(sid)
            if msg is None:
                # 兜底：用列表里显示的数字（那个不含失败/中间消息，只是个近似值）
                try:
                    msg = int(re.sub(r"\D", "", col1) or 0)
                except Exception:
                    msg = 0
            out.append({"id": sid, "name": name, "agent": agent_name,
                        "msg": msg, "deleted": is_deleted})
        return out

    def cleanup_copies(self, agent_id=None):
        cands = self._copy_candidates(agent_id)
        if not cands:
            self.toast_manager.show("🧹  没找到「副本」")
            self.log("🧹 没有名字里带「(副本)」的会话可以清理。")
            return
        scope = "" if agent_id is None else "（只列这个 Agent 的）"
        dlg = CleanupCopiesDialog(self, cands, scope)
        if dlg.exec() != QDialog.Accepted or not dlg.selected_ids:
            self.log(f"已取消清理副本（候选 {len(cands)} 个）")
            return
        self.delete_sessions(dlg.selected_ids, f"{len(dlg.selected_ids)} 个副本")

    def confirm_delete_session(self, d):
        sid = d.get("session_id")
        name = d.get("session_name") or (SESS.get(sid) or {}).get("name") or "(未命名)"
        info = inspect_session_for_delete(sid)
        dlg = ConfirmDeleteDialog(self, name, sid, info, bool(d.get("is_deleted")))
        if dlg.exec() != QDialog.Accepted:
            self.log(f"已取消删除：{name}")
            return
        self.delete_sessions([sid], name)

    def delete_sessions(self, ids, what):
        """真正动手删（在后台线程里跑引擎）。ids 为会话 id 列表。"""
        ids = [i for i in ids if i]
        if not ids:
            return
        if not BUN_PATH or not SCRIPT_PATH:
            QMessageBox.warning(self, "错误", "未找到 bun.exe")
            self.log("❌ 删除失败：未找到 bun.exe")
            return
        # 动手前先说清楚做了什么（日志里能查）
        self.log(f"🗑  开始删除{what}（{len(ids)} 个会话）……")
        self.toast_manager.show(f"⏳  正在删除：{what}")
        QApplication.processEvents()
        self._delete_worker = DeleteWorker(BUN_PATH, SCRIPT_PATH, ids, DATA_DIR, want_full_backup())
        self._delete_worker.done.connect(lambda rc, out, err, w=what: self._on_delete_done(rc, out, err, w))
        self._delete_worker.start()

    def _on_delete_done(self, rc, out, err, what):
        if rc != 0:
            m = (err or out or "").strip()
            for ln in m.splitlines()[:4]:
                self.log("   " + ln.strip())
            self.log("❌ 删除失败（上面是引擎的原话）")
            self.toast_manager.show("❌  删除失败，详情见右侧日志")
        else:
            for ln in (out or "").splitlines():
                s = ln.strip()
                if s.startswith(("·", "合计", "删除留底", "整库备份", "数据库里不存在", "（", "⚠️")):
                    self.log("   " + s)
            self.log(f"✅ 已删除{what}")
            self.toast_manager.show(f"✅  已删除{what}")
        # 不管成没成都要刷新：批量删除可能只完成了一部分
        _TOKEN_CACHE.clear()
        _reset_shared_conn()
        load_cherry_data(log_func=None)
        self.load_sessions(write_log=False)


if __name__ == "__main__":
    app = QApplication(sys.argv)

    # 应用图标：任务栏、窗口左上角、Alt+Tab 都靠它。
    # 没有它的话，任务栏上会显示 python 的默认图标（打包成 exe 后尤其明显）。
    _icon = resource_path("icon.ico")
    if os.path.exists(_icon):
        app.setWindowIcon(QIcon(_icon))

    cfg = load_config()

    # 顺序很重要：扫描 → 选数据目录 → 使用须知 → 主窗口。
    # 「使用须知」曾经是用 QTimer 在主窗口里延迟 200ms 弹的，
    # 那样只要这 200ms 内有任何代码处理事件（比如刷新列表里的 processEvents），
    # 一个模态框就会毫无征兆地冒出来并卡住界面。现在改成显式按顺序弹。
    if not cfg.get("hide_first_warning", False):
        FirstUseWarning().exec()

    if not cfg.get("scan_completed", False) or not cfg.get("data_dir"):
        dlg = StartupScanDialog()
        dlg.start_scan()
        dlg.exec()

    refresh_global_paths()

    # 发现多个候选数据目录、且用户还没确认过 → 让用户自己选
    # （换过安装位置、多个 Cherry 用户、旧的 AppData 残留都会造成多候选）
    cfg = load_config()
    if not cfg.get("data_dir_asked") and len(DATA_CANDIDATES) > 1:
        picker = DataDirPickerDialog()
        accepted = picker.exec() == QDialog.Accepted and picker.selected
        cfg = load_config()
        if accepted:
            cfg["data_dir"] = picker.selected
            cfg["data_dir_locked"] = False
        cfg["data_dir_asked"] = True
        save_config({k: v for k, v in cfg.items() if v is not None})
        refresh_global_paths()

    window = MainWindow()
    window.show()
    sys.exit(app.exec())

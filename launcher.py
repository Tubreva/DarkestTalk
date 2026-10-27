#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""DarkestTalk 启动器。

双击 `DarkestTalk.exe`（打包后）或 `pythonw launcher.py` 打开这个窗口。
它只做四件事：把环境看清楚、把主程序拉起来、能停下来、能一键导出日志。

为什么不直接双击 run.bat：黑框一闪、出错时什么也看不见，玩家第一反应是"坏了"。
这里每一行都在窗口里写清楚：游戏在哪、存档有没有、接口填了没、库要不要重建。
"""
import ctypes
import json
import os
import shutil
import subprocess
import sys
import time
import traceback
import tkinter as tk
from tkinter import messagebox

# 打包成 exe 时 __file__ 在临时解包目录里，一切路径都要以 exe 所在目录为准
HERE = (os.path.dirname(sys.executable) if getattr(sys, "frozen", False)
        else os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)

import theme as T                     # noqa: E402
import datadir                        # noqa: E402
import paths as P                     # noqa: E402
import logexport                      # noqa: E402
from version import VERSION, BUILD    # noqa: E402

P.JSON_PATH = os.path.join(HERE, "paths.json")   # 冻结后模块里的 HERE 是临时目录
DATA = datadir.get()                             # 玩家数据：<游戏目录>\\DarkestTalkData

import i18n                            # noqa: E402
i18n.init()
i18n.install()
from i18n import tr                    # noqa: E402

OUT = os.path.join(DATA, "out")
LOCK = os.path.join(OUT, ".running")
CACHE = os.path.join(OUT, ".python")
CHILD_LOG = os.path.join(OUT, "launcher.log")
CREATE_NO_WINDOW = 0x08000000


def _log(msg):
    """启动器自己的诊断日志（窗口版没有控制台，出问题只能靠它）。"""
    try:
        os.makedirs(OUT, exist_ok=True)
        with open(CHILD_LOG, "a", encoding="utf-8", errors="replace") as f:
            f.write("%s %s\n" % (time.strftime("%H:%M:%S"), msg))
    except Exception:
        pass


def _log_exc(exc_type, exc, tb):
    _log(tr("[异常] %s: %s") % (exc_type.__name__, exc))
    try:
        os.makedirs(OUT, exist_ok=True)
        with open(CHILD_LOG, "a", encoding="utf-8", errors="replace") as f:
            traceback.print_exception(exc_type, exc, tb, file=f)
    except Exception:
        pass


sys.excepthook = _log_exc


def _cached_python():
    try:
        exe = open(CACHE, encoding="utf-8").read().strip()
    except OSError:
        return ""
    return exe if exe and os.path.exists(exe) else ""


def _remember(exe):
    try:
        os.makedirs(OUT, exist_ok=True)
        with open(CACHE, "w", encoding="utf-8") as f:
            f.write(exe)
    except Exception:
        pass


# ------------------------------------------------------------------ 找 Python
def _reg_pythons():
    out = []
    try:
        import winreg
        for hive in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
            try:
                key = winreg.OpenKey(hive, r"Software\Python\PythonCore")
            except OSError:
                continue
            for i in range(64):
                try:
                    ver = winreg.EnumKey(key, i)
                except OSError:
                    break
                try:
                    ip = winreg.OpenKey(key, ver + r"\InstallPath")
                    exe = winreg.QueryValueEx(ip, "ExecutablePath")[0]
                    if exe:
                        out.append(exe)
                except OSError:
                    pass
    except Exception:
        pass
    return out


def _glob_pythons():
    out = []
    patterns = [
        (os.environ.get("LOCALAPPDATA", ""), r"Programs\Python\Python3*"),
        (os.environ.get("ProgramFiles", ""), r"Python3*"),
        (os.environ.get("ProgramFiles(x86)", ""), r"Python3*"),
        ("C:\\", r"Python3*"),
    ]
    for base, pat in patterns:
        if not base:
            continue
        try:
            import glob as _g
            for d in _g.glob(os.path.join(base, pat)):
                for exe in ("pythonw.exe", "python.exe"):
                    p = os.path.join(d, exe)
                    if os.path.exists(p):
                        out.append(p)
        except Exception:
            pass
    return out


def _probe(exe):
    """返回 (主版本, 次版本)；跑不起来或没有 tkinter 就返回 None。"""
    try:
        r = subprocess.run([exe, "-c",
                            "import sys,tkinter;print('%d.%d'%sys.version_info[:2])"],
                           capture_output=True, timeout=8,
                           creationflags=CREATE_NO_WINDOW)
        if r.returncode != 0:
            return None
        s = (r.stdout or b"").decode("ascii", "ignore").strip()
        maj, mi = s.split(".")
        return int(maj), int(mi)
    except Exception:
        return None


def find_python():
    """按可靠性排：本机显式指定 → PATH 上的 pythonw/python → 注册表 / 常见安装目录
    → 最后才是 Python Launcher（pyw.exe，它对参数的转发依赖版本，能用但排在后面）。

    版本上优先 3.9–3.13（这套代码实际验证过的范围）：机器上同时装了 3.9 和 3.14 时
    不要顺手挑最新的那个 —— 新版本的行为差异没人测过。

    探针要真跑一遍解释器（约 0.3 秒/个），所以找到之后记在 out\\.python 里，
    下次启动直接用；解释器被卸载了就自动重找。
    """
    c = _cached_python()
    if c:
        return c
    cands = []
    txt = os.path.join(DATA, "python_path.txt")
    if os.path.exists(txt):
        try:
            v = open(txt, encoding="utf-8").read().strip().strip('"')
            if v:
                cands += [v, v.replace("python.exe", "pythonw.exe")]
        except Exception:
            pass
    for name in ("pythonw.exe", "python.exe"):
        w = shutil.which(name)
        if w:
            cands.append(w)
    cands += _glob_pythons() + _reg_pythons()
    for name in ("pyw.exe", "py.exe"):
        w = shutil.which(name)
        if w:
            cands.append(w)

    seen, found = set(), []
    for exe in cands:
        key = exe.lower()
        if key in seen or not os.path.exists(exe):
            continue
        seen.add(key)
        v = _probe(exe)
        if not v:
            continue
        found.append((v, exe))
        # 第一个落在验证过的版本区间就够了，不必把所有候选都试一遍
        if v[0] == 3 and 9 <= v[1] <= 13:
            break
    if not found:
        return ""

    def rank(item):
        (maj, mi), _ = item
        if maj == 3 and 9 <= mi <= 13:
            grp = 0          # 验证过的范围
        elif maj == 3 and mi >= 14:
            grp = 1          # 能用，但没测过
        else:
            grp = 2          # 太老
        return (grp, -mi)

    found.sort(key=rank)
    exe = found[0][1]
    _remember(exe)
    return exe


def find_java():
    exe = shutil.which("java")
    if exe:
        return exe
    jh = os.environ.get("JAVA_HOME", "")
    if jh and os.path.exists(os.path.join(jh, "bin", "java.exe")):
        return os.path.join(jh, "bin", "java.exe")
    try:
        import glob as _g
        for p in _g.glob(os.path.join(os.environ.get("ProgramFiles", ""),
                                      r"Java\*\bin\java.exe")):
            return p
        for p in _g.glob(os.path.join(os.environ.get("ProgramFiles(x86)", ""),
                                      r"Java\*\bin\java.exe")):
            return p
    except Exception:
        pass
    return ""


# ------------------------------------------------------------------ 进程状态
def _alive(pid):
    try:
        k = ctypes.windll.kernel32
        h = k.OpenProcess(0x1000, False, int(pid))     # QUERY_LIMITED_INFORMATION
        if not h:
            return False
        code = ctypes.c_ulong()
        ok = k.GetExitCodeProcess(h, ctypes.byref(code))
        k.CloseHandle(h)
        return bool(ok) and code.value == 259          # STILL_ACTIVE
    except Exception:
        return False


def running_pid():
    try:
        with open(LOCK, encoding="utf-8") as f:
            pid = int((f.read() or "0").strip() or 0)
    except Exception:
        return 0
    return pid if pid and _alive(pid) else 0


def shorten(p, n=46):
    if not p:
        return "（没找到）"
    return p if len(p) <= n else "…" + p[-(n - 1):]


# ------------------------------------------------------------------ 窗口
class Launcher:
    OK = "#a9b58f"        # 灰绿（象牙色系里的"通"）
    BAD = "#d4232a"       # 参考红（"不通"）

    def __init__(self):
        self.proc = None
        self.started = 0.0
        self.py = ""
        self.java = ""
        self.paths = {}

        self.restart = False
        self.win = tk.Tk()
        self.win.title("DarkestTalk")
        self.win.geometry("%dx%d+%d+%d" % (640 + 2 * T.PAD, 470 + 2 * T.PAD, 
            max(20, (self.win.winfo_screenwidth() - 640) // 2),
            max(20, (self.win.winfo_screenheight() - 470) // 3)))
        self.win.configure(bg=T.BG)
        self.win.protocol("WM_DELETE_WINDOW", self.on_close)
        self.win.report_callback_exception = _log_exc
        T.shell(self.win, "DarkestTalk", self.on_close, 640 + 2 * T.PAD, 470 + 2 * T.PAD)
        # 双击启动时保证窗口在前台：无边框窗口不会自动抢焦点，先压 1.2 秒再放开
        try:
            self.win.attributes("-topmost", True)
            self.win.lift()
            self.win.focus_force()
            self.win.after(1200, lambda: self.win.attributes("-topmost", False))
        except Exception:
            pass

        body = T.content(self.win, 640, 436)
        self.body = body

        tk.Label(body, text="启动器", bg=T.BG, fg=T.GOLD,
                 font=T.head(13)).place(x=24, y=10)
        tk.Label(body, text="v%s · build %s" % (VERSION, BUILD or "dev"),
                 bg=T.BG, fg=T.DIM, font=T.body(9), anchor="e"
                 ).place(x=420, y=14, width=196)
        self._lang_box(body)

        # ---- 环境一览
        tk.Label(body, text="环境", bg=T.BG, fg=T.DIM,
                 font=T.body(9)).place(x=24, y=44)
        self.rows = {}
        y = 66
        for key, label in (("game", "游戏目录"), ("save", "存档目录"),
                           ("api", "模型接口"), ("java", "Java"),
                           ("pack", "译名库")):
            tk.Label(body, text=label, bg=T.BG, fg=T.DIM,
                     font=T.body(10)).place(x=24, y=y)
            lab = tk.Label(body, text="检查中……", bg=T.BG, fg=T.INK,
                           font=T.body(10), anchor="w", justify="left")
            lab.place(x=104, y=y, width=506)
            self.rows[key] = lab
            y += 26

        # ---- 主按钮
        self.btn_start = T.button(body, 24, 216, 200, 46,
                                  "开始旁白", self.start, "red", 13)
        self.btn_stop = T.button(body, 236, 216, 120, 46,
                                 "停止", self.stop, "plate", 11)
        self.btn_stop.enable(False)

        T.button(body, 24, 278, 120, 30, "导出日志", self.export_logs, "plate", 10)
        T.button(body, 152, 278, 120, 30, "数据目录", self.open_dir, "plate", 10)
        T.button(body, 280, 278, 120, 30, "重新检测", lambda: self.refresh(True), "plate", 10)
        T.button(body, 528, 278, 84, 30, "关闭", self.on_close, "ghost", 10)

        # ---- 状态行
        self.status = tk.Label(body, text="", bg=T.BG, fg=T.DIM, font=T.body(9),
                               anchor="w", justify="left", wraplength=588)
        self.status.place(x=24, y=330, width=592)

        self.tip = tk.Label(body,
                            text="启动后窗口会收起；再双击 exe 可停止。"
                                 "游戏请用窗口化 / 无边框全屏，运行中按 F8 调设置。"
                                 "右上角地球仪可切换界面语言 / Language",
                            bg=T.BG, fg=T.DIM, font=T.body(9),
                            anchor="w", justify="left", wraplength=592)
        self.tip.place(x=24, y=372, width=592)

        # 检查环境要跑几个解释器探针（约 1 秒），先把窗口显示出来再填
        self.status.configure(text="正在检查环境……", fg=T.DIM)
        self.win.after(60, self.refresh)
        self.win.after(600, self.poll)

    # ---------------- 语言
    def _lang_box(self, body):
        """右上角的语言开关：地球仪 + 当前语言名，点一下换语言。

        地球仪是跨文化的「语言 / 地区」通用符号（Windows、macOS、Steam 都用它），
        不含任何文字，不认识中文的外国玩家也能一眼认出这里是语言入口。
        悬停再给一条双语提示，鼠标一碰就明白。
        """
        langs = i18n.available() or [("zh_CN", "简体中文")]
        cur = i18n.current()
        native = dict(langs).get(cur, cur)

        W, H = 148, 26
        cv = tk.Canvas(body, width=W, height=H, bd=0, highlightthickness=0,
                       bg=T.PLATE, cursor="hand2")
        cv.place(x=262, y=8, width=W, height=H)

        def paint(hover=False):
            cv.delete("all")
            cv.create_rectangle(0, 0, W, H, outline="",
                                fill=T.PLATE_HI if hover else T.PLATE)
            T.globe(cv, 17, H // 2, 9,
                    T.INK_HI if hover else T.GOLD_EDGE,
                    T.GOLD_EDGE if hover else T.GOLD_DK)
            cv.create_text(33, H // 2, text=native, anchor="w",
                           fill=T.INK_HI if hover else T.INK, font=T.body(9))
            cy = H // 2
            cv.create_polygon(W - 15, cy - 2, W - 7, cy - 2, W - 11, cy + 3,
                              fill=T.GOLD if hover else T.GOLD_DK, outline="")

        m = tk.Menu(cv, tearoff=0, bg=T.PANEL, fg=T.INK,
                    activebackground=T.CRIMSON, activeforeground=T.INK_HI,
                    bd=0, relief="flat", font=T.body(9))
        for code, name in langs:
            m.add_command(label=name, command=lambda c=code: self.set_lang(c))

        tip = {"w": None, "job": None}

        def hide_tip(_e=None):
            if tip["job"]:
                try:
                    self.win.after_cancel(tip["job"])
                except Exception:
                    pass
                tip["job"] = None
            if tip["w"] is not None:
                try:
                    tip["w"].destroy()
                except Exception:
                    pass
                tip["w"] = None

        def show_tip():
            tip["job"] = None
            if tip["w"] is not None:
                return
            try:
                if not self.win.winfo_exists():
                    return
                t = tk.Toplevel(self.win)
                t.overrideredirect(True)
                t.attributes("-topmost", True)
                t.configure(bg=T.LINE_END)
                tk.Label(t, text="Language · 语言", bg=T.PANEL, fg=T.INK,
                         font=T.body(9), padx=10, pady=4).pack(padx=1, pady=1)
                t.update_idletasks()
                t.geometry("+%d+%d" % (cv.winfo_rootx() + W - t.winfo_reqwidth(),
                                       cv.winfo_rooty() + H + 6))
                tip["w"] = t
            except Exception:
                tip["w"] = None

        def popup(_e=None):
            hide_tip()
            try:
                m.tk_popup(cv.winfo_rootx(), cv.winfo_rooty() + H + 2)
                m.grab_release()
            except Exception:
                pass

        def on_enter(_e=None):
            paint(True)
            hide_tip()
            try:
                tip["job"] = self.win.after(420, show_tip)
            except Exception:
                pass

        def on_leave(_e=None):
            paint(False)
            hide_tip()

        cv.bind("<Button-1>", popup)
        cv.bind("<Enter>", on_enter)
        cv.bind("<Leave>", on_leave)
        paint()

    def set_lang(self, code):
        if code == i18n.current():
            return
        i18n.set_language(code, cache=True)   # 写进数据目录的 ui.json
        self.restart = True
        try:
            self.win.destroy()               # main() 会再开一个，整窗按新语言重画
        except Exception:
            pass

    # ---------------- 检测
    def refresh(self, force=False):
        try:                       # 换语言时窗口会被销毁，排队里的刷新要能安静退出
            if not self.win.winfo_exists():
                return
        except Exception:
            return
        if force:                      # 「重新检测」：忘掉缓存，重新找解释器
            try:
                os.remove(CACHE)
            except OSError:
                pass
        _log("refresh: 开始")
        self.paths = P.resolve()
        _log(tr("refresh: 路径 game=%s") % (self.paths.get("game") or "-"))
        self.py = find_python()
        _log("refresh: python=%s" % (self.py or "-"))
        self.java = find_java()
        _log("refresh: java=%s" % (self.java or "-"))

        game = self.paths.get("game") or ""
        save = self.paths.get("save") or ""
        self._row("game", ("✓ " if game else "✗ ") + shorten(game),
                  "ok" if game else "bad")
        self._row("save", ("✓ " if save else "✗ 没找到存档") + ("" if not save else shorten(save)),
                  "ok" if save else "bad")

        key = ""
        try:
            cfg = json.load(open(os.path.join(DATA, "config.json"), encoding="utf-8"))
            key = (cfg.get("api_key") or "").strip()
        except Exception:
            pass
        if key:
            self._row("api", "✓ 已填写", "ok")
        else:
            self._row("api", "○ 还没填 —— 启动后按 F8，在「③ 覆盖窗」下面填", "warn")

        self._row("java", ("✓ 已安装" if self.java else
                           "○ 没装（名字会显示成 id，其他照常）"),
                  "ok" if self.java else "warn")

        man = os.path.join(DATA, "world", "default", "manifest.json")
        if os.path.exists(os.path.join(DATA, "world", "default", "loc_zh.json")):
            n = 0
            try:
                n = json.load(open(man, encoding="utf-8")).get("mod_count") or 0
            except Exception:
                pass
            self._row("pack", tr("✓ 已就绪 · %d 个 mod") % n, "ok")
        else:
            self._row("pack", "○ 还没有 —— 第一次启动会自动生成（约十几秒）", "warn")

        pid = running_pid()
        if pid:
            self.set_running_ui(True)
            self.status.configure(text=tr("已经在运行（PID %d）。要停止就点「停止」。") % pid)
        elif not self.proc:
            self.status.configure(text="准备就绪。点「开始旁白」→ 悬浮窗出现 → 进游戏。")
            self.btn_start.enable(bool(self.py))
        if not self.py:
            self.status.configure(
                text="没找到 Python 3（需要 3.9+，装的时候勾选 Add Python to PATH）。"
                     "装好后点「重新检测」。", fg=self.BAD)

    def _row(self, key, text, state="ok"):
        color = {"ok": self.OK, "warn": T.GOLD, "bad": self.BAD}.get(state, T.INK)
        self.rows[key].configure(text=text, fg=color)

    # ---------------- 运行状态
    def set_running_ui(self, running):
        self.btn_start.enable(not running)
        self.btn_start.set_text("运行中…" if running else "开始旁白")
        self.btn_stop.enable(running)

    def start(self):
        if self.proc and self.proc.poll() is None:
            return
        if running_pid():
            self.status.configure(text="已经有一个实例在跑了。先点「停止」。", fg=self.BAD)
            return
        if not self.py:
            messagebox.showerror(
                "缺少 Python",
                "没找到可用的 Python 3。\n\n"
                "请到 python.org 下载 3.9 或更高版本，安装时勾选\n"
                "「Add Python to PATH」，装完再点「重新检测」。")
            return
        if not self.paths.get("game"):
            if not messagebox.askyesno(
                    "没找到游戏目录",
                    "没自动找到《暗黑地牢》的安装目录。\n\n"
                    "还是要启动吗？（启动后按 F8 →「路径…」手动指一下）"):
                return

        os.makedirs(OUT, exist_ok=True)
        try:
            log = open(CHILD_LOG, "a", encoding="utf-8", errors="replace")
            log.write(tr("\n===== %s 启动 =====\n") % time.strftime("%Y-%m-%d %H:%M:%S"))
            log.flush()
        except Exception:
            log = subprocess.DEVNULL

        cmd = [self.py, "-u", os.path.join(HERE, "launch.py")]
        try:
            self.proc = subprocess.Popen(cmd, cwd=HERE, stdout=log, stderr=log,
                                         creationflags=CREATE_NO_WINDOW)
        except Exception as e:
            try:                       # 缓存里的解释器可能已经被卸载/改名
                os.remove(CACHE)
            except Exception:
                pass
            self.status.configure(text=tr("启动失败：%s（点「重新检测」再试）") % e, fg=self.BAD)
            return
        try:
            with open(LOCK, "w", encoding="utf-8") as f:
                f.write(str(self.proc.pid))
        except Exception:
            pass

        self.started = time.time()
        self.set_running_ui(True)
        self.status.configure(text="正在启动…… 悬浮窗出现后就可以进游戏了。", fg=T.GOLD)
        # 无边框窗口没法最小化到任务栏，启动成功后直接收起；要停就再双击一次 exe
        self.win.after(1600, self._hide_after_start)

    def _hide_after_start(self):
        try:
            if self.proc and self.proc.poll() is None:
                self.win.withdraw()
        except Exception:
            pass

    def stop(self):
        pid = 0
        try:
            with open(LOCK, encoding="utf-8") as f:
                pid = int((f.read() or "0").strip() or 0)
        except Exception:
            pid = self.proc.pid if self.proc else 0
        if not pid:
            self.set_running_ui(False)
            return
        self.status.configure(text="正在停止……", fg=T.DIM)
        try:                       # 主程序自己认这个标记：解析器看到就收尾退出
            os.makedirs(OUT, exist_ok=True)
            with open(os.path.join(OUT, ".quit"), "w", encoding="utf-8") as f:
                f.write(time.strftime("%Y-%m-%d %H:%M:%S"))
        except Exception:
            pass
        self.win.after(4000, lambda: self._force_kill(pid))

    def _force_kill(self, pid):
        if _alive(pid):
            try:
                subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"],
                               creationflags=CREATE_NO_WINDOW, timeout=8,
                               capture_output=True)
            except Exception:
                pass

    def poll(self):
        """盯着子进程：退出就恢复按钮、清掉锁。"""
        try:
            if not self.win.winfo_exists():
                return
        except Exception:
            return
        alive = self.proc is not None and self.proc.poll() is None
        if self.proc is not None and not alive:
            rc = self.proc.returncode
            dur = int(time.time() - self.started) if self.started else 0
            self.proc = None
            try:
                os.remove(LOCK)
            except Exception:
                pass
            self.win.deiconify()
            try:
                self.win.lift()
            except Exception:
                pass
            self.set_running_ui(False)
            if rc and rc not in (0, 1):
                self.status.configure(
                    text="主程序退出了（代码 %s，运行 %d 秒）。点「导出日志」把 out\\ 一起发回来。"
                         % (rc, dur), fg=self.BAD)
            else:
                self.status.configure(text=tr("已结束（运行了 %d 秒）。") % dur, fg=T.DIM)
        try:
            self.win.after(800, self.poll)
        except tk.TclError:
            pass

    # ---------------- 其他按钮
    def export_logs(self):
        try:
            path = logexport.export(DATA, open_after=True)
            self.status.configure(text=tr("日志已导出：%s") % path, fg=self.OK)
        except Exception as e:
            self.status.configure(text=tr("导出失败：%s") % e, fg=self.BAD)

    def open_dir(self):
        try:
            os.startfile(DATA)
        except Exception as e:
            self.status.configure(text=tr("打不开目录：%s") % e, fg=self.BAD)

    def on_close(self):
        pid = running_pid() or (self.proc.pid if self.proc else 0)
        if pid:
            ans = messagebox.askyesnocancel(
                "还在运行",
                "旁白还在运行。\n\n"
                "「是」= 停止并退出\n「否」= 让它继续跑，只关这个窗口\n「取消」= 什么都不做")
            if ans is None:
                return
            if ans:
                self.stop()
                self.win.after(1200, self._quit)
                return
        self._quit()

    def _quit(self):
        try:
            self.win.destroy()
        except Exception:
            pass


def main():
    app = Launcher()
    app.win.mainloop()
    while getattr(app, "restart", False):    # 换语言后重建窗口
        app = Launcher()
        app.win.mainloop()


if __name__ == "__main__":
    main()

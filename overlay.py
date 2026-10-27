#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
overlay.py —— DarkestTalk 覆盖窗

两个窗口叠在一起：
  1. 底板层：半透明色块，透明度 bg_alpha 单独可调（嫌挡视野就调低）
  2. 文字层：背景用键控色抠成透明，只有字，透明度 text_alpha 单独可调

全局只占一个热键：F8 —— 打开设置台。
其余功能都是设置台里的按钮：世界观、文风、世界知识、编辑窗口。

用法
  python overlay.py
  python overlay.py --demo
"""
import argparse
import ctypes
import ctypes.wintypes as wt
import json
import os
import re
import sys
import urllib.error
import urllib.request
import threading
import time
import tkinter as tk
from tkinter import font as tkfont
import theme as T

try:
    from version import VERSION, BUILD
except Exception:                     # 单文件运行时也别崩
    VERSION, BUILD = "0.0", ""
APP_TITLE = "DarkestTalk %s" % VERSION

HERE = os.path.dirname(os.path.abspath(__file__))
# 玩家数据（配置 / 译名库 / 日志）都在数据目录里，见 datadir.py
try:
    import datadir
    DATA = datadir.get()
except Exception:
    DATA = HERE
OUT = os.path.join(DATA, "out")

# 界面多语言：语言包在 lang/*.json，缺键回落中文
import i18n
i18n.init()
i18n.install()
from i18n import tr

# Win32 调用必须写死参数类型：64 位下句柄是 8 字节，按默认的 int 传进去
# 上 32 位是垃圾，SetWindowPos 会直接返回 0 而不报错（置顶一直在静默失败）。
_user32 = ctypes.windll.user32
try:
    _user32.GetParent.argtypes = [wt.HWND]
    _user32.GetParent.restype = wt.HWND
    _user32.GetWindowLongW.argtypes = [wt.HWND, ctypes.c_int]
    _user32.GetWindowLongW.restype = ctypes.c_long
    _user32.SetWindowLongW.argtypes = [wt.HWND, ctypes.c_int, ctypes.c_long]
    _user32.SetWindowLongW.restype = ctypes.c_long
    _user32.SetWindowPos.argtypes = [wt.HWND, wt.HWND, ctypes.c_int, ctypes.c_int,
                                     ctypes.c_int, ctypes.c_int, ctypes.c_uint]
    _user32.SetWindowPos.restype = wt.BOOL
except Exception:                # 非 Windows 平台上跑语法检查时不至于炸
    pass
DEFAULT = {
    "text_file": os.path.join(DATA, "out", "live.txt"),
    "x": 640, "y": 44, "width": 860, "height": 210,
    "bg_alpha": 0.42,
    "text_alpha": 0.95,
    "bg": "#000000", "fg": "#ebebd5", "accent": "#b00d12",
    "font": "Microsoft YaHei", "size": 17,
    "padding": 18,
    "char_delay_ms": 22,
    "click_through": True,
    "fade_after_sec": 0,
    "force_topmost": True,       # 每 2 秒把窗口重新压回最上层
}

GWL_EXSTYLE = -20
WS_EX_LAYERED = 0x00080000
WS_EX_TRANSPARENT = 0x00000020
WS_EX_NOACTIVATE = 0x08000000
KEY_COLOR = "#010203"

# SetWindowPos 的几颗常量：只改 Z 序，不动位置和大小、不抢焦点
HWND_TOPMOST = -1
HWND_NOTOPMOST = -2
SWP_NOSIZE = 0x0001
SWP_NOMOVE = 0x0002
SWP_NOACTIVATE = 0x0010
SWP_NOOWNERZORDER = 0x0200

# 右侧控制栏配色与尺寸：底板用深炭色，外圈一道金线，跟覆盖窗同一套色
RAIL_BG = "#050508"
RAIL_HOVER = "#1a1a22"
RAIL_INK = "#c9c6b6"
RAIL_INK_ON = "#ebebd5"
RAIL_DONE_BG = "#8a0a0e"          # 「完成」= 深红填充，跟设置台的主操作同一档
RAIL_DONE_FG = "#ebebd5"
RAIL_DONE_HOVER = "#b00d12"
RAIL_CLOSE = "#c9c6b6"
RAIL_CLOSE_HOVER = "#d4232a"
RAIL_EDGE = "#6d6757"
RAIL_W_EDIT = 58
RAIL_W_IDLE = 26
RAIL_MARGIN = 4


def window_handle(win):
    """Tk 窗口 → 真正的顶层 HWND（overrideredirect 时 winfo_id 本身就是）。"""
    win.update_idletasks()
    return _user32.GetParent(win.winfo_id()) or win.winfo_id()


def read_jsonl_tail(path, limit=40, max_bytes=512 * 1024):
    """只读文件尾巴的 JSONL。

    调试记录能涨到好几 MB（一次战斗就上千行），以前整份 read 再 splitlines，
    点一下就卡快两秒。这里按字节定位到尾部，最多解析 512KB。
    """
    try:
        size = os.path.getsize(path)
    except OSError:
        return []
    try:
        with open(path, "rb") as f:
            if size > max_bytes:
                f.seek(size - max_bytes)
                f.readline()                 # 丢掉可能被截断的半行
            data = f.read().decode("utf-8", errors="replace")
    except OSError:
        return []
    out = []
    for line in data.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except Exception:
            pass
    return out[-limit:]


def apply_exstyle(win, click_through):
    try:
        hwnd = window_handle(win)
        style = _user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
        style |= (WS_EX_LAYERED | WS_EX_NOACTIVATE)
        if click_through:
            style |= WS_EX_TRANSPARENT
        else:
            style &= ~WS_EX_TRANSPARENT
        _user32.SetWindowLongW(hwnd, GWL_EXSTYLE, style)
    except Exception as e:
        print(tr("[警告] 设置窗口样式失败：%s") % e)


def force_topmost(win, on=True):
    """直接把 HWND 压到 Z 序顶端（或取消），不抢焦点、不动位置。

    Tk 的 -topmost 只在设置的那一刻起作用：游戏切全屏、换分辨率、或被别的
    置顶窗口（播放器、录屏工具）顶一下，标志就掉了，之后再也回不来。
    SetWindowPos(HWND_TOPMOST) 每次调用都会把窗口重新排到最前面，
    所以隔几秒重设一次就能一直压住。
    """
    try:
        hwnd = window_handle(win)
        _user32.SetWindowPos(
            hwnd, wt.HWND(HWND_TOPMOST if on else HWND_NOTOPMOST), 0, 0, 0, 0,
            SWP_NOSIZE | SWP_NOMOVE | SWP_NOACTIVATE | SWP_NOOWNERZORDER)
    except Exception:
        pass


class Hotkeys(threading.Thread):
    MOD_NOREPEAT = 0x4000

    def __init__(self, root, bindings):
        super().__init__(daemon=True)
        self.root, self.bindings = root, bindings

    def run(self):
        user32 = ctypes.windll.user32
        for vk in self.bindings:
            if not user32.RegisterHotKey(None, vk, self.MOD_NOREPEAT, vk):
                print(tr("[热键] 注册失败（可能被占用）：VK=%s") % vk)
        msg = wt.MSG()
        while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            if msg.message == 0x0312:
                cb = self.bindings.get(msg.wParam)
                if cb:
                    self.root.after(0, cb)



try:
    from prompts import (DEFAULT_STYLE, PRESETS, WORLD_TEMPLATE,
                         DEFAULT_USER_TEMPLATE)
except Exception:
    DEFAULT_STYLE = "你是《暗黑地牢》的旁白。写这一小段现场，3–6句，只写事实清单里的名字。"
    PRESETS = {"旁白机（内置）": DEFAULT_STYLE}
    WORLD_TEMPLATE = "# 世界设定（玩家自填）\n\n在这里写你自己的世界。\n"
    DEFAULT_USER_TEMPLATE = ("【这一趟的同伴】\n{{同伴}}\n\n"
                             "【相关背景】\n{{背景}}\n\n"
                             "【这一趟里刚发生的变化】\n{{变化}}\n\n"
                             "只写这一段旁白。")



class Overlay:
    # 旁白全文都留着，一个字都不删；开销与总字数无关（见 _build_text_layer 的注释）。
    LINE_GAP = 3                   # 行距：字号行高之外再多几个像素

    def __init__(self, cfg_path):
        # 覆盖窗日志也每次换一份，旧的那份留 .prev
        try:
            import housekeeping
            housekeeping.rotate_overlay_log(OUT)
        except Exception:
            pass
        self.cfg_path = cfg_path
        self.cfg = dict(DEFAULT)
        self.cfg_mtime = 0
        self.load_cfg(force=True)

        self.root = tk.Tk()
        self.root.overrideredirect(True)
        self.root.attributes("-topmost", True)
        self.root.configure(bg=self.cfg["bg"])
        self.root.attributes("-alpha", self.cfg["bg_alpha"])

        self.dialogs = []          # 自己开的设置窗口，编辑模式只清这些
        self.wins = {}             # 页名 → 窗口：同一页只留一个
        self.editing = False
        self._drag = None
        self._rs = None

        # 旁白缓冲：全文保留（_buf 只增不减），_lines 是排好版的行，
        # _partial 是最后那行还没排满的残行
        self._buf, self._lines, self._partial = "", [], ""
        self._items, self._texts, self._rows = [], [], 0
        self._job_relayout = None

        self._build_text_layer()

        self.hint = tk.Label(self.root, text="F8 打开设置页面",
                             bg=self.cfg["bg"], fg="#8b887a",
                             font=("Microsoft YaHei", 8))

        self.apply_geometry()
        self.root.after(30, self.setup_windows)
        self.root.after(150, self.bind_hotkeys)
        self.root.after(1500, self.maybe_onboard)

        self.offset = 0
        self.queue = ""
        self.typing = False
        self.last_change = time.time()
        self.root.after(180, self.poll)

    # ---------------- 配置
    def load_cfg(self, force=False):
        if not os.path.exists(self.cfg_path):
            if force:
                json.dump(DEFAULT, open(self.cfg_path, "w", encoding="utf-8"),
                          ensure_ascii=False, indent=1)
            return
        m = os.path.getmtime(self.cfg_path)
        if not force and m == self.cfg_mtime:
            return
        self.cfg_mtime = m
        try:
            cfg = json.load(open(self.cfg_path, encoding="utf-8"))
            self.cfg.update({k: v for k, v in cfg.items() if k in DEFAULT})
        except Exception as e:
            print(tr("[警告] 配置读取失败：%s") % e)

    def save_geometry(self):
        try:
            raw = {}
            if os.path.exists(self.cfg_path):
                raw = json.load(open(self.cfg_path, encoding="utf-8"))
            raw.update({"x": self.cfg["x"], "y": self.cfg["y"],
                        "width": self.cfg["width"], "height": self.cfg["height"],
                        "bg_alpha": self.cfg["bg_alpha"],
                        "text_alpha": self.cfg["text_alpha"],
                        "size": int(self.cfg.get("size", 17)),
                        "force_topmost": bool(self.cfg.get("force_topmost", True)),
                        "hint_seen": bool(self.cfg.get("hint_seen"))})
            json.dump(raw, open(self.cfg_path, "w", encoding="utf-8"),
                      ensure_ascii=False, indent=1)
            self.cfg_mtime = os.path.getmtime(self.cfg_path)
            print(tr("[配置] 位置 %s,%s 尺寸 %sx%s 已保存") %
                  (self.cfg["x"], self.cfg["y"], self.cfg["width"], self.cfg["height"]))
        except Exception as e:
            print(tr("[警告] 保存失败：%s") % e)

    def apply_hint(self):
        """一行小提示：按过 F8 之后就不再显示。"""
        try:
            if self.cfg.get("hint_seen"):
                self.hint.place_forget()
            else:
                self.hint.place(x=9, rely=1.0, y=-3, anchor="sw")
                self.hint.lift()
        except Exception:
            pass

    def apply_geometry(self):
        c = self.cfg
        self.root.geometry("%dx%d+%d+%d" % (c["width"], c["height"], c["x"], c["y"]))
        self.apply_hint()
        pad = c["padding"]
        # 文字列右边给控制栏让出一条：栏宽 + 贴边 4 + 与文字留 12
        inset = self._rail_width() + RAIL_MARGIN + 12
        self.txt_win.geometry("%dx%d+%d+%d" % (
            max(40, c["width"] - pad - inset), max(30, c["height"] - 2 * pad),
            c["x"] + pad, c["y"] + pad))
        self._place_rail()
        # 宽度变了要重新折行：拖拽时 Configure 连发，攒 120ms 再排一次
        self._queue_relayout()

    # ---------------- 置顶
    def _overlay_wins(self):
        """三层窗口：底板、文字、控制栏。"""
        return [w for w in (self.root, getattr(self, "txt_win", None),
                            getattr(self, "rail", None)) if self._alive(w)]

    def set_force_topmost(self, on):
        """开关「始终置顶」：立即对三层窗口生效，并写回 overlay.json。"""
        self.cfg["force_topmost"] = bool(on)
        for w in self._overlay_wins():
            if on:
                try:
                    w.attributes("-topmost", True)
                except Exception:
                    pass
            force_topmost(w, bool(on))
        self.save_geometry()
        print(tr("[覆盖窗] 始终置顶 %s") % ("开启" if on else "关闭"))

    def keep_on_top(self):
        """每 2 秒重新压一次 Z 序。

        游戏切全屏、换分辨率、别的置顶窗口闪一下，都会把「置顶」标志顶掉，
        掉了之后 Tk 自己不会补。定时重设是目前不往游戏里注入 DLL、
        又能在无边框全屏下稳定压住的做法。

        设置窗排在后面压 —— 它们同为置顶时「谁后设谁在上」，顺序反了
        旁白窗就会盖住设置台。
        """
        if self.cfg.get("force_topmost", True):
            for w in self._overlay_wins():
                force_topmost(w, True)
            for w in list(self.dialogs):
                if self._alive(w):
                    force_topmost(w, True)
        try:
            self.root.after(2000, self.keep_on_top)
        except Exception:
            pass

    def _build_text_layer(self):
        """文字层单独一个窗口（键控色抠底）。销毁后可以重建。

        这里用的是 Canvas 自绘、不是 tk.Text：Text 每次插入都要把整段内容重排，
        实测 1500 字时插入一次 + see() 要 130ms、3000 字 400ms、6000 字 1.2 秒，
        想"不限制字数"就一定会卡死。改成自己按宽度折行、只画窗口里那几行之后，
        开销就跟总字数无关了（每拍 1~2ms），全文一律保留、不做任何截断。
        """
        self.txt_win = tk.Toplevel(self.root)
        self.txt_win.overrideredirect(True)
        self.txt_win.attributes("-topmost", True)
        self.txt_win.configure(bg=KEY_COLOR)
        try:
            self.txt_win.attributes("-transparentcolor", KEY_COLOR)
        except Exception:
            pass
        try:
            self.txt_win.attributes("-alpha", self.cfg["text_alpha"])
        except Exception:
            pass
        self._sync_font()
        self.text = tk.Canvas(self.txt_win, bd=0, highlightthickness=0,
                              bg=KEY_COLOR, cursor="arrow")
        self.text.pack(fill="both", expand=True)
        self._items, self._texts, self._rows = [], [], 0
        self.apply_geometry()
        self._relayout()               # 缓冲里已有的文字（如重建之前收到的）立刻补画

    # ---------------- 文字排版（Canvas 版）

    def _sync_font(self):
        """按当前配置重建字体对象（字号滑条 / overlay.json 改动都走这里）。"""
        size = max(8, min(48, int(self.cfg.get("size", 17))))
        self.cfg["size"] = size
        self.f = tkfont.Font(family=self.cfg.get("font", "Microsoft YaHei"), size=size)
        self._wcache = {}              # 单字宽度缓存：换字体/字号就作废

    def _char_w(self, ch):
        w = self._wcache.get(ch)
        if w is None:
            w = self._wcache[ch] = self.f.measure(ch)
        return w

    def _text_area(self):
        """文字区实际宽高（窗口还没映射时按配置算）。"""
        c = self.cfg
        try:
            w, h = self.text.winfo_width(), self.text.winfo_height()
        except Exception:
            w = h = 0
        if w < 60 or h < 40:
            pad = c["padding"]
            w = max(40, c["width"] - pad - (self._rail_width() + RAIL_MARGIN + 12))
            h = max(30, c["height"] - 2 * pad)
        return w, h

    def _wrap_into(self, text, max_w, out):
        """把 text 按宽度折行追加到 out，返回最后那行没排满的残行。

        中日文按字断行；碰到英文/数字尽量整词断（比如英雄名、mod 名）。
        """
        cur, w = "", 0.0
        for ch in text:
            if ch == "\r":             # CRLF 的 \r 直接丢掉，免得每行尾巴多一个空位
                continue
            if ch == "\n":
                out.append(cur)
                cur, w = "", 0.0
                continue
            cw = self._char_w(ch)
            if cur and w + cw > max_w:
                if ch.isascii() and ch.isalnum() and cur[-1:].isascii() and cur[-1:].isalnum():
                    sp = cur.rfind(" ")
                    if 0 < sp and len(cur) - sp <= 16:
                        out.append(cur[:sp])
                        cur = cur[sp + 1:]
                    else:
                        out.append(cur)
                        cur = ""
                else:
                    out.append(cur)
                    cur = ""
                w = self.f.measure(cur) if cur else 0.0
            cur += ch
            w += cw
        return cur

    def _append_text(self, chunk):
        """追加一小段并重画。全文都留着（self._buf 只增不减）。"""
        if not chunk:
            return
        self._buf += chunk
        w, _ = self._text_area()
        self._partial = self._wrap_into(self._partial + chunk, max(40, w - 2),
                                        self._lines)
        self._render()

    def _relayout(self):
        """宽度 / 字号变了：按当前设置把整段重排一遍（防抖后调用）。"""
        self._job_relayout = None
        if not self._alive(getattr(self, "text", None)):
            return
        w, _ = self._text_area()
        self._lines = []
        self._partial = self._wrap_into(self._buf, max(40, w - 2), self._lines)
        self._items, self._texts, self._rows = [], [], 0
        try:
            self.text.delete("all")
        except tk.TclError:
            return
        self._render()

    def _queue_relayout(self, delay=120):
        """改窗口尺寸时 Configure 会连发几十次，攒一下再重排。"""
        job = getattr(self, "_job_relayout", None)
        if job:
            try:
                self.root.after_cancel(job)
            except Exception:
                pass
        try:
            self._job_relayout = self.root.after(delay, self._relayout)
        except Exception:
            self._job_relayout = None

    def _render(self):
        """只画窗口里放得下的最后几行 —— 和字数无关，恒定开销。"""
        cv = self.text
        if not self._alive(cv):
            return
        try:
            h = cv.winfo_height()
        except tk.TclError:
            return
        ls = max(8, int(self.f.metrics("linespace"))) + self.LINE_GAP
        if h <= 20:
            return
        rows = max(1, int(h // ls))
        lines = self._lines
        if self._partial:
            lines = lines + [self._partial]
        tail = lines[-rows:]
        if len(tail) != len(self._items):
            try:
                cv.delete("all")
                self._items = [cv.create_text(0, i * ls, text=ln, anchor="nw",
                                              fill=self.cfg["fg"], font=self.f)
                               for i, ln in enumerate(tail)]
            except tk.TclError:
                self._items, self._texts = [], []
                return
            self._texts = list(tail)
            self._rows = rows
            return
        for i, ln in enumerate(tail):
            if self._texts[i] != ln:
                try:
                    cv.itemconfigure(self._items[i], text=ln)
                except tk.TclError:
                    return
                self._texts[i] = ln

    def set_font_size(self, size, defer=True):
        """字号滑条：立即生效（重排 + 重画），写回文件由 save() 负责。

        拖动时 command 会被连发几十次，每次都整段重排会很顿：攒 140ms 再排。
        """
        size = int(round(float(size)))
        if size == int(self.cfg.get("size", 17)):
            return
        self.cfg["size"] = size
        self._sync_font()
        if defer:
            self._queue_relayout(140)
        else:
            self._relayout()

    def ensure_text_layer(self):
        """文字层没了就重建，别让程序看起来还活着却不出字。"""
        if self._alive(getattr(self, "text", None)):
            return True
        try:
            self._build_text_layer()
            apply_exstyle(self.txt_win, True)
            print("[覆盖窗] 文字层已重建")
            return True
        except Exception as e:
            print(tr("[覆盖窗] 文字层重建失败：%s") % e)
            return False

    # ---------------- 右侧控制栏（退出 / 完成 / 拖动 / 缩放）
    def _rail_width(self):
        return RAIL_W_EDIT if self.editing else RAIL_W_IDLE

    def _rail_metrics(self):
        """控制栏的宽高、每一项的行位，以及分隔线的 y（没有就是 None）。

        平时只留一个 ×（一条细窄的角标）；进入编辑后纵向排四行：
        × → 完成 → 拖动 → 缩放。窗口压得很矮时收掉分隔线、行距收紧。
        """
        if not self.editing:
            return RAIL_W_IDLE, 24, [("close", "×", 0, 24)], None
        compact = self.cfg["height"] < 150
        bh = 22 if compact else 26
        rows, y = [], 0
        rows.append(("close", "×", y, bh))
        y += bh
        sep = None
        if not compact:
            sep = y + 3               # 给金色分隔线留出空档
            y += 9
        for key, text in (("done", "完成"), ("move", "拖动"), ("resize", "缩放")):
            rows.append((key, text, y, bh))
            y += bh
        return RAIL_W_EDIT, y, rows, sep

    def _build_rail(self):
        """控制栏是单独一个小窗口。

        主窗和文字层都是"点击穿透"（WS_EX_TRANSPARENT），上面的按钮点不到，
        所以控制栏必须自己开窗：带 NOACTIVATE（不抢焦点），但不带 TRANSPARENT（能点）。
        """
        self.rail = tk.Toplevel(self.root)
        self.rail.overrideredirect(True)
        self.rail.attributes("-topmost", True)
        self.rail.configure(bg=RAIL_EDGE)          # 底板一像素金线 → 描边
        self.rail_body = tk.Frame(self.rail, bg=RAIL_BG, bd=0, highlightthickness=0)
        self.rail_body.pack(fill="both", expand=True, padx=1, pady=1)
        self._style_rail()
        # Toplevel 的 HWND 是懒创建的：刚建好就设样式会落空（点一下还会抢走
        # 游戏焦点），所以映射之后再补一次。
        try:
            self.rail.after(90, self._style_rail)
        except Exception:
            pass
        self._populate_rail()
        self.rail.lift()

    def _rail_alpha(self):
        """平时完全跟随文字透明度；编辑时兜一个 0.7 的下限，按钮才看得清。"""
        a = float(self.cfg.get("text_alpha", 0.95))
        return max(a, 0.7) if self.editing else a

    def _style_rail(self):
        """透明度 + 扩展样式一起设。

        Tk 改 -alpha 时会照它自己缓存的那份样式重写 exstyle，把 NOACTIVATE
        冲掉 —— 结果就是点一下控制栏、焦点从游戏跳走。所以每设一次 alpha
        都得把扩样样式补回来，顺序不能反。
        """
        try:
            self.rail.attributes("-alpha", self._rail_alpha())
        except Exception:
            pass
        apply_exstyle(self.rail, False)

    def _restyle_layers(self):
        """透明度滑块拉动后调用：同样得把三层的扩展样式补回来。"""
        try:
            apply_exstyle(self.root,
                          False if self.editing else self.cfg["click_through"])
            apply_exstyle(self.txt_win, True)
        except Exception:
            pass
        if self._alive(getattr(self, "rail", None)):
            self._style_rail()

    def _populate_rail(self):
        """按当前模式重排内容。切换编辑前后、缩放结束后各调一次。"""
        if not self._alive(getattr(self, "rail", None)):
            return
        try:
            for ch in self.rail_body.winfo_children():
                ch.destroy()
            w, h, rows, sep = self._rail_metrics()
            h = max(24, min(h, self.cfg["height"] - 2 * RAIL_MARGIN))
            self.rail_body.pack_propagate(False)
            self.rail_body.configure(width=w, height=h)
            self._style_rail()
            if sep is not None:
                tk.Frame(self.rail_body, bg=RAIL_EDGE, bd=0, highlightthickness=0
                         ).place(x=9, y=sep, width=max(6, w - 18), height=1)
            for key, text, y, bh in rows:
                if y >= h:
                    continue
                self._rail_btn(key, text, y, min(bh, h - y), w)
            self._place_rail()
            self.rail.lift()
        except Exception as e:
            print(tr("[覆盖窗] 控制栏重排失败：%s") % e)

    def _place_rail(self):
        """贴在窗口右侧：与上边距 4、与右边距 4，永远压在最上层。"""
        if not self._alive(getattr(self, "rail", None)):
            return
        c = self.cfg
        w, h, _, _ = self._rail_metrics()
        h = max(24, min(h, c["height"] - 2 * RAIL_MARGIN))
        try:
            self.rail.geometry("%dx%d+%d+%d" % (
                w + 2, h + 2,
                c["x"] + c["width"] - w - RAIL_MARGIN, c["y"] + RAIL_MARGIN))
        except Exception:
            pass

    def _rail_btn(self, key, text, y, h, w):
        """一格按钮：深炭底、悬停亮起；「完成」用暖金底表示这是收尾动作。"""
        big = key == "close"
        base_bg = RAIL_DONE_BG if key == "done" else RAIL_BG
        base_fg = RAIL_DONE_FG if key == "done" else RAIL_CLOSE if big else RAIL_INK
        hover_bg = RAIL_DONE_HOVER if key == "done" else RAIL_HOVER
        hover_fg = RAIL_CLOSE_HOVER if big else RAIL_INK_ON
        cur = {"move": "fleur", "resize": "size_nw_se"}.get(key, "hand2")

        # 每格左右内缩 3、上下留 1，读起来像四枚独立的按钮，而不是一条色带
        f = tk.Frame(self.rail_body, bg=base_bg, bd=0, highlightthickness=0,
                     cursor=cur)
        f.place(x=3, y=y + 1, width=max(8, w - 6), height=max(6, h - 2))
        lb = tk.Label(f, text=text, bg=base_bg, fg=base_fg,
                      font=("Microsoft YaHei", 13 if big else 9,
                            "bold" if big else "normal"),
                      cursor=cur)
        lb.place(relx=0.5, rely=0.5, anchor="center")

        def paint(bg, fg):
            f.configure(bg=bg)
            lb.configure(bg=bg, fg=fg)

        act = {"close": self.quit_app, "done": self.toggle_edit}.get(key)
        for wdg in (f, lb):
            wdg.bind("<Enter>", lambda e: paint(hover_bg, hover_fg))
            wdg.bind("<Leave>", lambda e: paint(base_bg, base_fg))
            if act is not None:
                wdg.bind("<Button-1>", lambda e, a=act: a())
            elif key == "move":
                wdg.bind("<Button-1>", self._drag_begin)
                wdg.bind("<B1-Motion>", self._drag_move)
                wdg.bind("<ButtonRelease-1>", self._drag_end)
            elif key == "resize":
                wdg.bind("<Button-1>", self._rs_begin)
                wdg.bind("<B1-Motion>", self._rs_move)
                wdg.bind("<ButtonRelease-1>", self._rs_end)

    def quit_app(self):
        """关闭整个工具：给解析器留一个退出标记，然后自己退出。"""
        try:
            with open(os.path.join(OUT, ".quit"), "w",
                      encoding="utf-8") as fh:
                fh.write(time.strftime("%Y-%m-%d %H:%M:%S"))
            print("[覆盖窗] 收到关闭请求，正在退出")
            sys.stdout.flush()
        except Exception:
            pass
        for w in (getattr(self, "rail", None), getattr(self, "txt_win", None),
                  self.root):
            try:
                if w is not None and w.winfo_exists():
                    w.destroy()
            except Exception:
                pass
        try:
            self.root.quit()
        except Exception:
            pass
        os._exit(0)              # 热键线程还挂在消息循环里，直接收掉

    def setup_windows(self):
        apply_exstyle(self.root, self.cfg["click_through"])
        apply_exstyle(self.txt_win, True)
        self._build_rail()
        self.apply_geometry()        # 建好之后再摆一次位置（创建时还没这个窗口）
        # 窗口第一次映射后 Tk 会把它摆回 0,0，延迟一次再定住
        self.root.after(150, self.apply_geometry)
        self.txt_win.lift()
        self.rail.lift()
        self.bind_drag()
        # 置顶看门狗：游戏切全屏、别的置顶程序闪一下都可能把置顶顶掉
        self.root.after(1200, self.keep_on_top)

    # ---------------- 拖动 / 缩放
    def _drag_begin(self, e):
        if self.editing:
            self._drag = (e.x_root, e.y_root, self.cfg["x"], self.cfg["y"])

    def _drag_move(self, e):
        if not self.editing or not self._drag:
            return
        self.cfg["x"] = self._drag[2] + (e.x_root - self._drag[0])
        self.cfg["y"] = self._drag[3] + (e.y_root - self._drag[1])
        self.apply_geometry()

    def _drag_end(self, e):
        if self.editing and self._drag:
            self._drag = None
            self.save_geometry()

    def _rs_begin(self, e):
        self._rs = (e.x_root, e.y_root, self.cfg["width"], self.cfg["height"])

    def _rs_move(self, e):
        if not self._rs:
            return
        self.cfg["width"] = max(160, self._rs[2] + (e.x_root - self._rs[0]))
        self.cfg["height"] = max(80, self._rs[3] + (e.y_root - self._rs[1]))
        self.apply_geometry()

    def _rs_end(self, e):
        if self._rs:
            self._rs = None
            self.save_geometry()
            self._populate_rail()    # 高度跨过紧凑阈值时重排一次

    def bind_drag(self):
        # 编辑模式下，窗口任意空白处按住也能拖，不必非得抓着「拖动」
        self.root.bind("<Button-1>", self._drag_begin)
        self.root.bind("<B1-Motion>", self._drag_move)
        self.root.bind("<ButtonRelease-1>", self._drag_end)

    def toggle_edit(self):
        self.editing = not self.editing
        if self.editing:
            apply_exstyle(self.root, False)
            print("[编辑] 拖「拖动」移动窗口，拖「缩放」改大小，点「完成」退出")
        else:
            apply_exstyle(self.root, self.cfg["click_through"])
            self.save_geometry()
            print("[编辑] 已退出，鼠标恢复穿透")
        self._populate_rail()
        self.apply_geometry()

    def enter_edit_mode(self):
        """从设置面板进入拖动/缩放模式。

        只关自己开过的设置窗 —— 以前按 winfo_class 扫 Toplevel，会把
        self.txt_win（文字层）一起销毁，之后打字就报 invalid command name。
        """
        for w in list(self.dialogs):
            try:
                if w.winfo_exists():
                    w.destroy()
            except Exception:
                pass
        self.dialogs = [w for w in self.dialogs if self._alive(w)]
        self.wins = {k: v for k, v in self.wins.items() if self._alive(v)}
        if not self.editing:
            self.toggle_edit()

    def _alive(self, w):
        try:
            return bool(w) and bool(w.winfo_exists())
        except Exception:
            return False

    # ---------------- 热键
    def bind_hotkeys(self):
        # 全局只占一个键：F8。其余功能都是 F8 面板里的按钮。
        self.hotkeys = Hotkeys(self.root, {
            0x77: self.open_prompt_editor,   # F8
        })
        self.hotkeys.start()

    def maybe_onboard(self):
        """第一次跑、还没填 key 的时候，直接把设置台弹出来。"""
        try:
            uv = self.load_user_cfg()
            if uv.get("api_key"):
                return
            if self.cfg.get("onboarded"):
                return
            self.cfg["onboarded"] = True
            self.save_geometry()
            self.open_prompt_editor()
        except Exception:
            pass

    # ---------------- 设置窗：同一个页面只留一个
    def _reuse(self, key):
        """这一页已经开着就抬到最前，返回 True（调用方直接 return）。

        以前每点一次按钮就新开一个窗口，能同时开出三四个「设置」互相压着，
        改动在各自的内存副本里，保存时互相覆盖。现在一页一窗，重复点只是置顶。
        """
        w = self.wins.get(key)
        if not self._alive(w):
            self.wins.pop(key, None)
            return False
        try:
            if w.state() == "withdrawn":
                w.deiconify()
            w.attributes("-topmost", True)
            w.lift()
            w.focus_force()
            print(tr("[窗口] 「%s」已经开着，抬到最前") % (w.title() or key))
        except Exception:
            pass
        return True

    def _own(self, key, win):
        """登记一个自建的设置窗，记住它属于哪一页。"""
        self.wins[key] = win
        self.dialogs.append(win)
        return win

    # ---------------- 路径设置
    def open_paths(self):
        """让玩家把「游戏 / 存档 / 工坊 / mod」四个目录对上。

        为什么需要手动：游戏可能装在任意盘、可能不是 Steam 版（就没有 userdata
        和 workshop），也可能把 mod 放在别处。自动探测覆盖大多数情况，剩下的
        用这个窗口指一下就好，结果写进 paths.json，下次启动直接读。
        """
        if self._reuse("paths"):
            return
        try:
            import paths as paths_mod
        except Exception as e:
            print(tr("[路径] paths.py 读不到：%s") % e)
            return

        ui = T.ui()
        cur = paths_mod.resolve()

        win = tk.Toplevel(self.root)
        win.title("DarkestTalk · 路径")
        win.configure(bg=ui["bg"])
        win.geometry("%dx%d+%d+%d" % (820 + 2 * T.PAD, 400 + 2 * T.PAD, max(20, self.cfg["x"] - 40),
                                        max(20, self.cfg["y"] + 30)))
        win.resizable(False, False)
        win.attributes("-topmost", True)
        T.shell(win, "DarkestTalk · 路径", win.destroy, 820 + 2 * T.PAD, 400 + 2 * T.PAD)
        body = T.content(win, 820, 366)
        self._own("paths", win)
        self.make_editable(body)

        tk.Label(body, text="这四个目录决定工具能不能读到游戏数据。留空=自动探测；改完保存，重启工具后生效。",
                 bg=ui["bg"], fg=ui["dim"], font=T.body(9)
                 ).place(x=16, y=12)

        rows = [("game", "游戏目录", "里面有 DarkestDungeon.exe / app.log"),
                ("save", "存档目录", "profile_0，里面有 persist.game.json"),
                ("workshop", "创意工坊", "steamapps\\workshop\\content\\262060（非 Steam 版留空）"),
                ("mods", "本地 mod", "一般是 <游戏目录>\\mods")]

        entries = {}
        y = 44
        for key, label, tip in rows:
            tk.Label(body, text=label, bg=ui["bg"], fg=ui["ink"],
                     font=T.body(10)).place(x=16, y=y + 4)
            e = T.entry(body, 110, y, 560, 26, cur.get(key) or "", T.mono(9))
            entries[key] = e
            tk.Label(body, text=tip, bg=ui["bg"], fg=ui["dim"],
                     font=T.body(8)).place(x=110, y=y + 27)

            def browse(k=key, ent=e):
                from tkinter import filedialog
                d = filedialog.askdirectory(initialdir=ent.get() or os.path.expanduser("~"),
                                            title="选择目录")
                if d:
                    ent.delete(0, "end")
                    ent.insert(0, os.path.normpath(d))
                    check()
            T.button(body, 678, y, 64, 26, "浏览…", browse, "plate", 9)
            y += 58

        status = tk.Label(body, text="", bg=ui["bg"], fg=ui["dim"], justify="left",
                          font=T.body(9))
        status.place(x=16, y=y + 6)

        def read_entries():
            return {k: e.get().strip() for k, e in entries.items()}

        def check(*_a):
            v = read_entries()
            bits = []
            if v["game"]:
                bits.append((tr("游戏目录") + ("✓" if paths_mod.looks_like_game(v["game"]) else "✗"),
                             paths_mod.looks_like_game(v["game"])))
            else:
                bits.append(("游戏目录：自动", None))
            if v["save"]:
                bits.append((tr("存档目录") + ("✓" if paths_mod.looks_like_save(v["save"]) else "✗"),
                             paths_mod.looks_like_save(v["save"])))
            else:
                bits.append(("存档目录：自动", None))
            bad = [t for t, ok in bits if ok is False]
            msg = "  ".join(t for t, _ in bits)
            if bad:
                msg += "\n打 ✗ 的目录里没有该有的文件，检查一下是不是选到了上一级。"
            status.config(text=msg, fg=T.CRIMSON_HI if bad else ui["dim"])
            return not bad

        def autodetect():
            r = paths_mod.resolve()
            for k, e in entries.items():
                e.delete(0, "end")
                if r.get(k):
                    e.insert(0, r[k])
            check()

        def save():
            if not check():
                return
            v = read_entries()
            try:
                paths_mod.save_json_config(v["game"], v["save"], v["workshop"], v["mods"])
            except Exception as e:
                status.config(text=tr("保存失败：%s") % e, fg=T.CRIMSON_HI)
                return
            status.config(text="已保存到 paths.json · 重启工具后生效", fg=T.GOLD)

        def btn(x, width, text, cmd, primary=False):
            T.button(body, x, y + 54, width, 30, text, cmd,
                     "red" if primary else "plate", 10)

        btn(16, 110, "自动检测", autodetect)
        btn(136, 110, "保存", save, primary=True)
        btn(256, 90, "关闭", win.destroy)
        tk.Label(body, text="非 Steam 版：工坊那行留空即可，本地 mod 仍然会读。",
                 bg=ui["bg"], fg=ui["dim"], font=T.body(8)
                 ).place(x=360, y=y + 60)

        check()
        win.lift()
        win.focus_force()
        win.bind("<Escape>", lambda e: win.destroy())

    # ---------------- 世界知识
    def open_knowledge(self):
        """世界知识：左边一条条列出来，点哪条就改哪条的介绍正文。

        标题与方括号里的关键词是拿去和存档 id 匹配的，只读；
        正文改动写进 knowledge_overrides.json（独立的覆盖层），
        重建知识库、换 mod 都不会把它冲掉。
        """
        if self._reuse("knowledge"):
            return
        base = os.path.join(DATA, "world", "default")
        try:
            import knowledge as KB
        except Exception:
            KB = None

        win = tk.Toplevel(self.root)
        win.title("DarkestTalk · 世界知识")
        self._own("knowledge", win)
        win.attributes("-topmost", True)
        win.geometry("%dx%d+%d+%d" % (1024 + 2 * T.PAD, 758 + 2 * T.PAD,
                                      max(20, self.cfg["x"] - 160), self.cfg["y"] + 20))
        win.configure(bg=T.BG)
        T.shell(win, "DarkestTalk · 世界知识", win.destroy,
                1024 + 2 * T.PAD, 758 + 2 * T.PAD)
        body = T.content(win, 1024, 700)
        try:
            for w in (self.root, win):
                w.update_idletasks()
                h = ctypes.windll.user32.GetParent(w.winfo_id()) or w.winfo_id()
                st = ctypes.windll.user32.GetWindowLongW(h, GWL_EXSTYLE)
                ctypes.windll.user32.SetWindowLongW(h, GWL_EXSTYLE, st & ~WS_EX_NOACTIVATE)
        except Exception:
            pass

        # 顶部：这份库是什么时候、按本机多少个 mod 生成的
        head = "由已安装 mod 的本地化生成 · ★ 表示被 mod 覆盖过的译名"
        try:
            man = json.load(open(os.path.join(base, "manifest.json"), encoding="utf-8"))
            head = tr("本机生成于 %s · %s 个 mod · ★ 表示被 mod 覆盖过的译名") % (
                str(man.get("built_at") or "")[5:16] or "?", man.get("mod_count") or 0)
        except Exception:
            pass
        tk.Label(body, text=head, bg=T.BG, fg=T.INK,
                 font=T.body(10), anchor="w").place(x=16, y=6, width=690)

        count = tk.Label(body, text="", bg=T.BG, fg=T.DIM,
                         font=T.body(9), anchor="e")
        count.place(x=680, y=8, width=328)

        # 搜索框 + 分类（用 StringVar 跟踪，输入法/粘贴都能触发）
        e_search = T.entry(body, 16, 34, 300, 26)
        qvar = tk.StringVar()
        e_search.configure(textvariable=qvar)
        state = {"kind": "all", "q": ""}
        KINDS = [("all", "全部"), ("heroes", "英雄"), ("monsters", "敌人"),
                 ("items", "物品"), ("trinkets", "饰品")]
        kind_btns = []

        def paint_kinds():
            for b, k in kind_btns:
                b.set_kind("sel" if state["kind"] == k else "plate")

        for i, (k, label) in enumerate(KINDS):
            b = T.button(body, 328 + i * 66, 34, 60, 26, label, None, "plate", 9)
            def pick(kk=k):
                state["kind"] = kk
                paint_kinds()
                rebuild()
            b._cmd = pick
            kind_btns.append((b, k))
        paint_kinds()

        # 左：条目列表（带滚动条）
        lb = tk.Listbox(body, bg=T.PANEL, fg=T.INK, bd=0, highlightthickness=0,
                        activestyle="none", font=T.body(10),
                        selectbackground=T.CRIMSON, selectforeground=T.INK_HI)
        lb.place(x=16, y=72, width=320, height=488)
        sb = T.scroll(body, lb.yview)
        sb.place(x=338, y=72, width=11, height=488)
        lb.configure(yscrollcommand=sb.set)

        # 右：详情 / 编辑
        lb_title = tk.Label(body, text="", bg=T.BG, fg=T.INK, font=T.head(13),
                            anchor="w", justify="left")
        lb_title.place(x=360, y=70, width=648)
        lb_tags = tk.Label(body, text="", bg=T.BG, fg=T.DIM, font=T.body(9),
                           anchor="w", justify="left", wraplength=640)
        lb_tags.place(x=360, y=98, width=648)
        tk.Label(body, text="介绍正文（可改；标题与方括号关键词用于匹配存档，只读）",
                 bg=T.BG, fg=T.DIM, font=T.body(9), anchor="w"
                 ).place(x=360, y=124, width=648)
        txt = tk.Text(body, bg=T.PANEL, fg=T.INK, bd=0, insertbackground=T.INK,
                      wrap="word", font=T.body(10), padx=10, pady=8,
                      selectbackground="#4d0407", selectforeground=T.INK_HI)
        txt.place(x=360, y=148, width=632, height=412)
        sb2 = T.scroll(body, txt.yview)
        sb2.place(x=994, y=148, width=11, height=412)
        txt.configure(yscrollcommand=sb2.set)
        txt.configure(state="disabled")

        status = tk.Label(body, text="", bg=T.BG, fg=T.DIM,
                          font=T.body(9), anchor="w")
        status.place(x=360, y=614, width=648)

        items, view = [], []
        cur = {"key": None, "idx": None, "dirty": False}

        def row_text(it):
            mark = "✎ " if it.get("edited") else ""
            t = it["title"]
            if len(t) > 18:
                t = t[:18] + "…"
            tag = it["tags"][0] if it["tags"] else ""
            return "%s%s  [%s]" % (mark, t, tag)

        def rebuild(keep=None):
            q = state["q"].strip().lower()
            kind = state["kind"]
            view.clear()
            for i, it in enumerate(items):
                if kind != "all" and it["kind"] != kind:
                    continue
                if q and q not in (it["title"] + " " + " ".join(it["tags"]) + " "
                                   + it["body"]).lower():
                    continue
                view.append(i)
                if len(view) >= 1200:
                    break
            lb.delete(0, "end")
            for i in view:
                lb.insert("end", row_text(items[i]))
            n_all = len(items)
            if len(view) >= 1200:
                count.config(text=tr("共 %d 条 · 仅显示前 1200 条") % n_all)
            else:
                count.config(text=tr("共 %d 条 · 当前 %d 条") % (n_all, len(view)))
            if keep:
                for row, i in enumerate(view):
                    if items[i]["key"] == keep:
                        lb.selection_clear(0, "end")
                        lb.selection_set(row)
                        lb.see(row)
                        break
            elif view:
                # 搜索/换分类之后自动选中第一条，右边立刻跟着换
                lb.selection_clear(0, "end")
                lb.selection_set(0)
                lb.see(0)
                show_item(items[view[0]], quiet=True)
            else:
                lb_title.config(text="")
                lb_tags.config(text="")
                txt.configure(state="normal")
                txt.delete("1.0", "end")
                txt.configure(state="disabled")

        def current():
            sel = lb.curselection()
            if not sel or sel[0] >= len(view):
                return None
            return items[view[sel[0]]]

        def show_item(it, quiet=False):
            cur["key"] = it["key"]
            cur["dirty"] = False
            lb_title.config(text=it["title"])
            lb_tags.config(text=tr("关键词（只读）：%s") % "、".join(it["tags"]))
            txt.configure(state="normal")
            txt.delete("1.0", "end")
            txt.insert("1.0", it["body"])
            txt.configure(state="disabled")
            if not quiet:
                status.config(text=tr("已编辑过 · 点「还原」可回到自动生成")
                              if it.get("edited") else "", fg=T.DIM)

        def save_current(silent=False, it=None):
            it = it or current()
            if it is None or KB is None:
                return
            body_txt = txt.get("1.0", "end-1c").strip()
            try:
                if body_txt:
                    KB.save_override(base, it["key"], body_txt, it["title"])
                    it["body"] = body_txt
                    it["edited"] = True
                    msg = tr("已保存到覆盖层 · 重建知识库也不会丢")
                    # 只刷新这一行的行首标记，不重建列表（否则会丢掉刚点的选中项）
                    for row, i in enumerate(view):
                        if items[i]["key"] == it["key"]:
                            lb.delete(row)
                            lb.insert(row, row_text(items[i]))
                            lb.selection_set(row)     # delete 会清掉选中，补回来
                            break
                    cur["dirty"] = False
                else:
                    KB.drop_override(base, it["key"])
                    msg = tr("正文为空 → 已恢复自动生成")
                    reload_all(keep=it["key"], quiet=True)
                if not silent:
                    status.config(text=msg, fg=T.GOLD)
            except Exception as e:
                status.config(text=tr("保存失败：%s") % e, fg="#d4232a")

        def restore_current():
            it = current()
            if it is None or KB is None:
                return
            try:
                KB.drop_override(base, it["key"])
                reload_all(keep=it["key"], quiet=True)
                status.config(text=tr("已还原为自动生成的内容"), fg=T.INK)
            except Exception as e:
                status.config(text=tr("还原失败：%s") % e, fg="#d4232a")

        def reload_all(keep=None, quiet=False):
            nonlocal items
            items = KB.load_items(base) if KB else []
            rebuild(keep=keep)
            if keep:
                for row, i in enumerate(view):
                    if items[i]["key"] == keep:
                        lb.selection_clear(0, "end")
                        lb.selection_set(row)
                        show_item(items[i], quiet=quiet)
                        break

        def on_select(_evt=None):
            it = current()
            if it is None:
                return
            # 切走前先把上一条存了。不看 dirty 标记，直接比文本 ——
            # 输入法上屏、粘贴、鼠标删除都不一定触发 KeyRelease。
            prev = next((x for x in items if x["key"] == cur["key"]), None) \
                if cur["key"] else None
            if prev is not None and prev["key"] != it["key"]:
                now = txt.get("1.0", "end-1c").strip()
                if now != (prev["body"] or "").strip():
                    save_current(silent=True, it=prev)
            show_item(it)

        def on_edit(_evt=None):
            cur["dirty"] = True

        def on_search(*_a):
            job = getattr(on_search, "job", None)
            if job:
                try:
                    win.after_cancel(job)
                except Exception:
                    pass
            def fire():
                state["q"] = qvar.get()
                rebuild()
            on_search.job = win.after(180, fire)

        lb.bind("<<ListboxSelect>>", on_select)
        txt.bind("<KeyRelease>", on_edit)
        qvar.trace_add("write", on_search)

        T.button(body, 360, 574, 120, 30, "保存", lambda: save_current(), "red", 10)
        T.button(body, 488, 574, 140, 30, "还原为自动生成", restore_current, "plate", 9)
        T.button(body, 636, 574, 116, 30, "打开文件夹",
                 lambda: os.startfile(base), "plate", 9)
        T.button(body, 888, 574, 100, 30, "关闭", win.destroy, "ghost", 10)

        win.bind("<Escape>", lambda e: win.destroy())
        win.bind("<Control-s>", lambda e: save_current())

        if KB is None:
            status.config(text=tr("knowledge 模块不可用，只能查看"), fg="#d4232a")
        reload_all()
        if view:
            lb.selection_set(0)
            show_item(items[view[0]])
        else:
            txt.configure(state="normal")
            txt.insert("1.0", tr("还没有生成知识库。首次启动会自动按你启用的 mod 建一次。"))
            txt.configure(state="disabled")

        win.lift()
        win.focus_force()

    # ---------------- 提示词编辑
    # ---------------- 配置读写（各编辑器共用）
    def user_cfg_path(self):
        return os.path.join(DATA, "config.json")

    def load_user_cfg(self):
        p = self.user_cfg_path()
        try:
            raw = open(p, encoding="utf-8").read()
        except Exception:
            return {}                         # 没文件 → 空配置，正常
        try:
            return json.loads(raw)
        except Exception:
            pass
        try:
            raw2 = open(p, encoding="gbk", errors="replace").read()
            return json.loads(raw2)           # 用户用记事本存成 GBK
        except Exception:
            pass
        try:
            return json.loads(re.sub(r",\s*([}\]])", r"\1", raw))   # 尾逗号
        except Exception:
            return {"__broken__": True}       # 真坏了：标记一下，两个编辑器顶部亮红灯

    def save_user_cfg(self, cfg):
        p = self.user_cfg_path()
        # 先备份：万一玩家的 config 里有我们没显示的键，不能让一次保存把它们抹掉
        if os.path.exists(p):
            try:
                import shutil
                shutil.copyfile(p, p + ".bak")
            except Exception:
                pass
        json.dump(cfg, open(p, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
        # 读回验证：写坏了要立刻让用户知道，不能装没事
        back = json.load(open(p, encoding="utf-8"))
        for k in ("system_prompt", "world_bible", "api_key", "model"):
            if k in cfg and cfg[k] != back.get(k):
                raise IOError(tr("写盘校验失败：%s 没写进去") % k)
        return True

    def update_user_cfg(self, changes):
        """只改传进来的键。

        每次都重新读盘再写：窗口打开时拿的是一份副本，整份写回会把别的窗口
        （或热重载期间）刚写进去的内容冲掉。
        """
        cur = self.load_user_cfg()
        if not isinstance(cur, dict):
            cur = {}
        cur.pop("__broken__", None)
        cur.update(changes)
        self.save_user_cfg(cur)
        return cur

    def make_editable(self, win):
        """Tk 的子窗不继承主窗的 WS_EX_NOACTIVATE（已实测），所以不用碰窗口标志，
        只需要把它提到前面并给焦点。主窗保持不可激活 —— 不能抢游戏的焦点。"""
        try:
            win.update_idletasks()
            win.lift()
            win.focus_force()
        except Exception:
            pass

    # ---------------- 知识库状态
    def pack_summary(self):
        """(manifest 内容, 人话原因, 是否需要重建)。读不到也不抛异常。"""
        mf = os.path.join(DATA, "world", "default", "manifest.json")
        info = None
        if os.path.exists(mf):
            try:
                info = json.load(open(mf, encoding="utf-8"))
            except Exception:
                info = None
        try:
            import launch
            stale, why = launch.pack_status()
        except Exception as e:
            return info, tr("无法检查：%s") % e, True
        return info, why, bool(stale)

    def rebuild_pack(self):
        """重建映射库（会扫当前启用的 mod）。返回 (成功, 提示文字)。"""
        old = os.getcwd()
        try:
            os.chdir(HERE)
            import launch
            rc = launch.build_pack()
        except Exception as e:
            return False, tr("重建失败：%s") % e
        finally:
            try:
                os.chdir(old)
            except Exception:
                pass
        if rc != 0:
            return False, tr("重建失败（代码 %s），继续用旧库。详见 out/dd_scribe.log") % rc
        info, why, stale = self.pack_summary()
        if stale:
            return False, tr("重建完成但状态仍不对：%s") % why
        return True, tr("%s 个 mod · 建于 %s\n%s") % (info.get("mod_count", 0),
                                                info.get("built_at", "?"), why)

    # ---------------- 设置台（F8）
    def open_prompt_editor(self):
        if self._reuse("setup"):
            return
        if not self.cfg.get("hint_seen"):
            self.cfg["hint_seen"] = True
            self.apply_hint()
            self.save_geometry()
        ui = T.ui()
        cfg = self.load_user_cfg()
        win = tk.Toplevel(self.root)
        win.title(tr("%s · 设置") % APP_TITLE)
        self._own("setup", win)
        if cfg.get("__broken__"):
            tk.Label(body, text="⚠ config.json 解析失败（可能手改坏了），"
                     "现在按空配置显示；保存时会先备份到 config.json.bak",
                     bg="#2a0a0e", fg="#ebebd5", font=("Microsoft YaHei", 9)
                     ).place(x=16, y=14)
        win.attributes("-topmost", True)
        win.geometry("%dx%d+%d+%d" % (900 + 2 * T.PAD, 624 + 2 * T.PAD, max(10, self.cfg["x"] + 40),
                                        max(10, self.cfg["y"] + 40)))
        win.configure(bg=ui["bg"])
        T.shell(win, tr("%s · 设置") % APP_TITLE, win.destroy, 900 + 2 * T.PAD, 624 + 2 * T.PAD)
        body = T.content(win, 900, 590)
        self.make_editable(win)
        def cap(x, y, t, fg=None, size=10):
            tk.Label(body, text=t, bg=ui["bg"], fg=fg or ui["ink"],
                     font=T.body(size)).place(x=x, y=y)

        def sect(x, y, t):
            T.header(body, x, y, t, width=886 - x)

        # ---- 开关按钮（开着=深红金边小牌，关着=暗灰，跟游戏里的 toggle 一个意思）
        def toggle(x, y, text, key, default=True, tip=""):
            on = bool(cfg.get(key, default))
            state = [on]
            b = T.button(body, x, y, 66, 26, "● 开" if on else "○ 关", None,
                         "red" if on else "plate", 9)

            def flip():
                state[0] = not state[0]
                b._text = "● 开" if state[0] else "○ 关"
                b.set_kind("red" if state[0] else "plate")

            b._cmd = flip
            tk.Label(body, text=text, bg=ui["bg"], fg=ui["ink"],
                     font=T.body(10)).place(x=x + 78, y=y + 3)
            if tip:
                tk.Label(body, text=tip, bg=ui["bg"], fg=ui["dim"],
                         font=T.body(8), anchor="w", justify="left",
                         wraplength=300).place(x=x + 78, y=y + 25, width=300)
            return state

        # ---- 分段按钮：选中的那格金线更亮、字变金
        def seg(x, y, label, key, opts, default, w=78):
            tk.Label(body, text=label, bg=ui["bg"], fg=ui["ink"],
                     font=T.body(10)).place(x=x - 172, y=y + 5)
            state = [cfg.get(key, default)]
            if not any(v == state[0] for _, v in opts):
                state[0] = default
            btns = []

            def paint():
                for b, v in btns:
                    b.set_kind("sel" if v == state[0] else "plate")

            # 按文字实际宽度排：英文比中文长，固定 78px 会被截断
            try:
                fnt = tkfont.Font(font=T.body(9))
                widths = [max(w, fnt.measure(lab) + 20) for lab, _ in opts]
            except Exception:
                widths = [w] * len(opts)
            cx = x
            for i, (lab, val) in enumerate(opts):
                b = T.button(body, cx, y, widths[i], 28, lab, None, "plate", 9)
                b._cmd = (lambda v=val: (state.__setitem__(0, v), paint()))
                btns.append((b, val))
                cx += widths[i] + 4
            paint()
            return state

        # ---- 左栏：开关与节奏
        sect(24, 16, "① 开关")
        st_know = toggle(24, 44, "相关背景（队伍档案）", "knowledge_on", True,
                         "关掉后只发事实卡")
        st_tri = toggle(24, 92, "饰品名", "trinket_effects_on", True,
                        "只给名字；+15精准这类数值不发（会写成战报）")
        st_wb = toggle(24, 140, "世界观补充", "world_bible_on", True,
                       "内容点下面「世界观」写；这里只开关")
        st_curio = toggle(24, 188, "奇物互动", "curio_on", True,
                          "书堆、祭坛这类东西被翻动时单独出一段")

        sect(24, 236, "② 节奏")
        st_sal = seg(196, 260, "旁白密度", "salience",
                     [("沉默", 4.5), ("适中", 2.5), ("密集", 0.0)], 2.5)
        st_win = seg(196, 308, "回看几回合", "window",
                     [("1 回合", 1), ("2 回合", 2), ("3 回合", 3)], 2)
        st_emit = seg(196, 356, "每隔几回合", "emit_every",
                      [("每回合", 1), ("每 2 回合", 2), ("每 3 回合", 3)], 1)

        # ---- 右栏：覆盖窗外观（字数/温度不再暴露，走 config.json 的默认值）
        sect(486, 16, "③ 覆盖窗")
        tk.Label(body, text="底板不透明度", bg=ui["bg"], fg=ui["ink"],
                 font=T.body(10)).place(x=486, y=52)
        v_bg = T.slider(body, 590, 44, 300, 30,
                        float(self.cfg.get("bg_alpha", 0.42)))
        tk.Label(body, text="文字不透明度", bg=ui["bg"], fg=ui["ink"],
                 font=T.body(10)).place(x=486, y=112)
        v_txt = T.slider(body, 590, 104, 300, 30,
                         float(self.cfg.get("text_alpha", 0.95)))
        # 字号：拖动即时生效（重排一遍再重画），点「保存并生效」才写进 overlay.json
        tk.Label(body, text="输出字体大小", bg=ui["bg"], fg=ui["ink"],
                 font=T.body(10)).place(x=486, y=172)
        v_size = T.slider(body, 590, 164, 300, 30,
                          int(self.cfg.get("size", 17)),
                          command=self.set_font_size, fmt="%.0f", lo=10, hi=34)

        # 置顶：游戏切全屏、别的置顶程序闪一下，都会把旁白窗的置顶顶掉；
        # 打开这个开关就每 2 秒重新压一次（立即生效，不用等「保存并生效」）
        v_top = [bool(self.cfg.get("force_topmost", True))]
        top_btn = T.button(body, 486, 210, 66, 26,
                           "● 开" if v_top[0] else "○ 关", None,
                           "red" if v_top[0] else "plate", 9)

        def flip_top():
            v_top[0] = not v_top[0]
            top_btn._text = "● 开" if v_top[0] else "○ 关"
            top_btn.set_kind("red" if v_top[0] else "plate")
            self.set_force_topmost(v_top[0])

        top_btn._cmd = flip_top
        tk.Label(body, text="始终置顶", bg=ui["bg"], fg=ui["ink"],
                 font=T.body(10)).place(x=562, y=213)
        tk.Label(body, text="每 2 秒重压一次；游戏请用无边框/窗口化",
                 bg=ui["bg"], fg=ui["dim"], font=T.body(8), anchor="w",
                 justify="left", wraplength=320
                 ).place(x=562, y=234, width=320)

        # ---- 接口
        sect(24, 396, "⑤ 接口")
        def entry(x, y, w, val, show=None):
            return T.entry(body, x, y, w, 26, val or "", show=show)

        cap(24, 428, "base_url", ui["dim"], 9)
        e_url = entry(96, 424, 300, cfg.get("base_url", "https://api.deepseek.com"))
        cap(410, 428, "model", ui["dim"], 9)
        e_model = entry(458, 424, 150, cfg.get("model", "deepseek-chat"))
        cap(24, 464, "api_key", ui["dim"], 9)
        # 密钥按星号显示：截图发出去也不会把 key 带出去
        e_key = entry(96, 460, 512, cfg.get("api_key", ""), show="*")

        status = tk.Label(body, text="改完点「保存并生效」，1–2 秒后主程序自动重载",
                          bg=ui["bg"], fg=ui["dim"], font=("Microsoft YaHei", 9))
        status.place(x=24, y=500)

        def test_api():
            url = (e_url.get().strip().rstrip("/") + "/chat/completions")
            key = e_key.get().strip()
            model = e_model.get().strip()
            if not key:
                status.config(text="没填 api_key", fg="#d4232a")
                return
            status.config(text=tr("正在测试 %s …") % model, fg=ui["dim"])

            def run():
                body = json.dumps({"model": model,
                                   "messages": [{"role": "user", "content": "回复：1"}],
                                   "max_tokens": 16, "stream": False}).encode()
                req = urllib.request.Request(url, data=body, headers={
                    "Content-Type": "application/json",
                    "Authorization": "Bearer " + key})
                try:
                    t0 = time.time()
                    with urllib.request.urlopen(req, timeout=40) as r:
                        d = json.loads(r.read().decode("utf-8"))
                    dt = time.time() - t0
                    msg = (d["choices"][0]["message"].get("content") or "").strip()
                    if msg:
                        out = (tr("连接正常 · %.1f 秒 · 返回「%s」") % (dt, msg[:12]), "#a9b58f")
                    else:
                        out = ("接口通了但正文为空——多半是推理型模型（如 *-reasoner/flash），"
                               "换成 deepseek-chat 之类", "#cfc9ae")
                except urllib.error.HTTPError as e:
                    out = ("HTTP %s：%s" % (e.code, e.read().decode("utf-8", "replace")[:80]),
                           "#d4232a")
                except Exception as e:
                    out = (tr("失败：%s") % str(e)[:80], "#d4232a")
                win.after(0, lambda: status.config(text=out[0], fg=out[1]))

            threading.Thread(target=run, daemon=True).start()

        def save():
            # 以磁盘上的最新内容为底：别的编辑器刚写的东西不能被这个窗口的陈旧副本抹掉
            disk = self.load_user_cfg() or {}
            merged = dict(disk)
            for k, v in cfg.items():          # 只补上磁盘没有的键（例如 _说明）
                if k not in merged and not k.startswith("_"):
                    merged[k] = v
            merged.pop("__broken__", None)
            merged["knowledge_on"] = bool(st_know[0])
            merged["trinket_effects_on"] = bool(st_tri[0])
            merged["world_bible_on"] = bool(st_wb[0])
            merged["curio_on"] = bool(st_curio[0])
            merged["salience"] = float(st_sal[0])
            merged["window"] = int(st_win[0])
            merged["emit_every"] = int(st_emit[0])
            merged["base_url"] = e_url.get().strip() or "https://api.deepseek.com"
            merged["api_key"] = e_key.get().strip()
            merged["model"] = e_model.get().strip() or "deepseek-chat"
            merged.setdefault("system_prompt", "")
            try:
                self.save_user_cfg(merged)
            except Exception as e:
                status.config(text=tr("保存 config 失败：%s") % e, fg="#d4232a")
                return
            # 覆盖窗透明度 / 字号：立即生效并写回 overlay.json
            try:
                self.cfg["bg_alpha"] = round(float(v_bg.get()), 2)
                self.cfg["text_alpha"] = round(float(v_txt.get()), 2)
                self.set_font_size(int(round(float(v_size.get()))))
                self.root.attributes("-alpha", self.cfg["bg_alpha"])
                self.txt_win.attributes("-alpha", self.cfg["text_alpha"])
                # 改完 alpha 必须补回扩展样式，否则穿透/不抢焦点会被 Tk 冲掉
                self._restyle_layers()
                self.save_geometry()
            except Exception:
                pass
            status.config(text="已保存 · 主程序会在 1–2 秒内重载", fg="#a9b58f")

        def reset():
            for k, v in (("knowledge_on", True), ("trinket_effects_on", True),
                         ("world_bible_on", True), ("curio_on", True),
                         ("salience", 2.5), ("window", 2),
                         ("emit_every", 1), ("max_tokens", 1200), ("temperature", 0.9)):
                cfg[k] = v
            try:
                self.save_user_cfg(cfg)
            except Exception:
                pass
            win.destroy()
            self.open_prompt_editor()

        def btn(x, y, w, t, cmd, primary=False):
            # 主操作 = 深红填充金边（游戏里的确认键），其余是近黑金线底板
            T.button(body, x, y, w, 30, t, cmd, "red" if primary else "plate", 10)

        # ---- 知识库（映射库）：状态 + 重建
        # 这份库决定「游戏里的东西叫什么名字」：它必须跟当前启用的 mod 一致，
        # 否则会出现物品名对不上、人物名不对、甚至读到别人机器上的译名。
        sect(486, 268, "④ 知识库")
        kb_line = tk.Label(body, text="", bg=ui["bg"], fg=ui["dim"],
                           font=("Microsoft YaHei", 9), justify="left")
        kb_line.place(x=486, y=300)

        def kb_refresh():
            info, why, stale = self.pack_summary()
            if info:
                kb_line.config(
                    text=tr("%d 个 mod · 建于 %s\n%s")
                         % (info.get("mod_count", 0), info.get("built_at", "?"),
                            (tr("需要重建：") + why) if stale else why),
                    fg="#d4232a" if stale else ui["dim"])
            else:
                kb_line.config(text=why or "库尚未生成，点右边重建", fg="#d4232a")

        def kb_rebuild():
            kb_line.config(text="正在扫描已启用的 mod，重建中……\n"
                                "这一步可能要几十秒，期间游戏里名字会暂时读取旧库",
                           fg=ui["dim"])
            win.update_idletasks()

            def work():
                ok, msg = self.rebuild_pack()
                try:
                    self.root.after(0, lambda: kb_line.config(
                        text=msg, fg=ui["dim"] if ok else "#d4232a"))
                except Exception:
                    pass

            threading.Thread(target=work, daemon=True).start()

        btn(700, 352, 116, "重建知识库", kb_rebuild)
        btn(586, 352, 104, "路径…", self.open_paths)
        kb_refresh()

        # 底部一行八颗，24 起到 836 收，间距统一 8
        btn(24, 534, 124, "保存并生效", save, primary=True)
        btn(156, 534, 92, "恢复默认", reset)
        btn(256, 534, 96, "提示词", self.open_prompt_suite)
        btn(360, 534, 92, "世界知识", self.open_knowledge)
        btn(460, 534, 96, "调试记录", self.open_debug)
        btn(564, 534, 92, "导出日志", self.export_logs)
        btn(664, 534, 92, "编辑窗口", self.enter_edit_mode)
        btn(764, 534, 72, "关闭", win.destroy)
        btn(700, 422, 96, "测试连接", test_api)

        tk.Label(body, text="%s（build %s）" % (APP_TITLE, BUILD or "dev"),
                 bg=ui["bg"], fg=ui["dim"], font=T.body(8)).place(x=24, y=568)

        win.lift()
        win.focus_force()
        win.bind("<Escape>", lambda e: win.destroy())

    # ---------------- 调试窗口：最近几次 prompt
    def open_debug(self):
        if self._reuse("debug"):
            return
        ui = T.ui()
        path = os.path.join(OUT, "debug.jsonl")

        entries = read_jsonl_tail(path, 40)
        entries.reverse()                      # 最新在最上面

        win = tk.Toplevel(self.root)
        win.title(tr("DarkestTalk · 调试（最近 %d 次）") % len(entries))
        win.attributes("-topmost", True)
        sw, sh = win.winfo_screenwidth(), win.winfo_screenheight()
        ww = min(1180, sw - 80)
        hh = min(920, sh - 100)
        wx = max(10, min(self.cfg["x"] - 60, sw - ww - 20))
        wy = max(10, min(self.cfg["y"] - 40, sh - hh - 50))
        win.geometry("%dx%d+%d+%d" % (ww + 2 * T.PAD, hh + T.Shell.BAR_H + 2 * T.PAD, wx, wy))
        win.minsize(720, 480)
        win.resizable(True, True)
        win.configure(bg=ui["bg"])
        sh = T.shell(win, "DarkestTalk · 调试记录", win.destroy,
                     ww + 2 * T.PAD, hh + T.Shell.BAR_H + 2 * T.PAD)
        self._own("debug", win)
        self.make_editable(win)
        body = T.content(win, ww, hh)

        bar = tk.Frame(body, bg=ui["bg"])
        bar.pack(fill="x", padx=14, pady=(10, 4))
        tk.Label(bar, text="折叠=只看输出；「展开」看完整内容，框内可滚动、可选中复制、可右键复制全部",
                 bg=ui["bg"], fg=ui["dim"], font=T.body(9)).pack(side="left")

        def small(text, cmd, kind="plate"):
            b = T.Btn(bar, text, cmd, 96, 26, kind, 9)
            b.configure(width=96, height=26)
            b.pack(side="right", padx=(6, 0))
            return b

        small("关闭", lambda: close_debug())
        small("打开文件夹", lambda: os.startfile(OUT))
        def open_runlog():
            """打开这一次的日志。

            每次启动都会把上一份挤成 .prev，所以 dd_scribe.log 就是当前的；
            本次还没写出内容时退回 .prev，避免打开一个空文件让人以为没日志。
            """
            for name in ("dd_scribe.log", "dd_scribe.log.prev"):
                p = os.path.join(OUT, name)
                try:
                    if os.path.exists(p) and os.path.getsize(p) > 0:
                        os.startfile(p)
                        return
                except Exception:
                    pass
            os.startfile(OUT)
        small("运行日志", open_runlog)

        def reload_list():
            win.destroy()
            self.open_debug()
        small("刷新", reload_list)

        def toggle_max():
            cur = win.geometry()
            full = "%dx%d+%d+%d" % (sw - 20, sh - 80, 10, 30)
            if cur.split("+")[0] == "%dx%d" % (sw - 20, sh - 80):
                win.geometry("%dx%d+%d+%d" % (ww + 2 * T.PAD,
                                             hh + T.Shell.BAR_H + 2 * T.PAD, wx, wy))
            else:
                win.geometry(full)
        small("最大化", toggle_max)

        # 滚动区
        outer = tk.Frame(body, bg=ui["bg"])
        outer.pack(fill="both", expand=True, padx=(14, 6), pady=(0, 12))
        canvas = tk.Canvas(outer, bg=ui["bg"], highlightthickness=0, bd=0)
        sb = T.scroll(outer, canvas.yview)
        canvas.configure(yscrollcommand=sb.set)
        canvas.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")
        inner = tk.Frame(canvas, bg=ui["bg"])
        wid = canvas.create_window((0, 0), window=inner, anchor="nw")
        inner.bind("<Configure>",
                   lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
        # 让内层跟着画布宽度走，不然记录卡片只占左边一小条
        canvas.bind("<Configure>",
                    lambda e: canvas.itemconfigure(wid, width=e.width))

        def wheel(e):
            try:
                canvas.yview_scroll(int(-e.delta / 120), "units")
            except Exception:
                pass
        canvas.bind_all("<MouseWheel>", wheel)

        def close_debug():
            try:
                canvas.unbind_all("<MouseWheel>")   # 全局绑定要收掉
            except Exception:
                pass
            win.destroy()
        win.protocol("WM_DELETE_WINDOW", close_debug)
        try:
            sh.close_btn._cmd = close_debug      # 自绘标题栏的 × 也要走同一套收尾
        except Exception:
            pass

        if not entries:
            tk.Label(inner, text="记录会出现在这里：跑一段旁白，再按 F8 →「调试记录」刷新。",
                     bg=ui["bg"], fg=ui["dim"], font=T.body(10)
                     ).pack(pady=40, padx=20, anchor="w")

        kind_cn = {"combat_round": tr("战斗回合"), "map_area": tr("地图"),
                   "camp": tr("扎营"), "settlement": tr("关卡结算"),
                   "town": tr("回到城镇"), "curio": tr("奇物互动")}

        # 折行宽度估算一次就够：正文 10 号雅黑，卡片内宽约等于窗口宽减两侧留白
        try:
            _fnt = tkfont.Font(font=T.body(10))
            _per = max(14, max(200, ww - 110) // max(6, _fnt.measure("字")))
        except Exception:
            _per = 46

        for i, e in enumerate(entries):
            out = (e.get("output") or "").strip()
            meta = "%s · %s" % (e.get("t", ""),
                                kind_cn.get(e.get("kind", ""), e.get("kind", "")))
            edge = tk.Frame(inner, bg=T.GOLD_DK)
            edge.pack(fill="x", pady=5, padx=2)
            card = tk.Frame(edge, bg=ui["card"])
            card.pack(fill="both", expand=True, padx=1, pady=1)

            head = tk.Frame(card, bg=ui["card"])
            head.pack(fill="x", padx=10, pady=(8, 2))
            tk.Label(head, text=meta, bg=ui["card"], fg=ui["line"],
                     font=T.body(9, True)).pack(side="left")
            tk.Label(head, text="  %s" % e.get("model", ""), bg=ui["card"],
                     fg=ui["dim"], font=T.body(8)).pack(side="left")

            state = {"open": False}
            # 文本 + 自己的滚动条：内容比框长时能看全，也能整段选中复制
            box = tk.Frame(card, bg=ui["card"])
            body = tk.Text(box, bg=ui["card"], fg=ui["ink"], bd=0, wrap="word",
                           font=T.body(10), height=4,
                           padx=10, pady=6, insertbackground=ui["ink"],
                           highlightthickness=0, spacing1=2, spacing3=3)
            vsb = T.scroll(box, body.yview)
            body.configure(yscrollcommand=vsb.set)
            body.pack(side="left", fill="both", expand=True)

            def body_wheel(ev, body=body):
                body.yview_scroll(int(-ev.delta / 120), "units")
                return "break"          # 别让外层列表跟着滚
            body.bind("<MouseWheel>", body_wheel)

            def copy_text(which="all", body=body, e=e, out=out):
                if which == "sel":
                    try:
                        txt = body.get("sel.first", "sel.last")
                    except Exception:
                        return
                else:
                    txt = ("【系统提示词】\n" + (e.get("system") or "")
                           + "\n\n【输入】\n" + (e.get("user") or "")
                           + "\n\n【输出】\n" + out)
                win.clipboard_clear()
                win.clipboard_append(txt)

            menu = tk.Menu(win, tearoff=0, bg=ui["panel"], fg=ui["ink"],
                           activebackground=ui["line"], activeforeground=T.BG,
                           bd=0, font=T.body(9))
            menu.add_command(label="复制选中", command=lambda: copy_text("sel"))
            menu.add_command(label="复制这一条的全文", command=lambda: copy_text("all"))
            body.bind("<Button-3>", lambda ev, m=menu: m.tk_popup(ev.x_root, ev.y_root))

            def fill(body=body, e=e, out=out, state=state, vsb=vsb):
                body.configure(state="normal")
                body.delete("1.0", "end")
                if state["open"]:
                    body.insert("end", "【系统提示词】\n" + (e.get("system") or ""))
                    body.insert("end", "\n\n【输入】\n" + (e.get("user") or ""))
                    body.insert("end", "\n\n【输出】\n" + out)
                    # 展开时让框长到内容的高度（按实际换行后的显示行数算），
                    # 这样外层列表滚到底就能看完整段，不必在两个滚动条之间来回找。
                    body.configure(height=18)
                    body.update_idletasks()
                    n = None
                    try:
                        got = body.count("1.0", "end", "displaylines")
                        n = got[0] if got else None
                    except Exception:
                        n = None
                    if not n:
                        n = (len(e.get("system") or "") + len(e.get("user") or "")
                             + len(out)) // 52
                    body.configure(height=max(10, min(int(n) + 1, 120)))
                    vsb.pack(side="right", fill="y")
                else:
                    body.insert("end", out or "（空）")
                    # 这里**不能** update_idletasks：每张卡都冲刷一次整窗布局，
                    # 40 张卡就是 O(n²)，实测点开要卡近 2 秒。
                    # 折行宽度按窗口宽度估一个就够（差一两行无所谓）。
                    n = len(out) // _per + out.count("\n") + 1
                    body.configure(height=max(3, min(10, n + 1)))
                    vsb.pack_forget()
                body.configure(state="disabled")
                body.yview_moveto(0)

            btn = T.Btn(head, "展开", None, 56, 22, "plate", 8)
            btn.configure(width=56, height=22)

            def refresh_scroll():
                try:
                    inner.update_idletasks()
                    canvas.configure(scrollregion=canvas.bbox("all"))
                except Exception:
                    pass

            def toggle(body=body, btn=btn, state=state, fill=fill,
                       refresh=refresh_scroll):
                state["open"] = not state["open"]
                btn.set_text("收起" if state["open"] else "展开")
                fill()
                refresh()

            btn._cmd = toggle
            btn.pack(side="right", padx=(4, 0))
            cp = T.Btn(head, "复制", None, 56, 22, "plate", 8)
            cp.configure(width=56, height=22)

            def do_copy():
                copy_text("all")
                cp.set_text("已复制")
                win.after(1200, lambda: cp.set_text("复制"))

            cp._cmd = do_copy
            cp.pack(side="right")
            box.pack(fill="x", padx=(10, 10), pady=(0, 8))
            fill()                          # 默认全部折叠：先能一眼扫完最近几条

        # 全部卡片建完只冲刷一次布局，省掉每张卡一次的全窗重排
        try:
            win.update_idletasks()
            canvas.configure(scrollregion=canvas.bbox("all"))
        except Exception:
            pass
        win.lift()
        win.focus_force()
        win.bind("<Escape>", lambda e: win.destroy())

    # ---------------- 导出日志（给测试者反馈用）
    def export_logs(self):
        """把本次日志 + 完整的模型请求/返回 + 环境信息导出到程序目录，并打开它。"""
        try:
            import logexport
            path = logexport.export(HERE, open_after=True)
            print(tr("[日志] 已导出：%s") % path)
            return path
        except Exception as e:
            print(tr("[日志] 导出失败：%s") % e)
            try:
                from tkinter import messagebox
                messagebox.showerror("导出失败", str(e))
            except Exception:
                pass
            return None

    # ---------------- 提示词总面板（世界观 / 文风 / 角色状态）
    def open_prompt_suite(self):
        """三段提示词放在一个窗口里：世界观、文风、角色状态。

        每一段都按它所在的位置命名（system / user），因为这两层的作用不一样：
        system 是规矩，user 是这一场的数据；改错层会得到完全不同的结果。
        """
        if self._reuse("prompt"):
            return
        ui = T.ui()
        cfg = self.load_user_cfg()
        win = tk.Toplevel(self.root)
        win.title("DarkestTalk · 提示词")
        self._own("prompt", win)
        win.attributes("-topmost", True)
        sw, sh = win.winfo_screenwidth(), win.winfo_screenheight()
        ww = min(980, sw - 80)
        hh = min(880, sh - 90)
        win.geometry("%dx%d+%d+%d" % (ww + 2 * T.PAD, hh + T.Shell.BAR_H + 2 * T.PAD,
                                      max(10, min(self.cfg["x"] - 40, sw - ww - 20)),
                                      max(10, min(self.cfg["y"] + 10, sh - hh - 40))))
        win.minsize(760, 520)
        win.configure(bg=ui["bg"])
        sh_prompt = T.shell(win, "DarkestTalk · 提示词", win.destroy,
                ww + 2 * T.PAD, hh + T.Shell.BAR_H + 2 * T.PAD)
        body = T.content(win, ww, hh)
        self.make_editable(win)
        if cfg.get("__broken__"):
            tk.Label(body, text="⚠ config.json 解析失败，按空配置显示；保存时会先备份",
                     bg="#2a0a0e", fg="#ebebd5", font=("Microsoft YaHei", 9)
                     ).place(x=16, y=8)

        # 顶部说明 + 底部按钮条固定，中间是可滚动的三段
        tk.Label(body, text="三段提示词：世界观与文风进 system，角色状态进 user。改完点保存，1–2 秒内生效。",
                 bg=ui["bg"], fg=ui["ink"], font=("Microsoft YaHei", 10)
                 ).place(x=16, y=10)
        tk.Label(body, text="角色状态里 {{同伴}} / {{背景}} / {{变化}} 是数据插入点；删掉哪个，那一块就不发。",
                 bg=ui["bg"], fg=ui["dim"], font=("Microsoft YaHei", 9)
                 ).place(x=16, y=34)

        body_h = hh - 165
        canvas = tk.Canvas(body, bg=ui["bg"], bd=0, highlightthickness=0)
        canvas.place(x=0, y=58, width=ww - 12, height=body_h)
        page_sb = T.scroll(body, canvas.yview)
        page_sb.place(x=ww - 14, y=58, height=body_h)
        canvas.configure(yscrollcommand=page_sb.set)

        inner = tk.Frame(canvas, bg=ui["bg"])
        wid = canvas.create_window((0, 0), window=inner, anchor="nw")
        inner.bind("<Configure>",
                   lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.bind("<Configure>",
                    lambda e: canvas.itemconfigure(wid, width=e.width))

        # 世界观：存过什么就显示什么——包括你把它删空后的 ""。
        # 只有从来没存过（键不存在）时才铺一份骨架，方便第一次写。
        wb = (cfg.get("world_bible") or "").strip()
        if "world_bible" not in cfg:
            md = os.path.join(DATA, "world", "default", "world_bible.md")
            wb = ""
            if os.path.exists(md):
                try:
                    wb = open(md, encoding="utf-8").read().strip()
                except Exception:
                    wb = ""
            wb = wb or WORLD_TEMPLATE
        style_txt = (cfg.get("system_prompt") or "").strip() or DEFAULT_STYLE
        role_txt = (cfg.get("user_template") or "").strip() or DEFAULT_USER_TEMPLATE

        boxes = {}

        def section(key, title, hint, value, height, action=None):
            head = tk.Frame(inner, bg=ui["bg"])
            head.pack(fill="x", padx=16, pady=(14, 0))
            tk.Label(head, text=title, bg=ui["bg"], fg=T.GOLD,
                     font=T.head(11)).pack(side="left")
            if action:
                # 小按钮：把内置骨架插到光标处（不动你已经写的内容）
                ab = T.Btn(head, action[0], action[1], 92, 22, "plate", 8)
                ab.configure(width=92, height=22)
                ab.pack(side="right")
            tk.Label(inner, text=hint, bg=ui["bg"], fg=ui["dim"],
                     font=T.body(9), justify="left",
                     wraplength=ww - 70).pack(anchor="w", padx=16)
            # 金边 + 内层近黑：跟输入框、面板同一套层次
            edge = tk.Frame(inner, bg=T.GOLD_DK)
            edge.pack(fill="x", padx=16, pady=(6, 2))
            fr = tk.Frame(edge, bg=ui["panel"])
            fr.pack(fill="both", expand=True, padx=1, pady=1)
            box = tk.Text(fr, bg=ui["panel"], fg=ui["ink"], bd=0, wrap="word",
                          insertbackground=T.GOLD, font=T.body(10),
                          selectbackground="#4a3a22", selectforeground=T.INK_HI,
                          padx=10, pady=8, height=height, undo=True)
            sb = T.scroll(fr, box.yview)
            sb.pack(side="right", fill="y")
            box.pack(side="left", fill="both", expand=True)
            box.config(yscrollcommand=sb.set)
            box.insert("1.0", value)
            boxes[key] = box

        def restore(key, value, name):
            """把某一块恢复成内置（只动这一块，另外两块一个字都不碰）。"""
            box = boxes.get(key)
            if not box:
                return
            box.delete("1.0", "end")
            box.insert("1.0", value.strip())
            status.config(text=tr("已把【%s】恢复为内置文本 · 点「保存」写入") % name,
                          fg=ui["dim"])

        section("world", "【世界观-system】",
                "优先级最高。写角色来历、人物关系、哪些名字容易被误解。"
                "留空 = 只用游戏事实；模板可以随意删改。",
                wb, 9, action=("恢复内置", lambda: restore("world", WORLD_TEMPLATE, "世界观-system")))
        section("style", "【文风-system】",
                "只管「怎么写」，不管「发生了什么」。写场景、句式、语域与硬约束。"
                "留空 = 用内置。",
                style_txt, 12,
                action=("恢复内置", lambda: restore("style", DEFAULT_STYLE, "文风-system")))
        section("role", "【角色状态-user】",
                "跟着每一场的事实一起发。标题、说明文字、结尾那句都归这里；"
                "{{同伴}} 是队伍档案，{{背景}} 是状态补充，{{变化}} 是这一趟刚发生的变化。",
                role_txt, 10,
                action=("恢复内置", lambda: restore("role", DEFAULT_USER_TEMPLATE, "角色状态-user")))

        status = tk.Label(body, text="", bg=ui["bg"], fg=ui["dim"],
                          font=("Microsoft YaHei", 9))
        status.place(x=16, y=hh - 58)

        def save():
            world = boxes["world"].get("1.0", "end-1c").strip()
            style = boxes["style"].get("1.0", "end-1c").strip()
            role = boxes["role"].get("1.0", "end-1c").strip()
            changes = {
                "world_bible": world,          # 你写什么就存什么，一个字都不改
                "system_prompt": "" if style == DEFAULT_STYLE.strip() else style,
                "user_template": "" if role == DEFAULT_USER_TEMPLATE.strip() else role,
            }
            if world:
                changes["world_bible_on"] = True
            try:
                self.update_user_cfg(changes)
            except Exception as e:
                status.config(text=tr("保存失败：%s") % e, fg="#d4232a")
                return
            back = self.load_user_cfg()
            def same(key, val):
                got = back.get(key)
                if isinstance(val, bool):
                    return bool(got) == val
                return ((got or "").strip() == str(val).strip())
            if not all(same(k, v) for k, v in changes.items()):
                status.config(text="读回校验不一致（config.json 可能只读）", fg="#d4232a")
                return
            status.config(text=tr("已保存：世界观 %d 字 / 文风 %d 字 / 角色状态 %d 字 · "
                                  "1–2 秒内生效") % (len(world), len(style), len(role)),
                          fg="#a9b58f")

        def btn(x, y, w, t, cmd, primary=False):
            T.button(body, x, hh - 92, w, 30, t, cmd,
                     "red" if primary else "plate", 10)

        btn(16, hh - 92, 120, "保存", save, primary=True)
        btn(144, hh - 92, 100, "关闭", lambda: close())

        # 滚轮：指针在文本框里就滚文本框，否则滚整页
        def on_wheel(ev):
            w = win.winfo_containing(ev.x_root, ev.y_root)
            while w is not None and w is not win:
                if isinstance(w, tk.Text):
                    return
                w = w.master
            try:
                canvas.yview_scroll(int(-ev.delta / 120), "units")
            except Exception:
                pass
        win.bind_all("<MouseWheel>", on_wheel)

        def close():
            try:
                win.unbind_all("<MouseWheel>")
            except Exception:
                pass
            win.destroy()
        # 自绘标题栏的 × 也走同一套收尾（否则全局滚轮绑定会留在死窗口上）
        try:
            sh_prompt.close_btn._cmd = close
        except Exception:
            pass
        win.protocol("WM_DELETE_WINDOW", close)
        win.bind("<Escape>", lambda e: close())
        win.bind("<Control-s>", lambda e: save())

        win.lift()
        win.focus_force()
        win.after(150, lambda: boxes["world"].focus_set())

    # ---------------- 旧的两个独立编辑器（不再挂到界面；入口已合并为 open_prompt_suite）
    def open_world_editor(self):
        if self._reuse("world"):
            return
        ui = T.ui()
        cfg = self.load_user_cfg()
        win = tk.Toplevel(self.root)
        win.title("DarkestTalk · 世界观")
        self._own("world", win)
        win.attributes("-topmost", True)
        win.geometry("%dx%d+%d+%d" % (880 + 2 * T.PAD, 620 + T.Shell.BAR_H + 2 * T.PAD,
                                       max(10, self.cfg["x"] - 20),
                                       max(10, self.cfg["y"] + 20)))
        win.configure(bg=ui["bg"])
        T.shell(win, "DarkestTalk · 世界观", win.destroy, 880 + 2 * T.PAD,
                620 + T.Shell.BAR_H + 2 * T.PAD)
        body = T.content(win, 880, 620)
        self.make_editable(win)
        if cfg.get("__broken__"):
            tk.Label(body, text="⚠ config.json 解析失败，按空配置显示；保存时会先备份",
                     bg="#2a0a0e", fg="#ebebd5", font=("Microsoft YaHei", 9)
                     ).place(x=16, y=10)

        tk.Label(body,
                 text="世界观补充：优先级最高。写 mod 角色的来历、人物之间的关系、哪些名字容易被误解。",
                 bg=ui["bg"], fg=ui["ink"], font=("Microsoft YaHei", 10)
                 ).place(x=16, y=34)
        tk.Label(body,
                 text="写「事实」比写「形容词」有用；这里的名字必须和日志里出现的一致。Ctrl+S 保存。",
                 bg=ui["bg"], fg=ui["dim"], font=("Microsoft YaHei", 9)
                 ).place(x=16, y=58)

        box = tk.Text(body, bg=ui["panel"], fg=ui["ink"], bd=0, insertbackground=ui["ink"],
                      wrap="word", font=("Microsoft YaHei", 10), padx=10, pady=8)
        box.place(x=16, y=82, width=812, height=418)
        bar = T.scroll(body, box.yview)
        bar.place(x=828, y=82, height=418)
        box.config(yscrollcommand=bar.set)

        # 世界观：存过就用存的（空也算存过），没存过才铺骨架
        wb = (cfg.get("world_bible") or "").strip()
        if "world_bible" not in cfg:
            md = os.path.join(DATA, "world", "default", "world_bible.md")
            if os.path.exists(md):
                try:
                    wb = open(md, encoding="utf-8").read().strip()
                except Exception:
                    wb = ""
        # 存过就显示存的（空也照显示），没存过才给骨架
        box.insert("1.0", wb if "world_bible" in cfg else (wb or WORLD_TEMPLATE))

        status = tk.Label(body, text="", bg=ui["bg"], fg=ui["dim"],
                          font=("Microsoft YaHei", 9))
        status.place(x=16, y=580)

        def save():
            txt = box.get("1.0", "end-1c").strip()
            cfg.pop("__broken__", None)
            # 模板原样保存等于没写——存空串，避免占位符进 prompt
            cfg["world_bible"] = "" if txt == template.strip() else txt
            cfg["world_bible_on"] = True
            try:
                self.save_user_cfg(cfg)
                status.config(text="已保存（%d 字符）→ 读回验证 ✓ · 1–2 秒内生效"
                              % len(txt), fg="#a9b58f")
            except Exception as e:
                status.config(text=tr("保存失败：%s") % e, fg="#d4232a")

        def btn(x, y, w, t, cmd, primary=False):
            T.button(body, x, y, w, 30, t, cmd, "red" if primary else "plate", 10)

        btn(16, 512, 120, "保存", save, primary=True)
        btn(144, 512, 100, "关闭", win.destroy)

        win.lift()
        win.focus_force()
        win.after(150, box.focus_set)
        win.bind("<Escape>", lambda e: win.destroy())
        win.bind("<Control-s>", lambda e: save())

    # ---------------- 文风编辑器
    def open_style_editor(self):
        if self._reuse("style"):
            return
        ui = T.ui()
        cfg = self.load_user_cfg()
        win = tk.Toplevel(self.root)
        win.title("DarkestTalk · 文风")
        self._own("style", win)
        win.attributes("-topmost", True)
        win.geometry("%dx%d+%d+%d" % (880 + 2 * T.PAD, 620 + T.Shell.BAR_H + 2 * T.PAD,
                                       max(10, self.cfg["x"] - 20),
                                       max(10, self.cfg["y"] + 20)))
        win.configure(bg=ui["bg"])
        T.shell(win, "DarkestTalk · 文风", win.destroy, 880 + 2 * T.PAD,
                620 + T.Shell.BAR_H + 2 * T.PAD)
        body = T.content(win, 880, 620)
        self.make_editable(win)
        if cfg.get("__broken__"):
            tk.Label(body, text="⚠ config.json 解析失败（可能手改坏了），"
                     "现在按空配置显示；保存时会先备份到 config.json.bak",
                     bg="#2a0a0e", fg="#ebebd5", font=("Microsoft YaHei", 9)
                     ).place(x=16, y=34)

        tk.Label(body, text="你要的文风与规矩都写在这里。留空 = 使用内置。保存后 1–2 秒内生效。",
                 bg=ui["bg"], fg=ui["ink"], font=("Microsoft YaHei", 10)
                 ).place(x=16, y=10)
        tk.Label(body, text="事实清单不给你的东西，写进这里也没用；这里只管「怎么写」，不管「发生了什么」。",
                 bg=ui["bg"], fg=ui["dim"], font=("Microsoft YaHei", 9)
                 ).place(x=16, y=34)

        box = tk.Text(body, bg=ui["panel"], fg=ui["ink"], bd=0, insertbackground=ui["ink"],
                      wrap="word", font=("Microsoft YaHei", 10), padx=10, pady=8)
        box.place(x=16, y=60, width=812, height=440)
        bar = T.scroll(body, box.yview)
        bar.place(x=828, y=60, height=440)
        box.config(yscrollcommand=bar.set)
        box.insert("1.0", (cfg.get("system_prompt") or "").strip() or DEFAULT_STYLE)

        status = tk.Label(body, text="", bg=ui["bg"], fg=ui["dim"],
                          font=("Microsoft YaHei", 9))
        status.place(x=16, y=580)

        def save():
            txt = box.get("1.0", "end-1c").strip()
            # 与内置完全一致时存空串：以后内置改了，玩家这里自动跟着更新
            cfg["system_prompt"] = "" if txt == DEFAULT_STYLE.strip() else txt
            try:
                self.save_user_cfg(cfg)
                status.config(text="已保存（%d 字符）→ 读回验证 ✓ · 主程序 1–2 秒内自动重载"
                              % len(txt), fg="#a9b58f")
            except Exception as e:
                status.config(text=tr("保存失败：%s") % e, fg="#d4232a")

        def use_builtin():
            box.delete("1.0", "end")
            box.insert("1.0", DEFAULT_STYLE)
            status.config(text="已载入内置提示词，点保存生效", fg=ui["dim"])

        def load_preset(name):
            box.delete("1.0", "end")
            box.insert("1.0", PRESETS[name])
            status.config(text=tr("已载入「%s」，可以继续改，点保存生效") % name, fg=ui["dim"])

        def btn(x, y, w, t, cmd, primary=False):
            T.button(body, x, y, w, 30, t, cmd, "red" if primary else "plate", 10)

        btn(16, 512, 120, "保存", save, primary=True)
        btn(144, 512, 108, "载入内置", use_builtin)
        x = 264
        for name in list(PRESETS)[:3]:
            w = max(96, 18 * len(name))
            if x + w > 700:
                break
            btn(x, 512, w, name, lambda n=name: load_preset(n))
            x += w + 8
        btn(716, 512, 108, "关闭", win.destroy)

        win.lift()
        win.focus_force()
        win.after(150, box.focus_set)
        win.bind("<Escape>", lambda e: win.destroy())
        win.bind("<Control-s>", lambda e: save())

    # ---------------- 文本流
    def poll(self):
        self.load_cfg()
        # 手改 overlay.json 里的字号也能跟上（滑条改的中途不会走这条：值已经一致）
        try:
            if int(self.cfg.get("size", 17)) != int(self.f.cget("size")):
                self._sync_font()
                self._queue_relayout(0)
        except Exception:
            pass
        path = self.cfg["text_file"]
        if not os.path.isabs(path):
            path = os.path.join(DATA, path)
        try:
            size = os.path.getsize(path)
        except OSError:
            size = 0
        if size < self.offset:
            self.offset = 0
        if size > self.offset:
            with open(path, "rb") as f:
                f.seek(self.offset)
                chunk = f.read()
                self.offset = f.tell()
            self.queue += chunk.decode("utf-8", errors="replace")
            self.last_change = time.time()
            if self._alive(self.root) and self.root.state() == "withdrawn":
                self.root.deiconify()
                if self._alive(self.txt_win):
                    self.txt_win.deiconify()

        if self.queue and not self.typing:
            self.typing = True
            self.type_next()

        if self.cfg["fade_after_sec"] > 0 and not self.queue:
            if time.time() - self.last_change > self.cfg["fade_after_sec"]:
                try:
                    if self._alive(self.root):
                        self.root.attributes("-alpha", 0.0)
                    if self._alive(self.txt_win):
                        self.txt_win.attributes("-alpha", 0.0)
                except Exception:
                    pass
        self.root.after(160, self.poll)

    def type_next(self):
        if not self.queue:
            self.typing = False
            return
        if not self._alive(self.text):
            if not self.ensure_text_layer():
                self.typing = False
                return

        # 一次吐一小段：正常逐字，积压变多时成段推进（字仍然是一个个出来的）
        q = len(self.queue)
        if q < 60:
            n = 1
        elif q < 200:
            n = 2
        elif q < 500:
            n = 3
        else:
            n = 5
        chunk, self.queue = self.queue[:n], self.queue[n:]
        try:
            self._append_text(chunk)
        except tk.TclError:
            # 窗口在打字途中被关掉（退出、切进程）：安静收尾
            self.typing = False
            return
        delay = max(1, int(self.cfg["char_delay_ms"]))
        last = chunk[-1]
        if last in "。！？…\n":
            delay *= 9
        elif last in "，、；：":
            delay *= 4
        self.root.after(delay, self.type_next)


DEMO_TEXT = ("御影先动。刀尖在石壁上划出两道白痕，第三下才咬进骨缝。\n"
             "碎屑落下来的时候，那具骸骨官僚还保持着举臂的姿势。")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default=os.path.join(DATA, "overlay.json"))
    ap.add_argument("--demo", action="store_true")
    ap.add_argument("--duration", type=float, default=0)
    args = ap.parse_args()

    cfg_path = args.config if os.path.isabs(args.config) else os.path.join(DATA, args.config)
    ov = Overlay(cfg_path)
    if args.demo:
        ov.queue += DEMO_TEXT
    if args.duration:
        ov.root.after(int(args.duration * 1000), ov.root.destroy)
    print("[覆盖窗] DarkestTalk 已启动 · 按 F8 打开设置")
    try:
        ov.root.mainloop()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()

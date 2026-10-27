# -*- coding: utf-8 -*-
"""theme.py —— DarkestTalk 的暗黑地牢美术皮肤

配色按项目定稿的三色规范：
  底色     纯黑 RGB(0,0,0)
  参考红   RGB(176,13,18)  #b00d12 —— 主操作、选中、强调
  参考黄   RGB(235,235,213) #ebebd5 —— 正文、金线高光（象牙白，不是土黄）
  背景光晕 RGB(25,16,39)   #191027 —— 由中心向外减暗的蓝紫光，压住纯黑的死板

线条仍取自游戏素材（fe_flow / panels / shared）：
  start_button 的上下双细线、saveslot 的双层框、modal_dialog 的内嵌细框、
  scrollpip 的亮金疙瘩 —— 只是把"金"从土黄换成象牙白，避免整屏发黄。

规则：
  1. 底 = 纯黑 + 中心蓝紫光晕（径向，向外减暗）
  2. 线 = 常态暗金灰 #6d6757，高光一道象牙白 #ebebd5
  3. 主操作 / 选中 = 参考红填底 + 象牙描边
  4. 文字 = 参考黄 #ebebd5，标题衬线（Georgia），正文雅黑
"""
import tkinter as tk
from tkinter import ttk

# ---------------------------------------------------------------- 调色板
WINDOW = "#000000"       # 窗口最底层：纯黑（定稿 RGB 0,0,0）
GLOW = "#191027"         # 中心光晕：蓝紫（RGB 25,16,39）
BG = "#0c0916"           # 内容面：蓝紫黑（光晕中段的色，控件都用它，避免整屏死黑）
BG_DEEP = "#000000"      # 更沉的一档
BAR_BG = "#030304"       # 标题带
PLATE = "#050506"        # 面板
PLATE_HI = "#0b0b0e"     # 面板顶部（只比 BG 亮一点点，免得文字块出现色差缝）
PANEL = "#050507"        # 输入框/凹槽
INK = "#ebebd5"          # 正文（定稿参考黄，象牙白）
INK_HI = "#ffffff"       # 悬停高亮
DIM = "#8b887a"          # 次要说明
GOLD = "#ebebd5"         # 金线高光（象牙白）
GOLD_DK = "#6d6757"      # 暗金灰（常规分隔线）
GOLD_EDGE = "#b9b49b"    # 金边（暗一档的象牙）
EMBER = "#b00d12"        # 参考红（选中、强调、悬停亮线）
EMBER_HI = "#d4232a"
EMBER_LO = "#5e060a"
ASH = "#1a1a1e"          # 暗灰
ASH_HI = "#26262c"
CRIMSON = "#b00d12"      # 主操作 / 危险（定稿参考红）
CRIMSON_HI = "#d4232a"
CRIMSON_LO = "#4d0407"
BLOOD_TEXT = "#ebebd5"

# 镶金线（对照游戏内「战役」标题牌）：2px 细线，两端沉入黑、中央提亮到暖铜金
GOLD_LINE = "#cbb98c"        # 线中央的暖铜金（参考图 160,148,124 略提亮）
LINE_END = "#3a3225"         # 线两端 / 竖边的暗铜（渐变最末端的颜色）
GLINT = "#ebebd5"            # 参考黄（保留常量，供其它地方引用）

# 兼容旧代码：以前每个窗口都写死一个 ui dict，这里统一发一份
def ui():
    return dict(bg=BG, card=PLATE, panel=PANEL, ink=INK, dim=DIM,
                line=GOLD, off=ASH, hi=INK_HI, gold=GOLD_DK,
                crimson=CRIMSON, ash=ASH_HI)


# ---------------------------------------------------------------- 字体
def head(size=10, bold=True):
    """标题：衬线体（DD 的 Dwarven Axe 是浓重衬线，Georgia 是最接近的系统字体）。"""
    return ("Georgia", size, ("bold" if bold else "normal"))


def body(size=10, bold=False):
    return ("Microsoft YaHei", size, ("bold" if bold else "normal"))


def mono(size=10):
    return ("Consolas", size)


# ---------------------------------------------------------------- ttk 滚动条
def init_ttk(root):
    """把 ttk 滚动条也调成这套配色（原生那条白杠跟黑金面板太冲突）。"""
    try:
        st = ttk.Style(root)
        try:
            st.theme_use("clam")
        except Exception:
            pass
        st.configure("DD.Vertical.TScrollbar", gripcount=0, relief="flat",
                     background=ASH_HI, darkcolor=ASH, lightcolor=ASH_HI,
                     troughcolor=PANEL, bordercolor=BG, arrowcolor=GOLD_DK,
                     arrowsize=11, width=11)
        st.map("DD.Vertical.TScrollbar",
               background=[("active", GOLD_DK), ("pressed", GOLD)])
        st.configure("DD.Horizontal.TScrollbar", gripcount=0, relief="flat",
                     background=ASH_HI, darkcolor=ASH, lightcolor=ASH_HI,
                     troughcolor=PANEL, bordercolor=BG, arrowcolor=GOLD_DK,
                     arrowsize=11, width=11)
    except Exception:
        pass


def scroll(parent, command, orient="vertical"):
    init_ttk(parent)
    style = "DD.Vertical.TScrollbar" if orient == "vertical" else "DD.Horizontal.TScrollbar"
    return ttk.Scrollbar(parent, orient=orient, command=command, style=style)


# ---------------------------------------------------------------- 绘图小工具
def _lerp(c1, c2, t):
    a = int(c1[1:3], 16), int(c1[3:5], 16), int(c1[5:7], 16)
    b = int(c2[1:3], 16), int(c2[3:5], 16), int(c2[5:7], 16)
    return "#%02x%02x%02x" % tuple(int(a[i] + (b[i] - a[i]) * t) for i in range(3))


def vgrad(cv, x1, y1, x2, y2, top, bottom, steps=48):
    """竖直渐变：Canvas 不支持渐变，用一叠 1px 线段堆出来（只跑一次，开销可忽略）。"""
    h = max(1, y2 - y1)
    n = min(steps, h)
    for i in range(n):
        ya = y1 + h * i // n
        yb = y1 + h * (i + 1) // n
        cv.create_rectangle(x1, ya, x2, yb, outline="", fill=_lerp(top, bottom, i / max(1, n - 1)))


def line(cv, x1, y1, x2, y2, color=GOLD_DK, width=1, dash=None):
    kw = dict(fill=color, width=width)
    if dash:
        kw["dash"] = dash
    cv.create_line(x1, y1, x2, y2, **kw)


def globe(cv, cx, cy, r=9, color=GOLD_EDGE, inner=GOLD_DK):
    """地球仪图标：外圆 + 经线 + 赤道。

    这是跨文化的「语言 / 地区」通用符号 —— Windows、macOS、iOS、Steam、
    浏览器都用它，不含任何文字，不懂界面语言的玩家也能认出"这里能换语言"。
    比旗帜可靠：旗帜代表国家，不代表语言，而且同一种语言用哪面旗永远有争议。

    画法上的坑（都在 18–20px 实测过）：
      外圆 1px 会糊成"雨伞"，2px 又像"灯笼"；实心球挖线在小尺寸下像"花纹"。
      最后用「外圆亮 1px + 内线暗 1px」——轮廓靠亮度立住，刻线退到背景里，
      看上去才是一颗球，而不是一张网。
    """
    cv.create_oval(cx - r, cy - r, cx + r, cy + r, outline=color, width=1)
    cv.create_oval(cx - r * 0.40, cy - r, cx + r * 0.40, cy + r,
                   outline=inner, width=1)                          # 经线
    cv.create_line(cx - r + 1, cy, cx + r - 1, cy,
                   fill=inner, width=1)                             # 赤道


def glow(cv, w, h, steps=56):
    """纯黑底 + 由中心向外减暗的蓝紫光晕。

    Canvas 没有径向渐变，用一叠同心椭圆堆出来：先画满黑底，再从最大的椭圆
    画到最小的，越往里颜色越接近 #191027。椭圆之外的角落保持纯黑，
    正好形成"中心亮、四边沉"的晕影，比整块渐变更像 DD 的暗角。
    """
    cv.create_rectangle(0, 0, w, h, outline="", fill=WINDOW)
    cx, cy = w / 2.0, h / 2.0
    rx0, ry0 = w * 0.78, h * 0.78       # 椭圆略大于窗口，边缘仍有余辉
    for i in range(steps):
        k = 1.0 - i / float(steps - 1)   # 1 → 0.02（由外到内）
        t = (1.0 - k) ** 1.35            # 0 外 → 1 内
        cv.create_oval(cx - rx0 * k, cy - ry0 * k,
                       cx + rx0 * k, cy + ry0 * k,
                       outline="", fill=_lerp(WINDOW, GLOW, t))


def plate(cv, w, h, inset=0, grain=True):
    """在画布上画一块 DD 面板：黑底光晕 + 双层框 + 缩进金线 + 四角小钩 + 颗粒。"""
    cv.configure(bg=WINDOW)
    glow(cv, w, h)
    if grain:
        # 一层极淡的颗粒：DD 的底板是手绘墨块/石头，纯渐变会显得太"数字"。
        # 只用中性灰黑，不带黄褐——底板一发黄整套界面就显旧
        import random
        rnd = random.Random(1104)
        n = max(120, min(700, (w * h) // 900))
        for _ in range(n):
            x = rnd.randrange(inset + 2, max(inset + 3, w - inset - 2))
            y = rnd.randrange(inset + 2, max(inset + 3, h - inset - 2))
            c = rnd.choice(("#0d0d10", "#121216", "#070708", "#17171c"))
            cv.create_line(x, y, x, y, fill=c)
    # 顶部压一道暗，让上沿"沉"下去
    cv.create_rectangle(inset + 1, inset + 1, w - inset - 1, inset + 4,
                        outline="", fill="#000000")
    # 外框（暗金）+ 内框（纯黑），像 saveslot 的双线
    cv.create_rectangle(inset + 0.5, inset + 0.5, w - inset - 0.5, h - inset - 0.5,
                        outline=GOLD_DK, width=1)
    cv.create_rectangle(inset + 1.5, inset + 1.5, w - inset - 1.5, h - inset - 1.5,
                        outline="#000000", width=1)
    # 缩进 6px 的细线：上下各走一道，中段提亮到象牙白，像 menu.background 的双线
    cv.create_line(inset + 7, inset + 6, w - inset - 7, inset + 6, fill="#2a2830")
    cv.create_line(inset + 7, h - inset - 7, w - inset - 7, h - inset - 7, fill="#2a2830")
    mid1, mid2 = int(w * 0.30), int(w * 0.70)
    cv.create_line(mid1, inset + 6, mid2, inset + 6, fill=GOLD_DK)
    cv.create_line(mid1, h - inset - 7, mid2, h - inset - 7, fill=GOLD_DK)
    # 四角小钩：暗金钩 + 钩尖一点参考红
    for cx, cy, dx, dy in ((inset + 2, inset + 2, 1, 1),
                           (w - inset - 2, inset + 2, -1, 1),
                           (inset + 2, h - inset - 2, 1, -1),
                           (w - inset - 2, h - inset - 2, -1, -1)):
        cv.create_line(cx, cy, cx + 9 * dx, cy, fill=GOLD_EDGE)
        cv.create_line(cx, cy, cx, cy + 9 * dy, fill=GOLD_EDGE)
        cv.create_line(cx, cy, cx + 3 * dx, cy, fill=EMBER)
        cv.create_line(cx, cy, cx, cy + 3 * dy, fill=EMBER)


PAD = 12          # 内容面板与窗口边缘之间留给光晕的宽度


def content(win, w, h):
    """在窗口里铺一块内容面板：四周留 PAD，让底下的蓝紫光晕露出来。

    窗口尺寸要比内容大 2*PAD，调用方负责（见各窗口的 geometry）。
    名字不能叫 body —— 那会盖住上面的正文字体函数 body(size, bold)。
    """
    fr = tk.Frame(win, bg=BG, bd=0, highlightthickness=0)
    fr.place(x=PAD, y=Shell.BAR_H + PAD, width=w, height=h)
    return fr


def paint_plate(win, w, h):
    """给一个 Toplevel 铺上 DD 面板底，返回画布（自动压在最底层）。"""
    cv = tk.Canvas(win, width=w, height=h, bd=0, highlightthickness=0, bg=BG)
    cv.place(x=0, y=0, width=w, height=h)
    plate(cv, w, h)
    cv.tk.call("lower", cv._w)      # Canvas.lower 是 tag_lower，别用错
    return cv


def raise_win(w):
    """Canvas 的 .lift() 是 tag_raise，抬窗口得直接喊 Tcl 的 raise。"""
    try:
        w.tk.call("raise", w._w)
    except Exception:
        pass


def hairline(win, x, y, w, color=GOLD_DK):
    """一条横向分隔线：中间金、两边淡出（用三段线模拟）。"""
    mid = max(1, int(w * 0.22))
    tk.Frame(win, bg=_lerp(BG, color, 0.25), height=1).place(x=x, y=y, width=mid)
    tk.Frame(win, bg=color, height=1).place(x=x + mid, y=y, width=max(1, w - 2 * mid))
    tk.Frame(win, bg=_lerp(BG, color, 0.25), height=1).place(
        x=x + w - mid, y=y, width=mid)


def header(win, x, y, text, width=0, fg=GOLD):
    """小节标题：火金衬线字 + 右侧一道淡金长线 + 结尾火橘小菱形，DD 的招牌分割。"""
    lb = tk.Label(win, text=text, bg=BG, fg=fg, font=head(10))
    lb.place(x=x, y=y)
    if width:
        lb.update_idletasks()
        sx = x + max(60, lb.winfo_reqwidth()) + 10
        ex = x + width
        if ex - sx > 24:
            tk.Frame(win, bg=_lerp(BG, GOLD_DK, 0.55), height=1
                     ).place(x=sx, y=y + 9, width=ex - sx - 8)
            d = tk.Canvas(win, width=7, height=7, bd=0, highlightthickness=0, bg=BG)
            d.place(x=ex - 7, y=y + 6)
            d.create_polygon(3, 0, 6, 3, 3, 6, 0, 3, fill=EMBER, outline="")
    return lb


# ---------------------------------------------------------------- 按钮
# 恢复原本的按钮样式：近黑底 + 上下两道细线（不封左右）；
# 选中＝红调压深，主操作＝参考红填底 + 象牙描边，高亮＝参考黄填底黑字。
KINDS = {
    # kind: (顶部色, 底部色, 悬停顶部, 悬停底部, 文字, 悬停文字, 线色)
    "plate": ("#232326", "#0d0d10", "#32323a", "#15151a", INK, INK_HI, GOLD_DK),
    "sel":   ("#3a1113", "#140708", "#4d1618", "#1b090a", GOLD, INK_HI, EMBER),
    "red":   ("#b00d12", "#4d0407", "#d4232a", "#5e060a", BLOOD_TEXT, "#ffffff", GOLD_EDGE),
    "gold":  ("#ebebd5", "#b9b49b", "#ffffff", "#cfc9ae", "#0a0a08", "#0a0a08", "#ffffff"),
    "ghost": (BG, BG, "#191027", "#191027", DIM, INK, GOLD_DK),
}


class Btn(tk.Canvas):
    """DD 风格按钮：自绘，所以能做出上下金线和深红填充。

    kind="plate" 常规 / "sel" 选中 / "red" 危险·主操作 / "gold" 高亮 / "ghost" 文字键
    """

    def __init__(self, parent, text, command=None, width=90, height=28,
                 kind="plate", size=10, font=None, pad=8):
        tk.Canvas.__init__(self, parent, width=width, height=height, bd=0,
                           highlightthickness=0, bg=BG, cursor="hand2")
        self._text = text
        self._cmd = command
        self._cw, self._ch = width, height
        self._kind = kind
        self._size = size
        self._font = font
        self._pad = pad
        self._state = "idle"
        self._enabled = True
        self.bind("<Enter>", self._on_enter)
        self.bind("<Leave>", self._on_leave)
        self.bind("<ButtonPress-1>", self._on_press)
        self.bind("<ButtonRelease-1>", self._on_release)
        self.redraw()

    # --- 对外
    def set_text(self, text):
        self._text = text
        self.redraw()

    def text(self):
        return self._text

    def set_kind(self, kind):
        self._kind = kind
        self.redraw()

    def enable(self, on=True):
        self._enabled = bool(on)
        self.configure(cursor="hand2" if on else "arrow")
        self.redraw()

    # --- 交互
    def _on_enter(self, _e):
        self._state = "hover"
        self.redraw()

    def _on_leave(self, _e):
        self._state = "idle"
        self.redraw()

    def _on_press(self, _e):
        self._state = "down"
        self.redraw()

    def _on_release(self, _e):
        self._state = "hover"
        self.redraw()
        if self._enabled and self._cmd:
            self._cmd()

    # --- 镶金线的画法
    @staticmethod
    def _line_color(t, mid=None):
        """线的横向配色：t=0/1 两端暗铜，t=0.5 中央最亮（参考图那条曲线）。"""
        import math
        mid = mid or GOLD_LINE
        k = math.sin(math.pi * max(0.0, min(1.0, t))) ** 0.75
        return _lerp(LINE_END, mid, k)

    def _gold_band(self, y, x1, x2, t=2, mid=None):
        """一道 t px 高的渐变线。

        逐像素取色（而不是几十段色块），所以不会出现台阶；
        两端是暗铜、中央提亮，整条线始终在 y..y+t 之内，不会向外发胖。
        """
        n = max(2, int(round(x2 - x1)))
        for i in range(n):
            col = self._line_color((i + 0.5) / n, mid)
            x = x1 + i
            self.create_line(x, y, x + 1, y, fill=col, width=t)

    def _gold_frame(self, w, h, mid=None, t=2):
        """整圈镶金：横边渐变，竖边直接用渐变末端的暗铜色，四角由横边铺满。"""
        x0, y0, x1, y1 = 2, 2, w - 2, h - 2
        self._gold_band(y0, x0, x1, t, mid)             # 上边（铺满整宽，封住两角）
        self._gold_band(y1 - t, x0, x1, t, mid)         # 下边
        # 竖边：整条同色（渐变末端），线宽向矩形内侧扩展
        self.create_line(x0, y0, x0, y1, fill=LINE_END, width=t)
        self.create_line(x1 - t, y0, x1 - t, y1, fill=LINE_END, width=t)

    # --- 画
    def redraw(self):
        self.delete("all")
        w, h = self._cw, self._ch
        top, bot, htop, hbot, fg, hfg, gl = KINDS.get(self._kind, KINDS["plate"])
        if not self._enabled:
            top, bot, fg, gl = "#0f0d0b", "#0b0a09", "#4a443a", "#332c22"
        if self._state == "hover":
            top, bot, fg = htop, hbot, hfg
        elif self._state == "down":
            top, bot = bot, top
            fg = hfg

        vgrad(self, 0, 0, w, h, top, bot)
        if self._kind in ("red", "gold") and self._enabled:
            # 开着 / 主操作的红色按钮：整圈镶金（全包边框）
            self._gold_frame(w, h, GOLD_LINE if self._kind == "red" else GOLD_EDGE, 2)
        elif self._kind != "ghost":
            # 上下两道镶金细线：中间亮、两端沉进黑底
            mid = GOLD_LINE if self._enabled else "#4a4436"
            self._gold_band(4, self._pad, w - self._pad, 2, mid)
            self._gold_band(h - 6, self._pad, w - self._pad, 2, mid)

        f = self._font or body(self._size, self._kind in ("red", "gold", "sel"))
        self.create_text(w // 2, h // 2, text=self._text, fill=fg, font=f)


def button(win, x, y, w, h, text, cmd, kind="plate", size=10, font=None):
    b = Btn(win, text, cmd, w, h, kind, size, font)
    b.place(x=x, y=y, width=w, height=h)
    return b


# ---------------------------------------------------------------- 滑条
class Slider(tk.Canvas):
    """横向滑条：暗槽 + 深红已填充段 + 金疙瘩手柄，拖动即时改值。"""

    def __init__(self, parent, width=300, height=30, value=0.5, command=None,
                 fmt="%.2f", lo=0.0, hi=1.0):
        tk.Canvas.__init__(self, parent, width=width, height=height, bd=0,
                           highlightthickness=0, bg=BG, cursor="hand2")
        self._cw, self._ch = width, height
        self._lo, self._hi = lo, hi
        self._v = float(value)
        self._cmd = command
        self._fmt = fmt
        self._pad = 26                     # 两端留白，给数值文字
        self.bind("<Button-1>", self._on_press)
        self.bind("<B1-Motion>", self._on_drag)
        self.redraw()

    def get(self):
        return self._v

    def set(self, v):
        self._v = max(self._lo, min(self._hi, float(v)))
        self.redraw()

    def _x2v(self, x):
        span = self._cw - 2 * self._pad
        t = (x - self._pad) / float(max(1, span))
        return self._lo + max(0.0, min(1.0, t)) * (self._hi - self._lo)

    def _on_press(self, e):
        self.set(self._x2v(e.x))
        if self._cmd:
            self._cmd(self._v)

    def _on_drag(self, e):
        self.set(self._x2v(e.x))
        if self._cmd:
            self._cmd(self._v)

    def redraw(self):
        self.delete("all")
        w, h = self._cw, self._ch
        y = h // 2
        x1, x2 = self._pad, w - self._pad
        t = (self._v - self._lo) / float(self._hi - self._lo or 1)
        xv = x1 + int((x2 - x1) * t)

        # 槽：纯黑底 + 上下一条极暗的线
        self.create_rectangle(x1, y - 3, x2, y + 3, outline="", fill="#000000")
        self.create_line(x1, y - 3, x2, y - 3, fill="#2a2830")
        self.create_line(x1, y + 3, x2, y + 3, fill="#141318")
        # 已填充：参考红渐变，上沿一道亮红高光（还原原来的深红填充）
        if xv > x1:
            vgrad(self, x1, y - 2, xv, y + 2, CRIMSON_HI, CRIMSON_LO, 6)
            self.create_line(x1, y - 2, xv, y - 2, fill=EMBER_HI)
        # 手柄：亮色疙瘩（scrollpip），中缝压暗
        self.create_line(xv, y - 9, xv, y + 9, fill="#000000", width=3)
        self.create_rectangle(xv - 4, y - 8, xv + 4, y + 8, fill="#ebebd5",
                              outline="#4a4636")
        self.create_line(xv, y - 7, xv, y + 7, fill="#8b887a")
        # 数值
        self.create_text(w - 2, h // 2, text=self._fmt % self._v, fill=INK,
                         font=mono(8), anchor="e")


def slider(win, x, y, w, h, value, command=None, fmt="%.2f", lo=0.0, hi=1.0):
    s = Slider(win, w, h, value, command, fmt, lo, hi)
    s.place(x=x, y=y, width=w, height=h)
    return s


# ---------------------------------------------------------------- 输入框
def entry(win, x, y, w, h=28, value="", font=None, show=None):
    fr = tk.Frame(win, bg=GOLD_DK)          # 1px 金边
    fr.place(x=x, y=y, width=w, height=h)
    inner = tk.Frame(win, bg=PANEL)
    inner.place(x=x + 1, y=y + 1, width=w - 2, height=h - 2)
    e = tk.Entry(inner, bg=PANEL, fg=INK, bd=0, highlightthickness=0,
                 insertbackground=EMBER, font=font or mono(10),
                 selectbackground="#4d0407", selectforeground=INK_HI,
                 show=show or "")
    e.pack(fill="both", expand=True, padx=6, pady=3)
    if value:
        e.insert(0, value)
    return e


# ---------------------------------------------------------------- 窗口外壳
class Shell:
    """把普通 Tk 窗口换成 DD 的样子：无系统标题栏 + 金框 + 自制标题条。

    系统标题栏（白蓝那一条）跟这套美术根本不搭，所以整个换掉：
    自绘一条 34px 的标题带，左边标题、右边 ×，按住标题带可以拖窗口。
    """

    BAR_H = 34

    def __init__(self, win, title, on_close=None, w=None, h=None):
        self.win = win
        self.title = title
        self.on_close = on_close
        win.overrideredirect(True)
        win.configure(bg=BG)
        win.update_idletasks()
        self.w = w or win.winfo_width()
        self.h = h or win.winfo_height()
        self.cv = None
        self._drag = None
        self.repaint()
        win.bind("<Configure>", self._on_conf, add="+")

    # --- 布局
    def repaint(self):
        self._paint_job = None
        self.win.update_idletasks()
        w = self.win.winfo_width() or self.w
        h = self.win.winfo_height() or self.h
        if w < 60 or h < 60:
            w, h = self.w, self.h
        self.w, self.h = w, h
        if self.cv is not None:
            self.cv.destroy()
        self.cv = tk.Canvas(self.win, width=w, height=h, bd=0,
                            highlightthickness=0, bg=BG)
        self.cv.place(x=0, y=0, width=w, height=h)
        plate(self.cv, w, h)
        # 标题带：比面板再暗一档，下沿压一道金线
        self.cv.create_rectangle(2, 2, w - 2, self.BAR_H, outline="",
                                 fill=BAR_BG)
        self.cv.create_line(2, self.BAR_H, w - 2, self.BAR_H, fill=GOLD_DK)
        self.cv.create_line(8, self.BAR_H + 3, w - 8, self.BAR_H + 3, fill="#242230")
        # 标题左侧一颗参考红小菱形（暗角里的一点血）
        fx, fy = 15, self.BAR_H // 2
        self.cv.create_polygon(fx, fy - 4, fx + 4, fy, fx, fy + 4, fx - 4, fy,
                               fill=EMBER, outline="")
        # 标题带右侧的装饰菱形：暗金
        self.cv.create_polygon(w - 44, self.BAR_H // 2, w - 40, self.BAR_H // 2 - 4,
                               w - 36, self.BAR_H // 2, w - 40,
                               self.BAR_H // 2 + 4, fill=GOLD_DK, outline="")
        self.cv.tk.call("lower", self.cv._w)

        # 拖动热区：只铺标题带，不遮标题文字（文字是它上面的 Label）
        if getattr(self, "drag_area", None) is None:
            self.drag_area = tk.Frame(self.win, bg=BAR_BG, cursor="fleur")
            self.drag_area.bind("<Button-1>", self._start)
            self.drag_area.bind("<B1-Motion>", self._move)
        self.drag_area.place(x=2, y=2, width=max(40, w - 60), height=self.BAR_H - 2)

        if getattr(self, "title_lb", None) is None:
            self.title_lb = tk.Label(self.win, text=self.title, bg=BAR_BG,
                                     fg=GOLD, font=head(10), anchor="w",
                                     cursor="fleur")
            self.title_lb.bind("<Button-1>", self._start)
            self.title_lb.bind("<B1-Motion>", self._move)
        self.title_lb.configure(text=self.title)
        self.title_lb.place(x=26, y=6)

        if getattr(self, "close_btn", None) is None:
            self.close_btn = Btn(self.win, "×", self.on_close, 26, 24, "ghost", 12)
        self.close_btn.place(x=w - 32, y=5, width=26, height=24)

        raise_win(self.drag_area)
        raise_win(self.title_lb)
        raise_win(self.close_btn)

    def _on_conf(self, e):
        if e.widget is not self.win:
            return
        if abs(e.width - self.w) > 2 or abs(e.height - self.h) > 2:
            self._queue_repaint()

    def _queue_repaint(self):
        """拖动改尺寸时 Configure 会连发几十次。

        每次重绘都要销毁旧画布、再画上千个元素（渐变 + 颗粒 + 框线），
        逐个事件跟着画就会一顿一顿的。这里攒 90ms 再画一次。
        """
        job = getattr(self, "_paint_job", None)
        if job:
            try:
                self.win.after_cancel(job)
            except Exception:
                pass
        try:
            self._paint_job = self.win.after(90, self.repaint)
        except Exception:
            self._paint_job = None

    # --- 拖动
    def _start(self, e):
        self._drag = (e.x_root, e.y_root, self.win.winfo_x(), self.win.winfo_y())

    def _move(self, e):
        if not self._drag:
            return
        self.win.geometry("+%d+%d" % (self._drag[2] + e.x_root - self._drag[0],
                                      self._drag[3] + e.y_root - self._drag[1]))

    # --- 记住位置（供调用方写回配置）
    def pos(self):
        return self.win.winfo_x(), self.win.winfo_y()


def shell(win, title, on_close=None, w=None, h=None):
    return Shell(win, title, on_close, w, h)


# ---------------------------------------------------------------- 复选框 / 开关
def switch(win, x, y, on=True, size_h=24):
    """DD 式开关：开着是深红金边小牌，关着是暗灰。返回 (widget, 当前状态 list)。"""
    state = [bool(on)]
    b = Btn(win, "● 开" if on else "○ 关", None, 62, size_h,
            "red" if on else "plate", 9)
    b.place(x=x, y=y, width=62, height=size_h)

    def flip():
        state[0] = not state[0]
        b._text = "● 开" if state[0] else "○ 关"
        b._kind = "red" if state[0] else "plate"
        b.redraw()

    b._cmd = flip
    return b, state

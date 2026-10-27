#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""i18n.py —— 界面多语言。

每种语言是 `lang/` 下的一个独立 JSON 文件，文件名就是语言代码：

    lang/zh_CN.json     中文（源语言，界面文案本来就写中文）
    lang/en_US.json     英文
    lang/ja_JP.json     日语……（放进来就能用，不用改代码）

文件格式：

    {
      "_meta": {"code": "en_US", "native": "English", "author": "…", "version": 1},
      "strings": {
         "启动器": "Launcher",
         "已保存（%d 字符）→ 读回验证 ✓": "Saved (%d chars) · verified"
      }
    }

键 = 界面上的中文原文；值 = 译文。没有的键自动回落到中文原文，所以
**半成品翻译也能用**，翻多少显示多少。

给翻译者：复制 en_US.json，改 _meta 的 code / native，逐条替换右侧文字，
保存为 UTF-8，放进 lang/ 就出现在启动器的语言列表里（重启启动器可见）。
"""
import json
import os
import sys

CODE_DIR = (os.path.dirname(sys.executable) if getattr(sys, "frozen", False)
            else os.path.dirname(os.path.abspath(__file__)))
LANG_DIR = os.path.join(CODE_DIR, "lang")

_current = {"code": "zh_CN", "map": {}}
_installed = {"done": False}


# ------------------------------------------------------------------ 语言发现
def lang_dir():
    return LANG_DIR


def _read(path):
    try:
        with open(path, encoding="utf-8-sig") as f:
            d = json.load(f)
        if not isinstance(d, dict):
            return None
        meta = d.get("_meta") or {}
        strings = d.get("strings") or {}
        if not isinstance(strings, dict):
            strings = {}
        code = meta.get("code") or os.path.splitext(os.path.basename(path))[0]
        meta.setdefault("code", code)
        meta.setdefault("native", code)
        return {"code": code, "meta": meta, "strings": strings, "path": path}
    except Exception as e:
        print("[i18n] 读不了 %s：%s" % (path, e))
        return None


def available():
    """[(code, native_name)]，按 native 排序；中文排第一。"""
    out = []
    try:
        names = sorted(os.listdir(LANG_DIR))
    except OSError:
        names = []
    for n in names:
        if not n.lower().endswith(".json"):
            continue
        d = _read(os.path.join(LANG_DIR, n))
        if d:
            out.append((d["code"], str(d["meta"].get("native") or d["code"])))
    out.sort(key=lambda x: (x[0] != "zh_CN", x[1].lower()))
    return out


# ------------------------------------------------------------------ 选择语言
def _ui_file():
    """语言选择记在数据目录的 ui.json 里（和 config.json 分开，不掺业务配置）。"""
    try:
        import datadir
        return os.path.join(datadir.get(), "ui.json")
    except Exception:
        return ""


def saved_choice():
    p = _ui_file()
    if not p or not os.path.exists(p):
        return ""
    try:
        with open(p, encoding="utf-8") as f:
            return (json.load(f).get("lang") or "").strip()
    except Exception:
        return ""


def save_choice(code):
    p = _ui_file()
    if not p:
        return
    data = {}
    try:
        if os.path.exists(p):
            with open(p, encoding="utf-8") as f:
                data = json.load(f) or {}
    except Exception:
        data = {}
    data["lang"] = code
    try:
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=1)
    except Exception as e:
        print("[i18n] 保存语言失败：%s" % e)


def _system_default():
    """没选过就按系统语言：中文系统 → zh_CN，其余 → en_US。"""
    for key in ("LC_ALL", "LC_MESSAGES", "LANG"):
        v = (os.environ.get(key) or "").lower()
        if v:
            return "zh_CN" if v.startswith("zh") else "en_US"
    try:
        import locale
        v = (locale.getdefaultlocale()[0] or "").lower()
        if v:
            return "zh_CN" if v.startswith("zh") else "en_US"
    except Exception:
        pass
    return "zh_CN"


def set_language(code, cache=True):
    global _current
    path = os.path.join(LANG_DIR, "%s.json" % code)
    if not os.path.exists(path):
        # 代码可能写在 _meta 里而文件名不同：按 meta 找一遍
        for c, _n in available():
            p = os.path.join(LANG_DIR, "%s.json" % c)
            d = _read(p)
            if d and d["code"] == code:
                path = p
                break
    d = _read(path) if os.path.exists(path) else None
    _current = {"code": d["code"] if d else "zh_CN",
                "map": d["strings"] if d else {}}
    if cache:
        save_choice(_current["code"])
    return _current["code"]


def init(code=""):
    """启动时调一次：显式指定 → 上次选择 → 系统语言 → 中文。"""
    for c in (code, saved_choice(), _system_default(), "zh_CN"):
        if c and os.path.exists(os.path.join(LANG_DIR, "%s.json" % c)):
            return set_language(c, cache=False)
    return set_language("zh_CN", cache=False)


def current():
    return _current["code"]


def tr(text):
    if not isinstance(text, str) or not text:
        return text
    return _current["map"].get(text, text)


# ------------------------------------------------------------------ 自动套到控件上
def install():
    """把 tr() 挂到 tkinter 的常用控件上。

    这样界面里 90% 的文案（Label 的 text、按钮、标题、菜单、弹窗）不用改调用点
    就能跟着语言变；动态拼出来的句子仍然可以显式调 tr()。
    """
    if _installed["done"]:
        return
    _installed["done"] = True
    import tkinter as tk
    from tkinter import messagebox

    # Label：创建时 + 之后 config(text=…) 都翻
    _Label = tk.Label

    class Label(_Label):
        def __init__(self, master=None, cnf={}, **kw):
            if kw.get("text"):
                kw["text"] = tr(kw["text"])
            _Label.__init__(self, master, cnf, **kw)

        def configure(self, cnf=None, **kw):
            if kw.get("text"):
                kw["text"] = tr(kw["text"])
            return _Label.configure(self, cnf, **kw)

        config = configure

    tk.Label = Label

    # 窗口标题
    _title = tk.Wm.title

    def title(self, string=None):
        if isinstance(string, str):
            string = tr(string)
        return _title(self, string)

    tk.Wm.title = title

    # Canvas 上的文字（我们的按钮/标题都是画出来的）
    _ctext = tk.Canvas.create_text

    def create_text(self, *a, **kw):
        if kw.get("text"):
            kw["text"] = tr(kw["text"])
        return _ctext(self, *a, **kw)

    tk.Canvas.create_text = create_text

    # 菜单项
    for fn in ("add_command", "add_radiobutton", "add_checkbutton"):
        orig = getattr(tk.Menu, fn)

        def make(orig=orig):
            def wrapped(self, cnf={}, **kw):
                if kw.get("label"):
                    kw["label"] = tr(kw["label"])
                return orig(self, cnf, **kw)
            return wrapped

        setattr(tk.Menu, fn, make())

    # 列表框（世界知识里的「总览」等）
    _lb_insert = tk.Listbox.insert

    def lb_insert(self, index, *elements):
        elements = tuple(tr(e) if isinstance(e, str) else e for e in elements)
        return _lb_insert(self, index, *elements)

    tk.Listbox.insert = lb_insert

    # 弹窗
    for fn in ("showinfo", "showerror", "showwarning", "askyesno",
               "askokcancel", "askquestion", "askretrycancel"):
        orig = getattr(messagebox, fn)

        def make(orig=orig):
            def wrapped(title=None, message=None, **kw):
                return orig(tr(title), tr(message), **kw)
            return wrapped

        setattr(messagebox, fn, make())

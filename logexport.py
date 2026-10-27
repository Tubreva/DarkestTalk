# -*- coding: utf-8 -*-
"""一键导出诊断材料。

测试者点 F8 →「导出日志」，就会在程序目录下生成

    logs_export/20261026_0038_v0.1/
        dd_scribe.log        本次运行日志
        overlay.log          覆盖窗日志
        llm_calls.jsonl      最近若干次「发给模型的完整内容 + 模型返回」
        env.txt              版本 / Python / Java / 屏幕 / 各目录（脱敏）
        config.json          配置快照（api_key 已打码）
        README.txt           这份包里有什么、怎么回传

所有文本都过一遍 _mask()：把用户名、主目录、sk- 开头的 key 抹掉。
"""

import ctypes
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import time

try:
    from version import VERSION, BUILD
except Exception:                     # 单独拷走这个文件时也不至于崩
    VERSION, BUILD = "0.0", ""

MASK = "***"
MAX_CALLS = 40                        # llm_calls.jsonl 保留最近多少条
MAX_LOG_BYTES = 4 * 1024 * 1024       # 单个日志最多拷这么多


# ------------------------------------------------------------------ 脱敏
def _mask(text, extra=()):
    """把用户名 / 主目录 / api_key 抹掉。返回 str。"""
    if not text:
        return ""
    if not isinstance(text, str):
        text = str(text)
    home = os.path.expanduser("~")
    user = os.environ.get("USERNAME") or os.environ.get("USER") or ""
    reps = [home, home.replace("\\", "/")]
    if user:
        reps.append(user)
    reps.extend([x for x in extra if x])
    for r in sorted({r for r in reps if r and len(r) > 2}, key=len, reverse=True):
        text = text.replace(r, MASK)
    text = re.sub(r"sk-[A-Za-z0-9_\-]{6,}", "sk-" + MASK, text)
    text = re.sub(r"\b\d{6,}\b(?=[\\\\/]262060)", MASK, text)   # Steam userdata 目录名
    return text


def _mask_lines(lines, extra=()):
    return [_mask(x, extra) for x in lines]


def _mask_path(p):
    """路径脱敏：抹掉用户名、把盘符换成 <盘>，保留层级（还能看出装在哪一层）。"""
    p = _mask(p or "")
    p = re.sub(r"^([A-Za-z]):", "<盘>:", p)
    return p


def _drive_mask(text):
    """把行内所有 `X://path` 的盘符换掉（URL 里的 s: 不会被误伤）。"""
    return re.sub(r"(?<![A-Za-z0-9])([A-Za-z]):[\\/]", r"<盘>:/", text)


# ------------------------------------------------------------------ 环境
def _java_version():
    try:
        p = subprocess.run(["java", "-version"], stdout=subprocess.PIPE,
                           stderr=subprocess.STDOUT, timeout=10)
        out = (p.stdout or b"").decode("utf-8", "replace").strip().splitlines()
        return out[0] if out else "(java -version 无输出)"
    except Exception:
        return "(没找到 java —— 存档解码会失败)"


def _screen_info():
    try:
        u = ctypes.windll.user32
        w, h = u.GetSystemMetrics(0), u.GetSystemMetrics(1)
        try:
            dpi = u.GetDpiForSystem()
        except Exception:
            dpi = 96
        return "%dx%d，系统 DPI %d（缩放 %d%%）" % (w, h, dpi, round(dpi / 96 * 100))
    except Exception:
        return "未知"


def _paths_block(extra=()):
    rows = []
    try:
        import paths as P
        r = P.resolve()
        for key, label in (("game", "游戏目录"), ("save", "存档目录"),
                           ("workshop", "创意工坊"), ("mods", "本地 mod")):
            rows.append("%s：%s" % (label, _mask_path(r.get(key)) or "（没找到）"))
    except Exception as e:
        rows.append("路径模块读不到：%s" % e)
    return rows


def _pack_block(root):
    mf = os.path.join(root, "world", "default", "manifest.json")
    if not os.path.exists(mf):
        return ["映射库：尚未生成"]
    try:
        man = json.load(open(mf, encoding="utf-8"))
        return ["映射库：%s 个 mod，建于 %s" % (man.get("mod_count", "?"),
                                          man.get("built_at", "?"))]
    except Exception as e:
        return ["映射库清单读不了：%s" % e]


def _cfg_block(root):
    """配置摘要：能看出用了什么模型、关键开关，但不泄露 key 和私人提示词。"""
    p = os.path.join(root, "config.json")
    if not os.path.exists(p):
        return ["config.json：还没有（首次启动会自动生成）"]
    try:
        c = json.load(open(p, encoding="utf-8"))
    except Exception as e:
        return ["config.json：解析失败（%s）" % e]
    sp = (c.get("system_prompt") or "").strip()
    wb = (c.get("world_bible") or "").strip()
    return [
        "model：%s" % c.get("model", "(空)"),
        "base_url：%s" % _mask(c.get("base_url", "(空)")),
        "api_key：%s" % ("已填写" if (c.get("api_key") or "").strip() else "未填写"),
        "temperature：%s  max_tokens：%s" % (c.get("temperature"), c.get("max_tokens")),
        "自定义提示词：%s" % (("有，%d 字" % len(sp)) if sp else "无（用内置旁白机）"),
        "世界观补充：%s" % (("有，%d 字" % len(wb)) if wb else "无"),
        "开关：knowledge=%s trinket=%s world_bible=%s curio=%s debug=%s"
        % (c.get("knowledge_on"), c.get("trinket_effects_on"),
           c.get("world_bible_on"), c.get("curio_on"), c.get("debug_on")),
        "节奏：salience=%s window=%s emit_every=%s"
        % (c.get("salience"), c.get("window"), c.get("emit_every")),
    ]


def _env_text(root, extra=()):
    lines = [
        "==== 版本 ====",
        "%s %s（build %s）" % ("DarkestTalk", VERSION, BUILD),
        "导出时间：%s" % time.strftime("%Y-%m-%d %H:%M:%S"),
        "",
        "==== 运行环境 ====",
        "系统：%s %s" % (platform.system(), platform.release()),
        "Python：%s" % sys.version.replace("\n", " "),
        "tkinter：%s" % _tk_version(),
        "Java：%s" % _java_version(),
        "屏幕：%s" % _screen_info(),
        "工作目录：%s" % os.getcwd(),
        "",
        "==== 目录 ====",
    ]
    lines += _paths_block(extra)
    lines += [""] + _pack_block(root)
    lines += [""] + _cfg_block(root)
    lines += ["", "==== 覆盖窗 ===="]
    lines += _overlay_block(root)
    return _drive_mask(_mask("\n".join(lines))) + "\n"


def _tk_version():
    try:
        import tkinter
        return str(tkinter.TkVersion)
    except Exception:
        return "不可用"


def _overlay_block(root):
    p = os.path.join(root, "overlay.json")
    if not os.path.exists(p):
        return ["overlay.json：还没有（首次启动会自动生成）"]
    try:
        c = json.load(open(p, encoding="utf-8"))
        return ["位置 %s,%s 尺寸 %sx%s 字号 %s 底色 %s 字色 %s"
                % (c.get("x"), c.get("y"), c.get("width"), c.get("height"),
                   c.get("size"), c.get("bg"), c.get("fg")),
                "透明度：底板 %s / 文字 %s，打字间隔 %sms"
                % (c.get("bg_alpha"), c.get("text_alpha"), c.get("char_delay_ms"))]
    except Exception as e:
        return ["overlay.json：解析失败（%s）" % e]


# ------------------------------------------------------------------ 导出
def _copy_text(src, dst, extra=()):
    """拷日志：过大只留尾部，内容做脱敏。"""
    try:
        size = os.path.getsize(src)
        with open(src, "rb") as f:
            if size > MAX_LOG_BYTES:
                f.seek(size - MAX_LOG_BYTES)
                f.readline()
            raw = f.read()
        text = raw.decode("utf-8", errors="replace")
        if size > MAX_LOG_BYTES:
            text = "（只保留最后 %d KB）\n" % (MAX_LOG_BYTES // 1024) + text
        with open(dst, "w", encoding="utf-8") as f:
            f.write(_mask(text, extra))
        return True
    except Exception:
        return False


def _export_calls(root, dst, extra=()):
    """最近若干次完整调用（system / user / output）。"""
    src = os.path.join(root, "out", "debug.jsonl")
    rows = []
    if os.path.exists(src):
        try:
            with open(src, encoding="utf-8", errors="replace") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        rows.append(json.loads(line))
                    except Exception:
                        pass
        except Exception:
            rows = []
    rows = rows[-MAX_CALLS:]
    with open(dst, "w", encoding="utf-8") as f:
        for r in rows:
            rec = dict(r)
            rec["version"] = VERSION
            rec["build"] = BUILD
            for k in ("system", "user", "output"):
                rec[k] = _mask(rec.get(k) or "", extra)
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    return len(rows)


README_TEXT = """这份文件夹是 DarkestTalk 的诊断包。

包含：
  dd_scribe.log     主程序日志（含事实卡、LLM 调用结果、异常栈）
  overlay.log       覆盖窗日志
  llm_calls.jsonl   最近若干次完整请求：system(系统提示词) / user(事实清单) / output(模型返回)
  env.txt           版本、Python/Java、屏幕与缩放、游戏/存档目录（已脱敏）、配置摘要
  config.json       配置快照（api_key 已打码）

说明：
  * 用户名、主目录、api_key 已经替换成 ***，可以直接发给我。
  * 如果日志里有你不想外传的剧情文字，可以直接删掉对应行，不影响定位问题。
  * 反馈时请附上：这个文件夹 + 一句话的复现步骤（第几趟副本、做了什么、第几回合）。
"""


def export(root, where=None, open_after=False):
    """导出到 <root>/logs_export/<时间>_v<版本>/，返回文件夹路径。"""
    base = where or os.path.join(root, "logs_export")
    name = "%s_v%s" % (time.strftime("%Y%m%d_%H%M"), VERSION)
    out = os.path.join(base, name)
    n = 1
    while os.path.exists(out):
        n += 1
        out = os.path.join(base, "%s_%d" % (name, n))
    os.makedirs(out, exist_ok=True)

    extra = ()
    try:
        import paths as P
        r = P.resolve()
        extra = tuple(x for x in (r.get("game"), r.get("save"),
                                  r.get("workshop"), r.get("mods")) if x)
    except Exception:
        pass

    copied = []
    for name_in, name_out in (("dd_scribe.log", "dd_scribe.log"),
                              ("dd_scribe.log.prev", "dd_scribe_prev.log"),
                              ("overlay.log", "overlay.log")):
        src = os.path.join(root, "out", name_in)
        if os.path.exists(src) and _copy_text(src, os.path.join(out, name_out), extra):
            copied.append(name_out)

    calls = _export_calls(root, os.path.join(out, "llm_calls.jsonl"), extra)

    with open(os.path.join(out, "env.txt"), "w", encoding="utf-8") as f:
        f.write(_env_text(root, extra))

    cfg_src = os.path.join(root, "config.json")
    if os.path.exists(cfg_src):
        try:
            c = json.load(open(cfg_src, encoding="utf-8"))
            if c.get("api_key"):
                c["api_key"] = "sk-" + MASK
            c["system_prompt"] = ("（%d 字，全文见 llm_calls.jsonl 的 system 字段）"
                                  % len((c.get("system_prompt") or "").strip())
                                  if (c.get("system_prompt") or "").strip() else "")
            with open(os.path.join(out, "config.json"), "w", encoding="utf-8") as f:
                json.dump(c, f, ensure_ascii=False, indent=1)
        except Exception:
            pass

    with open(os.path.join(out, "README.txt"), "w", encoding="utf-8") as f:
        f.write(README_TEXT)

    info = {"dir": out, "calls": calls, "files": copied}
    try:
        with open(os.path.join(out, "_export.log"), "w", encoding="utf-8") as f:
            f.write(json.dumps(info, ensure_ascii=False, indent=1))
    except Exception:
        pass

    if open_after:
        open_folder(out)
    return out


def open_folder(path):
    """在资源管理器里打开文件夹（Windows）。"""
    try:
        os.startfile(path)            # noqa: S606 - 只打开我们刚创建的目录
        return True
    except Exception:
        try:
            subprocess.Popen(["explorer", os.path.normpath(path)])
            return True
        except Exception:
            return False


if __name__ == "__main__":
    root = os.path.dirname(os.path.abspath(__file__))
    p = export(root, open_after="--open" in sys.argv)
    print(p)

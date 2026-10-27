# -*- coding: utf-8 -*-
"""打一个可以发给别人测试的发行包。

    python make_beta.py            # 生成 dist/DarkestTalk_0.1.zip + SHA256SUMS
    python make_beta.py --open     # 打完顺手打开 dist 目录

规则只有一条：**白名单**。改代码时新增的文件如果没写进 INCLUDE，就不会进包
（宁可漏，不可多）——这样 config.json / out / probe / *.bak 永远带不出去。

打完会做一次内容扫描：发现 api_key、本机用户名、绝对路径等就中止并列出文件。
"""

import argparse
import hashlib
import io
import json
import os
import re
import shutil
import sys
import time
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

try:
    from version import VERSION, STAGE
except Exception:
    VERSION, STAGE = "0.0", "beta"

ROOT_NAME = "DarkestTalk-%s" % VERSION
DIST = os.path.join(HERE, "dist")
BUILD = os.path.join(HERE, "build")

# ---- 白名单（相对 HERE）。目录会整棵拷，文件按名拷。
INCLUDE = [
    "launch.py", "launcher.py", "dd_scribe.py", "overlay.py", "theme.py",
    "prompts.py", "paths.py", "savedec.py", "loc_index.py", "housekeeping.py",
    "logexport.py", "version.py", "datadir.py", "i18n.py",
    "knowledge.py",
    "lang",
    "config.example.json", "README.md", "CHANGELOG.md",
    "README-FIRST.txt", "LICENSE.txt", "THIRD-PARTY-NOTICES.md",
    "tools/DDSaveEditor.jar", "tools/names.txt",
]

# 双击入口：PyInstaller 打出来的启动器（build_launcher.py 生成）。
# 它自己带 Python 运行时，只负责找到玩家机器上的 Python 并拉起主程序；
# 缺 Python / 缺 Java 都会弹中文窗口，不会像 bat 那样一闪而过。
EXE = os.path.join(HERE, "dist_exe", "DarkestTalk.exe")
EXE_SRC = ["launcher.py", "theme.py", "paths.py", "logexport.py",
           "version.py", "datadir.py", "i18n.py"]

# ---- 代码目录里不再放任何玩家数据。
# config / overlay / 译名库 / 日志 / 解码产物 全部在
# <暗黑地牢安装目录>\\DarkestTalkData\\ 里（见 datadir.py），
# 所以玩家把整个工具文件夹压包发人，不会连 key 和日志一起发出去。
# 这里只做一道保险：万一白名单被改错，把数据目录也收进来了，直接中止。

# ---- 就算白名单里写了也不收
DENY_NAMES = {"config.json", "overlay.json", "paths.json", "python_path.txt"}
DENY_DIRS = {"__pycache__", "out", "probe", "archive", "build", "dist",
             "logs_export"}
DENY_SUFFIX = (".bak", ".pyc", ".log", ".sav")

# ---- 内容扫描：命中任何一条就中止
FORBIDDEN = [
    (re.compile(r"sk-[A-Za-z0-9_\-]{12,}"), "看起来像 API key"),
    (re.compile(r"C:[\\/]Users[\\/]", re.I), "绝对用户目录"),
    (re.compile(r"[A-Za-z]:[\\/](Steam|Games|Program Files)[\\/]", re.I), "绝对游戏路径"),
]

# 本机专属词条（Steam userdata 目录名、本机用户名、工作目录名）不写进仓库：
# 放同目录的 .private_terms，一行一条，`#` 开头是注释。文件不存在就只跑通用规则，
# 所以别人 clone 下来直接打包也不会因为缺这个文件而失败。
_priv = os.path.join(HERE, ".private_terms")
if os.path.isfile(_priv):
    try:
        with open(_priv, encoding="utf-8") as _f:
            for _ln in _f:
                _ln = _ln.strip()
                if _ln and not _ln.startswith("#"):
                    FORBIDDEN.append((re.compile(re.escape(_ln)), "本机专属词条"))
    except OSError:
        pass
TEXT_EXT = (".py", ".md", ".json", ".bat", ".txt", ".cfg", ".ini")


def _iter_files(dst_root):
    for base, dirs, files in os.walk(dst_root):
        dirs[:] = [d for d in dirs if d not in DENY_DIRS]
        for f in files:
            yield os.path.join(base, f)


def _copy(src, dst):
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    shutil.copy2(src, dst)


def stage():
    """把白名单里的东西拷到 build/DarkestTalk-<ver>/。"""
    if os.path.exists(BUILD):
        shutil.rmtree(BUILD)
    out = os.path.join(BUILD, ROOT_NAME)
    os.makedirs(out, exist_ok=True)

    missing = []
    for item in INCLUDE:
        src = os.path.join(HERE, item)
        dst = os.path.join(out, item)
        if os.path.isdir(src):
            for base, dirs, files in os.walk(src):
                dirs[:] = [d for d in dirs if d not in DENY_DIRS]
                rel = os.path.relpath(base, src)
                target = os.path.join(dst, rel) if rel != "." else dst
                os.makedirs(target, exist_ok=True)
                for f in files:
                    if f in DENY_NAMES or f.endswith(DENY_SUFFIX):
                        continue
                    shutil.copy2(os.path.join(base, f), os.path.join(target, f))
        elif os.path.isfile(src):
            if os.path.basename(src) in DENY_NAMES or src.endswith(DENY_SUFFIX):
                continue
            _copy(src, dst)
        else:
            missing.append(item)

    # 版本文件
    with open(os.path.join(out, "VERSION"), "w", encoding="utf-8") as f:
        f.write("%s\n%s\n" % (VERSION, time.strftime("%Y-%m-%d")))

    # 双击入口
    if not os.path.exists(EXE):
        print("  [中止] 缺少 DarkestTalk.exe：先跑 python build_launcher.py")
        return None, []
    newer = [f for f in EXE_SRC
             if os.path.getmtime(os.path.join(HERE, f)) > os.path.getmtime(EXE)]
    if newer:
        print("  [中止] 启动器比源码旧（%s 改过了）：先跑 python build_launcher.py"
              % "、".join(newer))
        return None, []
    shutil.copy2(EXE, os.path.join(out, "DarkestTalk.exe"))

    # 数据目录不该出现在包里：玩家数据都在游戏目录下的 DarkestTalkData，
    # 万一白名单被改错把它拷进来了，这里当场撤掉并提示。
    data_like = os.path.join(out, "DarkestTalkData")
    if os.path.isdir(data_like):
        shutil.rmtree(data_like, ignore_errors=True)
        print("  [提示] 已从包里移除误收的 DarkestTalkData\\")

    return out, missing


def scan(root):
    """内容扫描：返回 [(相对路径, 原因, 命中片段)]。"""
    hits = []
    for path in _iter_files(root):
        rel = os.path.relpath(path, root)
        for part in rel.split(os.sep):
            for rx, why in FORBIDDEN:
                if rx.search(part):
                    hits.append((rel, "文件名：" + why, part))
        if not path.lower().endswith(TEXT_EXT):
            continue
        try:
            text = io.open(path, encoding="utf-8", errors="replace").read()
        except Exception:
            continue
        for rx, why in FORBIDDEN:
            m = rx.search(text)
            if m:
                line = text[:m.start()].count("\n") + 1
                hits.append((rel, why, "第 %d 行：%s" % (line, m.group(0)[:40])))
    return hits


def zipit(root, out_name):
    os.makedirs(DIST, exist_ok=True)
    dst = os.path.join(DIST, out_name)
    if os.path.exists(dst):
        os.remove(dst)
    with zipfile.ZipFile(dst, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as z:
        for path in _iter_files(root):
            rel = os.path.relpath(path, os.path.dirname(root))
            z.write(path, rel)
    return dst


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def human(n):
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024:
            return "%.1f %s" % (n, unit)
        n /= 1024.0
    return "%.1f TB" % n


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--open", action="store_true", help="打完打开 dist 目录")
    args = ap.parse_args()

    print("== 打包 DarkestTalk %s ==" % VERSION)
    root, missing = stage()
    if root is None:
        return 3
    if missing:
        print("  [提示] 白名单里这些东西不存在：%s" % "、".join(missing))

    files = list(_iter_files(root))
    total = sum(os.path.getsize(p) for p in files)
    print("  文件 %d 个，原始大小 %s" % (len(files), human(total)))

    hits = scan(root)
    if hits:
        print("\n[中止] 发现不该进包的内容：")
        for rel, why, frag in hits[:40]:
            print("  · %-32s %s  %s" % (rel, why, frag))
        print("\n把上面这些改掉（或加进 make_beta 的排除规则）再打包。")
        return 2

    name = "DarkestTalk_%s.zip" % VERSION
    dst = zipit(root, name)
    digest = sha256(dst)
    with open(os.path.join(DIST, "SHA256SUMS"), "w", encoding="utf-8") as f:
        f.write("%s  %s\n" % (digest, name))

    print("  包：%s（%s）" % (dst, human(os.path.getsize(dst))))
    print("  sha256：%s" % digest)
    print("  校验：%s" % os.path.join(DIST, "SHA256SUMS"))

    if args.open:
        try:
            os.startfile(DIST)
        except Exception:
            pass
    return 0


if __name__ == "__main__":
    sys.exit(main())

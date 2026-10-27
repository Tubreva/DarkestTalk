#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""datadir.py —— 把「代码」和「玩家数据」彻底分开。

代码目录里只放程序本身；玩家的一切都写到：

    <暗黑地牢安装目录>\\DarkestTalkData\\

    其中：
      config.json        接口、api_key、自定义提示词、节奏开关
      overlay.json       悬浮窗位置 / 透明度 / 字号
      paths.json         只有在自动探测失败、玩家手动指定时才出现
      world\\default\\    按本机 mod 生成的译名库
      out\\               运行日志、live.txt、debug.jsonl、导出包
      probe\\             解码过的存档（roster / game / campaign …）

这样玩家把整个工具文件夹压包发给别人，不会把 key、日志、译名库、自己的
提示词一起带出去 —— 那些东西根本不在这个文件夹里。

找不到游戏目录时（还没装、装在认不出的位置）退到
    %LOCALAPPDATA%\\DarkestTalkData
仍然是代码目录之外。一旦退到这里，以后就一直用它，不会来回搬家。
"""
import os
import shutil
import sys

FOLDER = "DarkestTalkData"
_cache = {}


def code_dir():
    """程序自己所在的目录（打包成 exe 时是 exe 所在目录，不是临时解包目录）。"""
    if getattr(sys, "frozen", False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__))


def _local_appdata():
    return (os.environ.get("LOCALAPPDATA")
            or os.path.join(os.path.expanduser("~"), "AppData", "Local"))


def _pick():
    env = (os.environ.get("DD_DATA") or "").strip()
    if env:
        return os.path.abspath(env)

    local = os.path.join(_local_appdata(), FOLDER)
    if os.path.isdir(local):        # 已经因为"找不到游戏"落到这里，就别再搬回去
        return local

    try:
        import paths as P
        game = P.find_game()
    except Exception:
        game = ""
    if game:
        return os.path.join(game, FOLDER)
    return local


README = """DarkestTalk 数据目录
====================

这里放的是**你的**数据，工具本体不在这里：

  config.json      接口地址、api_key、自定义提示词、节奏开关
  overlay.json     悬浮窗位置 / 透明度 / 字号
  world\\default\\   按你本机已启用的 mod 生成的译名库
  out\\             运行日志、debug.jsonl、导出的诊断包
  probe\\           从存档解码出来的人物 / 战役数据
  paths.json       只有自动探测失败、你手动指定过目录时才会出现

把工具文件夹发给别人时，这个目录不会跟着走（它不在那里面）。
反过来，换机器或备份时想保留设置和译名库，记得把整个目录一起带走。
"""


def get():
    if "dir" not in _cache:
        d = _pick()
        try:
            os.makedirs(d, exist_ok=True)
        except Exception:
            d = os.path.join(_local_appdata(), FOLDER)
            os.makedirs(d, exist_ok=True)
        _cache["dir"] = d
        _migrate(code_dir(), d)
        try:
            r = os.path.join(d, "README.txt")
            if not os.path.exists(r):
                with open(r, "w", encoding="utf-8") as f:
                    f.write(README)
        except Exception:
            pass
    return _cache["dir"]


def path(*parts):
    return os.path.join(get(), *parts)


def peek():
    """已经算过就返回，没算过返回空串（给 paths.py 用，避免 import 期互相递归）。"""
    return _cache.get("dir", "")


# ------------------------------------------------------------------ 旧数据搬家
MOVE_FILES = ("config.json", "overlay.json", "paths.json",
              "curio_tracker_live.json", "campaign_log_live.json",
              "raid_state.json")
MOVE_DIRS = ("out", "probe", "logs_export", os.path.join("world", "default"))


def _migrate(src, dst):
    """把老版本放在代码目录里的数据搬进新位置（只做一次）。"""
    marker = os.path.join(dst, ".migrated")
    if os.path.exists(marker) or os.path.normcase(src) == os.path.normcase(dst):
        return
    moved = []
    for name in MOVE_FILES:
        s = os.path.join(src, name)
        d = os.path.join(dst, name)
        if os.path.exists(s) and not os.path.exists(d):
            try:
                shutil.move(s, d)
                moved.append(name)
            except Exception:
                pass
    for name in MOVE_DIRS:
        s = os.path.join(src, name)
        d = os.path.join(dst, name)
        if os.path.isdir(s) and not os.path.exists(d):
            try:
                os.makedirs(os.path.dirname(d) or dst, exist_ok=True)
                shutil.move(s, d)
                moved.append(name + os.sep)
            except Exception:
                pass
    try:
        with open(marker, "w", encoding="utf-8") as f:
            f.write("moved from %s\n" % src)
            for m in moved:
                f.write(m + "\n")
    except Exception:
        pass
    if moved:
        print("[数据] 已把 %d 项旧数据搬到 %s" % (len(moved), dst))


if __name__ == "__main__":
    print(get())

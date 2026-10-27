#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""housekeeping.py —— 让 out/ 不越用越大。

规则只有一条：**每个滚动文件最多留"当前"和"上一份"**，再往上就直接删掉旧的。
另外把旧版本留下的零碎文件（按日期命名的 scenes_/facts_、*.log.1、演示样本）
在每次启动时清掉——它们不会再有程序读写，只会拖慢排查。

为什么必须在这里做、而不是边写边删：`scenes_v2.jsonl` / `facts_v2.md` 是
dd_scribe 全程以追加方式打开的，Windows 下对打开中的文件做 rename 会失败。
所以时机必须是"回合还没开始"——launch 启动时、scribe 启动前。
"""
import os
import re
import time

# 单个文件超过这个大小就把旧的挤成 .prev（保留一份）
MAX_BYTES = {
    "dd_scribe.log": 2 * 1024 * 1024,
    "overlay.log":  1024 * 1024,
    "scenes_v2.jsonl": 2 * 1024 * 1024,
    "facts_v2.md": 2 * 1024 * 1024,
}

# 旧版本遗留：按时间戳命名的产物、旧滚动后缀、演示样本
LEGACY_PATTERNS = (
    r"^scenes_\d{8}_\d{6}\.json$",
    r"^facts_\d{8}_\d{6}\.md$",
    r"^debug_\d{8}.*\.jsonl$",
    r"\.log\.1$",
    r"^示例_.*\.(md|txt)$",
)


def _size(p):
    try:
        return os.path.getsize(p)
    except Exception:
        return 0


def rotate(path, prev_suffix=".prev"):
    """把 path 改名成 path.prev（旧的那份直接删掉）。文件不存在就算了。"""
    if not os.path.exists(path) or _size(path) == 0:
        return False
    prev = path + prev_suffix
    try:
        if os.path.exists(prev):
            os.remove(prev)
        os.replace(path, prev)
        return True
    except Exception:
        return False


def trim(path, max_bytes, prev_suffix=".prev"):
    """超过上限才滚动；没超就继续追加。"""
    if _size(path) > max_bytes:
        return rotate(path, prev_suffix)
    return False


def prune_legacy(out_dir):
    """删掉旧版本留下的、已经没人读的文件。返回删掉的个数。"""
    n = 0
    try:
        names = os.listdir(out_dir)
    except Exception:
        return 0
    for name in names:
        for pat in LEGACY_PATTERNS:
            if re.search(pat, name):
                try:
                    os.remove(os.path.join(out_dir, name))
                    n += 1
                except Exception:
                    pass
                break
    return n


def begin_session(out_dir, verbose=False):
    """一次启动开头调用：滚动本次日志、清理遗留、按大小裁剪其他文件。"""
    if not os.path.isdir(out_dir):
        return {}
    dropped = prune_legacy(out_dir)
    rolled = []
    for name, cap in MAX_BYTES.items():
        if trim(os.path.join(out_dir, name), cap):
            rolled.append(name)
    if verbose:
        if dropped:
            print("[清理] 删掉旧版本遗留文件 %d 个" % dropped)
        for r in rolled:
            print("[清理] %s 超过上限，上一份留作 %s.prev" % (r, r))
    return {"dropped": dropped, "rolled": rolled}


def reset_log(path, quiet_seconds=60):
    """轮转不行时的退路：把日志清空，至少保证"打开的是这一次"。

    什么情况下会走到这里：文件被别的程序占着（比如你用编辑器一直开着它），
    Windows 不允许改名。清空比继续往后追加好——否则新一次的内容接在旧内容后面，
    打开时看到的还是几天前的第一屏。

    但只在文件**最近一分钟没被写过**时才清空：说明没人正在写它（多半是你拿编辑器
    开着）。否则可能是上一个实例还没退干净，这时清空会把人家正在写的内容抹掉。
    """
    try:
        if not os.path.exists(path):
            return False
        if time.time() - os.path.getmtime(path) < quiet_seconds:
            return False
        # 改名做不到，但读得到：先把内容复制成 .prev，再清空。
        # 这样即使日志被占着，"上一份"也不会凭空消失。
        prev = path + ".prev"
        try:
            import shutil
            if os.path.exists(prev):
                os.remove(prev)
            shutil.copyfile(path, prev)
        except Exception:
            pass
        open(path, "w", encoding="utf-8").close()
        return True
    except Exception:
        return False


def rotate_overlay_log(out_dir):
    """覆盖窗自己的日志：每次启动换一份新的，上一份留 .prev。"""
    p = os.path.join(out_dir, "overlay.log")
    if os.path.exists(p):
        # 空文件也滚掉，免得越攒越多 0 字节的旧文件
        return rotate(p, ".prev")
    return False


if __name__ == "__main__":
    import sys
    d = sys.argv[1] if len(sys.argv) > 1 else os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "out")
    print(begin_session(d, verbose=True))

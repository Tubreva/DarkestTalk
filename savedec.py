#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""savedec.py —— 调 DDSaveEditor.jar 解码存档，并处理中文路径。

为什么需要这一层：游戏存档是二进制，靠 `DDSaveEditor.jar` 转成 JSON。
但这个 jar 是 Java 8 编的，**命令行参数里的非 ASCII 路径会被按 ANSI 解码**，
于是装在 `D:\\游戏\\DarkestTalk` 这类目录时，它会报

    Could not read C:/.../DarkestTalk/probe/game2.json   （实际是一串乱码）

人话：路径里的中文变成了乱码，文件当然找不到。玩家把工具放在中文目录
（甚至 Windows 用户名是中文）就会整个功能失效，所以这里做中转：

    中文路径的存档  → 复制到纯 ASCII 临时目录 → jar 解码 → 结果复制回来

Python 自己处理 unicode 路径没问题，只有 java 那一段需要 ASCII。
"""
import json
import os
import shutil
import subprocess
import tempfile

_CREATE_NO_WINDOW = 0x08000000 if os.name == "nt" else 0


def _ascii_tmp_root():
    """找一个纯 ASCII 的临时目录。"""
    cands = []
    pd = os.environ.get("ProgramData") or r"C:\ProgramData"
    cands.append(os.path.join(pd, "dd_narrator_tmp"))
    sysdrive = os.environ.get("SystemDrive") or "C:"
    cands.append(os.path.join(sysdrive + os.sep, "Windows", "Temp", "dd_narrator_tmp"))
    cands.append(os.path.join(tempfile.gettempdir(), "dd_narrator_tmp"))
    for c in cands:
        try:
            if not c.isascii():
                continue
            os.makedirs(c, exist_ok=True)
            probe = os.path.join(c, ".w")
            open(probe, "w").close()
            os.remove(probe)
            return c
        except Exception:
            continue
    return ""


def _run(jar, args, timeout):
    cmd = ["java", "-jar", jar] + args
    try:
        subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                       timeout=timeout, check=False,
                       creationflags=_CREATE_NO_WINDOW)
        return True
    except Exception:
        return False


def decode(jar, src, dst, names=None, timeout=40):
    """把 src 解码到 dst。返回 True/False，不抛异常。"""
    if not (jar and src and dst and os.path.exists(jar) and os.path.exists(src)):
        return False
    if not shutil.which("java"):
        return False
    d = os.path.dirname(dst)
    if d:
        try:
            os.makedirs(d, exist_ok=True)
        except Exception:
            pass

    base = ["decode"]
    use_names = bool(names and os.path.exists(names))
    if use_names:
        base += ["-n", names]

    # 1) 快路径：路径全 ASCII 时直接解，省一次复制
    if src.isascii() and dst.isascii() and jar.isascii() and (not use_names or names.isascii()):
        if _run(jar, base + ["-o", dst, src], timeout) and os.path.exists(dst):
            return True

    # 2) 中转：中文路径 → ASCII 临时目录（jar 自己放中文目录时也要搬过去）
    root = _ascii_tmp_root()
    if not root:
        return False
    try:
        work = tempfile.mkdtemp(prefix="dec_", dir=root)
    except Exception:
        return False
    try:
        tin = os.path.join(work, "in.sav")
        tout = os.path.join(work, "out.json")
        shutil.copyfile(src, tin)
        tjar = jar
        if not jar.isascii():
            tjar = os.path.join(work, "dec.jar")
            shutil.copyfile(jar, tjar)
        args = ["decode"]
        if use_names:
            tn = names
            if not names.isascii():
                tn = os.path.join(work, "names.txt")
                shutil.copyfile(names, tn)
            args += ["-n", tn]
        if not _run(tjar, args + ["-o", tout, tin], timeout):
            return False
        if not os.path.exists(tout):
            return False
        shutil.copyfile(tout, dst)
        return True
    except Exception:
        return False
    finally:
        shutil.rmtree(work, ignore_errors=True)


def load(jar, src, dst, names=None, timeout=40):
    """解码并直接返回 dict；失败返回 None。"""
    if not decode(jar, src, dst, names=names, timeout=timeout):
        return None
    try:
        return json.load(open(dst, encoding="utf-8"))
    except Exception:
        return None


if __name__ == "__main__":
    import sys
    if len(sys.argv) < 4:
        print("用法: python savedec.py <jar> <src.sav> <dst.json>")
        raise SystemExit(2)
    ok = decode(sys.argv[1], sys.argv[2], sys.argv[3])
    print("ok" if ok else "failed")

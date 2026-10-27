#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""把 launcher.py 打成 DarkestTalk.exe（双击即用的启动器）。

    python build_launcher.py

产物：dist_exe/DarkestTalk.exe（单文件，自带 Python 运行时，约 9 MB）
依赖：pip install pyinstaller

为什么启动器要单独做成 exe：玩家双击 bat 时黑框一闪、出错还看不到字；
exe 打开的是一个正常窗口，缺 Python / 缺 Java 都能用中文说清楚。

注意：主程序（launch.py → dd_scribe.py / overlay.py）仍然用**玩家自己的**
Python 跑，exe 只负责找到它、拉起它、必要时停下来。
"""
import os
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ICON = os.path.join(HERE, "assets", "darkesttalk.ico")
NAME = "DarkestTalk"


def main():
    try:
        import PyInstaller  # noqa: F401
    except ImportError:
        print("缺少 PyInstaller：pip install pyinstaller")
        return 1
    if not os.path.exists(ICON):
        print("缺少图标：%s" % ICON)
        return 1

    for d in ("dist_exe", "_build_exe"):
        p = os.path.join(HERE, d)
        if os.path.isdir(p):
            shutil.rmtree(p, ignore_errors=True)

    cmd = [sys.executable, "-m", "PyInstaller",
           "--noconfirm", "--clean", "--onefile", "--windowed",
           "--name", NAME,
           "--icon", ICON,
           "--distpath", os.path.join(HERE, "dist_exe"),
           "--workpath", os.path.join(HERE, "_build_exe"),
           "--specpath", os.path.join(HERE, "_build_exe"),
           os.path.join(HERE, "launcher.py")]
    print("== 打包启动器 ==")
    rc = subprocess.run(cmd).returncode
    exe = os.path.join(HERE, "dist_exe", NAME + ".exe")
    if rc == 0 and os.path.exists(exe):
        print("  产物：%s（%.1f MB）" % (exe, os.path.getsize(exe) / 1048576.0))
    return rc


if __name__ == "__main__":
    sys.exit(main())

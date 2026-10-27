#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
launch.py —— 一键启动器

批处理只做一件事：调用本文件。所有路径判断、存档解码、映射库校验、
覆盖窗拉起、日志跟随，全部在这里完成，避免 .bat 的编码/换行/转义问题。

用法
  python launch.py                # 正常：解码 → 校验映射库 → 开覆盖窗 → 跟随日志出文
  python launch.py --build        # 只重建映射库
  python launch.py --offline      # 只把现有日志解析一遍，不跟随、不调模型
  python launch.py --no-overlay   # 不弹覆盖窗（只在命令行看）
  python launch.py --no-llm       # 只出事实卡，不调模型
  python launch.py --export-logs  # 只导出诊断包（日志 + 完整请求），然后退出
"""
import argparse
import glob
import json
import os
import shutil
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))

try:
    from version import VERSION, BUILD, TAG
except Exception:                     # 单文件运行时也别崩
    VERSION, BUILD, TAG = "0.0", "", "v0.0"

try:                                  # 界面语言（映射库状态这些文案会显示在 F8 里）
    import i18n
    i18n.init()
    from i18n import tr
except Exception:
    def tr(x):
        return x

# 路径不写死：paths.py 按 环境变量 → paths.json → 注册表/库目录 自己找。
try:
    import paths
    _P = paths.resolve()
    GAME, SAVE = _P["game"], _P["save"]
    WORKSHOP, MODS_DIR = _P["workshop"], _P["mods"]
except Exception:
    GAME = SAVE = WORKSHOP = MODS_DIR = ""

JAR = os.path.join(HERE, "tools", "DDSaveEditor.jar")
NAMES = os.path.join(HERE, "tools", "names.txt")

# 玩家数据（config / overlay / 译名库 / 日志 / 解码产物）全部放在数据目录里，
# 不在代码目录下 —— 见 datadir.py。代码目录只剩程序和 tools\\。
try:
    import datadir
    DATA = datadir.get()
except Exception:
    DATA = HERE
PACK = os.path.join(DATA, "world", "default")
PROBE = os.path.join(DATA, "probe")
OUT = os.path.join(DATA, "out")

def _tk_candidates():
    """能跑 tkinter 的 Python 候选。

    覆盖窗需要 tkinter，而某些发行版/精简安装的 python 没带它；
    这里把所有可能的地方都列出来，由 find_tk_python() 逐个实测。
    """
    cands = [sys.executable]
    for name in ("python", "python3"):
        p = shutil.which(name)
        if p:
            cands.append(p)
    for pat in (os.path.expandvars(r"%LOCALAPPDATA%\Programs\Python\Python*\python.exe"),
                os.path.expandvars(r"%PROGRAMFILES%\Python*\python.exe"),
                r"C:\Python*\python.exe"):
        cands += sorted(glob.glob(pat), reverse=True)
    # py 启动器放最后：它最终用哪个版本不确定，不如上面的实体路径可控
    p = shutil.which("py")
    if p:
        cands.append(p)
    out = []
    for c in cands:
        if c and c not in out:
            out.append(c)
    return out


TK_CANDIDATES = _tk_candidates()


def log(msg):
    print(msg, flush=True)


def notify(title, msg):
    """pythonw 启动时没有控制台，关键错误必须用系统弹窗说出来。"""
    log("[提示] %s：%s" % (title, msg))
    if os.name != "nt":
        return
    try:
        import ctypes
        ctypes.windll.user32.MessageBoxW(None, msg, title, 0x40 | 0x10000)
    except Exception:
        pass


def decode(src, dst, with_names=False):
    """用解码器把二进制存档转成 JSON。失败返回 False。

    实际动作交给 savedec：它会处理中文路径（java 8 会把非 ASCII 参数解成乱码）。
    """
    if not (os.path.exists(JAR) and os.path.exists(src)):
        return False
    if not shutil.which("java"):
        log("      没找到 java —— 存档解码器需要 Java 运行时（JRE 8+）。")
        log("      装好 Java 后重开一次即可；在那之前名字会退化为 id。")
        return False
    try:
        import savedec
    except Exception:
        return False
    ok = savedec.decode(JAR, src, dst, names=(NAMES if with_names else None))
    if not ok:
        log("      解码失败：%s" % os.path.basename(src))
    return ok


def run(cmd):
    """跑子进程，stdout/stderr 同时落盘到 out/dd_scribe.log。

    隐藏 cmd 窗口之后（pythonw），控制台什么也看不到；日志是唯一的事后依据。
    """
    out_dir = OUT
    try:
        os.makedirs(out_dir, exist_ok=True)
    except Exception:
        pass
    logfile = os.path.join(out_dir, "dd_scribe.log")
    try:
        if os.path.exists(logfile) and os.path.getsize(logfile) > 4 * 1024 * 1024:
            os.replace(logfile, logfile + ".1")
    except Exception:
        pass
    try:
        with open(logfile, "a", encoding="utf-8", errors="replace") as f:
            f.write("\n===== %s · %s（build %s）=====\n"
                    % (time.strftime("%Y-%m-%d %H:%M:%S"), TAG, BUILD or "?"))
            f.flush()
            return subprocess.run(cmd, cwd=HERE, stdout=f, stderr=subprocess.STDOUT).returncode
    except Exception:
        return subprocess.run(cmd, cwd=HERE).returncode


def ensure_config():
    """第一次运行：把 config.example.json 复制成 config.json。

    以前要玩家自己改名，很多人卡在这一步。复制出来的文件里 api_key 是空的，
    启动后会提示按 F8 填；绝不分发任何带 key 的配置。
    """
    dst = os.path.join(DATA, "config.json")
    src = os.path.join(HERE, "config.example.json")
    if os.path.exists(dst):
        return False
    if not os.path.exists(src):
        return False
    try:
        shutil.copyfile(src, dst)
        log("[配置] 已生成 config.json（api_key 为空，请按 F8 填写）")
        return True
    except Exception as e:
        log("[配置] 生成 config.json 失败：%s" % e)
        return False


def build_pack():
    game_json = os.path.join(PROBE, "game.json")
    if not os.path.exists(game_json):
        decode(os.path.join(SAVE, "persist.game.json"), game_json)
    log("[映射库] 正在生成（只扫当前启用的 mod）……")
    cmd = [sys.executable, os.path.join(HERE, "loc_index.py"),
           "--game", GAME, "--pack", PACK, "--game-json", game_json]
    if MODS_DIR:
        cmd += ["--mods-dir", MODS_DIR]
    if WORKSHOP:
        cmd += ["--workshop", WORKSHOP]
    return run(cmd)


PACK_SCHEMA = 4          # 4 = 去掉怪癖/奇物/城镇事件，物品与饰品清洗颜色标记


def pack_status():
    """判断映射库能不能用，以及为什么不能用。

    返回 (需要重建, 原因)。判定顺序很重要：
      1. 没有 manifest → 必建
      2. manifest 的 host 不是本机 → 必建（别人机器/别人 mod 集生成的库）
      3. 没有可读的启用列表 → 本机自建的就先用着，别每次启动都白建一遍
      4. 启用列表为空而库里记着有 mod → 必建（玩家关掉全部 mod，名字要回到原版）
      5. mod 指纹不同 → 必建
    """
    mf = os.path.join(PACK, "manifest.json")
    gj = os.path.join(PROBE, "game.json")
    sys.path.insert(0, HERE)
    try:
        import loc_index
    except Exception:
        return True, tr("loc_index 不可用")
    if not os.path.exists(mf):
        return True, tr("还没有映射库")
    try:
        man = json.load(open(mf, encoding="utf-8"))
    except Exception:
        return True, tr("manifest 读不了")
    if man.get("schema") != PACK_SCHEMA:
        return True, tr("知识库格式变了（需要重建英雄 / 敌人库）")
    # host 为空 = 发行版里带的通用库：不比对机器，只比对 mod 列表（下面那步）。
    # 本机自建的库仍然带 host，换机器/换目录会整份重建，免得读别人的译名。
    host = man.get("host")
    if host and host != loc_index.host_id(GAME):
        return True, tr("这份库是在别的机器/别的游戏目录生成的")
    if not os.path.exists(gj):
        return False, tr("本机自建，但这次没读到存档，先沿用")
    try:
        steam, local, _ = loc_index.read_active_mods(gj)
        if not (steam or local):
            if man.get("mod_count"):
                return True, tr("当前没有启用任何 mod，需要重建为原版")
            return False, tr("原版，无 mod")
        fp = loc_index.fingerprint(steam, local, man.get("scope") == "all")
        if fp != man.get("fingerprint"):
            return True, tr("启用 mod 列表变了")
        return False, tr("与当前 mod 列表一致（%d 个）") % len(steam | local)
    except Exception as e:
        return True, tr("比对失败：%s") % e


def find_tk_python():
    for cand in TK_CANDIDATES:
        if not cand:
            continue
        try:
            r = subprocess.run([cand, "-c", "import tkinter"],
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                               timeout=15)
            if r.returncode == 0:
                return cand
        except Exception:
            continue
    return None


def start_overlay():
    py = find_tk_python()
    if not py:
        log("[覆盖窗] 没找到带 tkinter 的 Python，改为只在命令行输出。")
        notify("旁白窗没起来",
               "没找到带 tkinter 的 Python，悬浮窗无法显示。\n\n"
               "解决办法：装一个标准版 Python 3.8+（python.org 的安装包默认带 tkinter），"
               "然后重新运行 DarkestTalk.exe。\n\n"
               "现在旁白仍会写进 out\\live.txt，但游戏里看不到。")
        return None
    try:
        ovlog = open(os.path.join(OUT, "overlay.log"), "a",
                     encoding="utf-8", errors="replace")
        # -u：日志实时落盘。覆盖窗是个长驻进程，不带 -u 的话 out/overlay.log
        # 要到退出才刷出来，"导出日志"拿到的会是空的。
        p = subprocess.Popen([py, "-u", os.path.join(HERE, "overlay.py"),
                              "--config", os.path.join(DATA, "overlay.json")],
                             cwd=HERE, stdout=ovlog, stderr=subprocess.STDOUT)
        log("[覆盖窗] 已启动（置顶、可穿透）。")
        return p
    except Exception as e:
        log("[覆盖窗] 启动失败：%s" % e)
        return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--build", action="store_true", help="只重建映射库")
    ap.add_argument("--offline", action="store_true", help="只解析现有日志，不跟随")
    ap.add_argument("--no-overlay", action="store_true")
    ap.add_argument("--no-llm", action="store_true")
    ap.add_argument("--export-logs", action="store_true",
                    help="只导出诊断包（日志 + 完整请求），然后退出")
    ap.add_argument("--salience", type=float, default=2.5)
    ap.add_argument("--window", type=int, default=2)
    ap.add_argument("--emit-every", type=int, default=1)
    args = ap.parse_args()

    os.makedirs(PROBE, exist_ok=True)
    os.makedirs(OUT, exist_ok=True)

    log("=" * 56)
    log("  DarkestTalk %s（build %s）" % (VERSION, BUILD or "dev"))
    log("=" * 56)

    if args.export_logs:
        try:
            import logexport
            path = logexport.export(DATA, open_after=True)
            log("[日志] 已导出到：%s" % path)
            return 0
        except Exception as e:
            log("[日志] 导出失败：%s" % e)
            return 1

    ensure_config()
    try:
        _cfg = json.load(open(os.path.join(DATA, "config.json"), encoding="utf-8"))
    except Exception:
        _cfg = {}
    if not (_cfg.get("api_key") or "").strip():
        log("[配置] 还没填 api_key —— 现在只出事实卡，不会调用模型。")
        log("       启动后按 F8 →「③ 覆盖窗」下面的接口栏，填 api_key 再点「保存并生效」。")

    # 每次启动换一份新日志：这样"运行日志"打开的一定是这一次，
    # 上一份留作 .prev，再早的直接删掉（out/ 不会越攒越大）。
    try:                       # 上次异常退出可能留下退出标记
        os.remove(os.path.join(OUT, ".quit"))
    except Exception:
        pass

    try:
        import housekeeping
        _log = os.path.join(OUT, "dd_scribe.log")
        if not housekeeping.rotate(_log, ".prev") and housekeeping.reset_log(_log):
            log("      [清理] 日志被别的程序占用，已清空（上一份没能留）")
        housekeeping.begin_session(OUT, verbose=True)
    except Exception as e:
        log("      [清理] 跳过：%s" % e)

    if not GAME:
        notify("找不到游戏目录",
               "没有找到《暗黑地牢》的安装目录。\n\n"
               "如果游戏是自定义位置，在本工具目录里新建 paths.json 写上：\n"
               '{\n  "game": "D:////Steam////steamapps////common////DarkestDungeon",\n'
               '  "save": "D:////Steam////userdata////<你的ID>////262060////remote////profile_0"\n}\n\n'
               "（save 那行可以省略，会自动找）")
        return 1
    if not SAVE:
        log("      [注意] 没找到存档目录：mod 列表与人物名册读不到。")
    log("[1/4] 读取存档……")
    gj_ok = decode(os.path.join(SAVE, "persist.game.json"),
                   os.path.join(PROBE, "game.json"))
    roster_ok = decode(os.path.join(SAVE, "persist.roster.json"),
                       os.path.join(PROBE, "roster.json"), with_names=True)
    log("      启用列表 %s；人物名册 %s" % ("ok" if gj_ok else "读取失败（不影响运行）",
                                    "ok" if roster_ok else "读取失败（名字会退化为职业名）"))

    log("[2/4] 检查映射库……")
    if args.build:
        return build_pack()
    if not os.path.exists(os.path.join(PACK, "loc_zh.json")):
        log("      还没有映射库，先建一个（扫你已启用的 mod，约十几秒）。")
        if build_pack() != 0:
            log("      建库失败，中止。")
            return 1
        log("      已按本机 mod 生成：%s" % PACK)
    else:
        stale, why = pack_status()
        if stale:
            log("      %s，重建中……" % why)
            if build_pack() != 0:
                log("      重建失败，继续用旧库（新 mod 的名字可能显示为 id）。")
            else:
                log("      已重建：%s" % PACK)
        else:
            log("      %s。" % why)

    # -u：行缓冲。pythonw 下 stdout 落到文件时是全缓冲的，出问题前日志是空的，
    # 事后只能看到崩溃时冲出来的那一段；带上 -u 就能实时看到每一行。
    cmd = [sys.executable, "-u", os.path.join(HERE, "dd_scribe.py"),
           "--log", os.path.join(GAME, "app.log"),
           "--roster", os.path.join(PROBE, "roster.json"),
           "--pack", PACK,
           "--game-json", os.path.join(PROBE, "game.json"),
           "--out", OUT,
           "--salience", str(args.salience),
           "--window", str(args.window),
           "--emit-every", str(args.emit_every)]
    if args.offline:
        cmd.append("--no-mod-check")
        if not args.no_llm:
            cmd.append("--llm")
    else:
        cmd += ["--follow", "--decode-every", "20", "--no-mod-check"]
        if not args.no_llm:
            cmd.append("--llm")

    ov = None
    if not args.no_overlay and not args.offline:
        log("[3/4] 打开覆盖窗……")
        ov = start_overlay()
    else:
        log("[3/4] 跳过覆盖窗。")

    log("[4/4] 开始跟随日志。Ctrl+C 结束。\n")
    try:
        rc = run(cmd)
    except KeyboardInterrupt:
        rc = 0
    finally:
        if ov and ov.poll() is None:
            ov.terminate()
        try:
            os.remove(os.path.join(OUT, ".quit"))
        except Exception:
            pass
    log("\n结束。")
    return rc


if __name__ == "__main__":
    sys.exit(main() or 0)

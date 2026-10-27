#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""paths.py —— 自己找出游戏、存档、mod、工坊各自在哪儿。

以前 launch.py 里写死了
    GAME = r"E:\\Steam\\steamapps\\common\\DarkestDungeon"
    SAVE = r"E:\\Steam\\userdata\\<id>\\262060\\remote\\profile_0"
这只对一台机器成立。别人拿到工具，或者游戏装在 G 盘、装的是 GOG 版，
第一件事就是读不到存档、建不了库。

查找顺序（先到先得，谁先命中用谁）：
  1. 环境变量  DD_GAME / DD_SAVE / DD_WORKSHOP / DD_MODS
  2. 同目录的 paths.json（F8 →「路径」里保存的就是它）
  3. 自动探测：
     · 游戏：注册表里的 Steam 根 → libraryfolders.vdf 里每个库 → steamapps/common
             找到库之后**列出目录内容**按名字找（能认 "Darkest Dungeon" 带空格、
             "DarkestDungeon Build 25632" 这类重命名过的），再扫常见的非 Steam 位置
     · 存档：Steam userdata 里所有账号、所有 profile_* 档位，取最近玩过的那个
             再找非 Steam 版常见的 Documents\\DarkestDungeon 一类位置
     · 工坊：<Steam 库>\\steamapps\\workshop\\content\\262060
     · mod： <游戏>\\mods
每个候选都用"里面有没有该有的东西"来判断，宁可返回空串也不瞎猜。
"名字像"只用来缩小候选范围，最终能不能用一律看内容（looks_like_game / looks_like_save）。
"""
import glob
import json
import os
import re

HERE = os.path.dirname(os.path.abspath(__file__))
APP_ID = "262060"
GAME_FOLDER = "DarkestDungeon"

# 游戏目录可能叫什么：Steam 版固定是 DarkestDungeon，但 GOG / 绿色版 / 玩家自己
# 重命名之后常见 "Darkest Dungeon"（带空格）、"Darkest_Dungeon"、"Darkest Dungeon
# Build 25632"。所以名字只用来挑候选，能不能用一律看 looks_like_game 的结果。
GAME_FOLDERS = ("DarkestDungeon", "Darkest Dungeon", "Darkest_Dungeon",
                "Darkest-Dungeon")
NAME_KEY = "darkestdungeon"
DRIVES = "CDEFGHIJKLMNOPQRSTUVWXYZ"        # 软驱 A/B 不扫
JSON_PATH = os.path.join(HERE, "paths.json")


def _norm(s):
    """只留字母数字并转小写：'Darkest Dungeon (Build 25632)' → 'darkestdungeonbuild25632'。"""
    return re.sub(r"[^a-z0-9]", "", (s or "").lower())


def _name_hint(name):
    return NAME_KEY in _norm(name)


# ------------------------------------------------------------------ 读配置
def _json_paths():
    """paths.json 可能在哪。数据目录优先；代码目录那份只作兼容（老版本留下的）。"""
    out = []
    try:
        import datadir
        d = datadir.peek()
        if d:
            out.append(os.path.join(d, "paths.json"))
    except Exception:
        pass
    out.append(JSON_PATH)
    seen, uniq = set(), []
    for p in out:
        k = os.path.normcase(p)
        if k not in seen:
            seen.add(k)
            uniq.append(p)
    return uniq


def load_json_config():
    for p in _json_paths():
        if not os.path.exists(p):
            continue
        try:
            d = json.load(open(p, encoding="utf-8"))
        except Exception:
            continue
        v = {k: val.strip() for k, val in d.items()
             if isinstance(val, str) and val.strip()}
        if v:
            return v
    return {}


def save_json_config(game="", save="", workshop="", mods=""):
    """F8 的路径设置保存到这里；空值不写入，避免把自动探测关掉。"""
    d = {}
    for k, v in (("game", game), ("save", save), ("workshop", workshop), ("mods", mods)):
        if v:
            d[k] = os.path.normpath(v)
    p = _json_paths()[0]
    os.makedirs(os.path.dirname(p) or ".", exist_ok=True)
    with open(p, "w", encoding="utf-8") as f:
        json.dump(d, f, ensure_ascii=False, indent=2)
    return d


# ------------------------------------------------------------------ 判据
def looks_like_game(p):
    """认游戏的依据：目录里有下面任意一个。"""
    if not p or not os.path.isdir(p):
        return False
    for name in ("app.log", "DarkestDungeon.exe", "_windows", "localization"):
        if os.path.exists(os.path.join(p, name)):
            return True
    return False


def looks_like_save(p):
    if not p or not os.path.isdir(p):
        return False
    return (os.path.exists(os.path.join(p, "persist.game.json"))
            or os.path.exists(os.path.join(p, "persist.roster.json")))


# ------------------------------------------------------------------ Steam
def _steam_roots():
    roots = []
    seen = set()

    def add(p):
        if not p:
            return
        p = p.replace("/", "\\").rstrip("\\")
        if not p or not os.path.isdir(p):
            return
        key = p.lower()             # 注册表里常是 e:\steam，兜底猜的是 E:\Steam，别重复
        if key in seen:
            return
        seen.add(key)
        roots.append(p)

    try:
        import winreg
        for hive, key in (
                (winreg.HKEY_CURRENT_USER, r"Software\Valve\Steam"),
                (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Valve\Steam"),
                (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Valve\Steam")):
            try:
                with winreg.OpenKey(hive, key) as k:
                    for name in ("SteamPath", "InstallPath"):
                        try:
                            add(winreg.QueryValueEx(k, name)[0])
                        except Exception:
                            pass
            except Exception:
                pass
    except Exception:
        pass

    # 注册表被清理过 / 免安装版：按最常见的几种摆法猜。C–Z 全扫，一次 isdir 约几十微秒。
    for drv in DRIVES:
        for pat in ("{d}:\\Steam", "{d}:\\SteamLibrary",
                    "{d}:\\Program Files (x86)\\Steam", "{d}:\\Program Files\\Steam",
                    "{d}:\\Games\\Steam", "{d}:\\SteamGames"):
            add(pat.format(d=drv))
    return roots


def _libraries(root):
    """Steam 根 + libraryfolders.vdf 里登记的其他库目录。

    vdf 有两代写法，都认：
      新： "path"    "D:\\\\SteamLibrary"
      旧： "1"       "D:\\\\SteamLibrary"
    """
    libs = [root]
    vdf = os.path.join(root, "steamapps", "libraryfolders.vdf")
    if os.path.exists(vdf):
        try:
            raw = open(vdf, encoding="utf-8", errors="replace").read()
            for m in re.finditer(r'"path"\s+"([^"]+)"', raw):
                libs.append(m.group(1).replace("\\\\", "\\"))
            for m in re.finditer(r'"\d+"\s+"([A-Za-z]:[^"]+)"', raw):
                libs.append(m.group(1).replace("\\\\", "\\"))
        except Exception:
            pass
    out, seen = [], set()
    for lib in libs:
        key = lib.lower().rstrip("\\")
        if key and key not in seen:
            seen.add(key)
            out.append(lib)
    return out


def _children_matching(base):
    """列出 base 下名字像游戏的子目录（按名字长度排，短的在前面）。

    Steam 版的目录名可能是 "DarkestDungeon"，绿色版常见 "Darkest Dungeon"、
    "Darkest Dungeon Build 25632" —— 只看名字挑候选，是否可用交给 looks_like_game。
    """
    try:
        names = os.listdir(base)
    except OSError:
        return []
    out = [os.path.join(base, n) for n in names if _name_hint(n)]
    return sorted(out, key=lambda p: (len(os.path.basename(p)), p.lower()))


def _scan_common(lib):
    """在 <库>\\steamapps\\common 里按名字找（能认重命名过 / 带版本号的目录）。"""
    for cand in _children_matching(os.path.join(lib, "steamapps", "common")):
        if looks_like_game(cand):
            return cand
    return ""


# ------------------------------------------------------------------ 游戏
def _loose_parents():
    """非 Steam 版可能待的"上一层"：GOG 目录、玩家自建的 Games、盘根、Program Files。

    只列"上一层"，再在它下面按名字找一层，不递归 —— 免得扫遍整块盘。
    """
    out = []
    for env in ("ProgramFiles", "ProgramFiles(x86)", "LOCALAPPDATA"):
        base = os.environ.get(env)
        if base:
            out.append(base)
    home = os.path.expanduser("~")
    out += [os.path.join(home, "GOG Games"), os.path.join(home, "Games"),
            os.path.join(home, "Documents"), home]
    for drv in DRIVES:
        out += ["%s:\\" % drv,
                "%s:\\Games" % drv,
                "%s:\\GOG Games" % drv,
                "%s:\\Program Files" % drv,
                "%s:\\Program Files (x86)" % drv]
    return out


def find_game():
    env = (os.environ.get("DD_GAME") or "").strip()
    if looks_like_game(env):
        return env
    j = load_json_config().get("game")
    if looks_like_game(j):
        return j

    # 1) Steam：注册表 / 常见摆法 → 每个库 → 固定名先试，再按名字扫 common
    for root in _steam_roots():
        for lib in _libraries(root):
            for name in GAME_FOLDERS:
                cand = os.path.join(lib, "steamapps", "common", name)
                if looks_like_game(cand):
                    return cand
            cand = _scan_common(lib)
            if cand:
                return cand

    # 2) 非 Steam：GOG / 绿色版 / 自己建的目录（同样先试固定名，再按名字扫一层）
    for parent in _loose_parents():
        for name in GAME_FOLDERS:
            cand = os.path.join(parent, name)
            if looks_like_game(cand):
                return cand
        for cand in _children_matching(parent):
            if looks_like_game(cand):
                return cand
    return ""


# ------------------------------------------------------------------ 存档
def _documents_dirs():
    home = os.path.expanduser("~")
    out = [os.path.join(home, "Documents"),
           os.path.join(home, "OneDrive", "Documents"),
           os.path.join(home, "OneDrive", "文档"),
           os.path.join(home, "文档")]
    out.append(os.environ.get("USERPROFILE", ""))
    return [p for p in out if p]


def _non_steam_save_candidates(game=""):
    out = []
    for doc in _documents_dirs():
        out.append(os.path.join(doc, GAME_FOLDER, "profile_0"))
        out.append(os.path.join(doc, GAME_FOLDER))
        out.append(os.path.join(doc, "My Games", GAME_FOLDER, "profile_0"))
    appdata = os.environ.get("APPDATA")
    if appdata:
        out.append(os.path.join(appdata, GAME_FOLDER, "profile_0"))
        out.append(os.path.join(appdata, GAME_FOLDER))
    local = os.environ.get("LOCALAPPDATA")
    if local:
        out.append(os.path.join(local, GAME_FOLDER, "profile_0"))
    if game:
        out.append(os.path.join(game, "profile_0"))
        out.append(os.path.join(game, "saves", "profile_0"))
    return out


def _newest_profile(root):
    """<Steam>\\userdata\\<账号>\\262060\\remote\\profile_* 里最近玩过的那个。

    一个账号下可能有 profile_0 / profile_9（多档位、联机档、mod 加的槽位），
    所以不能用固定名；只用"里面有 persist.game.json 或 persist.roster.json"
    这一条判断它是不是真存档，再按 persist.game.json 的时间挑最新的。
    """
    best, best_t = "", -1.0
    if not root:
        return ""
    for prof in glob.glob(os.path.join(root, "userdata", "*", APP_ID,
                                       "remote", "profile_*")):
        if not looks_like_save(prof):
            continue
        t = 0.0
        for mark in ("persist.game.json", "persist.roster.json"):
            p = os.path.join(prof, mark)
            if os.path.exists(p):
                t = max(t, os.path.getmtime(p))
        if t > best_t:
            best, best_t = prof, t
    return best


def find_save(game=""):
    env = (os.environ.get("DD_SAVE") or "").strip()
    if looks_like_save(env):
        return env
    j = load_json_config().get("save")
    if looks_like_save(j):
        return j
    for root in _steam_roots():
        p = _newest_profile(root)
        if p:
            return p
    root = steam_root_of(game)          # 自定义 Steam 目录：从游戏目录反推
    if root:
        p = _newest_profile(root)
        if p:
            return p
    for cand in _non_steam_save_candidates(game):
        if looks_like_save(cand):
            return cand
        # 非 Steam 版也可能用 profile_1 / profile_9 这类档位目录
        for prof in sorted(glob.glob(os.path.join(cand, "profile_*"))):
            if looks_like_save(prof):
                return prof
    return ""


def steam_root_of(path):
    """从游戏目录**或存档目录**反推 Steam 根（存档在 <root>/userdata 下）。

    游戏：…\\steamapps\\common\\DarkestDungeon（往上 3 层）
    存档：…\\userdata\\<id>\\262060\\remote\\profile_0（往上 5 层）
    所以统一往上找"里面有 userdata 的目录"，最多 8 层。
    """
    if not path:
        return ""
    cur = os.path.normpath(path)
    for _ in range(8):
        nxt = os.path.dirname(cur)
        if not nxt or nxt == cur:
            break
        cur = nxt
        if os.path.isdir(os.path.join(cur, "userdata")):
            return cur
    return ""


def game_from_save(save_dir):
    """存档找到了、游戏没找到：从 Steam 根反查 steamapps\\common。"""
    root = steam_root_of(save_dir)
    if not root:
        return ""
    for lib in _libraries(root):
        for name in GAME_FOLDERS:
            cand = os.path.join(lib, "steamapps", "common", name)
            if looks_like_game(cand):
                return cand
        cand = _scan_common(lib)
        if cand:
            return cand
    return ""


def find_save_near(game_dir):
    """只在游戏目录旁边找（不扫文档目录），给"最近一次"之类的小用途。"""
    root = steam_root_of(game_dir)
    return _newest_profile(root) if root else ""


# ------------------------------------------------------------------ mod / 工坊
def find_workshop(game=""):
    env = (os.environ.get("DD_WORKSHOP") or "").strip()
    if env and os.path.isdir(env):
        return env
    j = load_json_config().get("workshop")
    if j and os.path.isdir(j):
        return j
    for root in _steam_roots():
        for lib in _libraries(root):
            cand = os.path.join(lib, "steamapps", "workshop", "content", APP_ID)
            if os.path.isdir(cand):
                return cand
    return ""


def find_mods(game=""):
    env = (os.environ.get("DD_MODS") or "").strip()
    if env and os.path.isdir(env):
        return env
    j = load_json_config().get("mods")
    if j and os.path.isdir(j):
        return j
    if game:
        cand = os.path.join(game, "mods")
        if os.path.isdir(cand):
            return cand
    return ""


# ------------------------------------------------------------------ 汇总
def resolve():
    """返回 {'game','save','workshop','mods'}；找不到的项是空串。"""
    game = find_game()
    save = find_save(game)
    if not game and save:
        # 反过来也能走：存档找到了（比如注册表里 Steam 位置被改过），就顺着
        # userdata 的上一层找回 steamapps\common。
        game = game_from_save(save)
    return {"game": game,
            "save": save,
            "workshop": find_workshop(game),
            "mods": find_mods(game)}


def summary():
    """给人看的一句话诊断，启动日志和 F8 都用它。"""
    r = resolve()
    lines = []
    for key, label in (("game", "游戏目录"), ("save", "存档目录"),
                       ("workshop", "创意工坊"), ("mods", "本地 mod")):
        lines.append("%s：%s" % (label, r[key] or "（没找到）"))
    if r["game"] and not r["workshop"]:
        lines.append("（没有工坊目录：非 Steam 版，或这台机器没装工坊 mod）")
    return "\n".join(lines)


if __name__ == "__main__":
    print(summary())

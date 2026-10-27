#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
loc_index.py —— 生成「本地化映射包」（一次性，离线）

为什么要有这一步
  游戏界面上的文字全部来自 .string_table.xml，内部代码（如 str_quirk_name_bad_gambler）
  必须先查表才知道中文。但**运行时绝不能去扫 mod 库**：那会让工具在不同 mod 列表的
  玩家机器上又慢又脆。

  所以拆成两件事：
    · 建包（本脚本，慢，手动或换 mod 时跑一次）
    · 查表（dd_scribe.py，只读一个 JSON，毫秒级）

只扫「当前启用」的 mod
  启用列表来自存档 persist.game.json 的 applied_ugcs_1_0 —— 里面同时有 Steam 创意工坊 id
  和本地 mod 目录名。没启用的 mod 不进包。

用法
  # 推荐：按存档里的启用列表建包（需先解码 persist.game.json）
  python loc_index.py --game <游戏根目录> --game-json probe/game.json --pack world/default

  # 没有解码存档时：按目录里实际存在的 mod 建包
  python loc_index.py --game <游戏根目录> --pack world/default

  # 全量（含未启用 mod），仅用于排查
  python loc_index.py --game <游戏根目录> --pack world/all --all
"""
import argparse
import hashlib
import json
import os
import re
import time
from datetime import datetime

# 游戏文本里的富文本标记：
#   {colour_start|deathdoor} … {colour_end}   颜色
#   {?hero_names}                             条件段落标记
#   ¤ ¤ ¤                                     目标位置占位符
COLOUR_RE = re.compile(r"\{colour_start\|[^}]*\}|\{colour_end\}|\{\?[^}]*\}")
PLACEHOLDER_RE = re.compile(r"[¤¦]+")


def clean_text(s):
    """去掉颜色标记、占位符，把换行压成空格。

    世界知识里原来会留下 `{colour_start|deathdoor}POD技能{colour_end}`，
    既不可读，也会被模型当成正文抄进去。
    """
    if not s:
        return ""
    t = str(s)
    t = COLOUR_RE.sub("", t)
    t = PLACEHOLDER_RE.sub("", t)
    t = t.replace("\r", " ").replace("\n", " ")
    t = re.sub(r"[ \t\u3000]+", " ", t)
    return t.strip()


def last(v):
    """同一 key 有多份译名时，取最后加载的那份（就是玩家在游戏里看到的）。"""
    if not v:
        return ""
    if isinstance(v, list):
        return v[-1] if v else ""
    return v


ENTRY_RE = re.compile(
    r'<entry\s+id="([^"]+)"\s*>\s*(?:<!\[CDATA\[(.*?)\]\]>|(.*?))\s*</entry>', re.S)
LANG_OPEN = '<language id="{lang}">'


def blocks(text, lang):
    start = text.find(LANG_OPEN.format(lang=lang))
    if start < 0:
        return ""
    end = text.find("</language>", start)
    return text[start:end] if end > 0 else text[start:]


def first(v):
    return v[0] if isinstance(v, list) else (v or "")


# 这两个目录由 main() 按命令行/自动探测的结果填好，下面的函数直接用。
# 之所以做成全局：collect_files / 饰品 / 疾病 / 怪癖几处都要扫 mod，参数一路传下去太吵。
MODS_DIR = ""
WS_DIR = ""


def mods_dir_of(game):
    return MODS_DIR or os.path.join(game, "mods")


def ws_dir_of(game):
    if WS_DIR:
        return WS_DIR
    return os.path.normpath(os.path.join(game, "..", "..", "workshop", "content", "262060"))


def read_active_mods(game_json):
    """从解码后的 persist.game.json 取启用列表。"""
    if not game_json or not os.path.exists(game_json):
        return set(), set(), []
    try:
        d = json.load(open(game_json, encoding="utf-8"))
    except Exception:
        return set(), set(), []
    root = d.get("base_root") or {}
    ugc = root.get("applied_ugcs_1_0") or {}
    steam, local, raw = set(), set(), []
    items = ugc.items() if isinstance(ugc, dict) else []
    # 按 "0","1","2"... 的数字序还原真实加载顺序
    ordered = sorted(items, key=lambda kv: int(kv[0]) if str(kv[0]).isdigit() else 9999)
    for _, item in ordered:
        item = item or {}
        name, src = item.get("name") or "", item.get("source") or ""
        if not name:
            continue
        raw.append({"name": name, "source": src})
        if src == "Steam":
            steam.add(str(name))
        else:
            local.add(name)
    return steam, local, raw


def collect_files(game, steam=None, local=None, everything=False, order=None):
    """返回 [(path, 来源标签)]，顺序即游戏加载顺序：本体 → DLC → mod（按启用列表）→ 未在列表中的其余。"""
    out = []
    base = os.path.join(game, "localization")
    if os.path.isdir(base):
        for f in sorted(os.listdir(base)):
            if f.endswith(".string_table.xml"):
                out.append((os.path.join(base, f), "base"))

    d0 = os.path.join(game, "dlc")
    if os.path.isdir(d0):
        for name in sorted(os.listdir(d0)):
            d1 = os.path.join(d0, name, "localization")
            if os.path.isdir(d1):
                for f in sorted(os.listdir(d1)):
                    if f.endswith(".xml"):
                        out.append((os.path.join(d1, f), "dlc:" + name))

    mods_dir = mods_dir_of(game)
    ws = ws_dir_of(game)

    def add_dir(base, name, tag):
        d1 = os.path.join(base, name, "localization")
        if not os.path.isdir(d1):
            return
        for f in sorted(os.listdir(d1)):
            if f.endswith(".xml"):
                out.append((os.path.join(d1, f), tag + ":" + name))

    if order:                      # 按存档里的启用顺序逐个加载（后覆盖前）
        seen = set()
        for item in order:
            name, src = item["name"], item["source"]
            if src == "Steam":
                add_dir(ws, name, "workshop")
            else:
                add_dir(mods_dir, name, "mod")
            seen.add(name)
        if everything:             # 其余没启用的排最后
            for base, tag in ((ws, "workshop"), (mods_dir, "mod")):
                if not os.path.isdir(base):
                    continue
                for name in sorted(os.listdir(base)):
                    if name not in seen:
                        add_dir(base, name, tag)
    else:
        for base, tag in ((mods_dir, "mod"), (ws, "workshop")):
            if not os.path.isdir(base):
                continue
            for name in sorted(os.listdir(base)):
                if not everything and tag == "mod" and local and name not in local:
                    continue
                if not everything and tag == "workshop" and steam and name not in steam:
                    continue
                add_dir(base, name, tag)
    return out


def asset_roots(game, steam=None, local=None, everything=False, order=None):
    """素材目录（heroes/、monsters/）的加载顺序：本体 → DLC → mod。

    和 collect_files 用的是同一套顺序，因为后面的 mod 会覆盖前面的同名职业，
    职业介绍也要跟着"谁最后加载谁说了算"。
    """
    roots = [(game, "base")]
    d0 = os.path.join(game, "dlc")
    if os.path.isdir(d0):
        for name in sorted(os.listdir(d0)):
            roots.append((os.path.join(d0, name), "dlc:" + name))

    mods_dir = mods_dir_of(game)
    ws = ws_dir_of(game)

    def add(base, name, tag):
        d = os.path.join(base, name)
        if os.path.isdir(d):
            roots.append((d, tag + ":" + name))

    if order:
        seen = set()
        for item in order:
            name, src = item.get("name"), item.get("source")
            add(ws if src == "Steam" else mods_dir, name,
                "workshop" if src == "Steam" else "mod")
            seen.add(name)
        if everything:
            for base, tag in ((ws, "workshop"), (mods_dir, "mod")):
                if not os.path.isdir(base):
                    continue
                for name in sorted(os.listdir(base)):
                    if name not in seen:
                        add(base, name, tag)
    else:
        for base, tag in ((mods_dir, "mod"), (ws, "workshop")):
            if not os.path.isdir(base):
                continue
            for name in sorted(os.listdir(base)):
                if not everything and tag == "mod" and local and name not in local:
                    continue
                if not everything and tag == "workshop" and steam and name not in steam:
                    continue
                add(base, name, tag)
    return roots


def _name_of(zh, en, key):
    """中文优先；只有英文表的 mod 用英文，总比显示内部 id 强。"""
    return clean_text(last(zh.get(key))) or clean_text(last(en.get(key)))


def build_hero_kb(zh, en, roots):
    """英雄职业知识库：只认 heroes/ 目录里真实存在的职业。

    以前是拿 `hero_class_name_` 反推职业，于是怪物（skeleton_maid 之类）和
    只有名字没有资料的条目混进"职业"里，还把技能名当成职业介绍——那对写作没用。

    这里改成正路：heroes/<cls>/<cls>.info.darkest 存在，才是玩家能招募的职业。
    每个职业取三段界面介绍（它们正是游戏里在对应建筑中显示的那段话）：

      action_verbose_body_guild_<cls>              公会（战斗定位、擅长什么）
      action_verbose_body_blacksmith_<cls>         铁匠铺（武器与护甲）
      action_verbose_body_camping_trainer_<cls>    生存帐篷/营地训练（营火边的行为）
    """
    found = {}
    for root, tag in roots:
        hd = os.path.join(root, "heroes")
        if not os.path.isdir(hd):
            continue
        try:
            names = os.listdir(hd)
        except OSError:
            continue
        for name in names:
            d = os.path.join(hd, name)
            if not os.path.isdir(d):
                continue
            try:
                ok = any(f.endswith(".info.darkest") for f in os.listdir(d))
            except OSError:
                ok = False
            if ok:
                found[name] = tag          # 后加载的覆盖前面的

    kb = {}
    for cls in sorted(found):
        kb[cls] = {
            "name": _name_of(zh, en, "hero_class_name_" + cls) or cls,
            "guild": _name_of(zh, en, "action_verbose_body_guild_" + cls),
            "blacksmith": _name_of(zh, en, "action_verbose_body_blacksmith_" + cls),
            "camping": _name_of(zh, en,
                                "action_verbose_body_camping_trainer_" + cls),
            "source": found[cls],
        }
    return kb


SKILL_LINE_RE = re.compile(r'^[ \t]*skill:[ \t]*[.]id[ \t]*"([^"]+)"', re.M)
ETYPE_LINE_RE = re.compile(r'^[ \t]*enemy_type:[ \t]*[.]id[ \t]*"([^"]+)"', re.M)


def build_monster_kb(zh, en, roots):
    """敌人知识库：monsters/ 里的单位 + 它们的出手手段。

    敌人没有"介绍"文本，能拿到的只有技能名，所以这里就按用户要的形式存：
        名称：技能1、技能2
    自带招式的才收（没有 skill 行的多半是酒桶、指示器这类道具）。
    """
    kb = {}
    for root, tag in roots:
        md = os.path.join(root, "monsters")
        if not os.path.isdir(md):
            continue
        for dirpath, _dirs, files in os.walk(md):
            for fn in files:
                if not fn.endswith(".info.darkest"):
                    continue
                mid = fn[:-len(".info.darkest")]
                try:
                    txt = open(os.path.join(dirpath, fn), encoding="utf-8",
                               errors="replace").read()
                except OSError:
                    continue
                skills, seen = [], set()
                for sid in SKILL_LINE_RE.findall(txt):
                    if sid in seen:
                        continue
                    seen.add(sid)
                    nm = (_name_of(zh, en, "str_monster_skill_" + sid)
                          or _name_of(zh, en, "combat_skill_name_" + sid) or sid)
                    skills.append({"id": sid, "name": nm or sid})
                if not skills:
                    continue
                # 有些 mod 的变体 id 末尾带一个残字符（fishman_harpoon_[ 之类），
                # 取名字前先把它削掉；原 id 仍留作键（日志里就是它）。
                stem = re.sub(r"[^0-9A-Za-z_]+$", "", mid)
                base = re.sub(r"_[A-Za-z]$", "", stem)
                name = (_name_of(zh, en, "str_monstername_" + mid)
                        or _name_of(zh, en, "str_monstername_" + stem)
                        or _name_of(zh, en, "str_monstername_" + base)
                        or _name_of(zh, en, "hero_class_name_" + stem) or stem)
                entry = {"name": name or stem, "skills": skills, "source": tag,
                         "stem": stem}
                types = ETYPE_LINE_RE.findall(txt)
                if types:
                    entry["types"] = sorted(set(types))
                kb[mid] = entry
    return kb


def _parse_block(text, lang):
    """单个文件内：同一 key 出现多次视为多值（台词变体）。"""
    got = {}
    for m in ENTRY_RE.finditer(blocks(text, lang)):
        k = m.group(1)
        v = (m.group(2) if m.group(2) is not None else m.group(3) or "").strip()
        if not v:
            continue
        if k in got:
            got[k].append(v)
        else:
            got[k] = [v]
    return got


def build(files, lang="schinese", fallback="english"):
    """按加载顺序合并：后加载的文件覆盖先加载的（与游戏行为一致）。"""
    zh, en = {}, {}
    for path, _src in files:
        try:
            text = open(path, encoding="utf-8", errors="replace").read()
        except OSError:
            continue
        got = _parse_block(text, lang)
        for k, vals in got.items():
            prev = zh.get(k)
            if prev is None:
                zh[k] = vals[0] if len(vals) == 1 else vals
            else:
                plist = prev if isinstance(prev, list) else [prev]
                for v in vals:
                    if v not in plist:
                        plist.append(v)
                zh[k] = plist[0] if len(plist) == 1 else plist
        if not got:
            for k, vals in _parse_block(text, fallback).items():
                en[k] = vals[0] if len(vals) == 1 else vals
    return zh, en


def host_id(game_dir):
    """这一份知识库是在哪台机器/哪个游戏目录上生成的。

    分发时最有用的一个字段：别人拿到你的包，host 对不上就整份重建，
    不会出现「读着别人的 mod 译名玩游戏」。
    """
    tag = (os.path.normcase(os.path.abspath(game_dir or "")) + "|"
           + os.environ.get("COMPUTERNAME", "") + "|"
           + os.environ.get("USERNAME", ""))
    return hashlib.sha1(tag.encode("utf-8")).hexdigest()[:12]


def fingerprint(steam, local, everything):
    items = ["ALL"] if everything else sorted(steam | local)
    return hashlib.sha1("|".join(items).encode("utf-8")).hexdigest()[:16]



# ------------------------------------------------------------------ 世界知识
def write_knowledge(pack, zh, heroes, monsters):
    """把本地化整理成玩家可读、可改的「世界知识」。
    同一 key 有多个译名时全部列出，并标 ★ —— 那通常就是某个 mod 覆盖过的。
    """
    out = os.path.join(pack, "knowledge")
    os.makedirs(out, exist_ok=True)

    def vals(k):
        v = zh.get(k)
        if not v:
            return []
        return v if isinstance(v, list) else [v]

    def pick_last(k):
        vs = vals(k)
        return vs[-1] if vs else ""

    def modded(k):
        return len(vals(k)) > 1

    stats = {}

    # 英雄职业：名称 + 三段界面介绍
    lines = ["# 英雄职业（%d）" % len(heroes), "",
             "只收 heroes/ 目录里真实存在的可招募职业。",
             "每段文字就是游戏里在对应建筑中显示的那段介绍。", ""]
    for cls, d in sorted(heroes.items(), key=lambda kv: kv[1]["name"]):
        lines.append("## %s [%s]" % (d["name"], cls))
        for label, key in (("公会", "guild"), ("铁匠铺", "blacksmith"),
                           ("营地", "camping")):
            if d.get(key):
                lines.append("- %s：%s" % (label, d[key]))
        lines.append("")
    open(os.path.join(out, "heroes.md"), "w", encoding="utf-8").write("\n".join(lines))
    stats["heroes"] = len(heroes)

    # 敌人：名称 + 出手手段（按名字归并，A/B/C 变体列为同一个）
    groups = {}
    for mid, d in monsters.items():
        groups.setdefault(d["name"], []).append((mid, d))
    lines = ["# 敌人（%d 种 / %d 个单位）" % (len(groups), len(monsters)), "",
             "敌人没有介绍文本，能拿到的只有它们的招式名。", ""]
    for name in sorted(groups):
        ids = ", ".join(sorted(d.get("stem") or mid for mid, d in groups[name]))
        skills = []
        for _mid, d in groups[name]:
            for sk in d["skills"]:
                if sk["name"] not in skills:
                    skills.append(sk["name"])
        lines.append("- %s：%s　[%s]" % (name, "、".join(skills), ids))
    open(os.path.join(out, "monsters.md"), "w", encoding="utf-8").write("\n".join(lines))
    stats["monsters"] = len(groups)

    # 物品：名字与描述都去掉颜色标记，描述不截断（mod 的介绍本身就是完整的一句/一段）
    items = sorted(k for k in zh if k.startswith("str_inventory_title_"))
    lines = ["# 物品（%d）" % len(items), "",
             "格式：`名称 [id]（描述）　★ = 被 mod 覆盖过", ""]
    for k in items:
        iid = k[len("str_inventory_title_"):]
        name = clean_text(pick_last(k))
        desc = clean_text(pick_last("str_inventory_description_" + iid))
        mark = " ★" if modded(k) else ""
        lines.append("- %s [%s]%s%s" % (name, iid, mark,
                                        ("（%s）" % desc) if desc else ""))
    open(os.path.join(out, "items.md"), "w", encoding="utf-8").write("\n".join(lines))
    stats["items"] = len(items)

    # 怪癖、奇物、城镇事件都不再进知识库：
    #   · 怪癖直接进 prompt 的人物状态（不截断、全量），比另存一份 md 有用
    #   · 奇物互动是「谁碰了什么、得到什么结果」，等专门处理
    #   · 城镇事件本来就能从存档读到，不需要靠知识库 + 匹配

    # 索引
    idx = ["# 世界知识速查（由已安装 mod 的本地化自动生成）", "",
           "这些文件是给你看的：核对游戏里某个东西到底叫什么、是哪段文本。",
           "★ 标记的条目 = 同一个 id 有多个译名，通常就是你装的 mod 覆盖过的。", "",
           "| 文件 | 内容 | 条数 |", "|---|---|---|",
           "| heroes.md | 英雄职业 + 公会/铁匠铺/营地介绍 | %d |" % stats["heroes"],
           "| monsters.md | 敌人 + 它们的招式 | %d |" % stats["monsters"],
           "| items.md | 物品名与描述 | %d |" % stats["items"],
           "| trinkets.md | 饰品名与效果（另存 trinkets.json） | — |", "",
           "这些文字可以直接在工具的 F8 →「世界知识」里改：点左边任意一条，",
           "改右边的介绍正文即可。改动单独存在 knowledge_overrides.json，",
           "重建知识库、换 mod 都不会丢；标题与方括号关键词用于匹配存档，不可改。", "",
           "想改某个名字？在同目录的 overrides.json 里写：", "",
           '```json', '{"gem-emerald": {"name": "你想显示的名字", "desc": "可选描述"}}', '```', "",
           "运行时优先用 overrides.json，其次用 mod 最后一次覆盖的名字。"]
    open(os.path.join(pack, "knowledge.md"), "w", encoding="utf-8").write("\n".join(idx))

    ov = os.path.join(pack, "overrides.json")
    if not os.path.exists(ov):
        json.dump({"_说明": "键 = 游戏内部 id（items.md 里方括号中的那个）；值可只写 name"} ,
                  open(ov, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    return stats



def write_diseases(pack, game, order=None):
    """疾病在存档里就是一个 quirk，只能靠 quirk_library.json 的 is_disease 标记区分。"""
    found = set()
    base = os.path.join(game, "shared", "quirk")
    cands = []
    if os.path.isdir(base):
        cands += [os.path.join(base, f) for f in os.listdir(base)
                  if f.endswith(".json") and "quirk" in f]
    mods_dir = mods_dir_of(game)
    if os.path.isdir(mods_dir):
        for name in os.listdir(mods_dir):
            d = os.path.join(mods_dir, name, "shared", "quirk")
            if os.path.isdir(d):
                cands += [os.path.join(d, f) for f in os.listdir(d)
                          if f.endswith(".json") and "quirk" in f]
    for path in cands:
        try:
            d = json.load(open(path, encoding="utf-8"))
        except Exception:
            continue
        qs = d.get("quirks", d)
        items = qs.items() if isinstance(qs, dict) else [(q.get("id"), q) for q in qs]
        for qid, q in items:
            if isinstance(q, dict) and q.get("is_disease"):
                found.add(qid)
    out = os.path.join(pack, "diseases.json")
    json.dump(sorted(found), open(out, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    return len(found)



# ------------------------------------------------------------------ 饰品索引
def write_trinkets(pack, game, zh, order=None):
    """把饰品的「名字 + 效果」编成索引。

    效果不是硬编码的：buff 定义里有 stat_type / stat_sub_type / amount，
    本地化里有对应模板（buff_stat_tooltip_<stat_type>[_<sub_type>]），
    两边相乘就能还原出游戏里 tooltip 的那几行。模板缺失的 buff 直接丢掉，
    宁可少写，也不让 prompt 里出现 "?" 或原始 id。
    """
    def load_json(path):
        try:
            return json.load(open(path, encoding="utf-8"))
        except Exception:
            return None

    def entries_of(d, *keys):
        if not isinstance(d, dict):
            return []
        for k in keys:
            v = d.get(k)
            if isinstance(v, list):
                return v
            if isinstance(v, dict):
                return list(v.values())
        return []

    # ---- buff 库：本体 + 所有已启用 mod
    buffs = {}
    shared = os.path.join(game, "shared", "buffs")
    files = []
    if os.path.isdir(shared):
        files += [os.path.join(shared, f) for f in os.listdir(shared) if f.endswith(".json")]
    mods_dir = mods_dir_of(game)
    ws_dir = ws_dir_of(game)
    roots = [mods_dir] + ([ws_dir] if os.path.isdir(ws_dir) else [])
    for root in roots:
        if not os.path.isdir(root):
            continue
        for name in os.listdir(root):
            d = os.path.join(root, name, "shared", "buffs")
            if os.path.isdir(d):
                files += [os.path.join(d, f) for f in os.listdir(d) if f.endswith(".json")]
    for f in files:
        d = load_json(f)
        if not d:
            continue
        for b in entries_of(d, "buffs"):
            if isinstance(b, dict) and b.get("id"):
                buffs[b["id"]] = b

    low = {k.lower(): k for k in zh}

    def text(key):
        v = zh.get(key)
        if not v:
            return ""
        return (v[0] if isinstance(v, list) else v) or ""

    def render_buff(bid):
        b = buffs.get(bid)
        if not b:
            return ""
        st, sub = b.get("stat_type", ""), b.get("stat_sub_type", "")
        for cand in ("buff_stat_tooltip_%s_%s" % (st, sub), "buff_stat_tooltip_" + st):
            k = low.get(cand.lower())
            if not k:
                continue
            tpl = text(k)
            if not tpl:
                continue
            amt = b.get("amount", 0) or 0
            percent = ("%" in tpl)
            val = round(amt * 100) if percent else (round(amt, 2) if isinstance(amt, float) else amt)
            try:
                out = tpl % val
            except Exception:
                try:
                    out = tpl % (val, val)
                except Exception:
                    continue
            out = clean_text(out.replace("%%", "%"))
            return out
        return ""

    # ---- 饰品定义：本体 + mod
    trinkets = {}
    tf = []
    tdir = os.path.join(game, "trinkets")
    if os.path.isdir(tdir):
        tf += [os.path.join(tdir, f) for f in os.listdir(tdir) if f.endswith(".json")]
    for root in roots:
        if not os.path.isdir(root):
            continue
        for name in os.listdir(root):
            d = os.path.join(root, name, "trinkets")
            if os.path.isdir(d):
                tf += [os.path.join(d, f) for f in os.listdir(d) if f.endswith(".json")
                       and ".bak" not in f]
    for f in tf:
        d = load_json(f)
        for t in entries_of(d, "entries", "items"):
            if not (isinstance(t, dict) and t.get("id")):
                continue
            eff = []
            for bid in (t.get("buffs") or []):
                txt = render_buff(bid)
                if txt and txt not in eff:
                    eff.append(txt)
            trinkets[t["id"]] = {"name": "", "effects": eff[:4]}

    # ---- 名字
    for tid, d in trinkets.items():
        bare = tid.replace("-", "").replace("_", "")
        d["name"] = clean_text(text("str_inventory_title_trinket" + tid)
                               or text("str_inventory_title_" + tid)
                               or text("str_inventory_title_trinket" + bare)
                               or text("str_inventory_title_" + bare))

    out = os.path.join(pack, "trinkets.json")
    json.dump(trinkets, open(out, "w", encoding="utf-8"), ensure_ascii=False, indent=0)

    # 顺带出一份人看的清单（世界知识里能翻）
    kd = os.path.join(pack, "knowledge")
    os.makedirs(kd, exist_ok=True)
    rows = ["# 饰品（%d）" % len(trinkets), "",
            "格式：`名称 [id] —— 效果`；空效果表示该 mod 没提供可还原的模板。", ""]
    for tid, d in sorted(trinkets.items(), key=lambda kv: kv[1]["name"] or kv[0]):
        eff = "、".join(clean_text(x) for x in d["effects"])
        rows.append("- %s [%s]%s" % (d["name"] or "（未译）", tid,
                                     (" —— " + eff) if eff else ""))
    open(os.path.join(kd, "trinkets.md"), "w", encoding="utf-8").write("\n".join(rows))
    with_eff = sum(1 for d in trinkets.values() if d["effects"])
    return len(trinkets), with_eff


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--game", required=True)
    ap.add_argument("--pack", default="world/default")
    ap.add_argument("--game-json", default="", help="解码后的 persist.game.json（取启用列表）")
    ap.add_argument("--all", action="store_true", help="忽略启用列表，扫描全部 mod")
    ap.add_argument("--mods-dir", default="", help="本地 mod 目录（默认 <游戏>/mods）")
    ap.add_argument("--workshop", default="", help="创意工坊目录（默认 <Steam>/steamapps/workshop/content/262060）")
    ap.add_argument("--lang", default="schinese")
    args = ap.parse_args()

    global MODS_DIR, WS_DIR
    MODS_DIR = args.mods_dir or ""
    WS_DIR = args.workshop or ""
    if not os.path.isdir(MODS_DIR or os.path.join(args.game, "mods")):
        print("[注意] 本地 mod 目录不存在：%s" % (MODS_DIR or os.path.join(args.game, "mods")))
    if WS_DIR and not os.path.isdir(WS_DIR):
        print("[注意] 创意工坊目录不存在：%s" % WS_DIR)

    steam, local, raw = read_active_mods(args.game_json)
    everything = args.all or not (steam or local)
    if everything and not args.all:
        print("[提示] 没读到启用列表，改为扫描全部 mod（结果仍然可用，只是包更大）。")
    else:
        print("[启用] Steam mod %d 个、本地 mod %d 个" % (len(steam), len(local)))

    t0 = time.time()
    files = collect_files(args.game, steam, local, everything, order=raw)
    print("[扫描] 字符串表 %d 个（只含启用的 mod）" % len(files))
    zh, en = build(files, args.lang)

    roots = asset_roots(args.game, steam, local, everything, order=raw)
    heroes = build_hero_kb(zh, en, roots)
    monsters = build_monster_kb(zh, en, roots)
    print("[结果] 中文 %d 条，英文兜底 %d 条，英雄职业 %d 个，敌人 %d 个，用时 %.1fs"
          % (len(zh), len(en), len(heroes), len(monsters), time.time() - t0))

    os.makedirs(args.pack, exist_ok=True)
    p_loc = os.path.join(args.pack, "loc_zh.json")
    p_hero = os.path.join(args.pack, "heroes.json")
    p_mon = os.path.join(args.pack, "monsters.json")
    json.dump(zh, open(p_loc, "w", encoding="utf-8"), ensure_ascii=False)
    json.dump(heroes, open(p_hero, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    json.dump(monsters, open(p_mon, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    # 旧版产物：技能+台词拼的"职业卡"，运行时从未用过，留着只会误导
    try:
        os.remove(os.path.join(args.pack, "class_dossier.json"))
    except OSError:
        pass

    manifest = {
        "built_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "host": host_id(args.game),
        "game_dir": os.path.abspath(args.game),
        "fingerprint": fingerprint(steam, local, everything),
        "scope": "all" if everything else "active",
        "mod_count": 0 if everything else len(steam | local),
        "mods": [] if everything else sorted(steam | local),
        "tables": len(files),
        "entries_zh": len(zh),
        "schema": 4,
        "heroes": len(heroes),
        "monsters": len(monsters),
    }
    json.dump(manifest, open(os.path.join(args.pack, "manifest.json"), "w", encoding="utf-8"),
              ensure_ascii=False, indent=1)

    p_world = os.path.join(args.pack, "world_bible.md")
    if not os.path.exists(p_world):
        try:
            from prompts import WORLD_TEMPLATE as _wt
        except Exception:
            _wt = ("# 世界设定（玩家自填）\n\n"
                   "在这里写你自己的世界：人物来历、彼此关系、哪些名字容易被误解、"
                   "希望旁白遵守或避开的规矩。\n")
        open(p_world, "w", encoding="utf-8").write(_wt)

    print("[输出] %s  (%.1f MB)" % (p_loc, os.path.getsize(p_loc) / 1048576))
    print("[输出] %s  英雄 %d 个" % (p_hero, len(heroes)))
    print("[输出] %s  敌人 %d 个" % (p_mon, len(monsters)))
    tn, te = write_trinkets(args.pack, args.game, zh, raw)
    print("[输出] %s/trinkets.json  饰品 %d 个（其中 %d 个带效果文本）" % (args.pack, tn, te))
    dn = write_diseases(args.pack, args.game, raw)
    print("[输出] %s/diseases.json  疾病 %d 条" % (args.pack, dn))
    ks = write_knowledge(args.pack, zh, heroes, monsters)
    print("[输出] %s/knowledge/  英雄%d 敌人%d 物品%d"
          % (args.pack, ks["heroes"], ks["monsters"], ks["items"]))
    print("[输出] %s" % os.path.join(args.pack, "manifest.json"))
    print("[输出] %s" % p_world)


if __name__ == "__main__":
    main()

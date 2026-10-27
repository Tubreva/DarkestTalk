#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
dd_scribe.py —— 暗黑地牢「实时旁白」数据层 v2

设计原则
  1. 只读，不注入、不改游戏文件。稳定性优先。
  2. 敌我识别用 (guid, 职业) 配对，不用屏幕坐标 —— 布局会变、mod 会换。
  3. 一切内部 id 都经本地化字典翻译成中文，绝不让 LLM 看到原始键。
  4. 三种场景分开处理：战斗回合 / 地图区域 / 关卡结算。

场景粒度
  · combat_round : 一个「回合」= 场上所有单位都行动完毕（bs_start_round 为界），
                   每次输出最近 N 个回合（默认 2），可配置每 M 回合出一次（默认 1）。
  · map_area     : 一个「区域」= 一间房间或一条走廊（move to area 为界），
                   统计走过的格子、拾取、陷阱、火把、扎营等。
  · settlement   : 关卡结算的三个界面（战果 / 带回物品 / 城镇事件）合并为一次输出。

用法
  python dd_scribe.py --log <app.log> --roster <roster.json> --out out
  python dd_scribe.py ... --follow            实时跟随
  python dd_scribe.py ... --follow --llm      实时跟随 + 调模型出文
  python dd_scribe.py ... --emit-every 1 --window 2
"""
import argparse
import json
import os
import re
import sys
import subprocess
import time
import traceback
from collections import Counter, deque
from datetime import datetime

HERE = os.path.dirname(os.path.abspath(__file__))
HERE_DIR = HERE
# 玩家数据（config / 译名库 / 日志 / 解码产物）都在数据目录里，见 datadir.py
try:
    import datadir
    DATA = datadir.get()
except Exception:
    DATA = HERE
# 不写死任何本机路径：默认留空，由 paths.py 探测（或 paths.json / 命令行指定）。
DEFAULT_SAVE = ""
DEFAULT_GAME = ""
# 有 paths.py 就用它探测的结果（换机器/换盘符也能跑）
try:
    import paths as _paths
    _p = _paths.resolve()
    if _p.get("game"):
        DEFAULT_GAME = _p["game"]
    if _p.get("save"):
        DEFAULT_SAVE = _p["save"]
except Exception:
    pass

# ------------------------------------------------------------------ 存档解码
try:
    import savedec as _savedec
except Exception:
    _savedec = None


def decode_save(jar, src, dst, names=None, timeout=40):
    """解码一份存档并返回 dict（失败返回 None）。

    走 savedec：它会把中文路径的存档搬到 ASCII 临时目录再交给 java，
    否则放在中文目录里的工具在 Java 8 下永远拿不到文件。
    """
    if _savedec is None:
        return None
    try:
        return _savedec.load(jar, src, dst, names=names, timeout=timeout)
    except Exception:
        return None


# ------------------------------------------------------------------ 正则
RE_TS = re.compile(r"^\[(\d{4}-\d\d-\d\d \d\d:\d\d:\d\d)\]")
RE_STATE = re.compile(r"Battle: state change to (bs_[a-z_0-9]+)")
RE_ACTOR = re.compile(r"check pCurrentBattleActor #1:\s*(.+?)\s+\d+\s*$")
RE_CRIT = re.compile(r"'(.+?)' landed a crit")
RE_NARR = re.compile(r"Narration: entry type: (\w+)(?: audio event type: (\S+))?")
RE_RERANK = re.compile(r"\[CB\] RERANK-POS guid=(\d+) \((\S+?)\) spacingIndex=\d+ rank=(\S+) pos=(\d+)")
RE_LOOT = re.compile(r"Loot: Rolled ItemEntry:(\d+) (\S+) in LootTable:(\S+)")
RE_CHANCE = re.compile(r"Chance to hit:\s*([\d.]+)")
RE_ROLL = re.compile(r"Roll to hit:\s*([\d.]+)")
RE_AREA = re.compile(r"move to area '([^']+)', tile (\d+)")
RE_TILE = re.compile(r"Moved onto areatile (\d+)")
RE_QUEST_REWARD = re.compile(r"Quest (\S+) Rewards: (\w+), (\d+)")
# 死亡：
#   Actor with GUID 441 and name 莉法 : 莉薇娅 death start 14136
#   Actor with GUID 441 and name 莉法 death finish
# 敌方单位也会打这一行，所以拿到 GUID 后要去名册里认领。
RE_DEATH = re.compile(r"Actor with GUID (\d+) and name (.+?)(?:\s*:\s*(.+?))?\s+death start")
# 特质：折磨/美德会打一行带名字的，这是唯一能拿到"是谁崩溃/觉醒"的地方
#   雨宫优子: I have aquired the trait: Scourge_Affliction
#   兰博库尔: I have aquired the trait: EosHitomi_V
RE_TRAIT = re.compile(r"([^\[\]]+?): I have aquired the trait: (\S+)")
# 任务语音：/vo/neutral/weald_cleanse、/vo/good/weald_success_cleanse_01
# 这是唯一能在"中途"就知道当前地牢与任务类型的来源（存档里那条记录要下周才写）
RE_VO = re.compile(r"/vo/[a-z_]+/([A-Za-z_0-9]+)")

# 语音里出现的地牢段名（与本地化键 dungeon_name_<id> 对齐）
VO_DUNGEONS = ("weald", "warrens", "crypts", "cove", "farm", "town",
               "dungeon", "darkest", "manor", "tower")
# 语音里的任务段名。探索类在农庄叫 explore_farm
VO_QUESTS = ("cleanse", "explore_farm", "explore", "gather", "kill_boss",
             "inventory_activate", "wave")

NOISE = ("Random uniform", "Random int range", "Random float", "Get seed",
         "colour-markup decode", "Couldn't find bone", "RemoveGameCommandHandler",
         "RemoveConnectionStateChangedHandler", "cursor (event.edit",
         "UnpauseBurndownTime")

NARR_CN = {
    "quest_start": "启程", "quest_end_completed": "任务结束", "victory": "战斗结束",
    "kill_monster": "击杀", "crit_monster": "暴击", "crit_hero": "被暴击",
    "heal": "治疗", "buff": "增益", "debuff": "减益",
    "stress_dot_applied": "压力侵蚀", "afflicted": "精神崩溃", "virtue": "显现美德",
    "deaths_door": "濒死", "death": "阵亡", "loot": "拾取", "trap": "陷阱",
    "torchlight_out": "火把熄灭", "torchlight_full": "火把炽盛",
    "camp": "扎营", "recruit_hero": "招募", "town_visit_start": "返回城镇",
    "upgrade_building": "升级建筑", "enter_building": "进入建筑",
    "battle_retreat_fail": "撤退失败",
}


def first(v):
    return v[0] if isinstance(v, list) else (v or "")


BAD_NAMES = {"nullptr", "None", "null", "", "None_", "###"}
CORPSE_WORDS = ("残骸", "尸骸", "尸体", "骸骨堆")


def is_corpse(cls):
    """尸体单位：类名含 corpse（含 corpse_sword 这类 mod 变体）。"""
    return "corpse" in (cls or "").lower()


def safe_name(n):
    # mod 的单位名偶尔是数字 id，这里统一按字符串处理（不然 .strip() 会抛）
    n = n if isinstance(n, str) else ("" if n is None else str(n))
    return "无名之影" if n.strip() in BAD_NAMES else n


def _txt(v):
    """把可能不是字符串的字段转成字符串。

    存档里 mod 的地牢 / 任务 id 有时是整数（如 dungeon_type=17418668、
    quest=657224992），直接塞进 " · ".join() 会抛
    `TypeError: sequence item 0: expected str instance, int found` ——
    整个进程就崩在出文那一行。所有拼字符串的地方都先过这一道。
    """
    if v is None:
        return ""
    return v if isinstance(v, str) else str(v)


def _nice_id_name(v):
    """id 查不到名字时的兜底：`###warrens` 这类还能当名字用，
    纯数字的 mod 哈希 id 对人没有意义，宁可空着（不编、也不拿数字凑）。"""
    v = _txt(v)
    return "" if v.isdigit() else v


MARKUP_RE = re.compile(r"\{[^{}]*\}")      # {colour_start|xxx}、{?hero_names}
PLACEHOLDER_RE = re.compile(r"[¤¦]+")        # 目标位置占位符


def clean_markup(s):
    """去掉游戏文本里的富文本标记：{colour_start|MaiGreen}、{colour_end}、{?hero_names} 等。"""
    if not s:
        return s
    return PLACEHOLDER_RE.sub("", MARKUP_RE.sub("", s)).strip()


def fix_mojibake(s):
    """部分 mod 的本地化文件编码存错，会出现「鐤戞儜」这类乱码，尝试救回。"""
    if not s:
        return s
    marker = set("鐤戞儜鍓嶆柟浣犱滑鎴戜竴")
    if sum(1 for ch in s if ch in marker) >= 2:
        try:
            return s.encode("gbk", errors="strict").decode("utf-8", errors="strict")
        except Exception:
            return s
    return s


# ------------------------------------------------------------------ 本地化
class Loc:
    """内部 id → 玩家看到的文字。"""

    def __init__(self, path):
        self.zh = {}
        if path and os.path.exists(path):
            self.zh = json.load(open(path, encoding="utf-8"))
        self._cache = {}
        self.observed = set()      # 本局日志里真实出现过的显示名
        self.trinkets = {}
        self.heroes_kb = {}        # 职业 id → 名称 + 公会/铁匠铺/营地介绍
        self.monsters_kb = {}      # 敌人 id → 名称 + 招式
        if path:
            base = os.path.dirname(path)
            tp = os.path.join(base, "trinkets.json")
            if os.path.exists(tp):
                try:
                    self.trinkets = json.load(open(tp, encoding="utf-8"))
                except Exception:
                    self.trinkets = {}
            for fn, attr in (("heroes.json", "heroes_kb"),
                             ("monsters.json", "monsters_kb")):
                fp = os.path.join(base, fn)
                if os.path.exists(fp):
                    try:
                        setattr(self, attr,
                                json.load(open(fp, encoding="utf-8")) or {})
                    except Exception:
                        setattr(self, attr, {})
        self.ov = {}
        ovp = os.path.join(os.path.dirname(path), "overrides.json") if path else ""
        if ovp and os.path.exists(ovp):
            try:
                self.ov = {k: v for k, v in json.load(open(ovp, encoding="utf-8")).items()
                           if isinstance(v, (dict, str))}
            except Exception:
                self.ov = {}

    def candidates(self, key):
        v = self.zh.get(key)
        if not v:
            return []
        return list(v) if isinstance(v, list) else [v]

    def pick(self, key, prefer=None):
        """同名多版本时：优先取日志里出现过的那个，否则取最后加载的。"""
        cs = self.candidates(key)
        if not cs:
            return None
        pool = prefer if prefer is not None else self.observed
        for c in cs:
            if c in pool:
                return clean_markup(c)
        return clean_markup(cs[-1])

    def ov_name(self, *ids):
        for i in ids:
            if not i:
                continue
            v = self.ov.get(i)
            if isinstance(v, str):
                return v
            if isinstance(v, dict) and v.get("name"):
                return v["name"]
        return None

    def ov_desc(self, *ids):
        for i in ids:
            v = self.ov.get(i)
            if isinstance(v, dict) and v.get("desc"):
                return v["desc"]
        return None

    def get(self, key, default=None):
        if key in self._cache:
            return self._cache[key]
        v = self.zh.get(key)
        v = clean_markup(fix_mojibake(first(v))) if v else None
        self._cache[key] = v if v is not None else default
        return self._cache[key]

    def hero_class(self, cls):
        return (self.pick("hero_class_name_" + cls)
                or fix_mojibake(first(self.zh.get(cls))) or cls)

    def class_intro(self, cls, kind=""):
        """这个职业在界面上的那段介绍。

        kind == "camp" 时用营地那段（营火边的行为），其余用公会那段（战斗定位）。
        铁匠铺那段讲武器护甲，只在公会缺失时兜底。
        """
        e = self.heroes_kb.get(cls or "")
        if not isinstance(e, dict):
            return ""
        if kind == "camp" and e.get("camping"):
            return clean_markup(e["camping"])
        return clean_markup(e.get("guild") or e.get("camping")
                            or e.get("blacksmith") or "")

    def monster_skills(self, cls):
        """这个敌人的出手手段（技能名）。"""
        if not cls:
            return []
        e = self.monsters_kb.get(cls)
        if not isinstance(e, dict):
            e = self.monsters_kb.get(re.sub(r"_[A-Za-z]$", "", cls)) or {}
        out = []
        for sk in (e.get("skills") or []):
            nm = sk.get("name") if isinstance(sk, dict) else ""
            if nm and nm not in out:
                out.append(nm)
        return out

    def monster_skills_by_name(self, name):
        """按显示名找招式：日志里的 id 与库里的 id 对不上时兜底。"""
        if not name:
            return []
        if not getattr(self, "_mob_by_name", None):
            idx = {}
            for _mid, e in (self.monsters_kb or {}).items():
                nm = (e or {}).get("name")
                if nm and nm not in idx:
                    idx[nm] = e
            self._mob_by_name = idx
        e = self._mob_by_name.get(name) or {}
        out = []
        for sk in (e.get("skills") or []):
            nm = sk.get("name") if isinstance(sk, dict) else ""
            if nm and nm not in out:
                out.append(nm)
        return out

    def monster(self, cls):
        """怪物类 id → 界面显示名。优先精确匹配，再退到去掉 _A/_B/_C 后缀。"""
        for k in (cls, re.sub(r"_[A-Z]$", "", cls)):
            v = self.pick("str_monstername_" + k)
            if v:
                return v
        return self.pick("hero_class_name_" + cls) or re.sub(r"_[A-Z]$", "", cls)

    def affliction(self, aid):
        return (self.pick("str_affliction_name_" + aid) or aid) if aid else ""

    def virtue(self, vid):
        """美德 id → 中文。查不到就返回空，不要拿 id 冒充名字。"""
        return (self.pick("str_virtued_" + vid) or "") if vid else ""

    def affliction_desc(self, aid):
        return self.pick("str_affliction_description_" + aid) or ""

    def quirk(self, qid):
        return self.ov_name(qid) or self.pick("str_quirk_name_" + qid) or qid

    def quirk_desc(self, qid):
        return self.pick("str_quirk_description_" + qid) or ""

    def item(self, iid):
        base = iid.replace("-", "").replace("_", "")
        ov = self.ov_name(iid, base)
        if ov:
            return ov
        return (self.pick("str_inventory_title_" + base)
                or self.pick("str_inventory_title_" + iid.replace("-", ""))
                or iid)

    def item_desc(self, iid):
        base = iid.replace("-", "").replace("_", "")
        ov = self.ov_desc(iid, base)
        if ov:
            return ov
        return (self.pick("str_inventory_description_" + base)
                or self.pick("str_inventory_description_" + iid.replace("-", ""))
                or "")

    def curio(self, cid):
        """奇物 id → 界面显示名（书堆 / 光明祭坛 …）。"""
        if not cid:
            return ""
        return (self.ov_name(cid) or self.pick("str_curio_title_" + cid)
                or self.pick("curio_" + cid) or cid)

    def curio_content(self, cid):
        """奇物的调查文本（那句 flavour text）。"""
        if not cid:
            return ""
        return clean_markup(self.pick("str_curio_content_" + cid) or "")

    def trinket(self, tid):
        """饰品 id → 界面显示名。mod 饰品的键是 str_inventory_title_trinket<id>，
        本体是 str_inventory_title_<id>，两种都要试。"""
        if not tid:
            return ""
        ov = self.ov_name(tid)
        if ov:
            return ov
        bare = tid.replace("-", "").replace("_", "")
        return (self.pick("str_inventory_title_trinket" + tid)
                or self.pick("str_inventory_title_" + tid)
                or self.pick("str_inventory_title_trinket" + bare)
                or self.pick("str_inventory_title_" + bare)
                or tid)

    def trinket_effects(self, tid):
        """饰品效果（由 buff 数据 + 本地化模板还原，与游戏 tooltip 同源）。"""
        d = self.trinkets.get(tid) or {}
        eff = [clean_markup(e) for e in (d.get("effects") or [])]
        return [e for e in eff if e][:4]

    def trinket_tip(self, tid):
        """饰品的效果文本。本体没有，部分 mod 写成 <id>_tooltip。"""
        if not tid:
            return ""
        return (self.pick(tid + "_tooltip")
                or self.pick("str_inventory_title_trinket" + tid + "_tooltip") or "")

    def skill(self, cls, sid):
        return (self.pick("combat_skill_name_%s_%s" % (cls, sid))
                or self.pick("combat_skill_name_" + sid) or sid)

    def barks(self, cls, tag):
        v = self.zh.get("%s+str_bark_%s" % (cls, tag))
        if not v:
            return []
        vs = v if isinstance(v, list) else [v]
        return [fix_mojibake(x) for x in vs]


# ------------------------------------------------------------------ 名册
class Roster:
    def __init__(self, path):
        self.path = path
        self.mtime = 0
        self.heroes, self.pairs, self.by_name = {}, set(), {}
        self.last_party = []
        if path and os.path.exists(path):
            self.reload(force=True)

    def reload(self, force=False):
        if not self.path or not os.path.exists(self.path):
            return False
        m = os.path.getmtime(self.path)
        if not force and m == self.mtime:
            return False
        self.mtime = m
        try:
            d = json.load(open(self.path, encoding="utf-8"))
        except Exception:
            return False
        heroes, pairs, by_name = {}, set(), {}
        root = d.get("base_root", {})
        for guid, h in (root.get("heroes") or {}).items():
            try:
                r = h["hero_file_data"]["raw_data"]["base_root"]
            except Exception:
                continue
            a = r.get("actor", {})
            # roster.status：0=在镇上、1=正在出击、3=阵亡（墓园）。
            # 注意**不能**用血量判断死活：回城后死者的 current_hp 会被写成 1.0，
            # 跟活人一样；-1 只在他刚倒下、还没结算的那一小段时间里出现。
            try:
                status = int(r.get("roster.status") or 0)
            except Exception:
                status = 0
            tri = []
            for _, it in (((r.get("trinkets") or {}).get("items")) or {}).items():
                if isinstance(it, dict) and it.get("id"):
                    tri.append(it["id"])
            info = {
                "guid": guid, "name": a.get("name", ""), "class": r.get("heroClass", ""),
                "trinkets": tri, "status": status, "dead": (status == 3),
                "hp": a.get("current_hp", 0), "stress": round(r.get("m_Stress", 0)),
                "affliction": r.get("affliction_type_id") or "",
                "virtue": r.get("virtue_type_id") or "",
                "quirks": list((r.get("quirks") or {}).keys()),
                "kills": r.get("enemies_killed", 0),
                "deaths_door": bool(r.get("visited_deaths_door")),
            }
            heroes[guid] = info
            pairs.add((guid, info["class"]))
            if info["name"]:
                by_name[info["name"]] = info
        self.heroes, self.pairs, self.by_name = heroes, pairs, by_name
        # 最近一次出击选的队伍（进副本前后都可能还留着），作为补队伍的备用来源
        lp = (root.get("last_party") or {}).get("last_party_guids") or []
        self.last_party = [str(g) for g in lp]
        return True

    def is_hero(self, guid, cls):
        return (str(guid), cls) in self.pairs



# ------------------------------------------------------------------ 战役日志
class Campaign:
    """解码后的 persist.campaign_log.json → 每一趟的战果。"""

    DIFF = {0: "学徒", 1: "学徒", 2: "老练", 3: "老练", 4: "冠军", 5: "冠军", 6: "极暗"}
    # 同类型任务在同一地牢里有多个变体时，只报类型，不猜具体名字
    QUEST_KIND = {"gather": "采集遗物", "explore": "侦察", "cleanse": "清剿",
                  "kill_boss": "讨伐首领", "inventory_activate": "取回物品",
                  "explore_farm": "侦察", "wave": "无尽波次"}
    LEN = {1: "短", 2: "中", 3: "长", 4: "无尽"}

    def __init__(self, path, loc, loader=None):
        self.path, self.loc, self.loader = path, loc, loader
        self.data = {}
        self._loaded_at = 0.0
        if loader is None and path and os.path.exists(path):
            try:
                self.data = json.load(open(path, encoding="utf-8"))
            except Exception:
                self.data = {}

    def refresh(self, force=False):
        """用 loader 现取最新存档；3 秒节流，避免每回合都开一次 java。"""
        if not self.loader:
            return
        now = time.time()
        if not force and now - self._loaded_at < 3:
            return
        self._loaded_at = now
        try:
            d = self.loader() or {}
            if d:
                self.data = d
        except Exception:
            pass

    def chapters(self):
        return (self.data.get("base_root") or {}).get("chapters") or {}

    def latest_chapter(self):
        ks = list(self.chapters())
        if not ks:
            return None
        return max(ks, key=lambda x: int(x) if str(x).isdigit() else 0)

    def town_event(self, chapter=None):
        """某一周触发的城镇事件（rtti 844919810）。"""
        self.refresh()
        chapters = self.chapters()
        ch = chapters.get(chapter) if chapter else None
        if ch is None:
            ch = chapters.get(self.latest_chapter()) or {}
        eid = None
        for k, v in ch.items():
            if isinstance(v, dict) and v.get("rtti") == 844919810:
                eid = v.get("town_event_result_id")
        eid = self._clean(eid) if isinstance(eid, str) else eid
        if not isinstance(eid, str) or not eid:
            return {}                     # mod 事件是哈希，解析不了就交给文件兜底
        return {"id": eid,
                "title": self.loc.pick("town_event_title_" + eid) or "",
                "desc": self.loc.pick("town_event_description_" + eid) or ""}

    @staticmethod
    def _clean(v):
        return v[3:] if isinstance(v, str) and v.startswith("###") else v

    @staticmethod
    def _meta(loc, rec, chapter=None):
        """一条任务记录 → 给人看的元信息（地牢 / 任务 / 难度 / 长度）。"""
        if not rec:
            return {}
        # mod 的地牢 / 任务 id 可能是整数（17418668、657224992 这类哈希），
        # 统一成字符串：拼标题、查本地化、写 raid_state 都不会再炸。
        dtype = _txt(Campaign._clean(rec.get("dungeon_type")))
        quest = _txt(Campaign._clean(rec.get("quest")))
        try:
            diff = int(rec.get("difficulty", 0))
        except Exception:
            diff = 0
        qname = ""
        cands = [k for k in loc.zh
                 if k.startswith("town_quest_name_%s+" % quest)
                 and ("+%s+" % dtype) in k
                 and ("+%s+" % (diff + 1) in k or "+%s+" % diff in k)]
        if len(cands) == 1:            # 只有一个候选才敢用，多了会挑错任务
            qname = first(loc.zh[cands[0]])
        if not qname:
            qname = Campaign.QUEST_KIND.get(quest, _nice_id_name(quest))
        return {"chapter": chapter,
                "party": [h.get("name") for h in (rec.get("heroes") or {}).values()
                          if h.get("name")],
                "dungeon_id": dtype,
                "quest_id": quest,
                "dungeon": loc.get("dungeon_name_" + dtype) or _nice_id_name(dtype),
                "quest": qname or _nice_id_name(quest),
                "difficulty": Campaign.DIFF.get(diff, str(diff)),
                "length": Campaign.LEN.get(rec.get("length"), "")}

    def current_quest(self, dungeon_id="", quest_id=""):
        """这一趟**正在打**的任务（记录里 start=True 的那条）。

        给了 `dungeon_id` / `quest_id` 就只认对得上的记录：出发那一刻新任务
        可能还没写进存档，这时宁可返回空，也不能把**上一趟**的队员当成本趟的。
        

        存档里的任务结果要等下周才写（第 N 周出发、第 N+1 周才落结果），
        所以工具在副本中途读存档时，`latest_quest()` 拿到的是**上一趟**。
        当前这趟的地牢/任务只能看 start=True 那条，否则就会把上周的
        地牢名、难度、阵亡名单安到这一趟头上。
        """
        self.refresh()
        chapters = self.chapters()
        order = sorted(chapters, key=lambda x: int(x) if str(x).isdigit() else 0)
        best, best_ci = None, None
        for ci in order:
            for k, v in chapters[ci].items():
                if not (isinstance(v, dict) and "success" in v
                        and v.get("start") is True):
                    continue
                if dungeon_id and self._clean(v.get("dungeon_type")) != dungeon_id:
                    continue
                if quest_id and self._clean(v.get("quest")) != quest_id:
                    continue
                best, best_ci = v, ci
        return self._meta(self.loc, best, best_ci)

    def finished_record(self, dungeon_id, quest_id, names=()):
        """找这一趟**刚打完**的记录：地牢/任务对得上，人也要对得上。

        匹配不上就返回空——宁可这一趟不报难度和阵亡名单，也不能把
        上周那趟的战果搬过来。结果记录要下周才写，通常这里都是空。
        """
        self.refresh()
        chapters = self.chapters()
        order = sorted(chapters, key=lambda x: int(x) if str(x).isdigit() else 0)
        want = set(n for n in (names or []) if n)
        best = None
        for ci in order:
            for k, v in chapters[ci].items():
                if not (isinstance(v, dict) and "success" in v
                        and v.get("start") is False):
                    continue
                if dungeon_id and self._clean(v.get("dungeon_type")) != dungeon_id:
                    continue
                if quest_id and self._clean(v.get("quest")) != quest_id:
                    continue
                got = set(h.get("name") for h in (v.get("heroes") or {}).values()
                          if h.get("name"))
                if want and got and not (want & got):
                    continue
                best = v
        return best or {}

    def latest_quest(self):
        self.refresh()
        chapters = self.chapters()
        order = sorted(chapters, key=lambda x: int(x) if str(x).isdigit() else 0)
        best, best_ci = None, None
        for ci in order:                 # 从旧到新，最后一个命中的就是最新
            for k, v in chapters[ci].items():
                if isinstance(v, dict) and "success" in v and v.get("start") is False:
                    best, best_ci = v, ci
        if not best:
            return {}
        dtype = self._clean(best.get("dungeon_type"))
        quest = self._clean(best.get("quest"))
        diff = best.get("difficulty", 0)
        # 任务名：town_quest_name_<quest>+<难度>+<地牢>+<变体>
        # 存档里的 difficulty 是 0 起，键名是 1 起，所以两个都试
        qname = ""
        cands = [k for k in self.loc.zh
                 if k.startswith("town_quest_name_%s+" % quest)
                 and ("+%s+" % dtype) in k
                 and ("+%s+" % (diff + 1) in k or "+%s+" % diff in k)]
        if len(cands) == 1:            # 只有一个候选才敢用，多了会挑错任务
            qname = first(self.loc.zh[cands[0]])
        if not qname:
            qname = self.QUEST_KIND.get(quest, quest)
        return {
            "chapter": best_ci,
            "dungeon": self.loc.get("dungeon_name_" + str(dtype)) or dtype,
            "quest": qname or quest,
            "difficulty": self.DIFF.get(diff, str(diff)),
            "length": self.LEN.get(best.get("length"), ""),
            "success": bool(best.get("success")),
            "party": [h.get("name") for h in (best.get("heroes") or {}).values()],
            "deaths": [h.get("name") for h in (best.get("heroes") or {}).values()
                       if h.get("died")],
        }




def load_json_tolerant(path):
    """读取 JSON，容忍手写配置常见的尾逗号。"""
    raw = open(path, encoding="utf-8").read()
    try:
        return json.loads(raw)
    except Exception:
        fixed = re.sub(r",\s*([}\]])", r"\1", raw)
        try:
            data = json.loads(fixed)
            print("[提示] %s 有多余的逗号，已自动忽略（建议改掉）。" % os.path.basename(path))
            return data
        except Exception as e:
            print("[配置错误] %s 不是合法 JSON：%s" % (path, e))
            raise


# ------------------------------------------------------------------ 解析器
class Scribe:
    def __init__(self, loc, roster, window=2, emit_every=1, min_rounds=1, salience=0.0):
        self.loc, self.roster = loc, roster
        self.window, self.emit_every, self.min_rounds = window, emit_every, min_rounds
        self.salience = salience
        self.cfg = None           # 由 main 注入；改配置后无需重启即可生效

        self.raid = None
        self.mode = "idle"        # idle / explore / combat
        self.area = None
        # 上一次明确"在镇上"：用来在没有 quest_start 的情况下认出"出发"这一刻
        self._at_town = False
        self.last_area_kind = ""  # 刚离开的那段是房间还是走廊（战斗就发生在那儿）
        self.battle = None
        self.round = None
        self.actor = None
        self.action = None
        self.pending_chance = None
        self.scene_queue = []
        self.heroes_in_raid = {}
        self.event_count = 0
        # 当前场面快照：每次 RERANK 批量刷新，用来判断"此刻场上还有谁"
        # guid -> {"cls": 职业, "pos": 1..4, "rank": "0x8"}
        # pos 就是界面上的号位（1 最前），尸体也带，所以"谁在几号位"能写出来
        self.snapshot = {"hero": {}, "enemy": {}, "corpse": {}}
        self._rank_ts = None
        self.camp = None          # 正在进行的扎营
        self.last_ts = ""
        self.refresh_fn = None    # 由 main 注入：解码存档并刷新名册
        self.seed_fn = None       # 由 main 注入：从存档补这一趟的队伍
        self.town_event_fn = None # 由 main 注入：读取 persist.town_event.json
        self.town_log_fn = None   # 由 main 注入：读取 persist.campaign_log.json
        self.curio_watch = None   # 由 main 注入：奇物互动探测器
        self.state_path = None
        self.diseases = set()
        self.dead_guids = set()   # 本次运行里已经播报过阵亡的 guid（防止重复）
        self.known_dead = set()   # 启动时就在墓园里的 guid：他们"活过来"要单独播报
        self._death_at = {}       # guid -> 看到死亡行的本地时间，防"用死前名册判复活"
        self.death_log = []       # [{guid,name,class,t}]，结算时兜底用
        # 瞬时事件（濒死 / 崩溃 / 美德）：报一次就记在这里，后面不再重复
        self.pending_effects = []
        self._seen_effects = set()
        self._curio_done = set()  # 已经播报过的奇物记录键
        self._last_save = 0.0
        self._pack_sig = None      # 队伍档案的缓存键（队员/装备/怪癖）
        self._pack_text = ""
        self._ctx_prev = {}        # 上一次的队伍快照，用来算变化
        self.pending_changes = []  # 待播报的「刚刚的变化」
        # 这一趟刚打完时记一份"我们自己的归来记录"：回城那一幕用它，
        # 存档里还没写结果时就不会把上一周的归来文案当成这一趟的。
        self._pending_return = None
        # 刚从墓园回来的人：不单独出一拍，等回城那一拍接在镇上事件后面
        self._revived = []
        self._quest_start_ts = 0.0   # 进副本那一刻（用来判断 raid.json 是不是这一趟的）
        # 一场战斗里的 RERANK 全场刷新＝"这趟到底有谁"的权威答案，攒起来一次替换
        self._sync_guids = []
        self._sync_on = False

    # ---------------- 工具
    def hero_display(self, cls):
        cands = [h for (g, c), h in self.heroes_in_raid.items() if c == cls]
        if len(cands) == 1:
            return cands[0]["name"] or self.loc.hero_class(cls)
        return self.loc.hero_class(cls)

    def actor_label(self, raw):
        """判定行动者属于哪一边。

        不靠猜名字的"气质"，也不靠屏幕坐标，而是：
          · 本战我方职业集合（由 RERANK 的 guid+职业 与存档名册配对得出）
          · 本战敌方职业集合（同上）
          · 每个职业的**全部**候选显示名（mod 覆盖会让同一职业有多个名字）
        名字落在哪一边的候选集里，就是哪一边的人。
        """
        loc = self.loc
        hero_c = []
        for (g, c) in self.heroes_in_raid:
            if c == raw or raw in loc.candidates("hero_class_name_" + c):
                hero_c.append(c)
        if hero_c:
            return self.hero_display(hero_c[0]), "hero", hero_c[0]

        enemy_c = []
        for c in (self.battle or {}).get("enemy_classes", ()):  # noqa
            if raw in loc.candidates("str_monstername_" + c):
                enemy_c.append(c)
        if enemy_c:
            return raw, "enemy", enemy_c[0]

        # 兜底：可能是 mod 英雄的别名，或没有任何本地化条目的怪
        for (g, c), h in self.heroes_in_raid.items():
            if h.get("name") == raw:
                return h["name"], "hero", c
        return raw, "unknown", None

    def living_enemies(self):
        loc = self.loc
        names = [loc.monster(v["cls"]) if isinstance(v, dict) else loc.monster(v)
                 for v in self.snapshot["enemy"].values()]
        names = [safe_name(n) for n in names]
        out = []
        for n in names:                       # 去重保序
            if n not in out:
                out.append(n)
        if not out and self.battle is not None:
            out = sorted(safe_name(x) for x in (self.battle["enemies"] or
                                                self.battle["fallback_enemies"]))
        return out

    def field_rows(self):
        """场上单位与站位，分三行：我方 / 敌方 / 尸体。

        站位来自日志的 RERANK-POS（pos=1 是最前排），每方各自从 1 数。
        尸体也是真实单位：占格子、挡走位，所以单独一行并注明不是活敌。
        """
        def rows(bucket, translate):
            items = []
            for guid, info in self.snapshot[bucket].items():
                if not isinstance(info, dict):
                    items.append((99, translate(guid, info)))
                else:
                    items.append((info.get("pos") or 99, translate(guid, info["cls"])))
            items.sort(key=lambda x: (x[0], x[1]))
            return items

        def hero_name(guid, cls):
            h = (self.roster.heroes or {}).get(str(guid))
            return (h or {}).get("name") or self.hero_display(cls)

        def foe_name(guid, cls):
            return safe_name(self.loc.monster(cls) or cls)

        def corpse_name(guid, cls):
            return safe_name(self.loc.monster(cls) or "尸体")

        out = {}
        for key, bucket, fn in (("hero", "hero", hero_name),
                                ("enemy", "enemy", foe_name),
                                ("corpse", "corpse", corpse_name)):
            out[key] = ["%d 号位 %s" % (p, n) if p != 99 else n
                        for p, n in rows(bucket, fn)]
        return out

    # ---------------- 副本状态（跨重启）
    def ensure_raid(self, ts=""):
        if self.raid is not None:
            return self.raid
        st = None
        if self.state_path and os.path.exists(self.state_path):
            try:
                if time.time() - os.path.getmtime(self.state_path) < 12 * 3600:
                    st = json.load(open(self.state_path, encoding="utf-8"))
            except Exception:
                st = None
        if st and st.get("start"):
            self.raid = st
            self.raid.setdefault("loot", [])
            self.raid.setdefault("events", [])
            self.raid["resumed"] = True
            print("[续接] 读回上一段副本进度（出发于 %s）" % st["start"])
        else:
            self.event_count += 1
            self.raid = {"id": self.event_count, "start": ts, "loot": [], "events": [],
                         "party_guids": [],
                         "baseline": {g: {"name": h.get("name"), "hp": h.get("hp"),
                                          "stress": h.get("stress"),
                                          # 出发那一刻是否已经阵亡：结算只认"由活变死"
                                          "dead": bool(h.get("dead")),
                                          "quirks": list(h.get("quirks") or [])}
                                      for g, h in self.roster.heroes.items()},
                         "resumed": False}
            self.save_raid(force=True)
        return self.raid

    def save_raid(self, force=False):
        if not self.state_path or not self.raid:
            return
        now = time.time()
        if not force and now - self._last_save < 3:
            return
        self._last_save = now
        try:
            self.raid["saved_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            json.dump(self.raid, open(self.state_path, "w", encoding="utf-8"),
                      ensure_ascii=False)
        except Exception:
            pass

    def refresh(self):
        if self.refresh_fn:
            try:
                self.refresh_fn()
                self.roster.reload()
            except Exception:
                pass

    def hero_metrics(self):
        return {g: {"name": h.get("name"), "hp": h.get("hp"), "stress": h.get("stress")}
                for g, h in self.roster.heroes.items()}

    def close_camp(self, ts):
        c = self.camp
        self.camp = None
        if not c:
            return
        self.refresh()                     # 再读一次，作为"扎营后"
        after = self.hero_metrics()
        changes = []
        for g, b in (c["before"] or {}).items():
            a = after.get(g)
            if not a:
                continue
            if b.get("stress") is None or a.get("stress") is None:
                continue
            d_hp = round((a.get("hp") or 0) - (b.get("hp") or 0), 1)
            d_st = int((a.get("stress") or 0) - (b.get("stress") or 0))
            if d_hp or d_st:
                changes.append({"name": a.get("name"), "hp": d_hp, "stress": d_st,
                                "hp_now": a.get("hp"), "stress_now": a.get("stress")})
        self.emit({"kind": "camp", "t": c["start"], "end": ts,
                   "party": c["party"], "changes": changes,
                   "corpses": [], "ctx": self.party_context()})

    def corpse_note(self):
        """地上尸体单位。直接用游戏内的显示名（含 mod 译名），不做改写。

        尸体在 DD 里是刻意设计：占据格子、阻挡位移、影响阵型，所以要让模型知道
        "那不是活着的敌人，但确实占着位置"。
        """
        out = []
        for v in self.snapshot["corpse"].values():
            label = self.loc.monster(v["cls"] if isinstance(v, dict) else v) or "尸体"
            if label not in out:
                out.append(label)
        return out

    def live_hero(self, guid, fallback=None):
        """现取这个人的最新状态。

        名册每次刷新都会建一批新字典，所以不能在进场时把对象存下来一直用——
        那样换装、得病、治好、改名都不会反映出来。这里按 guid 现查。
        """
        h = (self.roster.heroes or {}).get(guid)
        return h if isinstance(h, dict) else (fallback or {})

    def seed_known_dead(self):
        """启动时先把"名册里已经是 status 3"的人记下来。

        这样即使这趟阵亡发生在工具启动之前，之后被墓园事件复活时也能认出
        "谁从墓园里回来了"，而不是默默当成一个普通在镇上的英雄。
        """
        self.known_dead = {str(g) for g, h in (self.roster.heroes or {}).items()
                           if h.get("dead")}
        return len(self.known_dead)

    def reconcile_dead(self, notify=False):
        """名册说"他又活了" → 撤销阵亡标记。

        暗黑地牢里阵亡不是永久的：墓园事件、部分 mod 都能把英雄复活。
        所以 `dead_guids` 不能当成永久黑名单——每次拿到新名册（20 秒一次）
        都核对一次：如果这个 guid 的 roster.status 不再是 3、血量也不是 -1，
        就说明他已经回来了，立刻放行，后面的站位/队伍/档案都按活人处理。

        notify=True 时把名字记进 `_revived`，回城那一拍会写成
        「XXX复活了」，接在镇上事件后面（见 town_log_items）。
        """
        revived = []
        watch = set(getattr(self, "dead_guids", set())) | set(getattr(self, "known_dead", set()))
        for g in watch:
            h = (self.roster.heroes or {}).get(str(g))
            if not h:
                continue
            if h.get("dead") or (h.get("hp") is not None and h.get("hp") < 0):
                continue
            # 名册如果还是"他死之前"那一份，就不能据此判断复活：
            # 刚看到死亡行、下一次解码还没发生时，名册里当然还是活着的。
            died_at = getattr(self, "_death_at", {}).get(str(g), 0)
            if died_at and getattr(self.roster, "mtime", 0) < died_at:
                continue
            self.dead_guids.discard(g)
            self.known_dead.discard(g)
            revived.append(h)
        if not revived:
            return []
        names = {h.get("name") for h in revived}
        # 这一趟还没结算的话，他不再是"没回来"的人
        self.death_log = [d for d in (getattr(self, "death_log", []) or [])
                          if d.get("name") not in names]
        for h in revived:
            nm = h.get("name") or "？"
            print("[复活] %s 回到了队伍" % nm)
            if notify:
                if not isinstance(getattr(self, "_revived", None), list):
                    self._revived = []
                if nm not in self._revived:
                    self._revived.append(nm)
        return revived

    def revived_heroes(self):
        """记录里阵亡、现在却活着的人 → [(名字, 第几周)]。

        墓园事件 / mod 可以把人复活，"曾经 died"不等于"现在还是死的"。
        这里只做事实对照：某条已完成记录里标了 died，而名册现在不是 status 3，
        就说明他后来被复活了。
        """
        if getattr(self, "campaign", None) is None:
            return []
        try:
            chapters = self.campaign.chapters() or {}
        except Exception:
            return []
        out = {}
        for ci, ch in chapters.items():
            try:
                cnum = int(ci)
            except Exception:
                cnum = 0
            for v in (ch or {}).values():
                if not (isinstance(v, dict) and "success" in v
                        and v.get("start") is False):
                    continue
                for h in (v.get("heroes") or {}).values():
                    nm = h.get("name")
                    if not (nm and h.get("died")):
                        continue
                    cur = (self.roster.by_name or {}).get(nm)
                    if cur and not cur.get("dead"):
                        if nm not in out or cnum > out[nm]:
                            out[nm] = cnum
        return sorted(out.items(), key=lambda x: -x[1])

    def _trim_party(self):
        """一队最多四人。多出来的只能是上一趟的残留，按"最后看到"的顺序留后四个。"""
        if len(self.heroes_in_raid) <= 4:
            return
        keep = list(self.heroes_in_raid.items())[-4:]
        self.heroes_in_raid = dict(keep)
        print("[队伍] 超过四人，已裁到最近出场的四位")

    def _begin_party_sync(self):
        """新的一场战斗开始：攒这一场 RERANK 里出现的我方 guid。"""
        self._sync_guids = []
        self._sync_on = True

    def _finish_party_sync(self):
        """用"这一场实际出场的人"整体替换队伍档案。

        RERANK 的全场刷新是游戏自己列的、此刻在场的单位：我方那几条就是
        这一趟真正带出来的人。拿它替换，任何上一趟留下的名字都会被清掉。
        已经阵亡的 guid 不再补回来。
        """
        if not self._sync_on:
            return
        self._sync_on = False
        guids = []
        for g, c in self._sync_guids:
            if str(g) in getattr(self, "dead_guids", set()):
                continue
            if (g, c) not in guids:
                guids.append((g, c))
        guids = guids[:4]                 # 一队最多四人
        self._sync_guids = []
        if not guids:
            return
        fresh = {}
        for g, c in guids:
            h = (self.roster.heroes or {}).get(str(g))
            if h and (h.get("dead") or (h.get("hp") is not None and h.get("hp") < 0)):
                continue                  # 名册里已阵亡：不要带回来
            if h:
                fresh[(str(g), h.get("class") or c)] = h
        if not fresh:
            return
        dropped = set(self.heroes_in_raid) - set(fresh)
        self.heroes_in_raid = fresh
        if dropped:
            names = []
            for g, c in dropped:
                nm = (self.roster.heroes.get(str(g)) or {}).get("name") or c
                names.append(nm)
            print("[队伍] 这些人不在这趟里，已移出档案：%s" % "、".join(names))

    def party_names(self):
        names = []
        for (g, c), h0 in self.heroes_in_raid.items():
            h = self.live_hero(g, h0)
            nm = h.get("name") or self.loc.hero_class(c)
            if nm not in names:
                names.append(nm)
        return names

    # ---------------- 这一趟：地牢 / 任务 / 所在区域
    def quest_ctx(self):
        """这一趟的地牢与任务（中途也拿得到）。

        来源有两条，按可靠度排：
          1. `quest_start` 那句语音（/vo/neutral/weald_cleanse）——进副本那一刻就有；
          2. 存档里 `start=True` 的任务记录——工具中途启动时靠它补。
        刻意**不**用 `latest_quest()`：那是"上一趟打完了什么"，拿它当这一趟
        就会出现"在兽窟里被告知这是荒野"这种错配。
        """
        q = dict((self.raid or {}).get("quest") or {})
        need_more = not (q.get("dungeon") and q.get("difficulty"))
        if need_more and getattr(self, "campaign", None) is not None:
            try:
                cur = self.campaign.current_quest(q.get("dungeon_id") or "",
                                                  q.get("quest_id") or "") or {}
            except Exception:
                cur = {}
            # 存档里那条"正在打"的记录也带难度/长度，但必须地牢+任务都对得上，
            # 否则宁可只写"荒野 · 清剿"，也不能把别的任务难度安到这一趟。
            same = cur and ((not q.get("dungeon_id") or
                             cur.get("dungeon_id") == q.get("dungeon_id")) and
                            (not q.get("quest_id") or
                             cur.get("quest_id") == q.get("quest_id")))
            if same:
                added = False
                for k, v in cur.items():
                    if v and not q.get(k):
                        q[k] = v
                        added = True
                if added and self.raid is not None:
                    self.raid["quest"] = dict(q)
                    self.save_raid()
        return q

    @staticmethod
    def quest_line(q):
        bits = [_txt(q.get("dungeon")), _txt(q.get("quest"))]
        if q.get("difficulty"):
            bits.append(_txt(q["difficulty"]))
        if q.get("length"):
            bits.append(_txt(q["length"]))
        return " · ".join(x for x in bits if x)

    def where_line(self):
        kind = (self.area or {}).get("kind") or self.last_area_kind
        if not kind:
            return ""
        return "房间" if kind == "room" else "走廊"

    def _quest_from_audio(self, audio):
        """从任务语音里读地牢与任务类型。

        /vo/neutral/weald_cleanse              → 荒野 · 清剿（出发）
        /vo/good/weald_success_cleanse_01      → 同上，且知道成功了
        /vo/bad/weald_failure_cleanse_02       → 同上，且知道没办成
        """
        m = RE_VO.search(audio or "")
        if not m:
            return {}
        tok = m.group(1).lower()
        parts = tok.split("_")
        dungeon = next((x for x in parts if x in VO_DUNGEONS), "")
        quest = next((x for x in VO_QUESTS if x in parts), "")
        success = True if "success" in parts else (False if "failure" in parts else None)
        if not (dungeon or quest):
            return {}
        return {"dungeon_id": dungeon, "quest_id": quest, "success": success}

    def _apply_quest(self, did="", qid="", success=None):
        did, qid = _txt(did), _txt(qid)
        if not (did or qid) and success is None:
            return
        raid = self.ensure_raid(self.last_ts)
        q = dict(raid.get("quest") or {})
        if did:
            q["dungeon_id"] = did
            q["dungeon"] = (self.loc.get("dungeon_name_" + did)
                            or _nice_id_name(did))
        if qid:
            q["quest_id"] = qid
            q["quest"] = Campaign.QUEST_KIND.get(qid, _nice_id_name(qid))
        if success is not None:
            q["success"] = bool(success)
        raid["quest"] = q
        self.save_raid(force=True)

    def seed_party_by_names(self, names):
        """出发记录里只有人名：按名册补成 (guid, 职业)，这样档案/血量当场就有。

        只在队伍还是空的时候补，不覆盖已经由日志认下来的队伍。
        """
        if self.heroes_in_raid:
            return 0
        for nm in list(names or [])[:4]:   # 一队最多四人
            h = (self.roster.by_name or {}).get(nm)
            if h and h.get("class") and not h.get("dead"):
                self.heroes_in_raid[(str(h.get("guid")), h["class"])] = h
        return len(self.heroes_in_raid)

    # ---------------- 瞬时事件（只报一次）
    def add_effect(self, who, what, detail=""):
        key = (who or "", what, detail or "")
        if key in self._seen_effects:
            return
        self._seen_effects.add(key)
        self.pending_effects.append({"who": who or "", "what": what,
                                     "detail": detail or "", "t": self.last_ts})

    def scan_effects(self):
        """从名册兜一层：濒死 / 折磨 / 美德。

        日志里的 `deaths_door` / `afflicted` / `virtue` 不带名字，崩溃那一行
        （`X: I have aquired the trait:`）带名字但只在部分版本出现。所以两条都收：
        日志给"什么时候发生"，名册（hp=0、affliction_type_id、virtue_type_id）给"是谁"。
        每人每种只报一次，报过就不再出现——这就是"死亡/濒死只展示一个回合"。
        """
        for p in self.party_context():
            if p.get("fallen"):
                continue
            if p.get("hp") == 0:
                self.add_effect(p["name"], "濒死")
            if p.get("affliction_id"):
                self.add_effect(p["name"], "精神崩溃", p.get("affliction") or "")
            if p.get("virtue_id"):
                self.add_effect(p["name"], "显现美德", p.get("virtue") or "")

    def prime_effects(self):
        """把"此刻已经存在"的濒死/折磨/美德记成已看过，不算刚发生。

        工具中途接入、或读到的名册本来就是最新状态时，如果不先压一遍，
        第一拍就会把"他三个小时前就疯了"当成"这一拍刚崩溃"。
        """
        for p in self.party_context():
            if p.get("hp") == 0:
                self._seen_effects.add((p["name"], "濒死", ""))
            if p.get("affliction_id"):
                self._seen_effects.add((p["name"], "精神崩溃", p.get("affliction") or ""))
            if p.get("virtue_id"):
                self._seen_effects.add((p["name"], "显现美德", p.get("virtue") or ""))
        self.pending_effects = []

    def collect_effects(self):
        self.scan_effects()
        out = list(self.pending_effects)
        self.pending_effects = []
        return out

    def _decorate(self, sc):
        """给场景挂上"这一趟在哪、打什么"和"此刻在房间还是走廊"。"""
        if sc.get("kind") in ("depart", "combat_round", "death", "map_area",
                              "curio", "camp", "retreat"):
            q = self.quest_ctx()
            if q:
                sc["quest"] = q
            where = self.where_line()
            if where:
                sc.setdefault("where", where)
        return sc

    def party_context(self):
        """队伍此刻的样子（每次都从名册现取，不受进场时的快照影响）。

        quirk_ids / trinket_ids 不做截断——展示时再按需挑，否则"第 7 个怪癖"
        这种变化会看不见，档案就会一直停在旧状态。
        """
        out = []
        for (g, c), h0 in self.heroes_in_raid.items():
            h = self.live_hero(g, h0)
            qids = list(h.get("quirks") or [])
            tids = list(h.get("trinkets") or [])
            out.append({
                "guid": g,
                "name": h.get("name") or self.loc.hero_class(c),
                "class": self.loc.hero_class(c),
                "class_id": c,
                "hp": h.get("hp"), "stress": h.get("stress"),
                "affliction": self.loc.affliction(h.get("affliction")),
                "affliction_id": h.get("affliction") or "",
                "virtue": self.loc.virtue(h.get("virtue")),
                "virtue_id": h.get("virtue") or "",
                "quirks": [self.loc.quirk(q) for q in qids],
                "quirk_ids": qids,
                "trinkets": [self.loc.trinket(t) for t in tids],
                "trinket_ids": tids,
                # 死活以 roster.status 为准（3 = 阵亡）。血量不能当判据：
                # 回城后死者的 current_hp 会被写成 1.0，跟活人一样；
                # -1 只在他刚倒下、还没结算的那一小段时间里出现，所以也留着。
                "fallen": bool(h.get("dead"))
                          or (h.get("hp") is not None and h.get("hp") < 0)
                          or (str(g) in getattr(self, "dead_guids", set())),
            })
        return out

    # ---------------- 本次出击的队伍档案
    def seed_party(self, save_dir, jar, names, work):
        """工具在一趟副本中途启动时，日志里没有 quest_start，队伍会是空的。

        结果就是 prompt 里一个人名都没有——只有位置和事件。这里按可靠度补：
          1. persist.raid.json 的 party.heroes（这一趟的 guid，最准）
          2. 名册里 roster.status = 1 的人（1 = 正在出击）
          3. 刚进副本那一刻，还可用 last_party（你这次选的队伍）
          4. 战役日志里"正在打"的那趟任务的队员
        职业一律以名册（roster）为准，因为任务记录里的 class 可能是哈希。
        """
        if self.heroes_in_raid:
            return 0
        self.seed_source = ""
        # 先把名册刷到最新：roster.status / last_party 是判断"谁在出击"的关键，
        # 用 20 秒前的那份可能会把刚回城的人当成还在副本里。
        if getattr(self, "refresh_fn", None):
            try:
                self.refresh()
            except Exception:
                pass
        guids = []
        try:
            src = os.path.join(save_dir, "persist.raid.json")
            # 太久没动过的 raid 存档是上一趟留下的，别拿来当"当前队伍"
            fresh = os.path.exists(src) and (time.time() - os.path.getmtime(src) < 12 * 3600)
            # 刚进副本那一刻调用时，还要求它确实是这次写的：
            # 上一趟的 raid.json 可能才过了几分钟，光看 12 小时窗口分不出来
            qs = getattr(self, "_quest_start_ts", 0)
            if qs:
                try:
                    fresh = fresh and os.path.getmtime(src) >= qs - 600
                except OSError:
                    fresh = False
            if fresh:
                d = decode_save(jar, src, os.path.join(work, "raid_seed.json"), names) or {}
                guids = ((d.get("base_root") or {}).get("party") or {}).get("heroes") or []
                if guids:
                    self.seed_source = "persist.raid.json"
        except Exception:
            guids = []
        # 名册里有 status=1 的人：游戏自己标的"正在出击"
        if not guids:
            guids = [g for g, h in (self.roster.heroes or {}).items()
                     if h.get("status") == 1 and not h.get("dead")]
            if guids:
                self.seed_source = "名册 roster.status=1"
        # 刚进副本那一刻，last_party 就是你这次选的队伍；在镇上启动时它是上一趟的，
        # 所以只在有 _quest_start_ts（＝刚出发）时才用
        if not guids and getattr(self, "_quest_start_ts", 0):
            cand = [str(g) for g in (getattr(self.roster, "last_party", []) or [])]
            guids = [g for g in cand
                     if (self.roster.heroes.get(g) or {}).get("class")
                     and not (self.roster.heroes.get(g) or {}).get("dead")]
            if guids:
                self.seed_source = "名册 last_party"
        if not guids and getattr(self, "campaign", None) is not None:
            # 只认"正在打的那趟"（start=True）。以前这里还退到 latest_quest()
            # （最后一条**已打完**的记录），中途启动时补进来的正是上一趟的人，
            # 连已经阵亡的都会混进档案。宁可先空着，等第一场战斗的 RERANK 来定。
            try:
                names = (self.campaign.current_quest() or {}).get("party") or []
            except Exception:
                names = []
            if names:
                self.seed_party_by_names(names)
                if self.heroes_in_raid:
                    self.seed_source = "战役记录（正在打的那趟）"
        for g in guids[:4]:               # 一队最多四人
            info = (self.roster.heroes or {}).get(str(g))
            if info and info.get("class") and not info.get("dead"):
                self.heroes_in_raid[(str(g), info["class"])] = info
        if self.heroes_in_raid:
            self.prime_effects()
        return len(self.heroes_in_raid)

    def party_pack_text(self, kind=""):
        """这一趟四个人的背景资料（放 user 段开头，跟着事实走）。

        - 每次调用都从名册现取：换装、染病、治愈、改名当场就跟上；
        - 另外顺手比一次上一次，把"刚刚发生了什么变化"记成事件发给模型；
        - 文本按签名缓存，签名没变就不重新排版（省 CPU，不是省 token）。
        """
        cfg = self.cfg or {}
        ctx = self.party_context()
        if ctx:
            changes = party_diff(self._ctx_prev, ctx, self.loc, self.diseases)
            if changes:
                self.pending_changes.extend(changes)
                for c in changes:
                    print("[队伍] " + c)
            self._ctx_prev = {p["guid"]: dict(p) for p in ctx}

        if not cfg.get("knowledge_on", True) or not cfg.get("party_pack_on", True):
            return ""
        if not ctx:
            return ""
        # 营地场景用营地那段介绍，其余用公会那段；两者分开缓存
        mode = "camp" if kind == "camp" else "field"
        sig = (mode,) + tuple((p.get("guid"), p.get("name"), p.get("class_id"),
                               bool(p.get("fallen")),      # 阵亡要立刻反映到档案
                               tuple(p.get("quirk_ids") or ()),
                               tuple(p.get("trinket_ids") or ())) for p in ctx)
        if sig == self._pack_sig:
            return self._pack_text
        lines, used = party_pack(ctx, self.loc, self.diseases, kind=kind)
        self._pack_sig = sig
        self._pack_text = "\n".join(lines)
        if self._pack_text:
            print("[知识] 队伍档案：%d 人 / %d 字" % (len(lines), used))
        return self._pack_text

    def take_changes(self):
        """取走这一场要附带的变化（取完就清空，避免反复播报）。"""
        ch = list(self.pending_changes)
        self.pending_changes = []
        return ch

    def party_bible_text(self, wb):
        """玩家世界观里和这一队相关的段落。没命中就退回全文。"""
        cfg = self.cfg or {}
        if not wb:
            return ""
        if not cfg.get("bible_filter_on", True):
            return wb
        try:
            roster_names = [h.get("name") for h in (self.roster.heroes or {}).values()]
        except Exception:
            roster_names = []
        picked = party_bible(wb, self.party_context(), roster_names,
                             int(cfg.get("bible_budget", 900)))
        if not picked.strip():
            return wb
        dropped = len(wb) - len(picked)
        if dropped > 120:
            print("[知识] 世界观按队伍筛过：%d→%d 字" % (len(wb), len(picked)))
        return picked

    # ---------------- 场景产出
    def emit(self, scene):
        self.scene_queue.append(self._decorate(scene))

    def close_round(self, force=False):
        """收一拍。force=True 是"这场战斗结束了"，不受回合数与显著性约束。

        为什么要有 force：`emit_every=2` 时奇数回合会 return（动作留到下一张卡），
        可 BOSS 往往死在奇数回合 —— 这一拍一 return，就再也没机会说了，
        直接跳到结算，玩家看到的就是"没打 BOSS 就结算了"。
        """
        self._finish_party_sync()          # 先按"这一场实际出场的人"校正队伍
        r, b = self.round, self.battle
        self.round = None
        if b is None:
            return
        if r and r["actions"]:
            b["rounds"].append(r)
        elif not force:
            return                          # 这一拍没有动作：非强制就不出卡
        if not b["rounds"]:
            return
        if not force and len(b["rounds"]) < self.min_rounds:
            return
        cfg = self.cfg or {}
        emit_every = max(1, int(cfg.get("emit_every", self.emit_every)))
        win_n = max(1, int(cfg.get("window", self.window)))
        salience = float(cfg.get("salience", self.salience))
        if not force and b["round_no"] % emit_every != 0:
            return
        # 已经播过的回合不再重复（force 时尤其重要：上一张卡可能刚播过第 4 回合）
        done = int(b.get("emitted_to") or 0)
        pending = [rr for rr in b["rounds"] if rr["no"] > done]
        if force:
            # 战斗结束：只补"还没播过的回合"。全都播过了就不再重复一张，
            # 否则会出现"同一段打两遍"的观感。
            if not pending:
                return
            window = pending[-win_n:]
        else:
            window = list(b["rounds"])[-win_n:]
        if salience and not force:
            score = 0.0
            for rr in window:
                for a in rr["actions"]:
                    score += 0.4 * sum(1 for x in a.get("rolls", []) if x["hit"])
                    score += 3 if a.get("crit") else 0
                    score += 2.5 * a.get("kill", 0)
                    score += 1.5 * a.get("heal", 0)
            if score < salience:
                return
        show_kb = bool(b.get("enemy_classes")) and not b.get("kb_shown")
        if show_kb:
            b["kb_shown"] = True
        b["emitted_to"] = window[-1]["no"]
        self.emit({
            "kind": "combat_round", "t": window[0]["t"], "raid": None,
            "battle_id": b["id"], "round_from": window[0]["no"],
            "round_to": window[-1]["no"], "focus_round": window[-1]["no"],
            "final_round": bool(force),
            "enemies": self.living_enemies(),
            "enemy_ids": sorted(b.get("enemy_classes") or ()),
            "enemy_kb": show_kb,
            "corpses": self.corpse_note(),
            "party": self.party_names(),
            "actions": [a for rr in window for a in rr["actions"]],
            # 站位与"这一拍里的濒死/崩溃/美德"：都是当场事实，报完即弃
            "field": self.field_rows(),
            "effects": self.collect_effects(),
            "ctx": self.party_context(),
        })

    def quest_return_line(self, rec):
        """用游戏自带的 str_returned_from_<任务>_<地牢>_<成败> 文案来写「归来」这句。"""
        q = Campaign._clean(rec.get("quest")) or ""
        dt = Campaign._clean(rec.get("dungeon_type")) or ""
        if not q or not dt:
            return ""
        res = "success" if rec.get("success") else "failure"
        tpl = (self.loc.pick("str_returned_from_%s_%s_%s" % (q, dt, res))
               or self.loc.pick("str_returned_from_%s_%s_success" % (q, dt))
               or self.loc.pick("str_returned_from_%s_%s_failure" % (q, dt)))
        if not tpl:
            return ""
        names = [h.get("name") for h in (rec.get("heroes") or {}).values() if h.get("name")]
        diff = Campaign.DIFF.get(rec.get("difficulty"), "")
        length = Campaign.LEN.get(rec.get("length"), "")
        txt = re.sub(r"\{?[a-z_]+\}", "", tpl) if False else tpl
        txt = re.sub(r"\{\?[a-z_]+\}", "", txt)          # 去掉 {?hero_names} 这类条件标记
        try:
            txt = txt % ("、".join(names), diff, length)
        except Exception:
            txt = re.sub(r"%s", "", txt)
        return txt.replace("（）", "").strip()

    def _on_death(self, guid, name, cls_name, ts):
        """日志里出现 death start → 判一次是不是我方的人。

        游戏对**每个**倒下的单位都打这一行（敌人、尸体、我方），
        只有 guid 在名册里才是玩家的人。同一 guid 只播报一次。
        血量为 -1 是游戏给"已倒下"的标记（见 persist.roster.json），
        所以这里不依赖血量：日志比存档快得多。
        """
        guid = str(guid)
        info = self.roster.heroes.get(guid)
        if not info:
            return                      # 敌方单位：不播报
        # 日志可能是跨启动累积的：翻到上一趟的 death 行时，别当成本趟的阵亡
        start = (self.raid or {}).get("start") or ""
        if start and ts and ts < start:
            return
        if guid in self.dead_guids:
            return
        self.dead_guids.add(guid)
        self._death_at[guid] = time.time()
        cls = info.get("class") or cls_name
        nm = info.get("name") or name
        self.death_log.append({"guid": guid, "name": nm,
                               "class": self.loc.hero_class(cls), "t": ts})
        print("[阵亡] %s（%s）" % (nm, self.loc.hero_class(cls)))
        # 立刻排一个阵亡场景：这是整场里最重的一拍，不能等结算才说。
        # 排完就把他从"这一趟的队伍"里摘掉——之后每回合的站位/档案里不会再有他，
        # 也就不会出现"人已经埋了还每回合提一句"。
        self._finish_party_sync()
        alive = [p["name"] for p in self.party_context()
                 if p.get("name") and p["name"] != nm]
        field = self.field_rows()
        self.emit({"kind": "death", "t": ts, "name": nm,
                   "class": self.loc.hero_class(cls),
                   "enemies": self.living_enemies() if self.battle else [],
                   "party": alive, "ctx": self.party_context(),
                   "field": field, "effects": self.collect_effects()})
        for key in [k for k in list(self.heroes_in_raid) if str(k[0]) == guid]:
            self.heroes_in_raid.pop(key, None)

    def on_curio(self, ev, ts):
        """一次奇物互动 → 一个场景（同一条记录只报一次）。"""
        key = ev.get("key") or (ev.get("curio_id"), ev.get("result"), ev.get("hero"))
        if key in self._curio_done:
            return
        self._curio_done.add(key)
        self.emit({"kind": "curio", "t": ts,
                   "curio": ev.get("curio") or "",
                   "desc": ev.get("desc") or "",
                   "result": ev.get("result") or "",
                   "hero": ev.get("hero"),
                   "quirk_gain": ev.get("quirk_gain") or [],
                   "stress_delta": ev.get("stress_delta"),
                   "hp_delta": ev.get("hp_delta"),
                   "area": (self.area or {}).get("id", ""),
                   "ctx": self.party_context()})

    def close_area(self, min_steps=6):
        a = self.area
        self.area = None
        if a:
            self.last_area_kind = a["kind"]
        if not a:
            return
        quiet = not a["loot"] and not a["events"]
        if quiet and (a["steps"] == 0 or (a["kind"] == "corridor" and a["steps"] < min_steps)):
            return
        self.emit({"kind": "map_area", "t": a["enter"], "area_kind": a["kind"],
                   "area_id": a["id"], "steps": a["steps"], "loot": a["loot"][:12],
                   "events": a["events"], "ctx": self.party_context()})

    # ---------------- 主解析
    def feed(self, line):
        m = RE_TS.match(line)
        if not m:
            return
        ts = m.group(1)
        self.last_ts = ts
        body = line[m.end():]
        if any(n in body for n in NOISE):
            return

        dm = RE_DEATH.search(body)
        if dm:
            self._on_death(dm.group(1), dm.group(2).strip(),
                           (dm.group(3) or "").strip(), ts)
            return

        rm = RE_RERANK.search(body)
        if rm:
            guid, cls = rm.group(1), rm.group(2)
            if self._rank_ts != ts:          # 新的一次场面刷新
                self._rank_ts = ts
                self.snapshot = {"hero": {}, "enemy": {}, "corpse": {}}
            try:
                pos = int(rm.group(4))
            except Exception:
                pos = 0
            cell = {"cls": cls, "pos": pos, "rank": rm.group(3)}
            if self.roster.is_hero(guid, cls):
                info = self.roster.heroes.get(guid)
                # 已经阵亡的人不该再出现在站位里。有的 mod / 读档会让他的槽位
                # 继续被刷新，如果照单全收，就会出现"人已经埋了还每回合写着"。
                if str(guid) in getattr(self, "dead_guids", set()):
                    return
                if info and info.get("dead"):
                    return
                if info:
                    self.heroes_in_raid[(guid, cls)] = info
                self.snapshot["hero"][guid] = cell
                if self._sync_on and (guid, cls) not in self._sync_guids:
                    self._sync_guids.append((guid, cls))
            else:
                bucket = "corpse" if is_corpse(cls) else "enemy"
                self.snapshot[bucket][guid] = cell
                if self.battle is not None:
                    self.battle["seen_classes"].add(cls)
                    self.battle["fallback_enemies"].add(self.loc.monster(cls))
                    if bucket == "enemy":
                        self.battle["enemy_classes"].add(cls)
            return

        sm = RE_STATE.search(body)
        if sm:
            self._on_state(sm.group(1), ts)
            return

        am = RE_ACTOR.search(body)
        if am and self.mode == "combat":
            self.actor = am.group(1).strip()
            self.loc.observed.add(self.actor)
            return

        cm = RE_CHANCE.search(body)
        if cm:
            self.pending_chance = float(cm.group(1))
            return

        rl = RE_ROLL.search(body)
        if rl and self.pending_chance is not None and self.action is not None:
            roll = float(rl.group(1))
            self.action["rolls"].append({"chance": round(self.pending_chance, 2),
                                         "roll": round(roll, 2),
                                         "hit": roll < self.pending_chance})
            self.pending_chance = None
            return

        km = RE_CRIT.search(body)
        if km and self.action is not None:
            self.action["crit"] = True
            self.action["by_name"] = km.group(1)
            return

        tm = RE_TRAIT.search(body)
        if tm:
            self._on_trait(tm.group(1).strip(), tm.group(2).strip())
            return

        nm = RE_NARR.search(body)
        if nm:
            self._on_narration(nm.group(1), ts, nm.group(2) or "")
            return

        lm = RE_LOOT.search(body)
        if lm:
            iid = lm.group(2)
            name = self.loc.item(iid)
            if self.area is not None:
                self.area["loot"].append({"id": iid, "name": name,
                                          "desc": self.loc.item_desc(iid)})
            raid = self.ensure_raid()
            raid.setdefault("loot", []).append(name)
            self.save_raid()
            return

        am2 = RE_AREA.search(body)
        if am2:
            # 刚从镇上踏进地牢：quest_start 那行经常没有（这局 5 小时只打了 1 次），
            # 用"在镇上 + 第一次出现房间/走廊"来补一张「出发」。
            if self._at_town and self.raid is None:
                self._begin_run(ts)
            # 换区域时还挂着一场战斗：说明上一场结束得没有 victory 行（撤退之类），
            # 先把它强制收掉，否则这一拍永远出不来。
            if self.mode == "combat":
                self.close_round(force=True)
                self.battle = None
            if self.mode == "camp":
                self.close_camp(ts)
            if self.mode == "explore":
                self.close_area()
            self.mode = "explore"
            aid = am2.group(1)
            try:
                entry_tile = int(am2.group(2))
            except Exception:
                entry_tile = 0
            self.area = {"id": aid, "kind": "room" if aid.startswith("roo") else "corridor",
                         "enter": ts, "steps": 0, "loot": [], "events": [],
                         "entry_tile": entry_tile}
            if self.curio_watch is not None:
                self.curio_watch.set_area(aid, entry_tile)
            return

        tm = RE_TILE.search(body)
        if tm and self.area is not None:
            self.area["steps"] += 1
            return

        rw = RE_QUEST_REWARD.search(body)
        if rw and self.area is not None:
            self.area["events"].append("战利品：" + self.loc.item(rw.group(2)))

    def _on_state(self, st, ts):
        if st == "bs_start_round":
            if self.mode == "camp":
                self.close_camp(ts)
            if self.mode != "combat":
                self.mode = "combat"
                self.close_area()
                self.event_count += 1
                self.battle = {"id": self.event_count, "round_no": 0,
                               "rounds": deque(maxlen=64), "enemies": set(),
                               "fallback_enemies": set(), "enemy_classes": set(),
                               "seen_classes": set()}
            else:
                self.close_round()
            if not self.battle["rounds"]:
                # 新的一场：上一场留在快照里的站位不能拿来当这一场的
                self.snapshot = {"hero": {}, "enemy": {}, "corpse": {}}
                self._begin_party_sync()
            self.battle["round_no"] += 1
            self.actor = None
            self.round = {"no": self.battle["round_no"], "t": ts, "actions": []}
            return

        if st in ("bs_turn_do_skill", "bs_turn_wait_to_do_skill") and self.mode == "combat":
            if self.action is None and self.actor:
                name, side, cls = self.actor_label(self.actor)
                if side in ("enemy", "unknown") and self.battle is not None:
                    self.battle["enemies"].add(name)
                self.action = {"actor": name, "side": side, "cls": cls, "at": ts,
                               "round": self.battle["round_no"] if self.battle else 0,
                               "rolls": [], "crit": False, "kill": 0, "heal": 0}
            return

        if st == "bs_after_turn_action_result" and self.action is not None:
            if self.round is not None:
                self.round["actions"].append(self.action)
            self.action = None
            self.actor = None

    def _on_trait(self, name, tid, ts=""):
        """`X: I have aquired the trait: xxx` —— 唯一"带名字"的崩溃/美德来源。"""
        low = (tid or "").lower()
        try:
            if "afflict" in low:
                detail = self.loc.affliction(tid) or ""
                self.add_effect(name, "精神崩溃", detail)
            elif "virtue" in low:
                detail = self.loc.virtue(tid) or ""
                self.add_effect(name, "显现美德", detail)
        except Exception:
            pass

    def _begin_run(self, ts, audio=""):
        """「出发」的完整初始化 + 出卡。

        两个入口共用：游戏打 `quest_start` 的时候；以及**没打** quest_start 时
        由"刚从镇上踏进地牢"（第一次出现房间/走廊）推断出来的出发。
        这局实测 5 小时里 quest_start 只出现 1 次，靠它一条路会整趟没有开场。
        """
        self.close_area()
        self.close_round(force=True)       # 上一场没收尾的，先补一张
        self.battle, self.actor, self.action = None, None, None
        self.snapshot = {"hero": {}, "enemy": {}, "corpse": {}}
        self.mode = "explore"
        self.heroes_in_raid = {}
        self.raid = None
        self.death_log = []
        self.dead_guids = set()
        self._pending_return = None
        self.pending_effects = []
        self._seen_effects = set()
        self._curio_done = set()
        self._at_town = False
        # 新的一趟：上一趟留在 raid_state.json 里的战利品 / 队伍不能带过来
        if self.state_path and os.path.exists(self.state_path):
            try:
                os.remove(self.state_path)
            except Exception:
                pass
        self.ensure_raid(ts)
        # 语音里直接带地牢和任务类型：进副本这一刻就知道"荒野 · 清剿"
        q = self._quest_from_audio(audio)
        if q.get("dungeon_id") or q.get("quest_id"):
            self._apply_quest(q.get("dungeon_id", ""), q.get("quest_id", ""))
        # 队伍来源按可靠度排：
        #   1. persist.raid.json（进副本那一刻游戏就写好了，就是这一趟的四个人）
        #   2. 战役日志里"地牢 + 任务都对得上"的出发记录
        #   3. 都没有就先空着，等第一场战斗的 RERANK 全场刷新来定
        # 以前是先读战役记录、而且不校验任务；出发记录还没写的时候拿到的是
        # **上一趟**的队员，于是档案里混进上一局的人（连死掉的都在）。
        self._quest_start_ts = time.time()
        if self.seed_fn:
            try:
                self.seed_fn()
            except Exception:
                pass
        if not self.heroes_in_raid and getattr(self, "campaign", None) is not None:
            try:
                cur = self.campaign.current_quest(q.get("dungeon_id", ""),
                                                  q.get("quest_id", "")) or {}
            except Exception:
                cur = {}
            self.seed_party_by_names(cur.get("party") or [])
        self._trim_party()
        self.prime_effects()          # 出发时身上就有的状态不算"刚发生"
        self.emit({"kind": "depart", "t": ts, "party": self.party_names(),
                   "enemies": [], "ctx": self.party_context()})

    def _on_narration(self, entry, ts, audio=""):
        cn = NARR_CN.get(entry, entry)

        if entry == "quest_start":
            self._begin_run(ts, audio)
            return

        if entry == "town_visit_start":
            # 注意 town_visit_start 不只是"打完回来"：进城镇界面、从建筑里出来
            # 也可能触发。所以只在镇上真有新事情（归来记录 / 升级 / 治疗…）
            # 或者这是刚结算回来（_pending_return）时才出一拍，否则会连着刷好几张空卡。
            #
            # 但如果刚才还在副本里，那这次就是"回城"：游戏只在部分情况下打
            # quest_end（这局 5 小时只打了 1 次），所以在这里兜底补一张结算，
            # 而且必须排回城卡**前面**——顺序不能倒。
            if self.raid is not None or self.mode in ("combat", "explore", "camp"):
                self.close_camp(ts)
                self.close_area()
                self.close_round(force=True)
                self.battle, self.mode = None, "idle"
                try:
                    self.roster.reload()
                except Exception:
                    pass
                if self.raid is not None:
                    print("[结算] 没收到 quest_end，按回城兜底补一张任务结束")
                    try:
                        self.emit(self._settlement(ts, reason="return"))
                    except Exception:
                        traceback.print_exc()
                    self.raid = None
                    if self.state_path and os.path.exists(self.state_path):
                        try:
                            os.remove(self.state_path)   # 这一趟结束，别再被下一趟接着用
                        except Exception:
                            pass
            self._at_town = True
            items = self.town_log_items()
            if items:
                self.emit({"kind": "town", "t": ts, "items": items,
                           "ctx": self.party_context()})
            return

        if entry.startswith("quest_end"):
            self.close_camp(ts)
            self.close_area()
            self.close_round(force=True)     # 最后一战必须落地，不能吞
            self.battle, self.mode = None, "idle"
            self.roster.reload()
            q = self._quest_from_audio(audio)
            ok = entry == "quest_end_completed" or bool(q.get("success"))
            self._apply_quest(q.get("dungeon_id", ""), q.get("quest_id", ""), ok)
            self.emit(self._settlement(ts, success=ok))
            if self.state_path and os.path.exists(self.state_path):
                try:
                    os.remove(self.state_path)
                except Exception:
                    pass
            self.raid = None
            return

        if entry == "camp":
            self.close_area()
            self.refresh()                 # 先读一次存档，作为"扎营前"
            self.camp = {"start": ts, "party": self.party_context(),
                         "before": self.hero_metrics(), "end": None}
            self.mode = "camp"
            return

        if entry == "victory":
            # 战斗结束：不管此刻 mode 是什么，只要手里还挂着一场战斗就把它收掉。
            # 以前这里有个 `and self.mode == "combat"` 的条件，mode 一旦被别的行
            # 改掉（比如提前出现区域切换），这场战斗的最后一拍就永远出不来了。
            self._finish_party_sync()
            self.close_round(force=True)     # 奇数回合打赢也不能吞
            self.battle = None
            if self.mode == "combat":
                self.mode = "explore"
            return

        if entry in ("deaths_door", "afflicted", "virtue"):
            # 这三行日志不带名字。刷一次名册（hp=0 / affliction / virtue）再认领，
            # 这样"谁濒死了"当场就能写出来，而不是等 20 秒后的例行解码。
            self.refresh()
            self.scan_effects()
            self.ensure_raid().setdefault("events", []).append(NARR_CN.get(entry, entry))
            self.save_raid()

        if entry == "battle_retreat_fail":
            self.add_effect("", "撤退失败，这一回合没能脱身")
            self.ensure_raid().setdefault("events", []).append(cn)
            self.save_raid()

        if self.action is not None:
            if entry == "kill_monster":
                self.action["kill"] += 1
            elif entry == "heal":
                self.action["heal"] += 1

        if (self.area is not None and self.mode == "explore"
                and entry in ("trap", "torchlight_out", "torchlight_full", "camp",
                              "battle_retreat_fail", "enter_building", "loot")):
            self.area["events"].append(cn)


    # ---------------- 城镇日志（小队外出期间镇上的事）
    @staticmethod
    def _clean_id(v):
        return v[3:] if isinstance(v, str) and v.startswith("###") else v

    def building_name(self, bid):
        bid = self._clean_id(bid)
        return (self.loc.pick("str_controls_town_%s_context" % bid)
                or self.loc.pick("town_activity_name_%s" % bid) or bid)

    def town_log_items(self):
        d = {}
        camp = getattr(self, "campaign", None)
        if camp is not None:
            camp.refresh()
            d = camp.data or {}
        if not d and self.town_log_fn:
            try:
                d = self.town_log_fn() or {}
            except Exception:
                d = {}
        chapters = (d.get("base_root") or {}).get("chapters") or {}
        rev = ["%s复活了" % n for n in self._take_revived()]
        if not chapters:
            return rev
        last = getattr(self, "_town_chapter", None)
        if not last or last not in chapters:
            try:
                last = max(chapters, key=lambda x: int(x))
            except Exception:
                last = list(chapters)[-1]
        self._town_chapter = last
        items = []
        pending = getattr(self, "_pending_return", None)
        got_return = False

        # 先写「谁回来了」——这一周结束的那趟
        for k, v in chapters[last].items():
            if isinstance(v, dict) and "success" in v and v.get("start") is False:
                # 有"刚打完的那趟"时，只认对得上的记录：地牢/任务一致、人也有重合。
                # 对不上就跳过——宁可这一句自己写，也不能把上周的归来文案搬过来。
                if pending is not None:
                    same_q = ((not pending.get("dungeon_id")
                               or self._clean_id(v.get("dungeon_type")) == pending["dungeon_id"])
                              and (not pending.get("quest_id")
                                   or self._clean_id(v.get("quest")) == pending["quest_id"]))
                    got = set(h.get("name") for h in (v.get("heroes") or {}).values()
                              if h.get("name"))
                    ours = set(pending.get("names") or [])
                    if not same_q or (ours and got and not (ours & got)):
                        continue
                got_return = True
                names = [h.get("name") for h in (v.get("heroes") or {}).values()
                         if h.get("name")]
                dead = [h.get("name") for h in (v.get("heroes") or {}).values()
                        if h.get("died")]
                if names:
                    if dead:
                        # 游戏自带那句「归来」文案会把四个人名字都填进去，
                        # 死了的那个也会写成"满载而归"。有人没回来时改用手写的：
                        alive = [n for n in names if n not in dead]
                        line = "%s 回来了，但 %s 没能回来" % (
                            "、".join(alive) or "队伍", "、".join(dead))
                        if v.get("success") is False:
                            line += "（这一趟没办成）"
                    else:
                        # 没有人缺员时才用游戏自带的「归来」文案
                        line = self.quest_return_line(v)
                        if not line:
                            line = "%s 回来了" % "、".join(names)
                            if v.get("success") is False:
                                line += "，但这一趟没办成"
                    items.append(line)
        if pending is not None and not got_return:
            # 存档里还没写这一趟的结果（通常就是这种情况）：用日志与名册自己写
            alive = [n for n in (pending.get("names") or [])
                     if n not in (pending.get("deaths") or [])]
            dead = list(pending.get("deaths") or [])
            if dead:
                line = "%s 回来了，但 %s 没能回来" % (
                    "、".join(alive) or "队伍", "、".join(dead))
            else:
                line = "%s 回来了" % ("、".join(alive) or "队伍")
            if pending.get("success") is False:
                line += "（这一趟没办成）"
            items.append(line)
        # 用过就作废：下一次回城是另一趟的事，不能把这一句一直重播下去
        if pending is not None:
            self._pending_return = None
        for _, v in chapters[last].items():
            if not isinstance(v, dict):
                continue
            r = v.get("rtti")
            if r == -579125384:                       # 升级
                items.append("%s 升到 %s 级" % (v.get("name"),
                                                v.get("level")))
            elif r == -317933210:                     # 建筑活动（多为治疗/消遣）
                act = self._clean_id(v.get("activity_id")) or ""
                bld = self.building_name(v.get("building_id"))
                hero = v.get("hero_name")
                qid = v.get("quirk_name")
                qid = qid if qid and qid not in ("none", "###none") else ""
                if qid:
                    kind = "疾病" if qid in self.diseases else "怪癖"
                    items.append("%s：%s 治好了%s「%s」"
                                 % (bld, hero, kind, self.loc.quirk(qid)))
                else:
                    room = self.loc.pick("town_activity_name_" + act) if act else ""
                    items.append("%s：%s 在%s待了一周"
                                 % (bld, hero, room or "那里"))
            elif r == -37270005:                      # 建筑升级
                items.append("%s 完成了升级" % self.building_name(v.get("id")))
            elif r == 1114009654:                     # 地牢解锁
                dn = self.loc.pick("dungeon_name_" + str(self._clean_id(v.get("dungeon_id"))))
                if dn:
                    items.append("%s 深处又通了一段（等级 %s）" % (dn, v.get("level")))
            elif r == 844919810:                      # 城镇事件（结算里也会有，这里再保一份）
                eid = self._clean_id(v.get("town_event_result_id"))
                title = self.loc.pick("town_event_title_" + str(eid)) if eid else ""
                if title:
                    items.append("镇上传开一件事：%s" % title)
        # 去重保序
        out = []
        for it in items:
            if it and it not in out:
                out.append(it)
        # 复活只是这一周镇上的事之一：接在所有镇上事件的后面
        if rev:
            out = [x for x in out if x not in rev][:max(0, 12 - len(rev))] + rev
            return out
        return out[:12]

    def _take_revived(self):
        """取走待播报的复活名单（一次性，取完就清空）。"""
        out = [n for n in (getattr(self, "_revived", None) or []) if n]
        self._revived = []
        return out

    def _settlement(self, ts, success=None, reason="quest_end"):
        self.reconcile_dead(notify=True)   # 死了又复活的，不算"没回来"
        raid = self.ensure_raid(ts)
        base = raid.get("baseline", {})
        party = set(raid.get("party_guids") or [])
        if not party:
            party = {g for (g, c) in self.heroes_in_raid}
        # 这一趟的身份（地牢 / 任务）来自出发语音，或存档里 start=True 那条。
        # 千万别用 latest_quest()：那是"上一趟打完了什么"，拿它当这一趟就会
        # 把上周的难度和阵亡名单安到这一趟头上。
        q = dict(raid.get("quest") or {})
        did, qid = q.get("dungeon_id", ""), q.get("quest_id", "")
        names_now = [p["name"] for p in self.party_context()]
        camp = {}
        if getattr(self, "campaign", None) is not None:
            try:
                camp = self.campaign.finished_record(did, qid, names_now) or {}
            except Exception:
                camp = {}
        meta = Campaign._meta(self.loc, camp) if camp else {}
        for k, v in q.items():
            if v and not meta.get(k):
                meta[k] = v
        if success is None:
            success = camp.get("success") if camp else q.get("success")
        # 兜底结算时经常不知道成败（没有 quest_end 那行）：那就只写"任务结束"
        known = success is not None
        if success is None:
            success = True
        gained, diseases, lost, deaths = [], [], [], []
        for g, h in self.roster.heroes.items():
            b = base.get(g)
            if not b:
                continue
            if party and g not in party:
                continue
            new_q = set(h["quirks"]) - set(b.get("quirks") or [])
            for q in new_q:
                item = {"hero": h["name"], "quirk": self.loc.quirk(q)}
                (diseases if q in self.diseases else gained).append(item)
            for q in set(b.get("quirks") or []) - set(h["quirks"]):
                lost.append({"hero": h["name"], "quirk": self.loc.quirk(q)})
            # 阵亡判定：出发时活着、现在 roster.status=3；或实时出现的血量 -1。
            # 以前只看 hp<0，而回城后死者血量会被重置成 1.0 —— 既会漏掉真死的，
            # 也会把"上一趟就死了"的人算到这一趟头上。
            was_dead = bool(b.get("dead"))
            now_dead = bool(h.get("dead"))
            hp = h.get("hp")
            if not was_dead and (now_dead or (hp is not None and hp < 0)):
                deaths.append(h["name"])
        loot = raid.get("loot") or []
        agg = {}
        for it in loot:
            agg[it] = agg.get(it, 0) + 1
        def cap(seq, n):
            return {"items": seq[:n], "more": max(0, len(seq) - n)}

        # 死亡名单三路合并，任何一路认得出来就不漏：
        # ① 本次运行里日志抓到的（实时） ② 名册 status=3 / 血量 -1 ③ 配得上的战役记录 died=true
        party_names_now = ([self.roster.heroes[g]["name"] for g in sorted(party)
                            if g in self.roster.heroes] or names_now)
        for d in (getattr(self, "death_log", []) or []):
            if d.get("name") and d["name"] not in deaths:
                deaths.append(d["name"])
        # 记录里的 died 只在"整支队伍完全一致"时采信：上周同一地牢同一任务、
        # 人员还高度重合的话，那条记录会把上周的死算到这一趟头上。
        rec_party = set(meta.get("party") or [])
        if camp and rec_party and rec_party == set(party_names_now):
            for n in (camp.get("deaths") or []):
                if n and n not in deaths:
                    deaths.append(n)
        # 队里本来有人、回城时名册里已经没了（被移走/埋葬）
        for (g, c) in list(self.heroes_in_raid):
            if str(g) not in self.roster.heroes:
                nm = (self.heroes_in_raid.get((g, c)) or {}).get("name")
                if nm and nm not in deaths:
                    deaths.append(nm)
        # 结果记录是**下周**才写进存档的，所以这里通常还没有：别猜，等它有了再说。
        # 城镇那一章不在这里钉死——结果会落在新的一章里，回城时再取最新的。
        self._town_chapter = None
        party_all = party_names_now
        self._pending_return = {"dungeon_id": did, "quest_id": qid,
                                "names": party_all, "deaths": list(deaths),
                                "success": bool(success)}

        # 任务结果：把"有没有人没回来"直接写进这一句，不留空让模型自己编
        if reason == "return" and not known:
            result = ("任务结束，无人死亡" if not deaths
                      else "任务结束，但是 %s 死亡" % "、".join(deaths[:6]))
        elif success:
            result = ("任务完成，无人死亡" if not deaths
                      else "任务完成，但是 %s 死亡" % "、".join(deaths[:6]))
        else:
            result = ("任务未完成，无人死亡" if not deaths
                      else "任务未完成，死亡 %s" % "、".join(deaths[:6]))
        return {"kind": "settlement", "t": ts, "party": self.party_context(),
                "party_names": (party_names_now or (meta.get("party") or [])),
                "quest": meta, "success": bool(success), "result": result,
                "quirks_gained": cap(gained, 8), "diseases": cap(diseases, 5),
                "quirks_lost": cap(lost, 5),
                "deaths": deaths[:6],
                "loot": ["%s×%d" % (k, v) for k, v in sorted(agg.items(), key=lambda x: -x[1])],
                "events": raid.get("events") or [],
                "campaign": camp,
                "town_event": self.town_event_info(),
                "partial": bool(raid.get("resumed")),
                "ctx": self.party_context()}

    def town_event_info(self):
        # 优先取战役日志里「这一周」的事件，和城镇日志同源，不会错位
        try:
            camp = getattr(self, "campaign", None)
            if camp is not None:
                te = camp.town_event(getattr(self, "_town_chapter", None))
                if te.get("title"):
                    return te
        except Exception:
            pass
        if not self.town_event_fn:
            return {}
        try:
            d = self.town_event_fn() or {}
            eid = ((d.get("base_root") or {}).get("current_result_event_id") or "")
            eid = eid[3:] if eid.startswith("###") else eid
            if not eid:
                return {}
            return {"id": eid,
                    "title": self.loc.pick("town_event_title_" + eid) or "",
                    "desc": self.loc.pick("town_event_description_" + eid) or ""}
        except Exception:
            return {}

    def campaign_info(self):
        """从解码后的 campaign_log 里取这一趟的战果（第一个结算界面）。"""
        if not getattr(self, "campaign", None):
            return {}
        try:
            self.campaign.refresh(force=True)     # 结算这一下必须是最新存档
        except Exception:
            pass
        rec = self.campaign.latest_quest()
        return rec or {}



# ------------------------------------------------------------------ 知识匹配
def knowledge_block(sc, loc, budget=400, use_trinkets=True, only=None):
    """只把本场景真正出现过的条目摘出来，硬性限制总字数。

    刻意不做的：职业技能表、敌人图鉴、整本物品库 —— 那些塞进去只会污染文风。
    """
    lines, used, seen = [], 0, set()

    def mechanical(t):
        """判断一条描述是不是纯数值说明（+20% 腐蚀、免疫诅咒…），这类不进 prompt。"""
        if not t:
            return True
        if re.search(r"[+-]\s*\d", t) or "%" in t:
            return True
        return any(w in t for w in ("免疫", "几率", "伤害", "抗性", "速度", "最大生命",
                                "属性", "提升", "降低"))

    def add(text):
        nonlocal used
        text = (text or "").strip()
        if not text or text in seen:
            return
        if used + len(text) > budget:
            return
        seen.add(text)
        lines.append(text)
        used += len(text)

    for p in (sc.get("ctx") or []):
        aid = p.get("affliction_id")
        if aid:
            d = loc.affliction_desc(aid)
            if d and not mechanical(d):
                add("%s 的状态「%s」：%s" % (p["name"], loc.affliction(aid), d[:44]))
        if only is None or "quirk" in only:
            for qid in (p.get("quirk_ids") or []):
                d = loc.quirk_desc(qid)
                if d and not mechanical(d):
                    add("「%s」：%s" % (loc.quirk(qid), d[:36]))
        # 饰品是静态装备：战斗里只在第一回合报，其他场景每次都报（本来就不频繁）
        if (only is None or "trinket" in only) and use_trinkets and \
                (sc.get("kind") != "combat_round" or sc.get("round_from") == 1):
            for tid in (p.get("trinket_ids") or []):
                name = loc.trinket(tid)
                # 纯数值词条（+15精准、-33%疾病抗性）一律不进 prompt：
                # 硬约束里写着"不写游戏术语、不复述统计"，喂进去只会被抄成战报。
                eff = [e for e in (loc.trinket_effects(tid) or [])
                       if e and not mechanical(e)]
                if eff:
                    add("饰品「%s」：%s" % (name, "、".join(eff)))
                    continue
                d = loc.trinket_tip(tid)
                if d and not mechanical(d):
                    add("饰品「%s」：%s" % (name, clean_markup(d)[:44]))
                else:
                    add("饰品「%s」" % name)      # 只剩名字，也够模型认出是什么口气
    return lines


def _is_mechanical(t):
    """纯数值说明（+20% 腐蚀、免疫诅咒…）——这类不进 prompt。"""
    if not t:
        return True
    if re.search(r"[+-]\s*\d", t) or "%" in t:
        return True
    return any(w in t for w in ("免疫", "几率", "伤害", "抗性", "速度", "最大生命",
                                "属性", "提升", "降低"))


def party_pack(ctx, loc, diseases=(), kind=""):
    """这一趟出击的几个人：姓名、职业介绍、全部怪癖、疾病、随身装备。

    数据分三类，更新频率完全不同，所以这里是"每次现算"，而不是进场时拍一张照片：

      职业      永远不会变 —— 用来查游戏自带的职业介绍
      名字      可以在游戏里改 —— 每次现取
      饰品/怪癖/疾病  会变（换装、获得、治愈） —— 每次现取

    这里**不做字数截断**。一局地牢很长，队伍状态在大多数回合里是不变的，
    真正变化的是事实卡；把四个人的来路写全，比每次省两百字更有价值。
    想关掉整段就设 knowledge_on / party_pack_on = false。

    调用方（Scribe.party_pack_text）保证 ctx 来自最新的名册。
    """
    people = [p for p in (ctx or []) if p.get("name")]
    if not people:
        return [], 0

    lines, total = [], 0

    for p in people:
        cname = p.get("class") or ""
        intro = loc.class_intro(p.get("class_id") or "", kind)
        # 介绍原文通常以职业名开头（「狼女可谓是百战余生…」）。这时名字后面
        # 就不再挂（狼女），否则读起来是「御影（狼女）：狼女可谓是…」。
        if cname and intro.startswith(cname) and p["name"] != cname:
            head = p["name"]
        else:
            head = p["name"] + (("（%s）" % cname) if cname and cname != p["name"] else "")
        bits = []
        if p.get("fallen"):
            bits.insert(0, "已阵亡（这一趟里不会再行动、不会再说话）")
        if intro:
            bits.append(intro)

        ill, quirks = [], []
        for qid in (p.get("quirk_ids") or []):
            name = loc.quirk(qid)
            if qid in diseases:
                ill.append(name)
                continue
            # 怪癖全收：哪怕本地化里没有描述，名字本身就够用（「风湿病」「淫纹：色欲」）。
            # 只有纯数值描述（+15精准、-33%疾病抗性）不带进来，它会和
            # 「不复述统计」的硬约束打架。题材不做任何筛检。
            d = clean_markup(loc.quirk_desc(qid) or "")
            if d and _is_mechanical(d):
                d = ""
            quirks.append("%s「%s」" % (name, d) if d else name)
        if ill:
            # "病：哥布林族" 这种写法会被模型当成"正在和哥布林打交道"，
            # 于是走廊里凭空打起来。写成"健康状态：患病「…」"才不会被误读成阵营。
            bits.append("健康状态：患病" + "".join("「%s」" % x for x in ill))
        if quirks:
            bits.append("怪癖：" + "、".join(quirks))

        tri = [loc.trinket(t) for t in (p.get("trinket_ids") or []) if t]
        if tri:
            bits.append("随身装备：" + "、".join(tri))

        body = "｜".join(bits)
        line = head + "：" + body if body else head
        lines.append(line)
        total += len(line)

    return lines, total


def party_diff(prev, ctx, loc, diseases=()):
    """比较两次队伍快照，返回"这一趟里刚发生的变化"。

    为什么要有这个：接口是无状态的，每次调用模型都从零看一遍系统提示与事实卡，
    所以"完整状态"必须每次都发全；但"刚刚换了什么"这种事件，只有对比上一次才知道，
    而它恰恰是旁白能用的素材（刚换上的护符、刚染上的病）。
    """
    if not prev:
        return []
    out = []
    now = {p["guid"]: p for p in ctx}
    for g, p in now.items():
        old = prev.get(g)
        if not old:
            continue
        nm = p["name"]
        if old.get("name") and old["name"] != nm:
            out.append("%s（原 %s）：改了名字" % (nm, old["name"]))

        o_tri, n_tri = set(old.get("trinket_ids") or ()), set(p.get("trinket_ids") or ())
        if o_tri != n_tri:
            bits = []
            off = [loc.trinket(t) for t in (o_tri - n_tri)]
            on = [loc.trinket(t) for t in (n_tri - o_tri)]
            if off:
                bits.append("摘下 " + "、".join(off))
            if on:
                bits.append("换上 " + "、".join(on))
            out.append("%s：%s" % (nm, "；".join(bits)))

        o_q, n_q = set(old.get("quirk_ids") or ()), set(p.get("quirk_ids") or ())
        if o_q != n_q:
            for q in (n_q - o_q):
                kind = "染上" if q in diseases else "得到怪癖"
                out.append("%s：%s「%s」" % (nm, kind, loc.quirk(q)))
            for q in (o_q - n_q):
                kind = "治好了" if q in diseases else "失去怪癖"
                out.append("%s：%s「%s」" % (nm, kind, loc.quirk(q)))

        o_a, n_a = old.get("affliction_id") or "", p.get("affliction_id") or ""
        if o_a != n_a:
            if n_a:
                out.append("%s：陷入「%s」" % (nm, loc.affliction(n_a)))
            elif o_a:
                out.append("%s：从「%s」里缓过来了" % (nm, loc.affliction(o_a)))
    return out


def party_bible(wb, ctx, others=(), budget=900):
    """从玩家写的世界观里，只摘和这一队有关的段落。

    世界观往往写了好几个人的来历，可这一趟只出动四个人；全长塞进 system
    既费 token 又抢注意力。按空行分段后，一段一段判：

      · 提到当前队员（名字或职业）        → 留
      · 提到没上场的其他人                → 丢
      · 谁都没提（世界、地牢、规矩这类）  → 留
    """
    if not wb or not ctx:
        return ""
    mine = set()
    for p in ctx:
        for k in (p.get("name"), p.get("class")):
            if k and len(k) >= 2:
                mine.add(k)
    idle = {n for n in others if n and len(n) >= 2} - mine
    if not mine:
        return ""
    blocks, cur = [], []
    for line in wb.splitlines():
        if line.strip():
            cur.append(line.rstrip())
        elif cur:
            blocks.append("\n".join(cur))
            cur = []
    if cur:
        blocks.append("\n".join(cur))

    keep, used = [], 0
    for b in blocks:
        if b.lstrip().startswith("#"):
            continue                       # 纯标题本身不带信息
        hit_mine = any(k in b for k in mine)
        if not hit_mine and any(k in b for k in idle):
            continue                       # 写的是没上场的人
        if used + len(b) > budget:
            break
        keep.append(b)
        used += len(b)
    return "\n\n".join(keep)


# ------------------------------------------------------------------ 事实卡
try:
    from prompts import DEFAULT_STYLE as SYS_STYLE
    from prompts import system_prompt as _resolve_style
    from prompts import user_template as _resolve_user_tpl
except Exception:          # 独立拷走 dd_scribe.py 时也不至于崩
    SYS_STYLE = (
        "你是《暗黑地牢》的旁白。把刚刚发生的几十秒写成一小段现场：3–6句，80–180字。"
        "以动作为主线，写动作、声音、气味、光线与身体的反应；允许短对话；句式要变。"
        "只能使用事实清单里出现的名字，不编造没发生的事，不写游戏术语，不复述统计。")


def _quest_tag(sc):
    """事实卡标题后缀：这一趟在哪、打什么。"""
    q = sc.get("quest") or {}
    return " · ".join(x for x in (_nice_id_name(q.get("dungeon")),
                                  _nice_id_name(q.get("quest"))) if x)


def _quest_meta(sc):
    """难度与尺寸单独成句，并且把字段名写出来。

    只写"老练 · 中"模型只能猜这两个词各自的含义；写成
    "地牢难度等级：老练，地牢尺寸：中" 它才知道这是关卡参数、不是场面形容词。
    每一拍都挂太重，所以只在出发与结算出现。
    """
    q = sc.get("quest") or {}
    bits = []
    if q.get("difficulty"):
        bits.append("地牢难度等级：%s" % q["difficulty"])
    if q.get("length"):
        bits.append("地牢尺寸：%s" % q["length"])
    return "，".join(bits)


def _effect_lines(sc):
    lines = []
    for e in sc.get("effects") or []:
        what = e.get("what") or ""
        det = e.get("detail") or ""
        who = e.get("who") or ""
        if who:
            lines.append("· %s %s%s" % (who, what, ("（%s）" % det) if det else ""))
        else:
            lines.append("· " + what)
    return lines


def scene_to_facts(sc, loc, lean=False):
    tag = _quest_tag(sc)
    meta = _quest_meta(sc)

    if sc["kind"] == "depart":
        lines = ["【出发" + (" · " + tag if tag else "") + "】"]
        if meta:
            lines.append(meta)
        if sc.get("party"):
            lines.append("这一趟的同伴：" + "、".join(sc["party"]))
        else:
            lines.append("这一趟的同伴：尚未读到（不要编人名）")
        lines.append("（一场新的地牢冒险开始了，接下来请根据你接收到的游戏信息，"
                     "用连续的叙述描述在地牢里的探险队每一个回合发生的事情。）")
        return "\n".join(lines)

    if sc["kind"] == "death":
        lines = ["【阵亡" + (" · " + tag if tag else "") + "】"]
        if sc.get("where"):
            lines.append("此刻在：%s" % sc["where"])
        lines.append("英雄 %s（%s）倒下了，希望这不是永别。"
                     % (sc["name"], sc["class"]))
        f = sc.get("field") or {}
        if f.get("enemy"):
            lines.append("当时还站着的对手：" + "、".join(f["enemy"]))
        elif sc.get("enemies"):
            lines.append("当时在场的对手：" + "、".join(
                safe_name(x) for x in sc["enemies"][:6]))
        if sc.get("party"):
            lines.append("还站着的同伴：" + "、".join(sc["party"]))
        lines += _effect_lines(sc)
        return "\n".join(lines)

    if sc["kind"] == "combat_round":
        head = "【战斗 · 第%d–%d回合" % (sc["round_from"], sc["round_to"])
        lines = [head + (" · " + tag if tag else "") + "】",
                 "现在正处于战斗之中！"]
        if sc.get("where"):
            lines.append("此刻在：%s" % sc["where"])

        # ① 场上单位与站位（含尸体）
        f = sc.get("field") or {}
        if f.get("hero"):
            lines.append("我方站位（1 号位在最前）：" + "、".join(f["hero"]))
        elif sc.get("ctx"):
            lines.append("我方：" + "、".join(
                ("%s（%s）" % (p["name"], p["class"])) for p in sc["ctx"]))
        elif sc.get("party"):
            lines.append("我方：" + "、".join(sc["party"]))
        if f.get("enemy"):
            lines.append("敌方站位（1 号位在最前）：" + "、".join(f["enemy"]))
        elif sc["enemies"]:
            lines.append("当前的对手：" + "、".join(
                safe_name(x) for x in sc["enemies"][:8]))
        else:
            lines.append("当前的对手：无（这一回合没有可交手的目标）")
        if f.get("corpse"):
            lines.append("地上的尸体（占着格子、挡走位，不是活敌）："
                         + "、".join(f["corpse"][:6]))
        elif sc.get("corpses"):
            lines.append("地上的尸体（占着格子、挡走位，不是活敌）："
                         + "、".join(sc["corpses"][:6]))
        if f.get("enemy"):
            lines.append("（站位表是从画面读到的；这一拍真正出手的敌人以"
                         "「敌方出手」为准，两边都没有的敌人不要自己加）")
        elif sc["enemies"]:
            lines.append("（这一回合的对手就是这些：不要加名单外的，"
                         "也不要把没出手的写成出手）")
        if sc.get("enemy_kb") and sc.get("enemy_ids"):
            tips = []
            for cid in sc["enemy_ids"][:6]:
                nm = safe_name(loc.monster(cid))
                sk = loc.monster_skills(cid) or loc.monster_skills_by_name(nm)
                if sk:
                    tips.append("%s（%s）" % (nm, "、".join(sk[:4])))
            if tips:
                lines.append("这些对手的手段（仅仅是它们会什么，不代表这一回合用了）："
                             + "；".join(tips))

        focus = sc.get("focus_round", sc["round_to"])

        def line(a):
            who = safe_name(a["actor"])
            bits = []
            rolls = a.get("rolls") or []
            hits = sum(1 for r in rolls if r["hit"])
            if rolls:
                bits.append("命中 %d/%d" % (hits, len(rolls)))
                if len(rolls) > 1:
                    bits.append("打向 %d 个目标" % len(rolls))
            if a.get("crit") and (hits > 0 or not rolls):
                bits.append("其中一记是重手")
            if a.get("kill"):
                bits.append("放倒 %d 个" % a["kill"])
            if a.get("heal"):
                bits.append("治疗同伴 %d 次（日志不记治的是谁）" % a["heal"])
            if not bits:
                bits.append("出手但没打中，也没治人")
            return "· " + who + "：" + "，".join(bits)

        # ② 这一拍里谁出手、中没中、谁倒下/崩溃/觉醒
        older = [a for a in sc["actions"] if a.get("round", focus) < focus]
        newest = [a for a in sc["actions"] if a.get("round", focus) == focus]
        if older:
            agg = {}
            for a in older:
                hits = sum(1 for r in a.get("rolls", []) if r["hit"])
                agg[a["actor"]] = agg.get(a["actor"], 0) + hits
            agg_txt = "、".join("%s 命中%d" % (safe_name(k), v) for k, v in agg.items())
            lines.append("（上一回合概况，只用于理解局势，禁止写进正文：" + agg_txt + "）")
        lines.append("（这一回合发生的事，仅限描写此处内容）")
        hero_acts = [a for a in newest if a.get("side") == "hero"]
        foe_acts = [a for a in newest if a.get("side") in ("enemy", "unknown")]
        if hero_acts:
            lines.append("我方出手：")
            lines += [line(a) for a in hero_acts]
        if foe_acts:
            lines.append("敌方出手：")
            lines += [line(a) for a in foe_acts]
        if not (hero_acts or foe_acts):
            lines.append("（无人有效出手）")

        eff = _effect_lines(sc)
        if eff:
            lines.append("这一拍里发生的事（只在这一拍有效，下一拍不再提）：")
            lines += eff

        if not lean:
            for p in sc["ctx"]:
                extra = []
                if p.get("affliction"):
                    extra.append("已陷入" + p["affliction"])
                if p.get("stress") is not None and p["stress"] >= 60:
                    extra.append("压力%s" % p["stress"])
                if p.get("trinkets") and sc.get("round_from") == 1:
                    extra.append("佩饰 " + "、".join(p["trinkets"]))
                if p.get("quirks"):
                    extra.append("怪癖：" + "、".join(p["quirks"][:3]))
                if extra:
                    lines.append("· 状态｜%s（%s）：%s"
                                 % (p["name"], p["class"], "；".join(extra)))
        return "\n".join(lines)

    if sc["kind"] == "map_area":
        kind = "房间" if sc["area_kind"] == "room" else "走廊"
        lines = ["【路上 · %s%s】" % (kind, (" · " + tag) if tag else "")]
        if sc.get("ctx"):
            lines.append("同行：" + "、".join(p["name"] for p in sc["ctx"]))
        if sc["loot"]:
            parts = []
            for it in sc["loot"]:
                if isinstance(it, dict):
                    d = clean_markup(it.get("desc") or "").strip()
                    parts.append("%s（%s）" % (it["name"], d[:42]) if d else it["name"])
                else:
                    parts.append(str(it))
            lines.append("拾得（谁捡的未知，不要指名）：" + "、".join(parts))
        evs = [e for e in (sc["events"] or []) if e not in ("拾取", "loot")]
        if evs:
            lines.append("发生：" + "、".join(evs))
        lines.append("（路上没有触发战斗：不要出现敌人，也不要写交手）")
        return "\n".join(lines)

    if sc["kind"] == "curio":
        where = sc.get("where") or ("走廊" if (sc.get("area") or "").startswith("co")
                                   else "房间")
        name = sc.get("curio") or ""
        head = "【奇物互动 · %s" % where
        lines = [head + (" · " + tag if tag else "") + "】"]
        if name:
            d = (sc.get("desc") or "").strip()
            lines.append("动的东西：%s%s" % (name, ("——" + d[:46]) if d else ""))
        else:
            lines.append("动的东西：某件东西（不知道是什么，不要替它编名字）")
        lines.append("结果：%s" % (sc.get("result") or "结果不明"))
        if sc.get("hero"):
            bits = []
            if sc.get("hp_delta"):
                bits.append("血量%+g" % sc["hp_delta"])
            if sc.get("stress_delta"):
                bits.append("压力%+d" % sc["stress_delta"])
            if sc.get("quirk_gain"):
                bits.append("新增怪癖：" + "、".join(sc["quirk_gain"]))
            lines.append("动手的人：%s%s" % (sc["hero"],
                                            ("（" + "，".join(bits) + "）") if bits else ""))
        else:
            lines.append("动手的人：不明（不要指名，用动作和环境来写）")
        lines.append("这是路上的一次停顿，不是战斗，也不是拾取。"
                     "（没有敌人，不要写交手；下一拍不会再提这件事）")
        return "\n".join(lines)

    if sc["kind"] == "town":
        lines = ["【回到城镇 · %s】" % sc["t"][11:],
                 "小队离开的这一周，镇上发生了这些："]
        lines += ["· " + x for x in (sc.get("items") or [])[:12]]
        if sc.get("ctx"):
            lines.append("回镇的队伍：" + "、".join("%s（%s）" % (p["name"], p["class"])
                                                 for p in sc["ctx"]))
        lines.append("（队伍回到了小镇，根据地牢结算情况，"
                     "决定队员各自的性格展开动作与神态描写。）")
        return "\n".join(lines)

    if sc["kind"] == "camp":
        lines = ["【扎营" + (" · " + tag if tag else "") + "】"]
        if sc.get("ctx"):
            lines.append("同行：" + "、".join("%s（%s）" % (p["name"], p["class"])
                                             for p in sc["ctx"]))
        ch = sc.get("changes") or []
        if ch:
            parts = []
            for c in ch[:6]:
                bits = []
                if c.get("stress"):
                    bits.append("压力%+d（现%d）" % (c["stress"], c.get("stress_now") or 0))
                if c.get("hp"):
                    bits.append("血量%+g（现%g）" % (c["hp"], c.get("hp_now") or 0))
                parts.append("%s %s" % (c["name"], "、".join(bits)))
            lines.append("休整结果：" + "；".join(parts))
        else:
            lines.append("休整结果：没有明显变化（或在读不到存档的情况下进行的）")
        lines.append("说明：日志不记录用了哪些扎营技能，只知道他们停下来休整过。")
        return "\n".join(lines)

    if sc["kind"] == "settlement":
        c = sc.get("quest") or {}
        lines = ["【任务结束" + (" · " + tag if tag else "") + "】"]
        if meta:
            lines.append(meta)
        if not tag:
            if c.get("dungeon"):
                lines.append("地点：%s" % c["dungeon"])
            if c.get("quest"):
                lines.append("任务：%s" % c["quest"])
        lines.append("结果：%s" % (sc.get("result")
                                  or ("任务完成" if sc.get("success") else "任务未完成")))
        names = [n for n in (sc.get("party_names")
                             or [p["name"] for p in sc.get("party") or []])
                 if n not in (sc.get("deaths") or [])]
        if names:
            lines.append("回来的人：" + "、".join(names))
        if sc.get("events"):
            ev = Counter(sc["events"])
            lines.append("途中：" + "、".join(
                "%s×%d" % (k, v) if v > 1 else k for k, v in ev.items()))
        if sc.get("loot"):
            lines.append("带回：" + "、".join(sc["loot"][:12]))
        else:
            lines.append("带回：无（或未记录到）")

        def fmt(block, label):
            if not block:
                return
            items = block.get("items") if isinstance(block, dict) else block
            more = block.get("more", 0) if isinstance(block, dict) else 0
            if not items:
                return
            txt = "、".join("%s—%s" % (q["hero"], q["quirk"]) for q in items)
            if more:
                txt += "，另有 %d 项" % more
            lines.append(label + txt)

        fmt(sc.get("quirks_gained"), "新增怪癖：")
        fmt(sc.get("diseases"), "染上疾病：")
        fmt(sc.get("quirks_lost"), "克服或治愈：")
        te = sc.get("town_event") or {}
        if te.get("title") or te.get("desc"):
            lines.append("小镇事件：%s%s" % (te.get("title") or "",
                                            ("——" + te["desc"][:90]) if te.get("desc") else ""))
        else:
            lines.append("小镇事件：本趟没有触发")
        if sc.get("partial"):
            lines.append("（这一趟是在中途才接入记录的，带回物资与人物变化可能不全）")
        return "\n".join(lines)

    return json.dumps(sc, ensure_ascii=False)


def _fill_template(tpl, values):
    """把 {{同伴}} 这类占位符换成数据；没有数据的块连标题一起撤掉。

    注意要按**模板里实际出现的**占位符来遍历：没有数据时 values 里根本没有这个键，
    只遍历 values 会留下 {{背景}} 这种原文。
    """
    out = tpl or ""
    for key in set(re.findall(r"\{\{([^{}]+)\}\}", out)):
        ph = "{{%s}}" % key
        val = (values or {}).get(key) or ""
        if val:
            out = out.replace(ph, val)
            continue
        lines = out.splitlines()
        for i, ln in enumerate(lines):
            if ph in ln:
                # 占位符独占一行、上一行是【…】标题时，标题也一起删
                if ln.strip() == ph and i > 0 and lines[i - 1].strip().startswith("【"):
                    lines[i - 1] = ""
                lines[i] = ""
                break
        out = "\n".join(lines)
    out = re.sub(r"\n{3,}", "\n\n", out).strip()
    return out


def build_prompt(sc, loc, cfg, pack="", bible=None, changes=()):
    """system 只放"规则"（文风、世界观）；队伍状态与事实放 user。

    这样分工的理由：system 是给模型看的规矩，越稳定越好；队伍里谁戴着什么、
    谁刚染了病，是**这一场的数据**，会随游戏进行变化。混进 system 有两个坏处：
    一是每换一次装、每得一个怪癖，指令块就整段变一次，模型每次都要重新"读规矩"；
    二是以后想加东西（更多队员信息、更多场景知识）很容易把 system 越堆越长，
    真正要紧的约束反而被稀释。数据跟着事实走，规矩留在上面，各归各的。
    """
    try:
        sys_p = _resolve_style(cfg)
    except Exception:
        sys_p = cfg.get("system_prompt") or SYS_STYLE
    # 世界观原样进 prompt：玩家改过模板就按他改的来，不猜、不裁
    wb = ((cfg.get("world_bible") or "") if bible is None else (bible or "")).strip()
    if wb and cfg.get("world_bible_on", True):
        sys_p += "\n\n【世界设定（玩家提供，优先级最高）】\n" + wb

    user = "刚刚发生（唯一事实来源）：\n" + scene_to_facts(sc, loc, lean=bool(pack))

    blocks = {}
    if pack and cfg.get("knowledge_on", True):
        blocks["同伴"] = pack
    if changes:
        blocks["变化"] = "\n· ".join(changes[:8])

    if cfg.get("knowledge_on", True):
        # 有了队伍档案，每场只补"当下状态"，怪癖和饰品不再重复发
        kb = knowledge_block(sc, loc, int(cfg.get("knowledge_budget", 400)),
                             bool(cfg.get("trinket_effects_on", True)),
                             only=("affliction",) if pack else None)
        if kb:
            blocks["背景"] = "\n".join(kb)

    # user 段的说明文字与结尾那句都由玩家写（见 prompts.DEFAULT_USER_TEMPLATE）。
    # {{同伴}} / {{背景}} / {{变化}} 是数据插入点；某块没有数据时，
    # 连同它上面那行【…】标题一起消失，不留空标题。
    tpl = _resolve_user_tpl(cfg)
    body = _fill_template(tpl, blocks)
    if body:
        user += "\n\n" + body
    return sys_p, user


# ------------------------------------------------------------------ 输出
class Sink:
    # 单个数据文件超过这个大小就滚动：本份留作 .prev，上一份删掉
    ROLL_BYTES = 2 * 1024 * 1024

    def __init__(self, out):
        os.makedirs(out, exist_ok=True)
        self.dir = out
        self.scene_path = os.path.join(out, "scenes_v2.jsonl")
        self.fact_path = os.path.join(out, "facts_v2.md")
        self.scenes = open(self.scene_path, "a", encoding="utf-8")
        self.facts = open(self.fact_path, "a", encoding="utf-8")
        self.live = os.path.join(out, "live.txt")
        open(self.live, "w", encoding="utf-8").close()
        self._writes = 0

    def _roll(self):
        """跑久了把数据文件滚一次，避免一个文件涨到几十兆。"""
        self._writes += 1
        if self._writes % 40:
            return
        try:
            import housekeeping
        except Exception:
            return
        for attr, path in (("scenes", self.scene_path), ("facts", self.fact_path)):
            try:
                if os.path.getsize(path) <= self.ROLL_BYTES:
                    continue
                getattr(self, attr).close()
                housekeeping.rotate(path)
                setattr(self, attr, open(path, "a", encoding="utf-8"))
                print("[清理] %s 已滚动，上一份留在 %s.prev"
                      % (os.path.basename(path), os.path.basename(path)))
            except Exception:
                pass

    def write_scene(self, sc, facts):
        self.scenes.write(json.dumps(sc, ensure_ascii=False) + "\n")
        self.scenes.flush()
        self.facts.write("---\n" + facts + "\n")
        self.facts.flush()
        self._roll()

    def push_live(self, text):
        with open(self.live, "a", encoding="utf-8") as f:
            f.write(text.rstrip() + "\n\n")

    def push_debug(self, scene, sys_p, user, output, cfg):
        """把最近几次完整 prompt 落盘，供 F8 的调试窗查看。滚动保留 300 条。"""
        if not cfg.get("debug_on", True):
            return
        try:
            path = os.path.join(os.path.dirname(self.live), "debug.jsonl")
            rec = {"t": time.strftime("%H:%M:%S"),
                   "kind": scene.get("kind", ""),
                   "model": cfg.get("model", ""),
                   "system": sys_p, "user": user, "output": output}
            with open(path, "a", encoding="utf-8") as f:
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")
            # 太长就截掉前面的
            if os.path.getsize(path) > 3 * 1024 * 1024:
                lines = open(path, encoding="utf-8").read().splitlines()[-300:]
                open(path, "w", encoding="utf-8").write("\n".join(lines) + "\n")
        except Exception:
            pass


def strip_markdown(text):
    """去掉模型自作主张加的标题与加粗标记。

    覆盖窗是纯文本，`## 夜归` 和 `**强调**` 会原样显示成符号。
    只处理行首标题与成对的星号/下划线，不改动正文词句。
    """
    if not text:
        return text
    out = []
    for line in text.splitlines():
        t = line.strip()
        if re.match(r"^#{1,6}\s*", t):        # 整行都是标题：直接丢掉
            continue
        out.append(line)
    txt = "\n".join(out).strip()
    txt = re.sub(r"\*\*(.+?)\*\*", r"\1", txt)
    txt = re.sub(r"(?<!\w)__(.+?)__(?!\w)", r"\1", txt)
    return txt.strip()


def call_llm(sys_p, user, cfg):
    import urllib.error
    import urllib.request
    # max_tokens 太小会把正文拦腰截断：300 token ≈ 380 个汉字，正好停在句子中间
    # （"……尾巴尖" 那种）。这里给个下限，配置里写得更小就按 1200 发并提示一次。
    try:
        mt = int(cfg.get("max_tokens") or 0)
    except Exception:
        mt = 0
    if mt < 800:
        if mt:
            print("  [提示] config.json 的 max_tokens=%s 太小，正文会被截断；本次按 1200 发"
                  % mt)
        mt = 1200
    payload = {"model": cfg.get("model", "deepseek-chat"),
               "messages": [{"role": "system", "content": sys_p},
                            {"role": "user", "content": user}],
               "temperature": cfg.get("temperature", 0.9),
               "max_tokens": mt,
               "stream": False}
    req = urllib.request.Request(
        cfg["base_url"].rstrip("/") + "/chat/completions",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json",
                 "Authorization": "Bearer " + cfg["api_key"]})
    try:
        with urllib.request.urlopen(req, timeout=90) as r:
            d = json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")[:300]
        raise RuntimeError("HTTP %s：%s" % (e.code, body))

    choice = (d.get("choices") or [{}])[0] or {}
    msg = strip_markdown((choice.get("message") or {}).get("content") or "")
    if not msg.strip():
        used = (d.get("usage") or {}).get("completion_tokens_details") or {}
        if used.get("reasoning_tokens"):
            raise RuntimeError(
                "模型把 %s 个 token 全花在推理上，正文为空。"
                "旁白需要非推理模型：把 config.json 的 model 改成 deepseek-chat，"
                "或把 max_tokens 提到 800 以上。" % used.get("reasoning_tokens"))
        raise RuntimeError("模型返回空正文（finish_reason=%s）" %
                           choice.get("finish_reason"))
    if choice.get("finish_reason") == "length":
        print("  [警告] 这次正文撞到 max_tokens=%s 被截断了，"
              "结尾少了一截；把 config.json 的 max_tokens 调大（建议 1200 以上）" % mt)
    return msg



# ------------------------------------------------------------------ 映射包维护
def check_modlist(pack, game_json):
    """比对「映射包里的启用列表」与「存档里的启用列表」。"""
    mf = os.path.join(pack, "manifest.json")
    if not (os.path.exists(mf) and os.path.exists(game_json)):
        return "unknown"
    try:
        man = json.load(open(mf, encoding="utf-8"))
        import loc_index
        steam, local, _ = loc_index.read_active_mods(game_json)
        if not (steam or local):
            return "unknown"
        fp = loc_index.fingerprint(steam, local, man.get("scope") == "all")
    except Exception:
        return "unknown"
    return "same" if fp == man.get("fingerprint") else "changed"


def build_pack(game, game_json, pack):
    """调用 loc_index.py 重建映射包（只扫启用的 mod）。"""
    here = os.path.dirname(os.path.abspath(__file__))
    cmd = [sys.executable, os.path.join(here, "loc_index.py"),
           "--game", game, "--pack", pack]
    if game_json and os.path.exists(game_json):
        cmd += ["--game-json", game_json]
    try:
        subprocess.run(cmd, timeout=180, check=False)
    except Exception as e:
        print("  [重建失败] %s" % e)


# ------------------------------------------------------------------ 奇物互动
# 日志里没有"谁动了哪个奇物"这种行，但三份存档合起来能还原：
#   persist.curio_tracker.json  每次互动的结果类别（loot / buff / heal_gen …）
#   persist.map.json            每个区域每一格上放着什么奇物（###stack_of_books）
#   persist.raid.json           当前在哪个区域、第几格
# 奇物的显示名与调查文本走本地化：str_curio_title_<id> / str_curio_content_<id>

CURIO_RESULT = {
    "loot":        "拿到了东西",
    "heal_gen":    "伤口被治好了",
    "heal_stress": "心神定了下来",
    "buff":        "得了一点好处",
    "purge_neg":   "身上的坏东西被清掉了",
    "summon":      "惊动了什么东西",
    "quirk":       "留下了怪癖",
    "disease":     "染上了病",
    "":            "什么也没发生",
}


class CurioWatch:
    """盯 curio_tracker，检测到新记录就还原成一次「奇物互动」。"""

    def __init__(self, save_dir, jar, names, loc, roster, work_dir, log=print):
        self.save_dir, self.jar, self.names = save_dir, jar, names
        self.loc, self.roster, self.work = loc, roster, work_dir
        self.log = log
        # 上一次看到的记录**内容**集合。不能用下标/条数：这张表是按 (curio, 用品)
        # 排序的，中间插一条，后面所有下标整体后移。
        self.last_sigs = None
        self.sig_count = {}        # 同一个内容签名出现过几次（用来给事件编唯一 key）
        self._pending = []         # 还没出卡的记录（同一次互动的多行要合并）
        self._pending_at = 0.0
        self.last_mtime = 0.0
        self.map_key = None
        self.map_now = {}          # 最新一次读到的 (area, tile) -> curio id
        self.map_known = {}        # 累积：互动后格子被清空也不丢
        self.area_max = {}         # area -> 最大格号，用来判断走廊方向
        self.snapshot = {}         # guid -> (hp, stress, quirks, name)
        self.area_hint = ("", 0)   # 日志给的 (区域, 进入时的格子)

    # ---------------- 底层
    def _path(self, name):
        return os.path.join(self.save_dir, "persist.%s.json" % name)

    def _decode(self, name):
        src = self._path(name)
        if not (os.path.exists(src) and os.path.exists(self.jar)):
            return None
        dst = os.path.join(self.work, "%s_live.json" % name)
        return decode_save(self.jar, src, dst, self.names)

    def _load_map(self):
        """文件变了才重新解码，返回 {(area, tile): curio_id}，否则 None。"""
        p = self._path("map")
        try:
            st = os.stat(p)
            key = (st.st_mtime, st.st_size)
        except OSError:
            return None
        if key == self.map_key:
            return None
        d = self._decode("map")
        if not d:
            return None
        areas = ((d.get("base_root") or {}).get("map") or {}) \
            .get("static_dynamic", {}).get("areas") or {}
        out = {}
        for aname, a in areas.items():
            tiles = a.get("tiles") or {}
            idxs = []
            for tname, t in tiles.items():
                try:
                    idx = int(re.sub(r"\D", "", tname) or 0)
                except Exception:
                    continue
                idxs.append(idx)
                cp = (t or {}).get("curio_prop")
                if isinstance(cp, str) and cp.startswith("###"):
                    out[(aname, idx)] = cp[3:]
            if idxs:
                self.area_max[aname] = max(idxs)
        for k, v in out.items():
            self.map_known.setdefault(k, v)   # 只增不减
        self.map_key = key
        return out

    def _roster_state(self):
        self.roster.reload()
        st = {}
        for g, h in self.roster.heroes.items():
            st[g] = (h.get("hp"), h.get("stress"),
                     tuple(sorted(h.get("quirks") or [])), h.get("name"))
        return st

    def _who_changed(self):
        """互动只落在一个人身上：谁的血/压力/怪癖变了，就是谁。"""
        now = self._roster_state()
        old = self.snapshot or {}
        party = getattr(self, "party_guids", set()) or set()
        self.snapshot = now
        out = []
        for g, v in now.items():
            o = old.get(g)
            if not o or v[:3] == o[:3]:
                continue
            if party and g not in party:
                continue
            out.append({"name": v[3],
                        "quirk_gain": sorted(set(v[2]) - set(o[2])),
                        "stress_delta": (v[1] or 0) - (o[1] or 0),
                        "hp_delta": round((v[0] or 0) - (o[0] or 0), 1)})
        return out

    def _tile_of(self, areatile):
        """当前站在该区域的第几格。

        走廊是线性的：进入时记下 entry_tile，之后每走一格 areatile 加一。
        方向靠"进入格旁边有没有格子"判断——入口在 0 就只能往大走，在末格就只能往小走。
        """
        area, entry = self.area_hint
        if not area or areatile is None:
            return None
        top = self.area_max.get(area)
        if top is None:
            return None
        fwd = (entry + 1) <= top and entry == 0
        back = (entry - 1) >= 0 and entry >= top
        if fwd and not back:
            step = 1
        elif back and not fwd:
            step = -1
        else:                      # 两端都能走（房间），默认往回
            step = -1 if entry > 0 else 1
        t = entry + step * int(areatile)
        return t if 0 <= t <= top else None

    def _curio_at(self, areatile):
        area, _e = self.area_hint
        t = self._tile_of(areatile)
        if t is None:
            return None
        return self.map_known.get((area, t))

    # ---------------- 对外
    def set_area(self, area, tile):
        self.area_hint = (area or "", int(tile or 0))

    def prime(self):
        """开始跟随时先建基线：当前记录内容、人物状态、地图快照。"""
        self.last_sigs = None
        self.poll()

    @staticmethod
    def _sig(rec):
        """一条追踪记录的身份：curio / 用品 / 结果。

        **tracked_results 不是按时间追加的日志，而是一张按 (curio, 用品) 排序的表**：
        下标 0..77 是按 prop_name_id 升序排的（实测：723642102 连排 7 条、
        -2010878259 连排 7 条……），中间插进一条新记录，后面所有记录的下标就整体后移。

        所以**绝不能**用"条数 / 下标"判断哪些是新的：以前用 `order[last_count:]`，
        每次存档都会把一串很早以前的老记录当成新事件重播一遍 —— 玩家看到的就是
        "同一个奇物互动被没完没了地播"。这里改成按内容签名比对，跟顺序无关。
        """
        return (rec.get("prop_name_id"), rec.get("item_type_hash"),
                rec.get("item_id_hash"), rec.get("curio_tracker_id") or "")

    COALESCE_SEC = 3.5      # 同一次互动的多行记录，在这个窗口内合并成一张卡

    def _make_event(self, key, rec, areatile, cleared, changes, i=0):
        sig = self._sig(rec)
        self.sig_count[sig] = self.sig_count.get(sig, 0) + 1
        cid = self._curio_at(areatile) or (cleared[i] if i < len(cleared) else None)
        cat = (rec.get("curio_tracker_id") or "").strip()
        who = changes[i] if i < len(changes) else (changes[0] if changes else None)
        return {
            # key 用内容 + 出现次数，不用下标：下标会整体滑动
            "key": "curio:%s:%s:%s:%s#%d" % (sig + (self.sig_count[sig],)),
            "curio_id": cid or "",
            "curio": self.loc.curio(cid) if cid else "",
            "desc": self.loc.curio_content(cid) if cid else "",
            "result": CURIO_RESULT.get(cat, cat or "结果不明"),
            "cat": cat,
            "hero": (who or {}).get("name"),
            "quirk_gain": [self.loc.quirk(q) for q in ((who or {}).get("quirk_gain") or [])],
            "stress_delta": (who or {}).get("stress_delta"),
            "hp_delta": (who or {}).get("hp_delta"),
        }

    def flush(self, force=False):
        """把攒着的记录出成**一张**卡。

        同一次互动，游戏可能写成好几行（实测一次互动写了 3 行：不同用品、不同结果）。
        所以不在"读到一条就出一张"，而是等 COALESCE_SEC 秒没有新行再合并；
        否则玩家只点了一下，却连着看到三张几乎一样的卡。
        """
        if not self._pending:
            return []
        if not force and time.time() - self._pending_at < self.COALESCE_SEC:
            return []
        batch, self._pending = self._pending, []
        # 优先用认得出奇物名字的那条（地图查得到就写具体是什么），否则用最后一条
        pick = next((e for e in batch if e.get("curio_id")), batch[-1])
        ev = dict(pick)
        if len(batch) > 1:
            self.log("[奇物] %d 条记录是同一次互动，合并成一张卡" % len(batch))
            ev["merged_rows"] = len(batch)
        return [ev]

    def poll(self):
        # 先看有没有攒够时间的（合并窗口到期）
        events = self.flush()

        try:
            m = os.path.getmtime(self._path("curio_tracker"))
        except OSError:
            return events
        if m == self.last_mtime:
            return events
        self.last_mtime = m

        d = self._decode("curio_tracker")
        if not d:
            return events
        recs = (d.get("base_root") or {}).get("tracked_results") or {}
        order = sorted(recs, key=lambda k: int(k) if str(k).isdigit() else 0)
        cur = {self._sig(recs.get(k) or {}) for k in order}

        moved = self._load_map()
        cleared = []
        if moved is not None:
            cleared = [v for k, v in self.map_now.items() if k not in moved]
            self.map_now = dict(moved)

        if self.last_sigs is None:          # 基线，不补发历史
            self.last_sigs = cur
            self.snapshot = self._roster_state()
            self.log("[奇物] 已有 %d 条历史互动，只播之后新增的" % len(cur))
            return events

        # 只有"上次没见过"的签名才算新事件；顺序怎么变都不影响判断
        new_keys = [k for k in order if self._sig(recs.get(k) or {}) not in self.last_sigs]
        self.last_sigs = cur
        if not new_keys:
            return events

        raid = self._decode("raid") or {}
        areatile = (raid.get("base_root") or {}).get("areatile")
        changes = self._who_changed()
        for i, key in enumerate(new_keys):
            self._pending.append(self._make_event(key, recs.get(key) or {},
                                                  areatile, cleared, changes, i))
        self._pending_at = time.time()
        if len(self._pending) > 1:
            self.log("[奇物] 收到 %d 条新记录，等 %.1f 秒确认是不是同一次互动"
                     % (len(self._pending), self.COALESCE_SEC))
        return events


def print_status(sc, save_dir, jar, names, work):
    """--status：打印"当前队伍 / 活死 / 进行中的任务"，用来核对读档是否正确。

    不发请求、不写输出，纯读档。修改了队伍判定后想确认"到底认成哪四个人"，
    跑这一条就够，不用真的开一局。
    """
    n = sc.seed_party(save_dir, jar, names, work)
    q = sc.quest_ctx()
    line = "=" * 64
    print(line)
    deads = [h for h in (sc.roster.heroes or {}).values() if h.get("dead")]
    print("[名册] %d 人，其中阵亡 %d 人%s" % (
        len(sc.roster.heroes), len(deads),
        ("：" + "、".join(x["name"] for x in deads[:12])) if deads else ""))
    if q:
        bits = [_txt(q.get("dungeon")), _txt(q.get("quest"))]
        if q.get("difficulty"):
            bits.append("难度 " + _txt(q["difficulty"]))
        if q.get("length"):
            bits.append("尺寸 " + q["length"])
        print("[这一趟] " + " · ".join(x for x in bits if x))
    try:
        rev = sc.revived_heroes()
    except Exception:
        rev = []
    if rev:
        print("[复活] " + "；".join("%s（第 %d 周记录中阵亡，现已复活）" % (n, c)
                                    for n, c in rev[:6]))
    print("[队伍] %d 人（来源：%s）" % (len(sc.party_context()),
                                    getattr(sc, "seed_source", "") or "未读到"))
    if not n:
        print("       还没读到队伍：等第一场战斗的全场刷新；确认游戏确实在副本里")
    for p in sc.party_context():
        tags = []
        if p.get("fallen"):
            tags.append("已阵亡")
        if p.get("affliction"):
            tags.append("折磨：" + p["affliction"])
        if p.get("virtue"):
            tags.append("美德：" + p["virtue"])
        hp = p.get("hp")
        if isinstance(hp, (int, float)):
            tags.append("濒死" if hp == 0 else "hp %g" % hp)
        print("  · %-8s %-16s 压力 %-4s %s" % (
            p["name"], p["class"], p.get("stress"),
            "，".join(tags) if tags else "正常"))
    print(line)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--log", default="", help="游戏 app.log；用 --status 时可不填")
    ap.add_argument("--roster", default=os.path.join(DATA, "probe", "roster.json"))
    ap.add_argument("--pack", default=os.path.join(DATA, "world", "default"),
                    help="本地化映射包目录（build_pack.bat 生成）")
    ap.add_argument("--loc", default="", help="兼容旧用法：直接指定 loc_zh.json")
    ap.add_argument("--game-json", default=os.path.join(DATA, "probe", "game.json"),
                    help="解码后的 persist.game.json，用于校验启用列表是否变了")
    ap.add_argument("--no-mod-check", action="store_true")
    ap.add_argument("--auto-build", action="store_true",
                    help="启用列表变化时自动重建映射包")
    ap.add_argument("--game", default=DEFAULT_GAME)
    ap.add_argument("--out", default=os.path.join(DATA, "out"))
    ap.add_argument("--config", default=os.path.join(DATA, "config.json"))
    ap.add_argument("--follow", action="store_true")
    ap.add_argument("--status", action="store_true",
                    help="只打印当前队伍与活死状态就退出（不跟随、不调用模型）")
    ap.add_argument("--llm", action="store_true")
    ap.add_argument("--emit-every", type=int, default=1)
    ap.add_argument("--window", type=int, default=2)
    ap.add_argument("--min-rounds", type=int, default=1)
    ap.add_argument("--salience", type=float, default=0.0,
                    help="显著性阈值：低于此分数的平淡回合不输出（建议 2.0~3.0）")
    ap.add_argument("--campaign", default=os.path.join(DATA, "probe", "campaign_log_named.json"))
    ap.add_argument("--decode-every", type=int, default=0,
                    help="跟随模式下每 N 秒自动解码一次存档刷新人物状态（需 java）")
    ap.add_argument("--jar", default=os.path.join(HERE, "tools", "DDSaveEditor.jar"))
    ap.add_argument("--names", default=os.path.join(HERE, "tools", "names.txt"))
    ap.add_argument("--save-dir", default=DEFAULT_SAVE)
    ap.add_argument("--from-start", action="store_true")
    ap.add_argument("--limit", type=int, default=0, help="最多输出几个场景（试跑省钱用）")
    ap.add_argument("--no-curio", action="store_true",
                    help="关闭奇物互动探测（它会额外解码 curio_tracker/map/raid 三份存档）")
    ap.add_argument("--dry", action="store_true", help="只打印 prompt，不调用模型（零成本核对事实）")
    args = ap.parse_args()
    if not args.log and not args.status:
        ap.error("需要 --log；只想核对队伍与活死状态可以用 --status")

    cfg = {}
    if os.path.exists(args.config):
        cfg = load_json_tolerant(args.config)
    try:
        from version import VERSION as _V, BUILD as _B
        print("[版本] DarkestTalk %s（build %s）" % (_V, _B or "dev"))
    except Exception:
        pass
    wb = os.path.join(args.pack, "world_bible.md")
    if "world_bible" not in cfg and os.path.exists(wb):
        text = open(wb, encoding="utf-8").read().strip()
        if text:
            cfg["world_bible"] = text

    pack = args.pack
    loc_path = args.loc or os.path.join(pack, "loc_zh.json")
    if not os.path.exists(loc_path):
        print("[缺少映射包] 没找到 %s" % loc_path)
        print("            先运行 build_pack.bat（或 loc_index.py）生成一次。")
        sys.exit(2)

    if not args.no_mod_check:
        changed = check_modlist(pack, args.game_json)
        if changed == "changed":
            print("[提示] 启用 mod 列表与映射包不一致（新增/移除了 mod）。")
            if args.auto_build:
                print("       正在重建映射包……")
                build_pack(args.game, args.game_json, pack)
            else:
                print("       新建/新增的 mod 文字可能显示为原始 id。跑一次 build_pack.bat 即可。")

    if args.llm and not cfg.get("api_key"):
        print("[注意] 没填 api_key，只解析不出文。打开 config.json 填上即可（或去掉 --llm 只看事实卡）。")

    loc = Loc(loc_path)
    roster = Roster(args.roster)
    print("[就绪] 本地化 %d 条；名册 %d 人" % (len(loc.zh), len(roster.heroes)))

    scribe = Scribe(loc, roster, window=args.window,
                    emit_every=args.emit_every, min_rounds=args.min_rounds,
                    salience=args.salience)
    scribe.cfg = cfg          # 同一份 dict，配置热重载后这里自动跟着变
    scribe.campaign = Campaign(args.campaign, loc)
    sink = Sink(args.out)

    written = [0]

    def drain():
        for sc in scribe.scene_queue:
            if args.limit and written[0] >= args.limit:
                scribe.scene_queue.clear()
                return
            written[0] += 1
            # 一张卡的数据出问题，不应该连带整条流水线一起死：
            # 以前 scene_to_facts 抛一个 TypeError，跟随后台进程就直接退出，
            # 覆盖窗跟着被收掉，看起来就是"整晚白打、程序闪退"。
            try:
                facts = scene_to_facts(sc, loc)
            except Exception:
                print("[跳过] 这一拍的事实卡生成失败（%s）：" % sc.get("kind"))
                traceback.print_exc()
                continue
            sink.write_scene(sc, facts)
            print("\n" + facts)
            # 回城那一幕不用出击档案：这一趟已经结束，重点是这一周镇上发生了什么
            # 回城也带队伍档案：这一周归来的四个人，身上的病和怪癖正是该写的
            pack = scribe.party_pack_text(sc.get("kind", ""))
            changes = [] if sc.get("kind") == "town" else scribe.take_changes()
            bible = scribe.party_bible_text((cfg.get("world_bible") or "").strip())
            if args.dry:
                sp, up = build_prompt(sc, loc, cfg, pack=pack, bible=bible,
                                      changes=changes)
                print("  [prompt]\n" + "\n".join("    " + x for x in up.splitlines()))
            elif args.llm and cfg.get("api_key"):
                sp, up = build_prompt(sc, loc, cfg, pack=pack, bible=bible,
                                      changes=changes)
                try:
                    text = call_llm(sp, up, cfg)
                    sink.push_live(text)
                    sink.push_debug(sc, sp, up, text, cfg)
                    print("  → " + text.replace("\n", " ")[:180])
                except Exception as e:
                    print("  [LLM失败] %s" % e)
        scribe.scene_queue.clear()

    _roster_mtime = [0.0]

    def refresh_roster():
        """调用解码器把二进制存档转成 JSON，刷新血量 / 压力 / 怪癖。

        每 20 秒跑的那一次以前不管存档有没有变都会起一个 JVM（约 0.5 秒 CPU），
        在游戏里就是一卡一卡的那种顿。这里先看源文件 mtime：没变就不再解码。
        """
        if not (os.path.exists(args.jar) and os.path.exists(args.names)):
            return
        src = os.path.join(args.save_dir, "persist.roster.json")
        if not os.path.exists(src):
            return
        try:
            m = os.path.getmtime(src)
        except OSError:
            return
        if m == _roster_mtime[0]:
            return
        try:
            if decode_save(args.jar, src, args.roster, args.names, timeout=30):
                roster.reload()
                _roster_mtime[0] = m
        except Exception:
            pass

    scribe.refresh_fn = refresh_roster
    # 进副本那一刻（quest_start）用它补队伍：先读 persist.raid.json，再退到任务记录
    scribe.seed_fn = lambda: scribe.seed_party(
        args.save_dir, args.jar, args.names, os.path.join(DATA, "probe"))
    scribe.state_path = os.path.join(args.out, "raid_state.json")
    dpath = os.path.join(args.pack, "diseases.json")
    if os.path.exists(dpath):
        try:
            scribe.diseases = set(json.load(open(dpath, encoding="utf-8")))
        except Exception:
            pass

    def load_town_event():
        """结算时解一次 persist.town_event.json，取本趟触发的小镇事件。"""
        src = os.path.join(args.save_dir, "persist.town_event.json")
        dst = os.path.join(DATA, "probe", "town_event.json")
        if not (os.path.exists(args.jar) and os.path.exists(src)):
            return {}
        return decode_save(args.jar, src, dst, args.names) or {}

    scribe.town_event_fn = load_town_event

    def load_campaign_log():
        src = os.path.join(args.save_dir, "persist.campaign_log.json")
        dst = os.path.join(DATA, "probe", "campaign_log_live.json")
        if not (os.path.exists(args.jar) and os.path.exists(src)):
            return {}
        return decode_save(args.jar, src, dst, args.names) or {}

    scribe.town_log_fn = load_campaign_log

    if args.status:
        if getattr(scribe, "campaign", None) is not None:
            scribe.campaign.loader = load_campaign_log
            try:
                scribe.campaign.refresh(force=True)
            except Exception:
                pass
        print_status(scribe, args.save_dir, args.jar, args.names,
                     os.path.join(DATA, "probe"))
        return
    # 跟随模式必须读实时存档：静态的 --campaign 文件会停留在上一次解码的时刻
    if args.follow and getattr(scribe, "campaign", None) is not None:
        scribe.campaign.loader = load_campaign_log
        scribe.campaign.refresh(force=True)

    if args.follow:
        refresh_roster()                  # 先拿一份最新名册，墓园/复活都以它为准
        n = scribe.seed_party(args.save_dir, args.jar, args.names,
                              os.path.join(DATA, "probe"))
        nd = scribe.seed_known_dead()
        if nd:
            print("[墓园] %d 人在墓园里（如果他们回来，会播报「复活」）" % nd)
        if n:
            print("[队伍] 从存档补上 %d 人（本次是从副本中途启动的）" % n)
        else:
            print("[队伍] 还没读到队伍：等下一次进副本（quest_start）就会补上")

    watch = None
    if args.follow and not getattr(args, "no_curio", False):
        watch = CurioWatch(args.save_dir, args.jar, args.names, loc, roster, DATA)
        scribe.curio_watch = watch

    if not args.follow:
        with open(args.log, "rb") as f:
            data = f.read()
        for line in data.decode("utf-8", errors="replace").splitlines():
            if args.limit and written[0] >= args.limit:
                break
            if line.startswith("["):
                scribe.feed(line.rstrip("\r"))
        scribe.close_area()
        scribe.close_round(force=True)
        scribe.close_camp(scribe.last_ts or "")
        drain()
        print("\n[完成] 输出在 %s" % args.out)
        return

    cfg_mtime = os.path.getmtime(args.config) if os.path.exists(args.config) else 0

    def reload_cfg_if_changed():
        nonlocal cfg_mtime
        if not os.path.exists(args.config):
            return
        m = os.path.getmtime(args.config)
        if m == cfg_mtime:
            return
        cfg_mtime = m
        try:
            new = load_json_tolerant(args.config)
            cfg.clear()
            cfg.update(new)
            print("[配置] 已重载提示词 / 温度 / 字数上限")
        except Exception:
            pass

    last_decode = 0.0
    if args.decode_every:
        refresh_roster()
        last_decode = time.time()

    offset = 0 if args.from_start else os.path.getsize(args.log)
    print("[跟随] %s，Ctrl+C 结束" % ("从头" if args.from_start else "从当前末尾"))
    buf = b""
    try:
        quit_flag = os.path.join(args.out, ".quit")
        while True:
            # 覆盖窗右上角的 × 会写这个文件：收到就收工，别留下僵尸进程
            if os.path.exists(quit_flag):
                print("\n[退出] 收到关闭请求，停止跟随。")
                try:
                    os.remove(quit_flag)
                except Exception:
                    pass
                break
            size = os.path.getsize(args.log)
            if size < offset:
                offset, buf = 0, b""
            if size > offset:
                with open(args.log, "rb") as f:
                    f.seek(offset)
                    chunk = f.read()
                    offset = f.tell()
                buf += chunk
                text = buf.decode("utf-8", errors="replace")
                cut = text.rfind("\n")
                # 先拿新名册、先认复活，再喂这一块的日志：要是这块里正好有
                # town_visit_start（回城那一拍），复活这行才能接在镇上事件后面，
                # 而不是等下一次进城镇界面才补上。
                roster.reload()
                scribe.reconcile_dead(notify=True)
                if cut >= 0:
                    for line in text[:cut].splitlines():
                        if line.startswith("["):
                            # 一行怪日志不该把整晚的跟随打断：记下来，跳过它
                            try:
                                scribe.feed(line.rstrip("\r"))
                            except Exception:
                                print("[跳过] 解析这一行失败：%s" % line[:120])
                                traceback.print_exc()
                    buf = text[cut + 1:].encode("utf-8")
                drain()
            if watch is not None and cfg.get("curio_on", True):
                watch.party_guids = {g for (g, c) in scribe.heroes_in_raid}
                try:
                    evs = watch.poll()
                except Exception:
                    evs = []
                for ev in evs:
                    scribe.on_curio(ev, time.strftime("%Y-%m-%d %H:%M:%S"))
                if evs:
                    drain()
            reload_cfg_if_changed()
            if args.decode_every and time.time() - last_decode > args.decode_every:
                refresh_roster()
                # 20 秒一次的新名册：这里最容易发现"有人被复活了"
                scribe.reconcile_dead(notify=True)
                drain()
                last_decode = time.time()
            time.sleep(0.4)
    except KeyboardInterrupt:
        # 攒在合并窗口里的奇物记录，退出前也要落地，别白丢一次互动
        if watch is not None:
            try:
                for ev in watch.flush(force=True):
                    scribe.on_curio(ev, scribe.last_ts or "")
            except Exception:
                pass
        scribe.close_area()
        scribe.close_round(force=True)
        scribe.close_camp(scribe.last_ts or "")
        drain()


if __name__ == "__main__":
    main()

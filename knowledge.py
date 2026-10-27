# -*- coding: utf-8 -*-
"""knowledge.py —— 世界知识的解析、覆盖层与重建后重新套用

世界知识是 loc_index.write_knowledge() 按本机 mod 扫出来的四份 Markdown：
  knowledge/heroes.md     英雄职业 + 公会/铁匠铺/营地介绍
  knowledge/monsters.md   敌人 + 招式
  knowledge/items.md      物品名 + 描述
  knowledge/trinkets.md   饰品名 + 效果

玩家可以改每一条的「介绍正文」，但不能改标题与方括号里的关键词 ——
那两个是拿去和存档里的 id 匹配用的，改了就对不上了。

改动不写回自动生成的 Markdown（那些文件每次重建都会被覆盖），而是单独存进
knowledge_overrides.json（同一目录）。读取时 load_items() 把覆盖层叠上去，
所以换 mod、知识库增删都不会把玩家的编辑冲掉。
"""
import json
import os
import re
import time

KINDS = ("heroes", "monsters", "items", "trinkets")
KIND_CN = {"heroes": "英雄", "monsters": "敌人", "items": "物品", "trinkets": "饰品"}
OVERRIDES = "knowledge_overrides.json"


# ---------------------------------------------------------------- 覆盖层
def overrides_path(pack):
    return os.path.join(pack, OVERRIDES)


def load_overrides(pack):
    """返回 {key: {"body":..., "title":..., "updated":...}}；坏文件按空处理。"""
    p = overrides_path(pack)
    if not os.path.exists(p):
        return {}
    try:
        d = json.load(open(p, encoding="utf-8"))
    except Exception:
        return {}
    items = d.get("items") if isinstance(d, dict) else None
    return items if isinstance(items, dict) else {}


def _write_overrides(pack, items):
    p = overrides_path(pack)
    data = {"_note": "玩家手动编辑的世界知识；知识库重建后会重新套用。"
                     "键 = 类型:方括号里的 id；只存正文，标题与关键词仍由扫描生成。",
            "items": items}
    tmp = p + ".tmp"
    json.dump(data, open(tmp, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    os.replace(tmp, p)
    return True


def save_override(pack, key, body, title=""):
    """写入/更新一条覆盖。body 为空视为「还原成自动生成」。"""
    items = load_overrides(pack)
    body = (body or "").strip()
    if not body:
        items.pop(key, None)
    else:
        items[key] = {"body": body, "title": title or "",
                      "updated": time.strftime("%Y-%m-%d %H:%M:%S")}
    _write_overrides(pack, items)
    return True


def drop_override(pack, key):
    items = load_overrides(pack)
    if key in items:
        items.pop(key)
        _write_overrides(pack, items)
        return True
    return False


# ---------------------------------------------------------------- 解析
def _split_tags(s):
    return [x.strip() for x in s.split(",") if x.strip()]


def _parse_heroes(lines):
    out = []
    i, n = 0, len(lines)
    while i < n:
        ln = lines[i]
        m = re.match(r"^##\s+(.+?)\s+\[([^\]]+)\]\s*$", ln)
        if not m:
            i += 1
            continue
        title, tag = m.group(1).strip(), m.group(2).strip()
        j = i + 1
        body = []
        while j < n and not lines[j].startswith("## "):
            s = lines[j].rstrip()
            if s.startswith("- "):
                body.append(s[2:])
            elif s.strip() and body:          # 介绍里的续行：并到上一行
                body[-1] += s.strip()
            j += 1
        out.append({"kind": "heroes", "key": "heroes:%s" % tag, "title": title,
                    "tags": [tag], "body": "\n".join(body), "star": False,
                    "start": i, "end": j})
        i = j
    return out


def _parse_line(kind, ln, idx):
    """- 名称 [id] … 形式的一行（物品 / 饰品 / 敌人）。"""
    if not ln.startswith("- "):
        return None
    rest = ln[2:]
    m = re.search(r"\s\[([^\]]+)\]", rest)
    if not m:
        return None
    title = rest[:m.start()].strip()
    tag = m.group(1).strip()
    tail = rest[m.end():]
    star = "★" in tail
    if kind == "monsters":
        # - 名称：招式　[ids]
        sep = rest.find("：")
        if sep > 0:
            title = rest[:sep].strip()
            body = rest[sep + 1:m.start()].strip()
        else:
            body = ""
        tags = _split_tags(m.group(1))
    elif kind == "items":
        b = re.search(r"（(.*)）\s*$", tail)
        body = (b.group(1) if b else "").strip()
        tags = [tag]
    else:                                    # trinkets
        body = re.sub(r"^\s*——\s*", "", tail).strip()
        tags = [tag]
    return {"kind": kind, "key": "%s:%s" % (kind, tags[0] if tags else tag),
            "title": title, "tags": tags, "body": body, "star": star,
            "start": idx, "end": idx + 1}


def parse_file(path, kind):
    try:
        lines = open(path, encoding="utf-8").read().splitlines()
    except Exception:
        return []
    if kind == "heroes":
        return _parse_heroes(lines)
    out = []
    for i, ln in enumerate(lines):
        it = _parse_line(kind, ln, i)
        if it:
            out.append(it)
    return out


def load_items(pack):
    """四份知识全部解析出来，并把玩家覆盖过的正文替换进去。"""
    base = os.path.join(pack, "knowledge")
    ov = load_overrides(pack)
    items, seen = [], {}
    for kind in KINDS:
        for it in parse_file(os.path.join(base, "%s.md" % kind), kind):
            key = it["key"]
            if key in seen:
                seen[key] += 1
                it["key"] = "%s#%d" % (key, seen[key])
            else:
                seen[key] = 1
            o = ov.get(it["key"]) or ov.get(key)
            if o and o.get("body"):
                it["body"] = o["body"]
                it["edited"] = True
            items.append(it)
    return items

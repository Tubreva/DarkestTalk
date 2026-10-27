# Nexus Mods 上架资料（DarkestTalk 0.2）

> 说明：这份文件**不进发行包**，只是给你复制粘贴用。

## 基本信息

| 字段 | 建议填写 |
|---|---|
| Game | Darkest Dungeon |
| Category | Utilities（或 Miscellaneous） |
| Mod name | DarkestTalk - AI Narrator Overlay |
| Version | 0.2 |
| File name | `DarkestTalk_0.2.zip` |
| Adult content | No |
| Mod manager download | 关闭（仅手动下载；不是 mod，不需要装进 mods 目录） |
| Requirements | Python 3.9–3.13、Java 8+（可选但强烈建议）、OpenAI 兼容 API key |

## Summary（≤250 字符）

EN:
> A desktop overlay that turns Darkest Dungeon's log into short AI-written narration for every fight, room, curio and quest result. Chinese + English UI, more languages via drop-in files. Requires Python + your own OpenAI-compatible API key. No game files included.

ZH（如果页面允许双语，放描述里）:
> 读取游戏日志、为每场战斗/每个房间/奇物/结算生成一小段 AI 旁白的悬浮窗。内置中英双语；
> 需要自带 Python、Java 和 OpenAI 兼容接口的 key。压缩包内不含任何游戏文件。

## Description（英文，直接粘贴）

```
[h1]What this is[/h1]
DarkestTalk is a desktop overlay for Darkest Dungeon (Steam/GOG). It tails the game's
log, works out what just happened - a combat round, entering a room, looting a curio,
camping, finishing or abandoning a quest - and asks an OpenAI-compatible model to write
one short paragraph about it. The text appears in a transparent, click-through window
above the game.

[h1]Languages[/h1]
The launcher has a language menu in the top-right corner. Chinese and English are
built in and cover the whole interface (launcher, overlay, settings, knowledge and
debug windows). Switching redraws everything immediately and is remembered.

Translations are plain JSON files in lang\. To add a language, copy lang\en_US.json,
change _meta.code / _meta.native, translate the values (keys are the Chinese source
strings, placeholders like %s must stay), save as UTF-8 and drop it in - it shows up
in the menu, no code changes. Missing keys fall back to Chinese, so a half-finished
translation is still usable. See lang\README.txt.

[h1]Requirements[/h1]
[list]
[*]Windows 10 / 11 (64-bit)
[*]Python 3.9 - 3.13 from python.org (tick "Add Python to PATH")
[*]Java 8 or newer - used to decode save files. Without it the tool still runs, but names
    fall back to internal IDs.
[*]An OpenAI-compatible API endpoint + key: DeepSeek, Moonshot/Kimi, Qwen, OpenRouter, or
    a local Ollama (http://localhost:11434/v1). [b]No key is included[/b] and the tool
    does not proxy your traffic - requests go straight from your PC to the endpoint you
    configure.
[/list]

[h1]Installation[/h1]
[list=1]
[*]Extract the archive anywhere [b]except[/b] the game's "mods" folder. It is a standalone
    desktop tool, not a mod, and it is not loaded by the game.
[*]Double-click [b]DarkestTalk.exe[/b]. A launcher window opens and checks your setup:
    game folder, save folder, API key, Java, name library.
[*]Click "开始旁白" (Start). The overlay appears; the launcher hides itself.
[*]Press [b]F8[/b] -> section "⑤ 接口" -> fill base_url, model and api_key ->
    "保存并生效" (Save & apply). Changes take effect within a second.
[*]Run the game in [b]Windowed[/b] or [b]Borderless[/b] mode. Exclusive fullscreen will
    cover the overlay.
[/list]

[h1]Where your data lives[/h1]
Everything writable goes to [b]<Darkest Dungeon install folder>\\DarkestTalkData\\[/b]:
config.json (your API key and custom prompts), overlay position, the name library built
from your own enabled mods, and logs. The tool folder contains code only, so you can zip
and share it without leaking your key. To reset to a fresh state, rename that folder.

[h1]What it can and cannot do[/h1]
[list]
[*]Combat: one card every N rounds (configurable), plus a guaranteed card at the end of
    every battle - including boss kills on odd rounds.
[*]Non-combat: entering a room/corridor, camping, curios, quest start, quest result and
    returning to town each get their own card.
[*]A "salience" setting filters out quiet combat rounds (misses, buffs, stuns).
[*]The model only sees structured facts extracted from the log - it does not invent
    party members or loot. If a fact is unknown, the line is left out.
[*]It does not read or write your save files (the bundled decoder only reads them).
[*]No AI-generated assets are bundled. Narration text is produced at runtime, on your
    machine, by the endpoint you configure - nothing is pre-generated or shipped.
[/list]

[h1]Known limitations[/h1]
[list]
[*]Chinese UI only, for now.
[*]The exe is unsigned, so SmartScreen or an antivirus may warn on first run. If it is
    blocked, run "python launcher.py" in the same folder - identical behaviour.
[*]With 200+ mods installed, the first launch spends ~10-20 seconds building the name
    library (once; rebuilt only when your mod list changes).
[*]Tested on Windows 10/11 at 1920x1080 and 2560x1600, 100-125% scaling.
[/list]

[h1]Feedback[/h1]
Press F8 -> "导出日志" (Export logs). It writes a folder containing the session log, the
exact prompts and responses sent to the model, and environment info - with your API key
and user name masked - and opens it. Attach that folder to your bug report.

[h1]Credits[/h1]
[list]
[*]Save decoding: [url=https://github.com/robojumper/DarkestDungeonSaveEditor]DarkestDungeonSaveEditor[/url]
    by robojumper (MIT), redistributed unmodified.
[*]No assets from Darkest Dungeon are included. Darkest Dungeon is (c) Red Hook Studios.
[/list]
```

## Description（中文，可放在英文之后）

```
[h1]这是什么[/h1]
读取《暗黑地牢》日志，把「战斗回合 / 进入房间 / 奇物互动 / 扎营 / 任务开始与结算 / 回城」
整理成事实，交给 OpenAI 兼容接口写一小段旁白，显示在游戏上方的透明悬浮窗里。
界面为中文；需要自带 Python、Java 和一个 API key。
压缩包内不含任何 AI 生成的素材——旁白是你本机运行时向你自己配置的接口请求生成的。

[h1]安装[/h1]
解压到任意位置（不要解压进 mods）→ 双击 DarkestTalk.exe → 点「开始旁白」→
按 F8 在「⑤ 接口」填 base_url / model / api_key → 保存并生效。游戏用窗口化或无边框全屏。

[h1]数据与隐私[/h1]
所有可写内容都在 <游戏目录>\DarkestTalkData\：key、自定义提示词、译名库、日志。
工具文件夹里只有程序，整个文件夹可以放心分享。压缩包里不含任何游戏文件，也不含 key。
```

## Permissions（按你的偏好勾选，建议）

| 项 | 建议 |
|---|---|
| 允许他人修改我的文件 | 允许（但需署名） |
| 允许他人转载到其他站点 | 允许（需署名并链接原页） |
| 允许在付费内容中使用 | 不允许 |
| 允许用于合集/整合包 | 允许（需署名） |

## 提交前检查清单

- [ ] 上传文件是 `dist\DarkestTalk_0.1.zip`（有顶层目录、无 `__pycache__`、文件名是 ASCII）
- [ ] 页面写明「中文界面」「需要自己准备 key」
- [ ] 至少 4 张截图：启动器、设置页、游戏内旁白、世界知识/调试记录
- [ ] 版本号与文件名一致（0.2）
- [ ] 先传到「未发布/隐藏」状态自测一次，再公开
- [ ] 首次上传后等 Nexus 扫描（带 exe，可能进人工审核队列）

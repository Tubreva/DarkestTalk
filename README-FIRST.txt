DarkestTalk 0.2 — READ ME FIRST / 先读我
========================================

ENGLISH
-------
What it is: a desktop overlay that watches Darkest Dungeon's log and writes a short
narrative line for each combat round / room / curio / quest result, using an
OpenAI-compatible LLM. Interface: Chinese and English (switch in the launcher).

Requirements
  * Windows 10 / 11 (64-bit)
  * Python 3.9 – 3.13 from python.org (tick "Add Python to PATH" during setup)
  * Java 8 or newer (used to decode save files; without it names fall back to IDs)
  * An OpenAI-compatible API key (DeepSeek, Moonshot, Qwen, OpenRouter, or a local
    Ollama at http://localhost:11434/v1). No key is included.

Install
  1. Extract this archive anywhere EXCEPT the game's "mods" folder.
  2. Double-click DarkestTalk.exe.
     Pick your language in the top-right box (简体中文 / English).
     The launcher shows: game folder / save folder / API / Java / name library.
  3. Click "开始旁白" (Start). The overlay appears and the launcher hides itself.
  4. Press F8 → section "⑤ 接口" → fill base_url, model and api_key → "保存并生效".
  5. Run the game in Windowed or Borderless mode.

Where your data lives
  <Darkest Dungeon install folder>\DarkestTalkData\
  (config.json with your key and prompts, overlay position, name library, logs)
  Nothing personal is stored inside the tool folder, so sharing the folder is safe.
  To start over, rename that folder.

Stopping: double-click DarkestTalk.exe again, it will offer "停止" (Stop).
Uninstall: delete this folder; delete DarkestTalkData next to the game to remove data.

Troubleshooting
  * Antivirus / SmartScreen warning: the exe is unsigned. Allow it, or run
    "python launcher.py" in the same folder instead.
  * Overlay hidden by the game: switch to Windowed or Borderless fullscreen.
  * Feedback: F8 → "导出日志" creates a zip-friendly folder with logs, the exact
    prompts sent to the model, and environment info (API key masked).

中文
----
这是什么：读取《暗黑地牢》日志，在屏幕上方显示旁白的小工具，用 OpenAI 兼容接口
（DeepSeek 等）为战斗回合、房间、奇物、任务结算各写一小段。界面支持中英双语，
启动器右上角可切换。

运行需要
  * Windows 10 / 11 64 位
  * python.org 的 Python 3.9–3.13（安装时勾选 Add Python to PATH）
  * Java 8+（解码存档用；没有也能跑，只是名字会显示成 id）
  * 一个 OpenAI 兼容接口的 key（DeepSeek / Kimi / 通义 / OpenRouter，或本地 Ollama）。
    压缩包里没有任何 key。

安装
  1. 解压到任意位置，**不要**解压进游戏的 mods 文件夹。
  2. 双击 DarkestTalk.exe，启动器会列出：游戏目录 / 存档 / 接口 / Java / 译名库。
  3. 点「开始旁白」，悬浮窗出现后启动器自动收起。
  4. 按 F8 →「⑤ 接口」填 base_url、model、api_key，点「保存并生效」。
  5. 游戏用「窗口化」或「无边框全屏」。

数据位置
  <暗黑地牢安装目录>\DarkestTalkData\（config.json 含 key 与提示词、窗口位置、
  译名库、日志）。工具文件夹里不含任何个人数据，整个文件夹可以放心分享。
  想重来一遍，把这个目录改个名即可。

停止：再双击一次 DarkestTalk.exe，会显示「停止」。
卸载：删掉本文件夹；想清干净，同时删掉游戏目录下的 DarkestTalkData。

常见问题
  * 杀软 / SmartScreen 拦截：exe 未签名。放行即可，或在同目录执行
    python launcher.py，效果一样。
  * 悬浮窗被游戏盖住：请用窗口化 / 无边框全屏。
  * 反馈：F8 →「导出日志」，会生成含日志、完整 prompt、环境信息的文件夹（key 已打码）。

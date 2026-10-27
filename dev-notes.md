# 开发笔记（内部，不进发行包）

记录那些「花几个小时才搞明白、三个月后会忘」的结论。按主题分，不按时间。

## 日志与事件

- 游戏**很少**打 `quest_start` / `quest_end`：实测 5 小时各只出现 1 次
  （同期 `kill_monster` 92 次、`victory` 14 次、`town_visit_start` 4 次）。
  所以出发/结算只能靠兜底：
  - 出发 = 上一次在镇上（`_at_town`）+ 第一次出现房间/走廊（`move to area`）；
  - 结算 = 回城（`town_visit_start`）时这一趟还没结算 → `_settlement(reason="return")`。
- `victory` 与 `combat_start` 才是战斗边界的可靠信号；撤退/逃跑没有专门的行，
  所以换区域时如果还挂着 battle，要强制收尾。
- `Narration: Not queueing /vo/...` 这类行**不会**产生 entry（被音频队列挡了），
  不能当成事件。

## 存档

- 判死活只看 `persist.roster.json` 的 `roster.status`（0 在镇 / 1 出击 / 3 阵亡），
  `current_hp` 不可信（回城后死者会写成 1.0）。
- 存档字段不保证是字符串：mod 的地牢/任务是哈希整数（`dungeon_type=17418668`、
  `quest=657224992`），拼 `" · ".join()` 会 `TypeError`。一律先过 `_txt()`。
- `persist.curio_tracker.json` 的 `tracked_results`：
  - 是**按 (奇物, 用品) 排序的表**，不是时间序（下标按 `prop_name_id` 升序）。
    插一条中间记录，后面下标整体后移 → 绝不能用条数/下标判断新旧。
  - **一次互动可能写成多行**（不同用品/不同结果，分几次落盘）→ 读到时先攒 3.5 秒再合并。
  - 解码一份约 0.4 秒（要起 JVM），所以只在 mtime 变化时解。
- 战役日志里第 N 周的出发记录、第 N+1 周才落结果：结算时 `finished_record` 通常还是空，
  别把上一趟的战果安过来。
- 映射库 `manifest.json` 的 `host` 是本机标识（游戏路径+计算机名+用户名哈希）。
  发行包里要删掉它，程序看到 host 为空就只比 mod 指纹（见 `launch.pack_status`）。

## 界面（Tk）

- `tk.Text` 的开销与**总字数**线性相关：1400 字时插入一次 + `see()` 约 130ms，
  3000 字 400ms，6000 字 1.2s。所以悬浮窗正文改成 Canvas 自绘、只画可见的几行。
- 不要在每张卡片上 `update_idletasks()`：40 张卡 = O(n²) 布局冲刷，打开就卡两秒。
- 抓图核对 UI 时要考虑 DPI：Tk 进程若 DPI-unaware，`winfo_*` 是逻辑像素，
  `PIL.ImageGrab` 是物理像素，直接按逻辑坐标裁会错位。用 `canvas.bbox()` 核对更准。
- 改无边框窗口的 `-alpha` 会把 exstyle（NOACTIVATE/TRANSPARENT）冲掉，
  设完 alpha 必须重新 `apply_exstyle()`。

## LLM

- `max_tokens` 是回复上限，不是字数建议：300 token ≈ 380 汉字，正好切在句子中间。
  代码里小于 800 的值按 1200 发，并在 `finish_reason=="length"` 时打警告。
- 推理型模型（`*-reasoner`）会把 token 全花在思考上，正文为空 —— 旁白要用 chat 型。

## 进程与配置

- `pythonw` 下 stdout 落到文件是**全缓冲**的：崩溃前日志可能是空的。子进程加 `-u`。
- 覆盖窗和主程序是两个进程，覆盖窗的 × 是写 `out/.quit` 让主程序自己退。
- 配置重载靠 mtime；F8 保存后主程序 1–2 秒内自动重载，不用重启。

## 还没做 / 待验证

- 无边框全屏下置顶被顶掉的概率（现在靠 2 秒一次的 SetWindowPos 压住）。
- 1440p / 150% 缩放下的字号与行距（只验证过 1080p / 125%）。
- 撤退（主动逃离）与任务失败的结算文案区分：目前兜底写「任务结束」，不写「任务完成」。

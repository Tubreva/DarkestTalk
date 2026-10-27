DarkestTalk 语言包 / Language packs
===================================

放一个 JSON 文件进来，启动器的语言下拉框里就会多一项，不需要改代码。

文件格式（UTF-8）：

  {
    "_meta": {
      "code": "ja_JP",          // 语言代码，建议 <语言>_<地区>
      "native": "日本語",        // 下拉框里显示的名字（用该语言自己的写法）
      "author": "your name",
      "version": 1
    },
    "strings": {
      "启动器": "ランチャー",
      "开始旁白": "開始"
    }
  }

怎么翻译：
  1. 复制 en_US.json，改 _meta 的 code / native，保存为 ja_JP.json。
  2. 键（左边）**不要动**，那是程序里的原文；
     把值（右边）换成你的语言。没翻的键留空即可，会自动显示中文原文。
  3. 键里的 %s / %d / %.1f 是占位符，必须原样保留（顺序可以调）。
  4. 保存成 UTF-8（不要带 BOM 也行，读取时会自动处理）。

放进本目录后在启动器里选一次即可；没出现就点启动器右上角的下拉框，
或重启 DarkestTalk.exe。想让作者收录，把文件发到 mod 页面的评论区。

Translating (English):
  1. Copy en_US.json, change _meta.code / _meta.native, save as <code>.json.
  2. Keep the keys (left side) untouched — they are the Chinese source strings.
     Translate the values. Empty values fall back to Chinese.
  3. Keep placeholders like %s, %d, %.1f.
  4. Drop the file into this folder; it appears in the launcher's language menu.

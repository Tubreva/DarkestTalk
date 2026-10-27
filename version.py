# -*- coding: utf-8 -*-
"""版本号的唯一来源。

改版本只改这里：启动横幅、日志会话头、F8 设置页标题、导出的日志包都读它。
"""

APP_NAME = "DarkestTalk"
VERSION = "0.2"
BUILD = "2026-10-27"          # 打包日期（发布前改）
STAGE = "beta"                # beta / stable

FULL = "%s %s" % (APP_NAME, VERSION)
TAG = "v%s" % VERSION if STAGE == "stable" else "v%s-%s" % (VERSION, STAGE)

# -*- coding: utf-8 -*-
"""
作废.py —— 把长期库里过时的条目就地标成 deprecated（不再被检索召回）。

状态写在条目自己身上，行尾追加一截 HTML 注释（markdown 不显示、不破坏列表解析）：
  - 阿舟住在临川。 <!-- status: deprecated | since: 2026-11-06 | by: 搬到南屿了 -->

没有标记的条目一律视为 active。这个脚本只加标记，绝不批量改写存量条目。

用法：
  python memory\\作废.py 临川 --reason "搬到南屿了" --dry
  python memory\\作废.py 临川 --reason "搬到南屿了"
  python memory\\作废.py 白噪音 --reason "改了习惯"

  --dry            只打印要改哪几行，不动文件
  --reason/-r      必填，写进 by: 字段
  多个关键词 = 必须同时出现（AND），避免误伤

行为：
  - 只在 长期\\*.md 的「- 」条目行上动手；压缩.md 跳过（已经是墓碑）
  - 已经是 deprecated 的跳过并报出来
  - 一个都没匹配到 → 打印原因并以退出码 1 结束，不静默成功
  - 真改之前把源文件备份成 <文件>.bak-<yyyymmdd-HHMM>

这个文件现在只是 memory\\core.py 的命令行壳：扫描、打标、搬账本指纹都在 core.deprecate()，
参数、输出、退出码一个字都没动。
"""
import os
import sys

# 根目录/扫描/打标/备份/搬指纹全部在 core 里（core 自己认 LAYERED_MEMORY_HOME）。
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import core  # noqa: E402


def parse_args(argv):
    words, reason, dry = [], None, False
    i = 0
    while i < len(argv):
        a = argv[i]
        if a == "--dry":
            dry = True
        elif a in ("--reason", "-r"):
            i += 1
            if i >= len(argv):
                sys.exit("--reason 后面要跟原因")
            reason = argv[i]
        elif a.startswith("--reason="):
            reason = a.split("=", 1)[1]
        elif a.startswith("-"):
            sys.exit("不认识的参数：%s" % a)
        else:
            words.append(a)
        i += 1
    return words, reason, dry


def main():
    words, reason, dry = parse_args(sys.argv[1:])
    if not words:
        sys.exit("用法: python memory\\作废.py 关键词 [关键词2] --reason \"作废原因\" [--dry]")
    if not reason or not reason.strip():
        sys.exit("少了 --reason \"作废原因\"（要写进条目，将来自己看得懂）")

    res = core.deprecate(words, reason=reason, dry=dry)
    if not res["hits"]:
        sys.stderr.write("没找到同时包含 %s 的条目，什么都没改。\n"
                         % "、".join("「%s」" % w for w in words))
        sys.exit(1)

    for h in res["hits"]:
        if h["deprecated"]:
            print("跳过（已是 deprecated）：%s:%d  %s" % (h["file"], h["line"], h["text"][:90]))

    if not res["todo"]:
        print("匹配到 %d 条，但都已经作废过了，没东西可改。" % len(res["hits"]))
        return

    print("-- 要标作废的条目（%d 条）--" % len(res["todo"]))
    for h in res["todo"]:
        print("%s:%d  %s" % (h["file"], h["line"], h["text"]))

    if dry:
        print("\n（--dry：只是预览，文件没动。原因会写成 by: %s）" % res["reason"])
        return

    for w in res["written"]:
        print("改完 %s（备份：%s）" % (w["file"], w["backup"]))

    if not res["ledger_readable"]:
        print("！记忆账本.json 读不出来，跳过指纹搬家；下次 --sync 会补上。")
    elif res["changed"] and not res["ledger_moved"]:
        print("（记忆账本.json 里没有这些条目的指纹，未搬动）")
    elif res["ledger_moved"]:
        print("记忆账本.json：搬了 %d 条指纹（--verify 不会误报丢失）" % res["ledger_moved"])

    # 回读核对：标过的行必须真的带 deprecated
    print("\n标了 %d 条作废。回读核对：仍为 active 的 %d 条。" % (res["changed"], len(res["left"])))
    if res["left"]:
        for h in res["left"]:
            print("  ！没标上：%s:%d  %s" % (h["file"], h["line"], h["text"][:90]))
        sys.exit(1)


if __name__ == "__main__":
    main()

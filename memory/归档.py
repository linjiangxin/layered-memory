# -*- coding: utf-8 -*-
"""
归档.py —— 按遗忘曲线把「已经糊了、又不重要」的条目压成一句话。

默认只预览（dry-run）。加 -Apply 才真的动文件：
  1. 从 长期\\*.md 里删掉那条零碎条目
  2. 在 memory\\长期\\压缩.md 里留一行摘要
  3. 从 记忆权重.json 里移除

判定：R < 0.45 且 重要度 <= 3（关系与约定 5 / 事实库 4 的东西永不自动压）。

用法：
  python memory\\归档.py
  python memory\\归档.py -Apply
"""
import io, os, sys, json, datetime

# 根目录：优先 LAYERED_MEMORY_HOME，否则按脚本自身位置推（memory\归档.py 的根 = 上一级）。
ROOT = os.path.abspath(os.path.expanduser(
    os.environ.get("LAYERED_MEMORY_HOME", "").strip()
    or os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")))
MEM = ROOT
LONG = os.path.join(MEM, "长期")
WEIGHTS = os.path.join(MEM, "记忆权重.json")
ZIP = os.path.join(LONG, "压缩.md")
DECAY, FACTOR = 0.5, 19.0 / 81.0


def R(rec):
    t = max(0, (datetime.date.today() - datetime.date.fromisoformat(rec["last"])).days)
    return (1.0 + FACTOR * t / max(rec["S"], 0.1)) ** (-DECAY)


def main():
    if not os.path.exists(WEIGHTS):
        print("还没有 记忆权重.json，先跑记忆曲线.py")
        return
    with io.open(WEIGHTS, "r", encoding="utf-8") as f:
        db = json.load(f)

    doomed = [(R(r), k, r) for k, r in db.items() if R(r) < 0.45 and r["imp"] <= 3]
    doomed.sort()
    if not doomed:
        print("没有该压的条目（都还清楚，或者不够零碎）。")
        return

    print("-- 可以压成一句话 --")
    for r, text, rec in doomed:
        print("R=%.2f  [%s]  %s" % (r, rec["src"], text[:60]))

    if "-Apply" not in sys.argv:
        print("\n（只是预览。要真压就加 -Apply）")
        return

    # 1. 从源文件删行
    for r, text, rec in doomed:
        src = os.path.join(LONG, rec["src"])
        if not os.path.exists(src):
            continue
        with io.open(src, "r", encoding="utf-8") as f:
            lines = f.readlines()
        keep = [ln for ln in lines if not (ln.strip().startswith("- ") and ln.strip()[2:].strip() == text)]
        if len(keep) != len(lines):
            with io.open(src, "w", encoding="utf-8") as f:
                f.writelines(keep)

    # 2. 写压缩.md
    head = "" if os.path.exists(ZIP) else "# 压缩过的记忆\n\n> 忘得差不多、又不重要的旧条目，压成一句话留在这儿。原文删了，剩个影子。\n"
    with io.open(ZIP, "a", encoding="utf-8") as f:
        if head:
            f.write(head)
        f.write("\n## %s\n\n" % datetime.date.today().isoformat())
        for r, text, rec in doomed:
            f.write("- （原 %s）%s\n" % (rec["src"], text[:80]))

    # 3. 出库
    for _r, text, _rec in doomed:
        db.pop(text, None)
    with io.open(WEIGHTS, "w", encoding="utf-8") as f:
        f.write(json.dumps(db, ensure_ascii=False, indent=2))
    print("\n压掉了 %d 条，写进 memory\\长期\\压缩.md" % len(doomed))


if __name__ == "__main__":
    main()

# -*- coding: utf-8 -*-
"""
检索.py —— 带遗忘曲线的记忆检索（不是纯关键词匹配）。

打分 = 字面重合度 × 可提取度 R × 重要度
  - 重合度：中文按 2 字滑窗算 Dice 系数（对短词友好）
  - R：FSRS 幂律遗忘曲线 R = (1 + 19/81 * t/S)^-0.5（读 memory\\记忆权重.json）
  - 重要度：按来源文件（关系与约定 5 / 事实库 4 / 主题 3 / 日记 2）

条目状态：行尾带 `<!-- status: deprecated ... -->` 的算「作废」，默认跳过；
  加 --include-deprecated 才返回，并在正文前标 [废]。没有标记的一律当 active。

用法：
  python memory\\检索.py 白噪音
  python memory\\检索.py 临川 南屿
  python memory\\检索.py 临川 --include-deprecated
  python memory\\检索.py 白噪音 --log          # 顺手把命中记进 使用记录（used:false）

口径（2026-11-06 改）：可提取度默认走「新口径」——S = imp × (1+0.6×被用次数)，
  次数来自 memory\\使用记录.jsonl 的 used:true；召回/命中只算看见，不算使用。
  要看旧口径（记忆权重.json 里的 S 由 touch 累计）加 --legacy-weights。

这个文件现在只是 memory\\core.py 的命令行壳：打分、读库、记账都在 core 里，
参数和输出格式一个字都没动（桥和别的脚本按原来的输出解析）。
"""
import os
import sys
import json
import datetime

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import core  # noqa: E402  同目录的 core.py

USAGE = "用法: python memory\\检索.py 关键词 [关键词2] [--include-deprecated] [--legacy-weights] [--log]"


def main():
    argv = sys.argv[1:]
    show_dep = "--include-deprecated" in argv
    legacy = "--legacy-weights" in argv
    do_log = "--log" in argv
    flags = {"--include-deprecated", "--legacy-weights", "--log"}
    q = " ".join(a for a in argv if a not in flags).strip()
    if not q:
        print(USAGE)
        return

    scored, hidden = core._rank(q, include_deprecated=show_dep, legacy=legacy)
    if not scored:
        print("没找到和「%s」有关的记忆。" % q)
        if hidden:
            print("（另有 %d 条已作废的记忆被跳过，加 --include-deprecated 可看）" % hidden)
        return
    print("== 「%s」相关记忆 ==" % q)
    usage = core.load_usage()
    for it in scored[:6]:
        u = (usage.get(core.body_sha(it["text"])) or {}).get("used", 0)
        print("\n[%.2f] %s  (被用 %d 次)" % (it["score"], it["src"], u))
        print(("[废] " if it["deprecated"] else "") + it["text"].replace("\n", " ")[:220])
    if hidden:
        print("\n（另跳过 %d 条已作废的记忆，加 --include-deprecated 可看）" % hidden)
    if do_log:
        # 只记「命中过」：used 恒为 false，要不要算使用由 --mark-used 说了算
        ts = datetime.datetime.now().isoformat(timespec="seconds")
        try:
            with open(core.USE_LOG, "a", encoding="utf-8") as f:
                for it in scored[:6]:
                    f.write(json.dumps({
                        "ts": ts, "sha": core.body_sha(it["text"]), "key": it["text"][:30],
                        "src": it["src"], "query": q, "used": False, "source": "search-hit",
                    }, ensure_ascii=False) + "\n")
            print("\n（命中已记进 使用记录.jsonl，used:false）")
        except Exception as e:
            print("\n[warn] 使用记录写不进去：%s" % e)


if __name__ == "__main__":
    main()

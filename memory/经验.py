# -*- coding: utf-8 -*-
"""经验.py —— 子代理的工程执行记录：record / lookup / recent。

主脑派活之前先查这里：这条路走过没有？走过、结果是败，就别让子代理再走一遍。
走通过的，直接照抄步骤。

一条经验就是长期库里的一行 `- ` 条目（和 事实库.md 里的条目同一种形状），
所以 检索.py / 作废.py / 记忆账本.py 全都认得它，不用另造一套解析。

条目长这样：
  - [2026-11-06] 任务：全仓搜关键字 ｜ 路子：主脑自己 grep 二十个文件 ｜ 结果：败 ｜ 死因：上下文被原文灌满 ｜ 位置：- ｜ 下次：起子代理，只回 file:line

三个函数：
  record(task, *, path, result, cause="", loc="", next_try="", date=None) -> dict
      往 长期\经验.md 追加一条。result 只认 成 / 败 / 半；败必须写 cause。
      同一条已经在了就返回旧位置，不重复写（core.add 去重）。
  lookup(query, limit=3) -> list
      只在这份经验里查，复用 core.search 的打分（字面重合 × 遗忘曲线 × 来源权重）。
  recent(n=5) -> list
      文件里最后 n 条，最新的在后面。

用法：
  python memory\\经验.py 记录 --task "全仓搜关键字" --path "主脑自己 grep" \\
         --result 败 --cause "上下文被原文灌满" --next "起子代理只回 file:line"
  python memory\\经验.py 查 跨文件搜索 -n 3
  python memory\\经验.py 最近 -n 5
"""
import datetime
import io
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import core  # noqa: E402

FILE = "经验.md"          # 长期库里的文件名
SOURCE = "经验"           # core.add 的 source，落下就是 长期\经验.md
RESULTS = ("成", "败", "半")
SEP = " ｜ "
KEYS = (("task", "任务"), ("path", "路子"), ("result", "结果"),
        ("cause", "死因"), ("loc", "位置"), ("next_try", "下次"))


def _one(v):
    return " ".join(str(v or "").split()).strip()


def _render(task, path, result, cause, loc, next_try, date):
    v = {"task": _one(task), "path": _one(path), "result": _one(result),
         "cause": _one(cause), "loc": _one(loc), "next_try": _one(next_try)}
    if v["result"] not in RESULTS:
        raise ValueError("result 只能是 成 / 败 / 半，收到：%r" % v["result"])
    if not v["task"]:
        raise ValueError("task 不能空——写一句话，说清要干的是什么")
    if v["result"] == "败" and not v["cause"]:
        raise ValueError("结果为败时必须写 cause（为什么不行）。只写「不行」的记录等于没记。")
    head = "[%s]" % (date or datetime.date.today().isoformat())
    body = SEP.join("%s：%s" % (name, v[k]) for k, name in KEYS if v[k])
    return head + " " + body


def record(task, *, path="", result="成", cause="", loc="", next_try="", date=None):
    """追加一条经验。返回 core.add 的 {"key","file","line"}。

    这条写路径不自己上锁：core.add 里用 锁.rewriting 把「读—去重—写」整段包住了，
    多个子代理同时记经验不会互相覆盖。
    """
    text = _render(task, path, result, cause, loc, next_try, date)
    return core.add(text, source=SOURCE)


def _rows():
    """(行号, 正文) —— 只读 长期\经验.md 的 `- ` 条目。"""
    p = os.path.join(core.LONG, FILE)
    if not os.path.exists(p):
        return []
    out = []
    with io.open(p, "r", encoding="utf-8") as f:
        for i, line in enumerate(f, 1):
            s = line.strip()
            if s.startswith("- ") and len(s) > 8:
                text, _dep = core.split_status(s[2:].strip())
                out.append((i, text))
    return out


def lookup(query, limit=3):
    """在经验库里按核心打分检索，返回 [{"text","src","file","line","score","deprecated"}]。"""
    items = core.search(query, limit=None, record=False)   # 这里只是打分再筛文件，不记账
    rows = [x for x in items if os.path.basename(x["file"]) == FILE]
    return rows[:max(0, int(limit))]


def recent(n=5):
    """最后 n 条经验（旧的在前，新的在后）。"""
    rows = _rows()
    n = max(0, int(n))
    return [{"text": t, "file": "%s\\%s" % (os.path.basename(os.path.dirname(core.LONG)), FILE),
             "line": i} for i, t in (rows[-n:] if n else [])]


# ---- 命令行 --------------------------------------------------------------

def _parse(argv):
    if not argv:
        sys.exit("用法：记录 / 查 / 最近（详见文件头）")
    cmd, rest = argv[0], argv[1:]
    if cmd == "记录":
        kw = {"task": "", "path": "", "result": "成", "cause": "",
              "loc": "", "next_try": ""}
        i = 0
        while i < len(rest):
            a = rest[i]
            if a.startswith("--"):
                k = a[2:].replace("-", "_")
                k = {"next": "next_try", "result": "result"}.get(k, k)
                if k not in kw:
                    sys.exit("不认识的参数：%s" % a)
                i += 1
                if i >= len(rest):
                    sys.exit("%s 后面要跟内容" % a)
                kw[k] = rest[i]
            else:
                sys.exit("多余的参数：%s" % a)
            i += 1
        return cmd, kw
    if cmd == "查":
        n, words = 3, []
        i = 0
        while i < len(rest):
            if rest[i] in ("-n", "--limit"):
                i += 1
                n = int(rest[i]) if i < len(rest) else 3
            else:
                words.append(rest[i])
            i += 1
        return cmd, {"query": " ".join(words), "n": n}
    if cmd == "最近":
        n = 5
        if rest and rest[0] in ("-n", "--limit") and len(rest) > 1:
            n = int(rest[1])
        return cmd, {"n": n}
    sys.exit("不认识的子命令：%s" % cmd)


def main(argv):
    cmd, kw = _parse(argv)
    if cmd == "记录":
        try:
            r = record(kw["task"], path=kw["path"], result=kw["result"], cause=kw["cause"],
                       loc=kw["loc"], next_try=kw["next_try"])
        except ValueError as e:
            sys.exit(str(e))
        print("记下了 → %s:%d  (%s)" % (r["file"], r["line"], r["key"]))
        return 0
    if cmd == "查":
        if not kw["query"]:
            sys.exit("查什么？给个关键词")
        hits = lookup(kw["query"], limit=kw["n"])
        if not hits:
            print("经验库里没有匹配「%s」的记录。" % kw["query"])
            return 1
        for h in hits:
            print("%-6.3f  %s:%d  %s" % (h["score"], h["file"], h["line"], h["text"]))
        return 0
    rows = recent(kw["n"])
    if not rows:
        print("经验库还是空的。")
        return 1
    for r in rows:
        print("%d  %s" % (r["line"], r["text"]))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

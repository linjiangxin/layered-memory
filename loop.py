# -*- coding: utf-8 -*-
"""
loop.py —— 主循环：查经验 → 派子代理 → 收一条结论 → 落经验。

记忆层只负责「记和查」，这一层负责「怎么用」。三步，不多加：

  [1] 查：经验.lookup(task)。这活儿以前干过没有？撞上「败」，先把死因摆出来，
          再决定要不要换思路——这一步就是「别让子代理白跑一趟」。
  [2] 派：把活儿交给 delegate(task)。子代理的中间过程不进主循环，
          回来的只有一条 dict，主脑只做判断和验收。
  [3] 落：经验.record(...) 写进 长期/经验.md。
          **不写日记** —— 日记过 14 天就不进检索了，经验正好相反，越老越该在。

delegate 就是一个普通函数，签名 delegate(task) -> dict，字段：

  {"result": "成" | "败" | "半",
   "conclusion": "一句话结论",
   "path": "走的是哪条路子（写进经验的「路子」栏）",
   "cause": "败的原因，一句话",
   "loc": "file:line",
   "next_try": "下次换什么"}

仓库里给两个现成的：
  grep_delegate(keyword, root)   零依赖、干真活：搜关键词，只回结论 + file:line，不吐原文
  fake_delegate(**kw)            假的，给测试和演示用

想接真模型：自己写一个 delegate 调 API，把回传凑成上面那个 dict 就行，主循环一个字都不用改。

用法：
  python loop.py "在 memory 里找 split_status" --grep split_status --path memory
  python loop.py "看看某个词在哪" --grep 遗忘曲线 --dry
"""
import io
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(_HERE, "memory"))
import core    # noqa: E402
import 经验    # noqa: E402

TEXT_EXT = (".py", ".md", ".txt", ".json", ".jsonl", ".mjs", ".js", ".yml", ".yaml",
            ".toml", ".cfg", ".ini", ".ps1", ".sh", ".svg", ".html", ".css")
SKIP_DIRS = {".git", "__pycache__", "node_modules", ".venv", "venv", "dist", "build", ".idea"}


# ---- 子代理的两个替身 ----------------------------------------------------

def grep_delegate(keyword, root=".", limit=8, max_files=400):
    """返回一个子代理替身：真的去搜，但只回「命中几处 + file:line」，一个字的原文都不吐。

    这就是「不污染主上下文」的具体形状：主脑拿到的是位置，不是内容。
    """
    def _d(task):
        return _grep(keyword, root, limit, max_files)
    return _d


def _grep(keyword, root, limit, max_files):
    hits, scanned = [], 0
    for dirpath, dirs, files in os.walk(root):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS and not d.startswith(".")]
        for fn in sorted(files):
            if not fn.lower().endswith(TEXT_EXT):
                continue
            p = os.path.join(dirpath, fn)
            try:
                with io.open(p, "r", encoding="utf-8", errors="ignore") as f:
                    for i, line in enumerate(f, 1):
                        if keyword in line:
                            hits.append("%s:%d" % (os.path.relpath(p).replace("\\", "/"), i))
                            if len(hits) >= limit:
                                break
            except OSError:
                continue
            scanned += 1
            if scanned >= max_files or len(hits) >= limit:
                break
        if scanned >= max_files or len(hits) >= limit:
            break

    path = "子代理在 %s 下搜「%s」" % (root, keyword)
    if hits:
        return {"result": "成", "path": path,
                "conclusion": "扫了 %d 个文件，命中 %d 处" % (scanned, len(hits)),
                "loc": ", ".join(hits)}
    return {"result": "败", "path": path,
            "conclusion": "扫了 %d 个文件，一处没命中" % scanned,
            "cause": "关键词在整个范围内零命中", "next_try": "换关键词，或先缩小目录再搜"}


def fake_delegate(**kw):
    """假的子代理：返回给定结论。给测试和演示用，别拿它判断真实世界的成败。"""
    def _d(task):
        out = {"result": "成", "path": "fake-delegate（演示用）", "conclusion": "跑通了"}
        out.update({k: v for k, v in kw.items() if v})
        return out
    return _d


# ---- 主循环 --------------------------------------------------------------

def loop(task, delegate, *, dry=False, quiet=False):
    """跑一遍三步。返回 (旧经验, 子代理回传, 落盘结果 or None)。"""
    say = (lambda *a: None) if quiet else (lambda *a: print(*a))

    say("[1] 查经验：%s" % task)
    old = 经验.lookup(task, limit=3)
    if not old:
        say("    没走过这条路。")
    for h in old:
        say("    %s  %s" % (h["text"][:120], "· %s:%d" % (h["file"], h["line"])))
    if any("结果：败" in h["text"] for h in old):
        say("    ↑ 上次是败。死因就在上面那句里，这次别再走同一条路子。")

    say("[2] 派活：子代理在隔离环境里干，中间过程不进主循环。")
    out = dict(delegate(task) or {})
    out.setdefault("result", "半")
    say("    回传：" + str(out.get("conclusion") or "（没给结论）"))
    if out.get("loc"):
        say("    位置：" + str(out["loc"])[:300])      # 只给位置，不给原文
    if out.get("cause"):
        say("    死因：" + str(out["cause"]))

    say("[3] 落经验" + ("（--dry，不落盘）" if dry else "") + "：")
    rec = None
    if not dry:
        rec = 经验.record(task, path=out.get("path", ""), result=out["result"],
                          cause=out.get("cause", ""), loc=out.get("loc", ""),
                          next_try=out.get("next_try", ""))
        say("    → %s:%d" % (rec["file"], rec["line"]))
    return old, out, rec


# ---- 命令行 --------------------------------------------------------------

USAGE = """用法：
  python loop.py "任务描述" --grep 关键词 [--path 目录] [--dry]
  python loop.py "任务描述" --fake 成|败|半   [--cause ...] [--dry]"""


def main(argv):
    task, keyword, root, dry, fake = "", None, ".", False, None
    cause = ""
    i = 0
    while i < len(argv):
        a = argv[i]
        if a == "--grep":
            i += 1
            keyword = argv[i] if i < len(argv) else None
        elif a == "--path":
            i += 1
            root = argv[i] if i < len(argv) else root
        elif a == "--cause":
            i += 1
            cause = argv[i] if i < len(argv) else ""
        elif a == "--fake":
            i += 1
            fake = argv[i] if i < len(argv) else "成"
        elif a == "--dry":
            dry = True
        elif a.startswith("-"):
            sys.exit(USAGE)
        else:
            task = (task + " " + a).strip()
        i += 1

    if not task:
        sys.exit(USAGE)
    if not keyword and not fake:
        sys.exit("要么 --grep 关键词，要么 --fake：不给的话这活儿派不出去。\n" + USAGE)

    delegate = (grep_delegate(keyword, root) if keyword
                else fake_delegate(result=fake, cause=cause or ("演示用" if fake == "败" else "")))
    _old, out, _rec = loop(task, delegate, dry=dry)
    return 0      # 子代理败不等于主循环失败：败也是经验，照样落盘


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    sys.exit(main(sys.argv[1:]))

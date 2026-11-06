# -*- coding: utf-8 -*-
"""
固化.py —— 把日记里还没进长期库的事，挑成「候选记忆」。

不是自动收录（会收进一堆流水账）：它只把和长期库不重复的小节列到
memory\\候选记忆.md，由我看一眼决定要不要合进 事实库 / 主题。

用法：
  python memory\\固化.py            # 最近 7 天
  python memory\\固化.py 14         # 最近 14 天
  python memory\\固化.py -Apply     # 直接追加到候选记忆.md（默认只打印）
"""
import io, os, re, sys, datetime

# 根目录：优先 LAYERED_MEMORY_HOME，否则按脚本自身位置推（memory\固化.py 的根 = 上一级）。
ROOT = os.path.abspath(os.path.expanduser(
    os.environ.get("LAYERED_MEMORY_HOME", "").strip()
    or os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")))
MEM = ROOT
LONG = os.path.join(MEM, "长期")
DIARY = os.path.join(ROOT, "日记")
OUT = os.path.join(MEM, "候选记忆.md")


def bigrams(s):
    s = re.sub(r"[\s\W_]+", "", s.lower())
    return {s[i:i + 2] for i in range(len(s) - 1)} if len(s) > 1 else {s}


def known():
    """长期库里已有的条目（原文 + bigram）。"""
    out = []
    for dirpath, _d, files in os.walk(LONG):
        for fn in files:
            if not fn.endswith(".md") or fn == "压缩.md":
                continue
            with io.open(os.path.join(dirpath, fn), "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line.startswith("- ") and len(line) > 8:
                        out.append((line[2:], bigrams(line[2:])))
    return out


def smart_cut(text, limit=140):
    """截到 limit 以内，但尽量停在句子边界，别在半句话上断。"""
    text = " ".join(text.split())
    if len(text) <= limit:
        return text
    head = text[:limit]
    for sep in ("。", "；", "！", "？", "，", " "):
        i = head.rfind(sep)
        if i >= limit * 0.5:
            return head[:i + 1] + "…"
    return head + "…"


def novelty(text, kb):
    """和长期库最像的那条有多像（越大越重复）。"""
    tb = bigrams(text)
    if not tb:
        return 1.0
    return max((len(tb & kb2) / float(len(tb)) for _t, kb2 in kb), default=0.0)


def already_titled(title, kb):
    """标题本身已经在长期库某条里出现过 → 这事已经沉淀过了。"""
    t = re.sub(r"^[\d:：\s]+", "", title)[:12]
    if len(t) < 4:
        return False
    return any(t in text for text, _b in kb)


def diary_blocks(days):
    cutoff = datetime.date.today() - datetime.timedelta(days=days)
    res = []
    if not os.path.isdir(DIARY):
        return res
    for fn in sorted(os.listdir(DIARY)):
        m = re.match(r"(\d{4}-\d{2}-\d{2})\.md$", fn)
        if not m:
            continue
        d = datetime.date.fromisoformat(m.group(1))
        if d < cutoff:
            continue
        with io.open(os.path.join(DIARY, fn), "r", encoding="utf-8") as f:
            title, body = None, ""
            for line in f:
                if line.startswith("## "):
                    if title:
                        res.append((m.group(1), title, body.strip()))
                    title, body = line[3:].strip(), ""
                elif title:
                    body += line
            if title:
                res.append((m.group(1), title, body.strip()))
    return res


def main():
    args = sys.argv[1:]
    days = 3
    if args and args[0].isdigit():
        days = int(args[0])
    kb = known()
    hits = []
    for date, title, body in reversed(diary_blocks(days)):
        text = (title + "。" + body).strip()
        if len(text) < 40 or already_titled(title, kb):
            continue
        if novelty(title + "。" + smart_cut(body), kb) < 0.5:
            hits.append((date, title, smart_cut(body)))
    hits = hits[:8]

    if not hits:
        print("最近 %d 天的日记里没有新的、值得沉淀的事。" % days)
        return
    lines = ["\n## 候选 %s（来自最近 %d 天日记）\n" %
             (datetime.date.today().isoformat(), days)]
    for date, title, body in hits:
        lines.append("- **%s ｜ %s**：%s" % (date, title, body.replace("\n", " ")))
    block = "\n".join(lines) + "\n"
    print(block)
    if "-Apply" in args:
        with io.open(OUT, "a", encoding="utf-8") as f:
            f.write(block)
        print("（已追加 %d 条到 memory\\候选记忆.md）" % len(hits))


if __name__ == "__main__":
    main()

# -*- coding: utf-8 -*-
"""core.py —— 记忆库的三个库函数：add / search / deprecate。

只做三件事，别在这儿长出别的：
  add(text, source="事实库", tags=None)      往 长期\\<source>.md 追加一条 `- ` 条目
  search(query, limit=3, include_deprecated=False)   打分复用 检索.py 原来那套
  deprecate(keyword, reason="…", dry=False)   复用 作废.py 原来那套

memory\\检索.py 和 memory\\作废.py 现在都是这个文件的命令行薄壳（参数和输出不变）。

路径：优先环境变量 LAYERED_MEMORY_HOME（**数据根目录**：长期/、日记/、
记忆权重.json 就在它下面），没设就按本文件位置推 —— 发布版的数据根 = <仓库>/data
（memory\\core.py 往上两级 = 仓库，再拼 data）。不写死盘符，整个目录拷到别处也能跑。

用法：
    import os, sys
    sys.path.insert(0, os.path.join(仓库根, "memory"))   # 仓库根 = 环境变量 LAYERED_MEMORY_HOME 或本文件上一级
    import core
    core.add("阿舟写东西时必须开白噪音。", tags=["偏好"])
    core.search("白噪音", limit=3)
    core.deprecate("住在临川", reason="搬到南屿", dry=True)
"""
import io
import os
import re
import json
import shutil
import hashlib
import datetime

# ---- 路径 ----------------------------------------------------------------
_HOME = os.environ.get("LAYERED_MEMORY_HOME", "").strip()
ROOT = os.path.abspath(os.path.expanduser(
    _HOME or os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")))
MEM = ROOT                       # 发布版：数据根目录本身就是「记忆目录」
LONG = os.path.join(MEM, "长期")
DIARY = os.path.join(ROOT, "日记")
WEIGHTS = os.path.join(MEM, "记忆权重.json")
USE_LOG = os.path.join(MEM, "使用记录.jsonl")
# 账本单独用 MEM_LEDGER 覆盖（测试用临时账本），否则跟根目录走
LEDGER = os.path.abspath(os.environ.get("MEM_LEDGER") or os.path.join(MEM, "记忆账本.json"))

# ---- 打分参数（和 检索.py 原来的一模一样）--------------------------------
DECAY, FACTOR = 0.5, 19.0 / 81.0
USE_GAIN = 0.6
IMP_BY_FILE = {"关系与约定.md": 5, "事实库.md": 4, "索引.md": 2}
MIN_HIT = 0.34          # 查询二字组被覆盖的比例门槛
SKIP_FILES = {"压缩.md"}

# ---- 可选插件：同义扩展（只扩「匹配集合」，不动查询文本）----------------
SYN_PLUGIN = "同义扩展"   # plugins/同义扩展.json；没有这个文件 = 不启用
SYN_W = 0.7               # 兜底权重：插件没写 weight 时用这个

# 作废标记：条目行尾的 HTML 注释（markdown 不显示，不影响列表解析）
STATUS_RE = re.compile(r"\s*<!--\s*status\s*:\s*([A-Za-z]+).*?-->\s*$")


def rel(p):
    return os.path.relpath(p, ROOT)


def split_status(line):
    """剥掉行尾的状态标记，返回 (正文, 是否作废)。没标记 = active。"""
    m = STATUS_RE.search(line)
    if not m:
        return line, False
    return line[:m.start()].rstrip(), m.group(1).lower() == "deprecated"


def bigrams(s):
    s = re.sub(r"[\s\W_]+", "", s.lower())
    if len(s) < 2:
        return {s} if s else set()
    return {s[i:i + 2] for i in range(len(s) - 1)}


def dice(a, b):
    if not a or not b:
        return 0.0
    return 2.0 * len(a & b) / (len(a) + len(b))


def normalize(s):
    """去掉空白/标点、转小写 —— 只用来比同义组，不参与打分。"""
    return re.sub(r"[\s\W_]+", "", str(s or "").lower())


_PLUGIN_MOD = None


def plugin_module():
    """按路径加载同目录的 插件.py（不靠 sys.path，重载 core 也不会串味）。"""
    global _PLUGIN_MOD
    if _PLUGIN_MOD is None:
        import importlib.util
        here = os.path.dirname(os.path.abspath(__file__))
        spec = importlib.util.spec_from_file_location("lm_插件", os.path.join(here, "插件.py"))
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        _PLUGIN_MOD = mod
    return _PLUGIN_MOD


def synonym_plugin():
    """同义扩展插件；没装 / enabled:false / JSON 坏了 → None（静默，检索照跑）。"""
    try:
        return plugin_module().load(SYN_PLUGIN)
    except Exception:
        return None


def expand_query(query, plugin=None):
    """查询 → (原查询二字组, 同义扩展二字组, 权重)。

    只算「这条记忆里还有哪些词该算命中」，不改 query 本身；
    组内任一词被查询命中就展开整组（不做 key/value 方向区分，双向生效）。
    没插件 / 关掉 → ({...}, set(), 0.0)，等价于原来的字面检索。
    """
    qb = bigrams(query)
    plug = synonym_plugin() if plugin is None else plugin
    if not plug:
        return qb, set(), 0.0
    try:
        weight = float(plug.get("weight", SYN_W))
    except (TypeError, ValueError):
        weight = SYN_W
    weight = min(1.0, max(0.0, weight))
    groups = plug.get("groups") or []
    qn = normalize(query)
    extra = set()
    for group in groups:
        if not isinstance(group, (list, tuple)):
            continue
        words = [w for w in (normalize(x) for x in group) if w]
        if not any((w == qn) if len(w) == 1 else (w in qn) for w in words):
            continue
        for w in words:
            extra |= bigrams(w)
    return qb, extra - qb, weight


def load_weights():
    if os.path.exists(WEIGHTS):
        with io.open(WEIGHTS, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}


def spell(rec, imp=3, used_n=0, legacy=False, last_used=None):
    """一条记忆当前还记得多清楚。

    新口径：S = imp × (1 + 0.6×被用次数)，时间从「最后一次真用上」算；
    一次都没用过的条目按 0.75 起，不让老条目凭白挨罚。
    """
    if legacy:
        if not rec:
            return 0.75
        t = max(0, (datetime.date.today() - datetime.date.fromisoformat(rec["last"])).days)
        return (1.0 + FACTOR * t / max(rec["S"], 0.1)) ** (-DECAY)
    day = last_used or (rec or {}).get("last")
    if not day:
        return 0.75
    s = max(imp * (1.0 + USE_GAIN * used_n), 0.1)
    t = max(0, (datetime.date.today() - datetime.date.fromisoformat(day)).days)
    return (1.0 + FACTOR * t / s) ** (-DECAY)


def body_sha(body):
    """和 tools\\记忆召回.mjs / 记忆曲线.py 同一把尺：sha256(正文)[:16]。"""
    return hashlib.sha256(body.encode("utf-8")).hexdigest()[:16]


def load_usage():
    """{sha16: {"used":n, "last_used":"YYYY-MM-DD"|None}}；文件缺失 = 0 次使用。"""
    out = {}
    if not os.path.exists(USE_LOG):
        return out
    try:
        f = io.open(USE_LOG, "r", encoding="utf-8", errors="replace")
    except Exception:
        return out
    with f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                r = json.loads(line)
            except Exception:
                continue
            if not r.get("used"):
                continue
            sha = str(r.get("sha") or "")[:16]
            if not sha:
                continue
            d = out.setdefault(sha, {"used": 0, "last_used": None})
            d["used"] += 1
            ts = str(r.get("ts") or "")[:10]
            if ts and (d["last_used"] is None or ts > d["last_used"]):
                d["last_used"] = ts
    return out


def entries():
    """(正文, 展示用来源, 重要度, 是否作废, 相对路径, 行号) 的流：长期库 + 最近 14 天日记。

    展示用来源必须和 检索.py 旧版逐字一致：
      长期库 → "memory\\长期\\主题\\xx.md:49"；日记 → "2026-11-06.md 小节标题"（行号记 0）。
    """
    if os.path.isdir(LONG):
        for dirpath, _d, files in os.walk(LONG):
            for fn in sorted(files):
                if not fn.endswith(".md"):
                    continue
                p = os.path.join(dirpath, fn)
                imp = IMP_BY_FILE.get(fn, 3)
                with io.open(p, "r", encoding="utf-8") as f:
                    for i, line in enumerate(f, 1):
                        line = line.strip()
                        if line.startswith("- ") and len(line) > 8:
                            text, dep = split_status(line[2:].strip())
                            if len(text) < 2:
                                continue
                            r = rel(p)
                            yield text, "%s:%d" % (r, i), imp, dep, r, i

    # 日记：最近 14 天，按 `## ` 小节切
    cutoff = datetime.date.today() - datetime.timedelta(days=14)
    if os.path.isdir(DIARY):
        for fn in sorted(os.listdir(DIARY)):
            m = re.match(r"(\d{4}-\d{2}-\d{2})\.md$", fn)
            if not m:
                continue
            d = datetime.date.fromisoformat(m.group(1))
            if d < cutoff:
                continue
            with io.open(os.path.join(DIARY, fn), "r", encoding="utf-8") as f:
                buf = None
                for line in f:
                    if line.startswith("## "):
                        if buf:
                            yield buf[1].strip(), "%s %s" % (fn, buf[0]), 2, False, fn, 0
                        buf = [line[3:].strip(), ""]
                    elif buf is not None:
                        buf[1] += line
                if buf:
                    yield buf[1].strip()[:400], "%s %s" % (fn, buf[0]), 2, False, fn, 0


def _rank(query, include_deprecated=False, legacy=False):
    """内部：返回 (命中列表, 被跳过的作废条数)。排序和 检索.py 原来完全一致。"""
    q = (query or "").strip()
    if not q:
        return [], 0
    qb, extra, syn_w = expand_query(q)
    if not qb:
        return [], 0
    w = load_weights()
    usage = load_usage()
    scored = []
    hidden = 0
    for text, src, imp, dep, file, line in entries():
        if dep and not include_deprecated:
            hidden += 1
            continue
        eb = bigrams(text)
        direct = len(qb & eb)                    # 真命中
        syn = len(extra & eb) if extra else 0    # 同义扩展命中（按插件 weight 折）
        # 门槛看「覆盖了多少」，扩展命中也算覆盖，但计分时要折减
        if (direct + syn) / float(len(qb)) < MIN_HIT:
            continue
        # 分母仍是原查询长度：扩展只加分子，不会稀释精确命中；封顶 1.0
        s = min(1.0, (direct + syn_w * syn) / float(len(qb)))
        rec = w.get(text)
        u = usage.get(body_sha(text)) or {"used": 0, "last_used": None}
        score = s * (0.35 + 0.65 * spell(rec, imp, u["used"], legacy, u["last_used"])) * (0.5 + imp / 10.0)
        scored.append((score, s, imp, src, text, dep, u["used"], file, line))
    scored.sort(reverse=True)
    out = []
    for score, _s, _imp, src, text, dep, _used, file, line in scored:
        out.append({
            "text": text,
            "src": src,
            "file": file,
            "line": line,
            "score": score,
            "deprecated": dep,
        })
    return out, hidden


def search(query, *, limit=3, include_deprecated=False):
    """按字面重合 × 可提取度 × 重要度 打分，返回前 limit 条。

    返回 [{"text", "src", "file", "line", "score", "deprecated"}]，
    src 是 "文件:行号" 的展示串，file/line 是拆开的同一份位置。
    日记条目 line 记 0（正文是按小节拼的，没有单一行号）。
    """
    items, _hidden = _rank(query, include_deprecated=include_deprecated)
    if limit is None:
        return items
    return items[:max(0, int(limit))]


def add(text, *, source="事实库", tags=None):
    """往 长期\\<source>.md 追加一条 `- ` 条目（同一条已在库里就不再追加）。

    返回 {"key": sha16(正文), "file": "memory\\长期\\事实库.md", "line": 行号}
    """
    body = " ".join(str(text or "").split()).strip()
    if len(body) < 2:
        raise ValueError("add() 的正文太短（至少 2 个字）")
    name = str(source or "事实库").strip()
    if not name.endswith(".md"):
        name += ".md"
    path = os.path.join(LONG, os.path.basename(name))
    os.makedirs(LONG, exist_ok=True)

    line = "- " + body
    tag_list = [str(t).strip().lstrip("#") for t in (tags or []) if str(t).strip()]
    if tag_list:
        line += "  " + " ".join("#" + t for t in tag_list)

    existing = None
    lines = []
    if os.path.exists(path):
        with io.open(path, "r", encoding="utf-8", newline="") as f:
            lines = f.readlines()
        for i, raw in enumerate(lines, 1):
            if STATUS_RE.search(raw):
                continue                      # 作废的旧版本不算「已有」
            s = raw.strip()
            if not s.startswith("- "):
                continue
            # 行尾的 #标签 不算正文，去重时忽略
            if re.sub(r"(\s+#\S+)+$", "", s[2:].strip()) == body:
                existing = i
                break
    else:
        lines = ["# %s\n" % name[:-3], "\n", "## 新增（%s）\n" % datetime.date.today().isoformat(), "\n"]

    if existing:
        return {"key": body_sha(body), "file": rel(path), "line": existing}

    if lines and not lines[-1].endswith("\n"):
        lines[-1] += "\n"
    if not lines:
        lines = ["# %s\n" % name[:-3], "\n"]
    lines.append(line + "\n")
    with io.open(path, "w", encoding="utf-8", newline="") as f:
        f.writelines(lines)
    return {"key": body_sha(body), "file": rel(path), "line": len(lines)}


# ---- 作废（从 作废.py 抽出来）-------------------------------------------

def _marker(reason):
    return " <!-- status: deprecated | since: %s | by: %s -->" % (
        datetime.date.today().isoformat(), " ".join(str(reason).split()))


def _backup(p):
    stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M")
    dst = p + ".bak-" + stamp
    if os.path.exists(dst):                       # 同一分钟跑第二次，别互相盖
        dst = p + ".bak-" + datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    shutil.copy2(p, dst)
    return dst


def _sha(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _scan(words):
    """扫长期库，返回 [{"file","line","text","deprecated"}]（跳过 压缩.md）。"""
    hits = []
    if not os.path.isdir(LONG):
        return hits
    for dirpath, _d, files in os.walk(LONG):
        for fn in sorted(files):
            if not fn.endswith(".md") or fn in SKIP_FILES:
                continue
            p = os.path.join(dirpath, fn)
            with io.open(p, "r", encoding="utf-8", newline="") as f:
                for i, raw in enumerate(f, 1):
                    s = raw.strip()
                    if not s.startswith("- "):
                        continue
                    body = STATUS_RE.sub("", s[2:]).strip()
                    if not all(w in body for w in words):
                        continue
                    hits.append({"file": p, "line": i, "text": body,
                                 "deprecated": bool(STATUS_RE.search(s))})
    return hits


def _rekey_ledger(moves):
    """条目行被改写后，账本里的指纹跟着搬家，免得 --verify 永远误报丢了。"""
    if not moves or not os.path.exists(LEDGER):
        return 0
    try:
        with io.open(LEDGER, "r", encoding="utf-8") as f:
            data = json.loads(f.read().replace("\ufeff", ""))
    except Exception:
        return -1                                 # -1：账本读不出来
    entries_map = data.get("entries") or {}
    n = 0
    for old, new in moves:
        d = _sha(old)
        if d not in entries_map:
            continue
        rec = entries_map.pop(d)
        rec["sha256"] = _sha(new)
        rec["len"] = len(new)
        rec["text"] = new[:80]
        entries_map[rec["sha256"]] = rec
        n += 1
    if n:
        data["entries"] = entries_map
        with io.open(LEDGER, "w", encoding="utf-8") as f:
            f.write(json.dumps(data, ensure_ascii=False, indent=1))
    return n


def deprecate(keyword, *, reason, dry=False):
    """就地把长期库里过时的条目标成 deprecated（不改写正文，只挂行尾标记）。

    keyword 可以是字符串（空格分隔多个词 = AND）或词列表。
    返回 {"words","reason","dry","hits","todo","changed","written",
          "ledger_moved","ledger_readable","left"}
      hits/todo/left 里的条目都是 {"file","line","text","deprecated"}，
      file 是相对 ROOT 的路径；written = [{"file","backup"}]
    """
    words = keyword.split() if isinstance(keyword, str) else [str(w) for w in keyword]
    words = [w for w in words if w]
    reason = " ".join(str(reason or "").split())
    res = {"words": words, "reason": reason, "dry": bool(dry), "hits": [], "todo": [],
           "changed": 0, "written": [], "ledger_moved": 0, "ledger_readable": True,
           "left": []}
    if not words:
        return res
    hits = _scan(words)
    res["hits"] = [{"file": rel(h["file"]), "line": h["line"], "text": h["text"],
                    "deprecated": h["deprecated"]} for h in hits]
    todo = [h for h in hits if not h["deprecated"]]
    res["todo"] = [{"file": rel(h["file"]), "line": h["line"], "text": h["text"],
                    "deprecated": False} for h in todo]
    if not todo or dry:
        return res

    mk = _marker(reason)
    by_file = {}
    for h in todo:
        by_file.setdefault(h["file"], []).append(h["line"])

    moves = []
    for p, linenos in by_file.items():
        bak = _backup(p)
        with io.open(p, "r", encoding="utf-8", newline="") as f:
            lines = f.readlines()
        for i in linenos:
            raw = lines[i - 1]
            stripped = raw.rstrip("\r\n")
            tail = raw[len(stripped):]
            if STATUS_RE.search(stripped):
                continue
            old = stripped.strip()
            new = stripped.rstrip() + mk
            lines[i - 1] = new + tail
            moves.append((old, new))
            res["changed"] += 1
        with io.open(p, "w", encoding="utf-8", newline="") as f:
            f.writelines(lines)
        res["written"].append({"file": rel(p), "backup": rel(bak)})

    nkey = _rekey_ledger(moves)
    if nkey < 0:
        res["ledger_readable"] = False
        nkey = 0
    res["ledger_moved"] = nkey

    # 回读核对：标过的行必须真的带 deprecated
    res["left"] = [{"file": rel(h["file"]), "line": h["line"], "text": h["text"],
                    "deprecated": h["deprecated"]}
                   for h in _scan(words) if not h["deprecated"]]
    return res

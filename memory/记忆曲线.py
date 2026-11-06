# -*- coding: utf-8 -*-
"""
记忆曲线.py —— 给长期记忆加一条遗忘曲线。

参考：
- FSRS（Anki 现行算法）：遗忘不是指数衰减，是幂律 R = (1 + F*t/S)^-D，
  标准参数 D=0.5, F=19/81。t=距上次复述的天数，S=记忆稳定度（天）。
- Mem0 / Zep 一类 agent memory：条目要有「重要度」和「最近一次被提到」，
  检索时按 相似度 × 可提取度 排序，而不是单纯按时间。

口径（2026-11-06 改）：
  权重只看「被真正用上」，不看「被翻到」。
  - 使用 = memory\\使用记录.jsonl 里 used:true 的记录（由 --mark-used 或
    确认过引用之后写进去的）。
  - 召回 / 目录扫描 / -Touch 都只算「被看见」，只影响「最近复习时间」，
    不涨权重。
  - --legacy 跑旧口径（touch 涨 S），用来对照。旧状态原样留在
    记忆权重.json 的 S 字段里，新口径不覆盖它。

用法（在工作区根目录）：
  python memory\\记忆曲线.py                  # 今天该复习的 / 可以淡出的（新口径）
  python memory\\记忆曲线.py --legacy         # 同上，旧口径（touch 涨权重）
  python memory\\记忆曲线.py --compare        # 新旧两套 top10 并排
  python memory\\记忆曲线.py --mark-used 关键词 # 这条真的被用上了 → 记一次使用
  python memory\\记忆曲线.py -Touch 关键词     # 只是又翻到一次 → 只更新时间，不涨权重
  python memory\\记忆曲线.py -All             # 全部条目和当前 R 值

数据结构：
  memory\\记忆权重.json   { "条目原文": {"src":"事实库.md","imp":4,"S":3.0,"last":"2026-10-24","reps":1} }
  memory\\使用记录.jsonl  每行一条 {"ts","sha","key","src","query","used","source"}
"""
import argparse
import datetime
import hashlib
import io
import json
import math
import os
import sys

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

# 根目录：优先 LAYERED_MEMORY_HOME，否则按脚本自身位置推（memory\记忆曲线.py 的根 = 上一级）。
ROOT = os.path.abspath(os.path.expanduser(
    os.environ.get("LAYERED_MEMORY_HOME", "").strip()
    or os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")))
MEM = ROOT
STORE = os.path.join(MEM, "记忆权重.json")
USE_LOG = os.path.join(MEM, "使用记录.jsonl")
LONG = os.path.join(MEM, "长期")

DECAY = 0.5
FACTOR = 19.0 / 81.0
USE_GAIN = 0.6   # 每被真用一次，稳定度在 imp 基准上再涨 60%
SEARCH_W = 0.5   # 检索命中的折算系数（和 core.py 的 SEARCH_W 同一个数）

# 哪类文件里的条目更该被记住
IMPORTANCE = {
    "关系与约定.md": 5,
    "事实库.md": 4,
    "索引.md": 2,
}


def importance_of(path):
    name = os.path.basename(path)
    return IMPORTANCE.get(name, 3)


def today():
    return datetime.date.today()


def days_since(d):
    return max(0.0, (today() - datetime.date.fromisoformat(d)).days)


def retrievability(t, s):
    return (1.0 + FACTOR * t / max(s, 0.1)) ** (-DECAY)


def body_sha(body):
    """和 tools\\记忆召回.mjs 里 sha16() 必须一致：sha256(正文)[:16]。"""
    return hashlib.sha256(body.encode("utf-8")).hexdigest()[:16]


def load():
    if os.path.exists(STORE):
        with io.open(STORE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}


def save(db):
    with io.open(STORE, "w", encoding="utf-8") as f:
        f.write(json.dumps(db, ensure_ascii=False, indent=2))


def harvest(db):
    """把 长期\\ 下所有 md 的条目扫进来（新条目给默认稳定度）。只补，不改已有的。"""
    added = 0
    for dirpath, _dirs, files in os.walk(LONG):
        for fn in files:
            if not fn.endswith(".md"):
                continue
            p = os.path.join(dirpath, fn)
            with io.open(p, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line.startswith("- ") or len(line) < 8:
                        continue
                    body = line[2:].strip()
                    # 行尾的 <!-- status: deprecated ... --> 不算正文，别让它变成新的键
                    i = body.find("<!-- status:")
                    if i > 0:
                        body = body[:i].rstrip()
                    if body in db:
                        continue
                    imp = importance_of(p)
                    db[body] = {
                        "src": os.path.relpath(p, LONG),
                        "imp": imp,
                        "S": 3.0 * imp / 3.0,
                        "last": today().isoformat(),
                        "reps": 1,
                    }
                    added += 1
    return added


def touch(db, keyword, grow_weight=False):
    """又翻到一次。只更新「最近复习时间」；grow_weight=True 时（--legacy）才涨 S。"""
    hit = 0
    for body, rec in db.items():
        if keyword in body:
            if grow_weight:
                r = retrievability(days_since(rec["last"]), rec["S"])
                # 旧口径：复述一次 = 稳定度增长；越难想起来（R 低）增长越多
                rec["S"] = round(rec["S"] * (1 + 1.5 * (1 - r)) + 1.0, 2)
            rec["last"] = today().isoformat()
            rec["reps"] = rec.get("reps", 1) + 1
            hit += 1
            print("记了一次%s: %s (S=%.1f)" % ("" if grow_weight else "翻到（不涨权重）", body[:30], rec["S"]))
    if not hit:
        print("没找到含「%s」的条目" % keyword)
    return hit


# ---------------- 使用记录 ----------------

def use_weight(rec):
    """一条使用记录折成几次「真用」：检索命中的按 weight 折，人确认的算满值。"""
    if str(rec.get("via") or "") == "search":
        try:
            w = float(rec.get("weight", SEARCH_W))
        except (TypeError, ValueError):
            w = SEARCH_W
        return min(1.0, max(0.0, w))
    return 1.0


def load_usage():
    """读 使用记录.jsonl。文件不存在 / 行坏掉都当没有，绝不报错。

    返回 {sha16: {"used":n, "recalled":n, "last_used":date|None, "last_seen":date|None, "keys":set}}
    """
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
                rec = json.loads(line)
            except Exception:
                continue
            sha = rec.get("sha")
            if not sha:
                body = rec.get("body") or ""
                if not body:
                    continue
                sha = body_sha(body)
            sha = str(sha)[:16]
            d = out.setdefault(sha, {"used": 0, "recalled": 0, "last_used": None,
                                     "last_seen": None, "keys": set()})
            ts = str(rec.get("at") or rec.get("ts") or "")[:10]
            if rec.get("key"):
                d["keys"].add(rec["key"])
            if ts:
                if d["last_seen"] is None or ts > d["last_seen"]:
                    d["last_seen"] = ts
            if rec.get("used"):
                d["used"] += use_weight(rec)      # 老记录没 via → 当人确认的满值
                if ts and (d["last_used"] is None or ts > d["last_used"]):
                    d["last_used"] = ts
            else:
                d["recalled"] += 1
    return out


def append_usage(rec):
    """追加一条使用记录。目录不在就建；写失败只告警，不影响主流程。"""
    try:
        os.makedirs(os.path.dirname(USE_LOG), exist_ok=True)
        with io.open(USE_LOG, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        return True
    except Exception as e:
        print("[warn] 使用记录写不进去：%s" % e)
        return False


def mark_used(db, keyword):
    """确认「真的用上了」：写一条 used:true，并把最近复习时间推到今天。"""
    hit = 0
    ts = datetime.datetime.now().isoformat(timespec="seconds")
    for body, rec in db.items():
        if keyword in body:
            append_usage({
                "ts": ts,
                "sha": body_sha(body),
                "key": body[:30],
                "src": rec.get("src"),
                "query": keyword,
                "used": True,
                "source": "mark-used",
            })
            rec["last"] = today().isoformat()
            hit += 1
            print("记为用上: %s" % body[:40])
    if not hit:
        print("没找到含「%s」的条目，什么都没记" % keyword)
    return hit


# ---------------- 报表 ----------------

def view(db, usage, legacy=False):
    """算出每条在当前口径下的 (S, R, t, used)。"""
    rows = []
    for body, rec in db.items():
        u = usage.get(body_sha(body)) or {"used": 0, "recalled": 0, "last_used": None, "last_seen": None}
        if legacy:
            S = rec["S"]
            t = days_since(rec["last"])
        else:
            S = round(rec["imp"] * (1 + USE_GAIN * u["used"]), 2)
            base = u["last_used"] or rec["last"]
            t = days_since(base)
        rows.append({"body": body, "rec": rec, "S": S, "R": retrievability(t, S), "t": t,
                     "used": u["used"], "recalled": u["recalled"]})
    return rows


def report(db, usage, show_all=False, legacy=False):
    rows = view(db, usage, legacy)
    rows.sort(key=lambda x: (x["R"], -x["rec"]["imp"]))
    tag = "旧口径(touch 涨权重)" if legacy else "新口径(只看被用上)"
    tot_used = sum(r["used"] for r in view(db, usage, False))
    tot_recall = sum(r["recalled"] for r in view(db, usage, False))
    print("== 记忆库 %d 条 ｜ %s ==" % (len(rows), tag))
    if not legacy:
        print("   使用记录：used:true %.1f 次 / 仅召回 %d 次（召回不算使用）" % (tot_used, tot_recall))

    if show_all:
        for x in rows:
            print("R=%.2f S=%5.1f 重要%d %3d天 用%.1f次/召%d次  %s" %
                  (x["R"], x["S"], x["rec"]["imp"], x["t"], x["used"], x["recalled"], x["body"][:36]))
        return

    due = [x for x in rows if x["R"] < 0.9 and x["rec"]["imp"] >= 3][:5]
    print("\n-- 今天值得翻一遍 --")
    for x in due:
        print("R=%.2f (%d天没提) %s  %s" %
              (x["R"], x["t"], ("用%.1f次" % x["used"]) if not legacy else "", x["body"][:40]))
    if not due:
        print("（都还清楚，不用翻）")

    fade = [x for x in rows if x["R"] < 0.45 and x["rec"]["imp"] <= 3][:5]
    print("\n-- 可以淡出（归档成一句话）--")
    for x in fade:
        print("R=%.2f (%d天)  %s" % (x["R"], x["t"], x["body"][:40]))
    if not fade:
        print("（暂时没有该忘的）")


def compare(db, usage, n=10):
    new = sorted(view(db, usage, False), key=lambda x: (-x["S"], x["body"]))[:n]
    old = sorted(view(db, usage, True), key=lambda x: (-x["S"], x["body"]))[:n]
    old_rank = {x["body"]: i + 1 for i, x in enumerate(old)}
    new_rank = {x["body"]: i + 1 for i, x in enumerate(new)}

    def fmt(rows, other_rank):
        out = []
        for i, x in enumerate(rows, 1):
            o = other_rank.get(x["body"])
            delta = "新" if o is None else ("%+d" % (o - i) if o != i else "—")
            out.append("%2d. S=%5.1f 用%4.1f次 位次变化%s  %s" %
                       (i, x["S"], x["used"], delta, x["body"][:34]))
        return out

    print("== 新口径 top%d（S = imp × (1+0.6×被用次数)）==" % n)
    print("\n".join(fmt(new, old_rank)))
    print("\n== 旧口径 top%d（S 由 touch 累计）==" % n)
    print("\n".join(fmt(old, new_rank)))
    both = sorted(set(new_rank) | set(old_rank), key=lambda b: new_rank.get(b, 99))
    only_new = [b for b in both if b not in old_rank]
    only_old = [b for b in both if b not in new_rank]
    print("\n只在新榜：%d 条 ｜ 只在旧榜：%d 条" % (len(only_new), len(only_old)))
    for b in only_new[:3]:
        print("  + " + b[:40])
    for b in only_old[:3]:
        print("  - " + b[:40])


def main():
    ap = argparse.ArgumentParser(description="记忆曲线（默认新口径：只看被用上）")
    ap.add_argument("-Touch", "--touch", metavar="关键词", help="只是翻到一次：只更新复习时间")
    ap.add_argument("-All", "--all", action="store_true", help="全部条目")
    ap.add_argument("--legacy", action="store_true", help="跑旧口径（touch 涨权重）")
    ap.add_argument("--compare", action="store_true", help="新旧两套 top10 并排")
    ap.add_argument("--mark-used", metavar="关键词", help="确认这条真的被用上了")
    args = ap.parse_args()

    db = load()
    n = harvest(db)
    usage = load_usage()

    if args.mark_used:
        mark_used(db, args.mark_used)
    elif args.touch:
        touch(db, args.touch, grow_weight=args.legacy)
    elif args.compare:
        compare(db, usage)
    else:
        report(db, usage, show_all=args.all, legacy=args.legacy)

    save(db)
    if n:
        print("\n（新收录 %d 条）" % n)


if __name__ == "__main__":
    main()

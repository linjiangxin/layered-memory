# -*- coding: utf-8 -*-
"""记忆账本：给每一条记忆留指纹，证明"没丢过"。

设计原则：只记录、不改写；发现异常只报警，不自动修。
我们的记忆不是 sqlite，是 md 文件——所以账本按「条目」记账：
一条记忆 = 长期库/日记里一行以 `- ` 开头的正文（和 记忆曲线.py 的 harvest 保持一致）。

用法：
  python tools\\记忆账本.py --init     第一次建账（已存在的条目不会被覆盖）
  python tools\\记忆账本.py --sync     把新条目补进账本
  python tools\\记忆账本.py --verify   对账：真丢了才退出码 1
  python tools\\记忆账本.py --adopt 处置.json   给「正文被改写过」的旧条目补处置记录

对账把「找不到」拆成两类，不再混成一句话：
  1) 正文被编辑（指纹变）—— 内容还在，只是那一行被改写/浓缩了。条目上挂着
     resolved 处置记录（kind=rewritten/moved/replaced），--verify 每次都会重新
     校验证据（锚点还在文件里、相似度够、或新指纹确实在账里），证据不成立就降级
     成第 2 类。
  2) 整条消失 —— 没有任何处置记录、或者证据已失效。这一类才算失败，退出码 1。

顺序纪律：先 sync 再 verify。
反过来的话，新写的记忆会被误报成"丢了"。
"""
import argparse
import difflib
import hashlib
import json
import os
import sys
from datetime import datetime
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

# 根目录：优先 LAYERED_MEMORY_HOME，否则按脚本自身位置推（tools\记忆账本.py 的根 = 上一级）。
# MEM_LEDGER 仍可单独指定账本文件（测试用临时账本就靠它）。
_HOME = os.environ.get("LAYERED_MEMORY_HOME", "").strip()
ROOT = (Path(_HOME).expanduser().resolve() if _HOME
        else Path(__file__).resolve().parent.parent / "data")
LEDGER = Path(os.environ.get("MEM_LEDGER") or (ROOT / "记忆账本.json"))

# 只收「本来就该稳定」的东西：
# - 长期库整棵（含 主题/）
# - 日记（只会追加，不会改旧行）
# 不收 短期记忆.md（每轮覆盖写，它本来就该变）、不收账本自己、不收归档原文（体积大且不再动）
SCAN = [("长期", "*.md"), ("日记", "*.md")]
SKIP_PARTS = ("归档/原文", "归档\\原文")
MIN_LEN = 8


def sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def harvest():
    """返回 {sha256: {"len":n, "src":rel, "text":前80字}}；同一条记忆在多个文件里只记一次（取第一个 src）。"""
    items = {}
    for rel_dir, pattern in SCAN:
        base = ROOT / rel_dir
        if not base.exists():
            continue
        for path in sorted(base.rglob(pattern)):
            rel = path.relative_to(ROOT).as_posix()
            if any(s in rel for s in SKIP_PARTS):
                continue
            try:
                lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
            except Exception as e:
                print(f"[warn] 读不了 {rel}: {e}")
                continue
            for line in lines:
                text = line.strip()
                if not text.startswith("- ") or len(text) < MIN_LEN:
                    continue
                digest = sha(text)
                if digest in items:
                    continue
                items[digest] = {"len": len(text), "src": rel, "text": text[:80]}
    return items


def load():
    if not LEDGER.exists():
        return {"version": 1, "created": None, "entries": {}}
    try:
        data = json.loads(LEDGER.read_text(encoding="utf-8").replace("\ufeff", ""))
    except Exception as e:
        print(f"❌ 账本读不出来（JSON 坏了）：{e}")
        sys.exit(2)
    data.setdefault("entries", {})
    return data


def save(data):
    LEDGER.parent.mkdir(parents=True, exist_ok=True)
    LEDGER.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")


def cmd_init(data):
    current = harvest()
    data["created"] = data.get("created") or datetime.now().isoformat(timespec="seconds")
    added = 0
    for digest, info in current.items():
        if digest not in data["entries"]:
            data["entries"][digest] = {"sha256": digest, **info}
            added += 1
    save(data)
    print(f"✅ 建账完成：当前 {len(current)} 条，新入账 {added} 条，账本共 {len(data['entries'])} 条")


def cmd_sync(data):
    current = harvest()
    added = sum(1 for d in current if d not in data["entries"])
    for digest, info in current.items():
        if digest not in data["entries"]:
            data["entries"][digest] = {"sha256": digest, **info}
    save(data)
    print(f"✅ 同步完成：新入账 {added} 条，账本共 {len(data['entries'])} 条")


def _read_text(path):
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return None


RESOLVE_MIN_COVERAGE = 0.10


def _coverage(old, new):
    """旧正文有多少被新行吃掉了（0-1）。浓缩改写会掉很多字，所以这是「残留率」不是相似度。"""
    sm = difflib.SequenceMatcher(None, old, new)
    kept = sum(b.size for b in sm.get_matching_blocks())
    return kept / max(1, len(old))


def check_resolution(res, current):
    """校验一条处置记录今天还成不成立。返回 (ok, 说明)。"""
    kind = res.get("kind")
    if kind == "retired":
        note = res.get("note") or "已废弃"
        return True, f"明确废弃：{note}"
    if kind == "replaced":
        d = res.get("new_sha256")
        if d in current:
            return True, f"已由新条目承载 → {current[d]['src']}"
        return False, "记录的新指纹不在当前文件里（新条目也被改了）"
    if kind in ("rewritten", "moved"):
        src = res.get("src")
        anchor = res.get("anchor") or ""
        path = ROOT / src if src else None
        text = _read_text(path) if path else None
        if text is None:
            return False, f"证据文件读不到：{src}"
        hit = None
        for i, line in enumerate(text.splitlines(), 1):
            if anchor and anchor in line:
                hit = (i, line.strip())
                break
        if hit is None:
            return False, f"证据文件里找不到锚点「{anchor}」"
        cov = None
        if res.get("old_text"):
            cov = _coverage(res["old_text"], hit[1])
            if cov < RESOLVE_MIN_COVERAGE:
                return False, (f"锚点还在但原文残留只剩 {cov:.2f}（<{RESOLVE_MIN_COVERAGE}），"
                               f"疑似被整段换掉")
        tail = f"，原文残留 {cov:.2f}" if cov is not None else ""
        return True, f"{src}:{hit[0]} 承载{tail}"
    return False, f"未知处置类型 {kind!r}"


def find_in_baks(text80):
    """真丢了的时候，去 bak / 备份里找它还在哪。返回相对路径列表。"""
    hits = []
    cand = []
    cand += sorted((ROOT / "日记").glob("*.bak*"))
    cand += sorted((ROOT / "日记").glob(".*.bak*"))
    for p in sorted((ROOT / "备份").rglob("*")) if (ROOT / "备份").exists() else []:
        if p.is_file() and p.suffix in (".md", ".bak", ".txt"):
            cand.append(p)
    for p in cand:
        txt = _read_text(p)
        if txt is None:
            continue
        for i, line in enumerate(txt.splitlines(), 1):
            if line.strip().startswith(text80[:40]):
                hits.append(f"{p.relative_to(ROOT).as_posix()}:{i}")
                break
        if len(hits) >= 3:
            break
    return hits


def cmd_verify(data):
    if not data["entries"]:
        print("❌ 账本还是空的，先跑 --init")
        sys.exit(2)
    current = harvest()
    missing = [(d, e) for d, e in data["entries"].items() if d not in current]
    new = [d for d in current if d not in data["entries"]]
    edited, gone, retired = [], [], []
    for _d, e in missing:
        res = e.get("resolved")
        if not res:
            gone.append((e, "账本里没有处置记录"))
            continue
        ok, detail = check_resolution(res, current)
        if not ok:
            gone.append((e, detail))
        elif res.get("kind") == "retired":
            retired.append((e, detail))
        else:
            edited.append((e, detail))

    ok_n = len(data["entries"]) - len(missing)
    print(f"账本共 {len(data['entries'])} 条；指纹原样对上的 {ok_n} 条；"
          f"原指纹对不上 {len(missing)} 条。")
    if edited:
        print(f"\n① 正文被编辑（指纹变）{len(edited)} 条 —— 内容还在，只是那一行被改写过：")
        for e, detail in edited:
            print(f"   - [{e['src']}] {e['text']}")
            print(f"     → {detail}")
    if retired:
        print(f"\n② 已明确废弃 {len(retired)} 条（不算丢）：")
        for e, detail in retired:
            print(f"   - [{e['src']}] {e['text']}\n     → {detail}")
    if gone:
        print(f"\n③ 整条消失 {len(gone)} 条 —— 扫描范围里没有，也没有成立的处置记录：")
        for e, why in gone:
            print(f"   - [{e['src']}] {e['text']}")
            print(f"     原因：{why}")
            baks = find_in_baks(e.get("text", ""))
            print(f"     还能在：{', '.join(baks) if baks else '（备份里也没找到，需人工确认是否该标 retired）'}")
    if new:
        print(f"\nℹ️ 有 {len(new)} 条新记忆还没入账，跑一次 --sync")
    if gone:
        print(f"\n❌ 对账失败：{len(gone)} 条整条消失，退出码 1")
        sys.exit(1)
    print(f"\n✅ 对账通过：账本 {len(data['entries'])} 条全部有主"
          f"（原样 {ok_n} / 改写过 {len(edited)} / 废弃 {len(retired)}），"
          f"未入账 {len(new)} 条")


def cmd_adopt(data, path):
    """把一份处置记录写进账本条目。只加字段，不动、不删任何条目。"""
    try:
        items = json.loads(Path(path).read_text(encoding="utf-8").replace("\ufeff", ""))
    except Exception as e:
        print(f"❌ 处置文件读不了：{e}")
        sys.exit(2)
    entries = data["entries"]
    by_prefix = {d: d for d in entries}
    changed = 0
    for it in items:
        sha_prefix = it.get("sha", "")
        digest = next((d for d in by_prefix if d.startswith(sha_prefix)), None) if len(sha_prefix) < 64 else sha_prefix
        if digest is None or digest not in entries:
            print(f"[skip] 账本里没有这个指纹前缀：{sha_prefix}")
            continue
        entry = entries[digest]
        entry["resolved"] = {
            "kind": it["kind"],
            "src": it.get("src"),
            "anchor": it.get("anchor"),
            "new_sha256": it.get("new_sha256"),
            "old_text": it.get("old_text"),
            "bak": it.get("bak"),
            "bak_line": it.get("bak_line"),
            "note": it.get("note"),
            "judged_at": datetime.now().isoformat(timespec="seconds"),
        }
        entry["resolved"] = {k: v for k, v in entry["resolved"].items() if v is not None}
        changed += 1
    save(data)
    print(f"✅ 写入处置记录 {changed} 条（账本里没有条目被删、没有被改指纹）")



def cmd_retire_archived(data):
    """把「已被归档.py 删掉」的条目标成 retired。

    判据：账本里对不上、又没有处置记录，但它的开头能在 长期\\压缩.md 里找到
    —— 说明它是被归档压成了影子记录，不是真的丢了。
    找不到的仍按「整条消失」报警，不洗白。
    """
    current = harvest()
    zp = ROOT / "长期" / "压缩.md"
    compressed = _read_text(zp) if zp.exists() else ""
    changed = 0
    for d, e in data["entries"].items():
        if d in current or e.get("resolved"):
            continue
        head = (e.get("text") or "")[:20]
        if head and head in compressed:
            e["resolved"] = {
                "kind": "retired",
                "reason": "已归档进 长期/压缩.md",
                "at": datetime.now().isoformat(timespec="seconds"),
            }
            changed += 1
    save(data)
    print(f"✅ 标了 {changed} 条为「已归档」")


def main():
    ap = argparse.ArgumentParser()
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--init", action="store_true")
    g.add_argument("--sync", action="store_true")
    g.add_argument("--verify", action="store_true")
    g.add_argument("--retire-archived", action="store_true",
                   help="把已归档进 压缩.md 的旧条目标成 retired（跑在 归档 -Apply 之后）")
    ap.add_argument("--adopt", metavar="处置.json", help="把处置记录写进账本（只加字段）")
    args = ap.parse_args()
    data = load()
    if args.init:
        cmd_init(data)
    elif args.sync:
        cmd_sync(data)
    elif args.verify:
        cmd_verify(data)
    elif args.retire_archived:
        cmd_retire_archived(data)
    elif args.adopt:
        cmd_adopt(data, args.adopt)
    else:
        ap.print_help()


if __name__ == "__main__":
    main()

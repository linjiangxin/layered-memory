# -*- coding: utf-8 -*-
"""记忆体检：每天一行，出了问题一眼能看见。

输出是一行固定格式，方便直接追加进日志：
格式固定：

  [memory] 2026-10-31 16:20 total=116 pinned=23 cold=2 inject_cap=1200 ledger=OK pending=48

字段含义（针对我们这套「md 文件 + 遗忘曲线」的系统，不是 sqlite）：
  total      账本里记着的记忆条数（长期库 + 日记里的 `- ` 条目）
  pinned     权重表里 imp>=4 的条数（等于单子里的 0.7 保护线，永不自动压）
  cold       当前检索价值 R<0.45 的条数（该压了但还没压）
  inject_cap 注入上限，固定 1200 字（单子里建议值）
  ledger     账本对账结果：OK / FAIL(n)
  pending    日记里还没沉淀进长期库的 `- ` 条目数（待消化量）

用法：
  python tools\\记忆体检.py --line          打印一行
  python tools\\记忆体检.py --line --append 打印并追加进 memory\\健康.log
"""
import argparse
import json
import math
import os
import sys
from datetime import date, datetime
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

# 根目录：优先 LAYERED_MEMORY_HOME，否则按脚本自身位置推（tools\记忆体检.py 的根 = 上一级）。
_HOME = os.environ.get("LAYERED_MEMORY_HOME", "").strip()
ROOT = (Path(_HOME).expanduser().resolve() if _HOME
        else Path(__file__).resolve().parent.parent / "data")
# 兄弟脚本按「脚本自己所在目录」找，不跟数据目录走——数据可以搬，tools 不会搬。
_SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(_SCRIPT_DIR))

import importlib.util

spec = importlib.util.spec_from_file_location("记忆账本", _SCRIPT_DIR / "记忆账本.py")
ledger_mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ledger_mod)

WEIGHTS = ROOT / "记忆权重.json"
HEALTH_LOG = ROOT / "健康.log"
INJECT_CAP = 1200


def retention(s_stability, last_str, today=None):
    """FSRS 幂律：R = (1 + 0.2345 * t / S) ^ -0.5，t=距上次提到多少天。"""
    today = today or date.today()
    try:
        last = date.fromisoformat(str(last_str)[:10])
    except Exception:
        return 1.0
    t = max(0.0, (today - last).days)
    s = max(0.1, float(s_stability or 1.0))
    return (1 + 0.2345 * t / s) ** -0.5


def count_diary_entries():
    base = ROOT / "日记"
    if not base.exists():
        return 0
    n = 0
    for p in base.glob("*.md"):
        try:
            for line in p.read_text(encoding="utf-8", errors="replace").splitlines():
                s = line.strip()
                if s.startswith("- ") and len(s) >= 8:
                    n += 1
        except Exception:
            pass
    return n


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--line", action="store_true")
    ap.add_argument("--append", action="store_true")
    args = ap.parse_args()

    data = ledger_mod.load()
    total = len(data.get("entries", {}))

    pinned = cold = 0
    if WEIGHTS.exists():
        try:
            weights = json.loads(WEIGHTS.read_text(encoding="utf-8").replace("\ufeff", ""))
        except Exception:
            weights = {}
        for _, info in weights.items():
            imp = float(info.get("imp", 0) or 0)
            if imp >= 4:
                pinned += 1
            if retention(info.get("S", 1.0), info.get("last")) < 0.45:
                cold += 1

    missing = [d for d in data.get("entries", {}) if d not in ledger_mod.harvest()]
    ledger_state = "OK" if not missing else f"FAIL({len(missing)})"
    pending = count_diary_entries()

    line = (
        f"[memory] {datetime.now().strftime('%Y-%m-%d %H:%M')} "
        f"total={total} pinned={pinned} cold={cold} inject_cap={INJECT_CAP} "
        f"ledger={ledger_state} pending={pending}"
    )
    print(line)
    if args.append:
        HEALTH_LOG.parent.mkdir(parents=True, exist_ok=True)
        with HEALTH_LOG.open("a", encoding="utf-8") as f:
            f.write(line + "\n")

    # 告警（单子 §5.3）：ledger 不是 OK 就立刻查
    if missing:
        print(f"⚠️ 账本对不上：{len(missing)} 条记忆找不到了，跑 python tools\\记忆账本.py --verify 看细节")
    if pending > 50:
        print(f"⚠️ pending={pending}>50：日记积压太多没沉淀，该跑一次 固化.py --Apply")


if __name__ == "__main__":
    main()

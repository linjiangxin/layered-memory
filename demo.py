# -*- coding: utf-8 -*-
"""
demo.py —— 一条命令跑通整套分层记忆：建临时数据 → 检索 → 作废 → 再检索 → 归档。

全程在系统临时目录里跑，**不动仓库里的 data/**。
退出码 0 = 每一步都按预期走完；任何一步不对就退出码 1 并把原始输出贴出来。

用法：
  python demo.py
  python demo.py --keep        # 跑完保留临时目录，方便自己进去看
"""
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from datetime import date, timedelta
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

REPO = Path(__file__).resolve().parent
PY = sys.executable
NODE = shutil.which("node")

BAR = "─" * 66
step_no = [0]
failures = []


def title(text):
    step_no[0] += 1
    print()
    print(BAR)
    print("  [%d] %s" % (step_no[0], text))
    print(BAR)


def run(args, env, expect_rc=0, quiet=False):
    """跑一条命令，打印命令和输出。返回 (rc, out)。"""
    print("$ " + " ".join(str(a) for a in args))
    p = subprocess.run([str(a) for a in args], cwd=str(REPO), env=env,
                       capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    out = (p.stdout or "") + (p.stderr or "")
    if not quiet:
        for line in out.rstrip("\n").splitlines():
            print("  " + line)
        if p.returncode != 0:
            print("  [退出码 %d]" % p.returncode)
    if expect_rc is not None and p.returncode != expect_rc:
        failures.append("%s → 期望退出码 %s，实际 %d"
                        % (" ".join(str(a) for a in args), expect_rc, p.returncode))
    return p.returncode, out


def prepare(tmp):
    """复制假数据到临时目录，并把日记日期挪到最近三天（好让 14 天窗口读得到）。"""
    data = tmp / "data"
    shutil.copytree(REPO / "data", data)
    diary = data / "日记"
    files = sorted(diary.glob("*.md"))
    today = date.today()
    for i, p in enumerate(files):
        d = today - timedelta(days=len(files) - 1 - i)
        p.rename(diary / ("%s.md" % d.isoformat()))
    return data, [p.name for p in sorted(diary.glob("*.md"))]


def age_one_entry(data, needle="修表铺", days=400, stability=1.0):
    """把一条 imp=3 的记忆人为拨回很久以前，制造「该归档」的条件。"""
    wp = data / "记忆权重.json"
    db = json.loads(wp.read_text(encoding="utf-8") or "{}")
    hit = None
    for text, rec in db.items():
        if needle in text:
            rec["last"] = (date.today() - timedelta(days=days)).isoformat()
            rec["S"] = stability
            hit = text
            break
    wp.write_text(json.dumps(db, ensure_ascii=False, indent=2), encoding="utf-8")
    return hit


def main():
    keep = "--keep" in sys.argv
    tmp = Path(tempfile.mkdtemp(prefix="layered-memory-demo-"))
    print("=" * 66)
    print(" 分层记忆 · 端到端 demo")
    print(" 临时目录：%s" % tmp)
    print(" 数据目录：%s（LAYERED_MEMORY_HOME）" % (tmp / "data"))
    print("=" * 66)

    env = dict(os.environ)
    env["LAYERED_MEMORY_HOME"] = str(tmp / "data")
    env["PYTHONIOENCODING"] = "utf-8"

    try:
        title("准备假数据（复制 data/，日记日期挪到最近三天）")
        data, names = prepare(tmp)
        print("  日记：" + "、".join(names))
        print("  长期库：事实库.md、主题/关系与约定.md、主题/零碎见闻.md")

        title("记忆曲线.py —— 收录条目，算当前还记得多清楚")
        run([PY, "memory/记忆曲线.py"], env)

        title("检索.py 咖啡 —— 作废之前，这条能查到")
        rc, out = run([PY, "memory/检索.py", "咖啡"], env)
        if "咖啡" not in out or "没找到" in out:
            failures.append("检索「咖啡」应该命中一条，实际没命中")

        title("记忆账本.py --init —— 给每条记忆留指纹")
        run([PY, "tools/记忆账本.py", "--init"], env)

        title("作废.py 咖啡 --reason \"改喝焙茶\" —— 就地打标记")
        run([PY, "memory/作废.py", "咖啡", "--reason", "改喝焙茶"], env)
        run([PY, "memory/作废.py", "咖啡", "--reason", "改喝焙茶"], env, expect_rc=0)

        title("检索.py 咖啡 —— 默认跳过作废条目")
        rc, out = run([PY, "memory/检索.py", "咖啡"], env)
        if "已作废" not in out:
            failures.append("作废之后默认检索应该提示「另有 N 条已作废」")

        title("检索.py 咖啡 --include-deprecated —— 带 [废] 前缀看见它")
        rc, out = run([PY, "memory/检索.py", "咖啡", "--include-deprecated"], env)
        if "[废]" not in out:
            failures.append("--include-deprecated 应该返回带 [废] 前缀的条目")

        title("记忆账本.py --verify —— 标作废没让指纹对不上")
        rc, out = run([PY, "tools/记忆账本.py", "--verify"], env)

        title("归档.py —— 把一条放久了的零碎记忆压成一句话")
        aged = age_one_entry(data)
        print("  （人为把「%s」的 last 拨回 400 天前，稳定度 1.0，模拟真忘了）" % (aged or "?"))
        run([PY, "memory/归档.py"], env)
        run([PY, "memory/归档.py", "-Apply"], env)
        # 归档是「有意删除」：原条被压成一句话搬进 压缩.md。要让账本知道这件事，
        # 否则 --verify 会把它们当成「整条消失」报警。找不到的就照旧报警，不洗白。
        run([PY, "tools/记忆账本.py", "--retire-archived"], env)
        rc, out = run([PY, "memory/检索.py", "修表铺"], env)
        zp = data / "长期" / "压缩.md"
        print("\n  -- 长期/压缩.md 现在长这样 --")
        for line in zp.read_text(encoding="utf-8").strip().splitlines()[-4:]:
            print("  " + line)
        print("\n  ⚠ 归档会删掉原行，账本下次 --verify 会指出「1 条整条消失」——")
        print("    这是有意的，不是丢。确认确实该删，用 --adopt 写一条处置记录即可。")

        title("固化.py 3 —— 从最近三天的日记里挑候选（只列，不落库）")
        run([PY, "memory/固化.py", "3"], env)

        title("记忆体检.py --line —— 每天一行")
        run([PY, "tools/记忆体检.py", "--line"], env)

        title("记忆召回.mjs 偏好 —— 拼 prompt 用的那一段")
        if NODE:
            run([NODE, "tools/记忆召回.mjs", "偏好"], env)
        else:
            print("  （这台机器没有 node，跳过；装上 node 再跑同一条）")

        print()
        print("=" * 66)
        if failures:
            print(" 结果：%d 处不符合预期" % len(failures))
            for f in failures:
                print("  ! " + f)
            print("=" * 66)
            return 1
        print(" 结果：全部通过 ✅   （检索 → 作废 → 再检索 → 归档 → 账本 → 体检）")
        print("=" * 66)
        return 0
    finally:
        if keep:
            print("\n（--keep：临时目录留着：%s）" % tmp)
        else:
            shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())

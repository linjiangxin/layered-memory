# -*- coding: utf-8 -*-
"""tests\\test_锁.py —— 并发安全的底线测试（unittest，不依赖 pytest）。

跑法（仓库根）：
    python -m unittest discover -s tests -v

规矩同 test_memory.py：每个用例都在 tempfile.mkdtemp() 造的临时目录里跑，
LAYERED_MEMORY_HOME / MEM_LEDGER 都指向那儿，真实 data/ 一个字节都不碰。

钉住六件事：
  1. atomic_write 写出来的内容对、临时文件不留、失败了也清干净
  2. 8 个线程各追加 20 条 → 160 行，不多不少不重复
  3. 持锁期间第二个线程等不到 → 抛 LockTimeout（timeout 压到 0.3s）
  4. 拿着锁的进程直接死掉 → 后来者照样能拿到（系统级锁随 fd 关闭释放）
  5. core.add 走的是同一套锁：8 个线程并发写同一个 md 不丢条目
  6. rewriting 只读不写 → 不落盘、不动 mtime
  7. Windows 上目标被占用（WinError 5）→ os.replace 退避重试、超时抛错并清临时文件；
     非「被占用」类错误一次都不重试；read_bytes / read_json 同样带退避、坏 JSON 只告警
"""
import contextlib
import importlib.util
import io
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "memory"))
import 锁  # noqa: E402


def _lines(p):
    with io.open(str(p), "r", encoding="utf-8") as f:
        return [ln for ln in f.read().splitlines() if ln.strip()]


class 并发安全(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.mkdtemp()
        self.home = Path(self._tmp)

    def tearDown(self):
        shutil.rmtree(self._tmp, ignore_errors=True)

    # ---- 1 ---------------------------------------------------------------
    def test_原子写后内容正确且不留临时文件(self):
        p = self.home / "a.md"
        锁.atomic_write(str(p), "第一行\n第二行\n")
        self.assertEqual(p.read_text(encoding="utf-8"), "第一行\n第二行\n")

        锁.atomic_write(str(p), "整篇换掉\n")            # 整篇替换，不是追加
        self.assertEqual(p.read_text(encoding="utf-8"), "整篇换掉\n")
        self.assertEqual(p.read_bytes(), "整篇换掉\n".encode("utf-8"))

        leftovers = [n for n in os.listdir(self._tmp) if n.startswith(锁.TMP_PREFIX)]
        self.assertEqual(leftovers, [])

    def test_原子写失败要清掉临时文件(self):
        d = self.home / "是个目录"
        d.mkdir()
        with self.assertRaises(OSError):                 # 拿目录当目标，os.replace 必炸
            锁.atomic_write(str(d), "写不进去\n")
        leftovers = [n for n in os.listdir(self._tmp) if n.startswith(锁.TMP_PREFIX)]
        self.assertEqual(leftovers, [])                  # 半成品不许留

    # ---- 2 ---------------------------------------------------------------
    def test_八个线程各追加二十条不丢不重(self):
        p = self.home / "并发.md"
        p.write_text("# 并发\n", encoding="utf-8")
        errors = []

        def worker(tid):
            try:
                for i in range(20):
                    with 锁.rewriting(str(p), timeout=10.0) as doc:
                        doc.text = doc.text + "线程%d 第%02d条\n" % (tid, i)
            except BaseException as e:                   # 线程里的异常不会自己冒出来
                errors.append(e)

        ts = [threading.Thread(target=worker, args=(t,)) for t in range(8)]
        for t in ts:
            t.start()
        for t in ts:
            t.join(60)

        self.assertEqual(errors, [])
        rows = _lines(p)[1:]                             # 去掉标题行
        self.assertEqual(len(rows), 160)
        self.assertEqual(len(set(rows)), 160)            # 不许重复（重复=覆盖后重写）
        for tid in range(8):
            self.assertEqual(sum(1 for r in rows if r.startswith("线程%d " % tid)), 20)

    # ---- 3 ---------------------------------------------------------------
    def test_持锁期间第二个线程超时抛LockTimeout(self):
        p = self.home / "抢.md"
        holding, release = threading.Event(), threading.Event()
        errors = []

        def hold():
            try:
                with 锁.locked(str(p), timeout=2.0):
                    holding.set()
                    release.wait(10)
            except BaseException as e:
                errors.append(e)

        t = threading.Thread(target=hold)
        t.start()
        self.assertTrue(holding.wait(5), "持锁线程没起来")
        try:
            began = time.monotonic()
            with self.assertRaises(锁.LockTimeout):
                with 锁.locked(str(p), timeout=0.3):
                    pass
            cost = time.monotonic() - began
            self.assertGreaterEqual(cost, 0.25)          # 真的等了，不是立刻放弃
            self.assertLess(cost, 3.0)                   # 也没被 LK_LOCK 拖到 10 秒
        finally:
            release.set()
            t.join(10)
        self.assertEqual(errors, [])
        self.assertTrue(issubclass(锁.LockTimeout, TimeoutError))   # 兜底：TimeoutError 子类

    # ---- 4 ---------------------------------------------------------------
    def test_拿锁的进程崩了后来者还能拿到(self):
        p = self.home / "崩.md"
        holder = self.home / "holder.py"
        holder.write_text(
            "# -*- coding: utf-8 -*-\n"
            "import os, sys\n"
            "sys.path.insert(0, %r)\n"
            "import 锁\n"
            "ctx = 锁.locked(%r, timeout=2.0)\n"
            "ctx.__enter__()\n"
            "sys.stdout.write('locked')\n"
            "sys.stdout.flush()\n"
            "os._exit(0)          # 不释放、不 finally —— 模拟进程猝死\n"
            % (str(REPO / "memory"), str(p)),
            encoding="utf-8")

        r = subprocess.run([sys.executable, str(holder)], capture_output=True,
                           text=True, encoding="utf-8", timeout=30)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("locked", r.stdout)

        began = time.monotonic()
        with 锁.locked(str(p), timeout=1.0):             # 锁已经随子进程的 fd 一起没了
            pass
        self.assertLess(time.monotonic() - began, 0.9)
        self.assertTrue(os.path.exists(锁.lock_path(str(p))))

    # ---- 5 ---------------------------------------------------------------
    def test_core_add_八个线程并发不丢条目(self):
        old_home, old_ledger = os.environ.get("LAYERED_MEMORY_HOME"), os.environ.get("MEM_LEDGER")
        os.environ["LAYERED_MEMORY_HOME"] = str(self.home)
        os.environ["MEM_LEDGER"] = str(self.home / "记忆账本.json")
        try:
            spec = importlib.util.spec_from_file_location(
                "lm_core_并发", str(REPO / "memory" / "core.py"))
            core = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(core)
        finally:
            if old_home is None:
                os.environ.pop("LAYERED_MEMORY_HOME", None)
            else:
                os.environ["LAYERED_MEMORY_HOME"] = old_home
            if old_ledger is None:
                os.environ.pop("MEM_LEDGER", None)
            else:
                os.environ["MEM_LEDGER"] = old_ledger

        errors = []

        def worker(tid):
            try:
                for i in range(10):
                    core.add("并发条目 %d-%02d 号" % (tid, i), source="并发库")
            except BaseException as e:
                errors.append(e)

        ts = [threading.Thread(target=worker, args=(t,)) for t in range(8)]
        for t in ts:
            t.start()
        for t in ts:
            t.join(60)

        self.assertEqual(errors, [])
        rows = [ln for ln in _lines(self.home / "长期" / "并发库.md") if ln.startswith("- ")]
        self.assertEqual(len(rows), 80)
        self.assertEqual(len(set(rows)), 80)

    # ---- 6 ---------------------------------------------------------------
    def test_只读不写就不落盘(self):
        p = self.home / "只读.md"
        p.write_bytes("原样一行\n".encode("utf-8"))      # 写 bytes：别让 Windows 把 \n 翻成 \r\n
        before = p.stat().st_mtime_ns
        with 锁.rewriting(str(p)) as doc:
            self.assertEqual(doc.text, "原样一行\n")
            self.assertEqual(doc.lines(), ["原样一行\n"])
        self.assertEqual(p.stat().st_mtime_ns, before)   # 没赋值 → 不写盘、不动 mtime


class 替换退避与读侧(unittest.TestCase):
    """Windows 上目标文件正被别的进程 open 着时 os.replace 会 WinError 5。

    这里把「拒绝访问」造出来，钉住四件事：退避后能成功、超时要抛且清临时文件、
    非「被占用」类错误一次都不重试、读侧（read_bytes / read_json）同样不崩。
    超时一律压到 0.05s，别为了测退避把整套测试拖慢。
    """

    def setUp(self):
        self._tmp = tempfile.mkdtemp()
        self.home = Path(self._tmp)

    def tearDown(self):
        shutil.rmtree(self._tmp, ignore_errors=True)

    def _leftovers(self):
        return [n for n in os.listdir(self._tmp) if n.startswith(锁.TMP_PREFIX)]

    # ---- 7 ---------------------------------------------------------------
    def test_替换前两次拒绝访问第三次成功(self):
        p = self.home / "被占.md"
        real = os.replace
        calls = {"n": 0}

        def flaky(src, dst):
            calls["n"] += 1
            if calls["n"] <= 2:
                raise PermissionError(5, "拒绝访问")   # WinError 5 的形状
            return real(src, dst)

        with mock.patch.object(锁.os, "replace", flaky):
            锁.atomic_write(str(p), "第三次才写进去\n", timeout=2.0)

        self.assertEqual(calls["n"], 3)                # 真退避了，不是一次就放弃
        self.assertEqual(p.read_text(encoding="utf-8"), "第三次才写进去\n")
        self.assertEqual(self._leftovers(), [])        # 成功路径也不留垃圾

    # ---- 8 ---------------------------------------------------------------
    def test_替换一直拒绝访问超时抛错且清掉临时文件(self):
        p = self.home / "一直占着.md"
        calls = {"n": 0}

        def always(src, dst):
            calls["n"] += 1
            raise PermissionError(5, "拒绝访问")

        began = time.monotonic()
        with mock.patch.object(锁.os, "replace", always):
            with self.assertRaises(PermissionError):
                锁.atomic_write(str(p), "写不进去\n", timeout=0.05)
        cost = time.monotonic() - began

        self.assertGreaterEqual(calls["n"], 2)         # 预算内反复试过
        self.assertLess(cost, 1.0)                     # 到点就抛，不磨到底
        self.assertGreaterEqual(cost, 0.04)            # 确实等了，不是立刻抛
        self.assertFalse(p.exists())                   # 目标没被写坏
        self.assertEqual(self._leftovers(), [])        # 半成品清干净

    # ---- 9 ---------------------------------------------------------------
    def test_非目标错误立即抛不重试(self):
        p = self.home / "是目录.md"
        calls = {"n": 0}

        def boom(src, dst):
            calls["n"] += 1
            raise IsADirectoryError(21, "Is a directory")   # 不是「被占用」

        with mock.patch.object(锁.os, "replace", boom):
            with self.assertRaises(IsADirectoryError):
                锁.atomic_write(str(p), "x\n", timeout=5.0)

        self.assertEqual(calls["n"], 1)                # 一次都不多试
        self.assertEqual(self._leftovers(), [])

    # ---- 10 --------------------------------------------------------------
    def test_读遇拒绝访问重试后成功(self):
        p = self.home / "读.md"
        p.write_bytes("换到一半的另一半\n".encode("utf-8"))
        real_open = io.open
        calls = {"n": 0}

        def flaky(*a, **kw):
            calls["n"] += 1
            if calls["n"] <= 2:
                raise PermissionError(5, "拒绝访问")
            return real_open(*a, **kw)

        with mock.patch.object(锁.io, "open", flaky):
            text = 锁.read_bytes(str(p), timeout=2.0)

        self.assertEqual(text, "换到一半的另一半\n")
        self.assertEqual(calls["n"], 3)

    def test_读一直被占用超时抛ReadTimeout(self):
        p = self.home / "读不到.md"
        p.write_bytes(b"x\n")

        def always(*a, **kw):
            raise PermissionError(5, "拒绝访问")

        began = time.monotonic()
        with mock.patch.object(锁.io, "open", always):
            with self.assertRaises(锁.ReadTimeout):
                锁.read_bytes(str(p), timeout=0.05)

        self.assertLess(time.monotonic() - began, 1.0)
        self.assertTrue(issubclass(锁.ReadTimeout, 锁.LockTimeout))

    # ---- 11 --------------------------------------------------------------
    def test_read_json_文件不存在返回默认值(self):
        missing = str(self.home / "没有.json")
        self.assertIsNone(锁.read_json(missing))
        self.assertEqual(锁.read_json(missing, default={"a": 1}), {"a": 1})

    def test_read_json_坏掉只告警不抛(self):
        p = self.home / "坏.json"
        p.write_bytes('{"半截": '.encode("utf-8"))     # 写到一半的 JSON
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            got = 锁.read_json(str(p), default={"兜底": True})

        self.assertEqual(got, {"兜底": True})
        self.assertIn("[WARN]", err.getvalue())
        self.assertIn("JSON", err.getvalue())


if __name__ == "__main__":
    unittest.main()

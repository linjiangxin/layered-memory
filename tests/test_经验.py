# -*- coding: utf-8 -*-
"""tests\\test_经验.py —— 子代理经验库的底线测试（unittest，不依赖 pytest）。

跑法（仓库根）：
    python -m unittest discover -s tests -v

规矩同 test_memory.py：每个用例都在 tempfile.mkdtemp() 造的假仓库里跑，
      LAYERED_MEMORY_HOME / MEM_LEDGER 都指向那儿，实际数据一个字节都不碰。

钉住四件事：
  1. result 只认 成 / 败 / 半
  2. 结果败但没写死因 → 拒绝写入、退出码非 0
  3. 同一条记两次 → 文件里只有一行（去重）
  4. 记下的东西 查 / 最近 都能拿出来
"""
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
EXP_PY = REPO / "memory" / "经验.py"


def run(home: Path, *args):
    env = dict(os.environ)
    env["LAYERED_MEMORY_HOME"] = str(home)
    env["MEM_LEDGER"] = str(home / "记忆账本.json")
    env["PYTHONIOENCODING"] = "utf-8"
    return subprocess.run([sys.executable, str(EXP_PY), *args], cwd=str(REPO),
                          capture_output=True, text=True, encoding="utf-8", env=env)


def bullets(home: Path):
    p = home / "长期" / "经验.md"
    if not p.exists():
        return []
    return [ln for ln in p.read_text(encoding="utf-8").splitlines() if ln.startswith("- ")]


class 经验库(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.mkdtemp()
        self.home = Path(self._tmp)
        (self.home / "长期").mkdir(parents=True, exist_ok=True)

    def tearDown(self):
        import shutil
        shutil.rmtree(self._tmp, ignore_errors=True)

    def test_只认三种结果(self):
        r = run(self.home, "记录", "--task", "扫全仓", "--result", "很棒")
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("成 / 败 / 半", r.stdout + r.stderr)
        self.assertEqual(bullets(self.home), [])

    def test_败必须写死因(self):
        r = run(self.home, "记录", "--task", "扫全仓", "--result", "败")
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("cause", r.stdout + r.stderr)
        self.assertEqual(bullets(self.home), [])

    def test_写了就去重(self):
        args = ("记录", "--task", "扫全仓找关键字", "--path", "主脑自己 grep",
                "--result", "败", "--cause", "上下文被原文灌满",
                "--next", "起子代理只回 file:line")
        a = run(self.home, *args)
        self.assertEqual(a.returncode, 0, a.stdout + a.stderr)
        b = run(self.home, *args)
        self.assertEqual(b.returncode, 0)
        rows = bullets(self.home)
        self.assertEqual(len(rows), 1, rows)
        self.assertIn("死因：上下文被原文灌满", rows[0])
        self.assertIn("下次：起子代理只回 file:line", rows[0])
        self.assertNotIn("位置：", rows[0])            # 位置是可选字段，空就不落

    def test_能查能看最近(self):
        run(self.home, "记录", "--task", "跨文件搜索关键字", "--result", "成",
            "--loc", "memory/core.py:141")
        for name in ("查", "最近"):
            r = run(self.home, name, "跨文件搜索")
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            self.assertIn("跨文件搜索关键字", r.stdout)

    def test_空库时报缺(self):
        r = run(self.home, "查", "不存在的关键词")
        self.assertEqual(r.returncode, 1)
        self.assertIn("没有匹配", r.stdout)


if __name__ == "__main__":
    unittest.main()

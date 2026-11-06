# -*- coding: utf-8 -*-
"""tests\\test_loop.py —— 主循环（查 → 派 → 落）的底线测试。

跑法（仓库根）：
    python -m unittest discover -s tests -v

钉子：
  1. 子代理回「败」+死因 → 落进 长期/经验.md，且死因没丢
  2. 同一个任务再跑一次 → [1] 阶段要能把上次的败翻出来提醒
  3. --dry 一个字节都不写
  4. grep 子代理只回「命中几处 + file:line」，不吐原文
"""
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
LOOP_PY = REPO / "loop.py"


def run(home: Path, *args):
    env = dict(os.environ)
    env["LAYERED_MEMORY_HOME"] = str(home)
    env["MEM_LEDGER"] = str(home / "记忆账本.json")
    env["PYTHONIOENCODING"] = "utf-8"
    return subprocess.run([sys.executable, str(LOOP_PY), *args], cwd=str(REPO),
                          capture_output=True, text=True, encoding="utf-8", env=env)


class 主循环(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.mkdtemp()
        self.home = Path(self._tmp)
        (self.home / "长期").mkdir(parents=True, exist_ok=True)

    def tearDown(self):
        import shutil
        shutil.rmtree(self._tmp, ignore_errors=True)

    def _经验文件(self):
        p = self.home / "长期" / "经验.md"
        return p.read_text(encoding="utf-8") if p.exists() else ""

    def test_败也落盘且死因不丢(self):
        r = run(self.home, "全仓搜关键字", "--fake", "败", "--cause", "上下文被原文灌满")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        body = self._经验文件()
        self.assertIn("结果：败", body)
        self.assertIn("死因：上下文被原文灌满", body)

    def test_第二次会翻出上次的败(self):
        run(self.home, "全仓搜关键字", "--fake", "败", "--cause", "上下文被原文灌满")
        r = run(self.home, "全仓搜关键字又来了", "--fake", "成")
        self.assertIn("上次是败", r.stdout)
        self.assertIn("上下文被原文灌满", r.stdout)

    def test_dry不落盘(self):
        r = run(self.home, "看看某词在哪", "--grep", "遗忘曲线", "--path", "memory", "--dry")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(self._经验文件().strip(), "")

    def test_grep只回位置不回原文(self):
        r = run(self.home, "找 split_status", "--grep", "split_status", "--path", "memory", "--dry")
        self.assertIn("命中", r.stdout)
        self.assertIn("memory/core.py:", r.stdout.replace("\\", "/"))
        self.assertNotIn("def split_status", r.stdout)      # 原文不进主上下文

    def test_没给派活方式就报错(self):
        r = run(self.home, "干点啥")
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("--grep", r.stdout + r.stderr)


if __name__ == "__main__":
    unittest.main()

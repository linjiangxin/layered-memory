# -*- coding: utf-8 -*-
"""tests\\test_memory.py —— 记忆库三条底线测试（unittest，不依赖 pytest）。

跑法（仓库根）：
    python -m unittest discover -s tests -v

规矩：每个用例都在 tempfile.mkdtemp() 造的假仓库里跑，
      LAYERED_MEMORY_HOME / MEM_LEDGER 都指向那儿，实际数据一个字节都不碰。
"""
import importlib.util
import io
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from datetime import date
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
CORE_PY = REPO / "memory" / "core.py"
LEDGER_PY = REPO / "tools" / "记忆账本.py"


def read(p) -> str:
    return Path(p).read_text(encoding="utf-8")


def load_core(home: Path):
    """在指定假仓库上重新加载 core（模块级路径是 import 时算的，必须重载）。"""
    os.environ["LAYERED_MEMORY_HOME"] = str(home)
    os.environ["MEM_LEDGER"] = str(Path(home) / "记忆账本.json")
    sys.modules.pop("core", None)
    spec = importlib.util.spec_from_file_location("core", CORE_PY)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    sys.modules["core"] = mod
    return mod


def make_home(root: Path) -> Path:
    """搭一个最小假仓库：长期\\事实库.md、长期\\主题\\关系与约定.md、今天的一篇日记。"""
    mem = root          # 发布版：数据根就是 home 本身（长期/、日记/、记忆权重.json 平铺在这层）
    (mem / "长期" / "主题").mkdir(parents=True, exist_ok=True)
    (mem / "日记").mkdir(parents=True, exist_ok=True)
    (mem / "长期" / "事实库.md").write_text(
        "# 事实库（假）\n\n"
        "## 人和身份\n\n"
        "- 阿舟养了一只橘猫，叫团子，很胖。\n"
        "- 阿舟喜欢冰美式，不加糖。\n"
        "- 阿舟在写一个纯文件的记忆库，没有数据库。\n",
        encoding="utf-8")
    (mem / "长期" / "主题" / "关系与约定.md").write_text(
        "# 关系与约定（假）\n\n"
        "- 他讨厌香菜，点外卖必写「不要香菜」。\n"
        "- 他讨厌被反复问「你确定吗」。\n",
        encoding="utf-8")
    (mem / "日记" / (date.today().isoformat() + ".md")).write_text(
        "# 今天\n\n## 测试记忆库\n今天在造假仓库跑测试，橘猫在旁边睡觉。\n",
        encoding="utf-8")
    return mem


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="layered-memory-test-"))
        self.mem = make_home(self.tmp)
        self.core = load_core(self.tmp)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)
        os.environ.pop("LAYERED_MEMORY_HOME", None)
        os.environ.pop("MEM_LEDGER", None)
        sys.modules.pop("core", None)


class TestSearch(Base):
    def test_search_hits_and_sorts(self):
        # ① 能命中，且按分数从高到低
        hits = self.core.search("橘猫", limit=5)
        self.assertTrue(hits, "「橘猫」应该命中至少一条")
        self.assertIn("橘猫", hits[0]["text"])
        self.assertEqual(set(hits[0]) >= {"text", "src", "file", "line", "score", "deprecated"}, True)

        scores = [h["score"] for h in hits]
        self.assertEqual(scores, sorted(scores, reverse=True), "结果必须按 score 降序")

        # src 是「文件:行号」，file/line 是拆开的同一份位置
        self.assertEqual(hits[0]["src"], "%s:%d" % (hits[0]["file"], hits[0]["line"]))
        self.assertTrue(hits[0]["file"].endswith("事实库.md"))
        self.assertIs(hits[0]["deprecated"], False)

        # 完全不相干的查询 → 空
        self.assertEqual(self.core.search("量子色动力学"), [])

    def test_add_appends_and_returns_location(self):
        res = self.core.add("阿舟不吃香菜，闻到就皱眉。", tags=["口味"])
        self.assertEqual(set(res), {"key", "file", "line"})
        self.assertEqual(res["file"], os.path.join("长期", "事实库.md"))
        lines = read(self.mem / "长期" / "事实库.md").splitlines()
        self.assertEqual(lines[res["line"] - 1],
                         "- 阿舟不吃香菜，闻到就皱眉。  #口味")
        # 同一条再加一次不会重复
        again = self.core.add("阿舟不吃香菜，闻到就皱眉。")
        self.assertEqual(again["line"], res["line"])
        self.assertEqual(len(read(self.mem / "长期" / "事实库.md").splitlines()), len(lines))


class TestDeprecate(Base):
    def test_deprecate_hides_by_default(self):
        # ② 作废后默认搜不到，include_deprecated=True 能搜到并标 deprecated
        before = self.core.search("团子")
        self.assertTrue(before)

        lines_before = len(read(self.mem / "长期" / "事实库.md").splitlines())
        res = self.core.deprecate("团子", reason="猫送人了")
        self.assertEqual(res["changed"], 1)
        self.assertEqual(len(res["left"]), 0, "回读核对：不该有没标上的")

        self.assertEqual(self.core.search("团子"), [], "作废后默认不该再召回")

        shown = self.core.search("团子", include_deprecated=True)
        self.assertEqual(len(shown), 1)
        self.assertIs(shown[0]["deprecated"], True)

        raw = read(self.mem / "长期" / "事实库.md")
        self.assertIn("<!-- status: deprecated | since: %s | by: 猫送人了 -->"
                      % date.today().isoformat(), raw)
        # 只加标记不删内容：条目正文还在，行数不变
        self.assertIn("阿舟养了一只橘猫，叫团子，很胖。", raw)
        self.assertEqual(len(raw.splitlines()), lines_before)
        # 备份真的生成了
        baks = list((self.mem / "长期").glob("事实库.md.bak-*"))
        self.assertEqual(len(baks), 1)

    def test_deprecate_dry_does_nothing(self):
        res = self.core.deprecate("冰美式", reason="改喝热茶了", dry=True)
        self.assertEqual(res["changed"], 0)
        self.assertEqual(len(res["todo"]), 1)
        self.assertEqual(len(self.core.search("冰美式")), 1, "--dry 不许动文件")


class TestLedger(Base):
    def run_tool(self, *args):
        env = dict(os.environ)
        env["LAYERED_MEMORY_HOME"] = str(self.tmp)
        env["MEM_LEDGER"] = str(self.mem / "记忆账本.json")
        env["PYTHONIOENCODING"] = "utf-8"
        return subprocess.run([sys.executable, str(LEDGER_PY), *args],
                              cwd=str(REPO), env=env,
                              stdout=subprocess.PIPE, stderr=subprocess.STDOUT)

    def test_sync_then_verify_exit_zero(self):
        # ③ --sync 再 --verify，退出码 0（临时账本）
        synced = self.run_tool("--sync")
        self.assertEqual(synced.returncode, 0,
                         "sync 失败：\n" + synced.stdout.decode("utf-8", "replace"))
        self.assertTrue((self.mem / "记忆账本.json").exists())

        verified = self.run_tool("--verify")
        self.assertEqual(verified.returncode, 0,
                         "verify 失败：\n" + verified.stdout.decode("utf-8", "replace"))
        out = verified.stdout.decode("utf-8", "replace")
        self.assertIn("对账通过", out)


if __name__ == "__main__":
    unittest.main()

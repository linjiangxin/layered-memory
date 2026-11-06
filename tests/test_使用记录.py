# -*- coding: utf-8 -*-
"""tests\\test_使用记录.py —— 检索命中自动回写使用记录（unittest，不依赖 pytest）。

跑法（仓库根）：
    python -m unittest discover -s tests -v

规矩同 test_memory.py：每个用例都在 tempfile.mkdtemp() 造的假仓库里跑，
LAYERED_MEMORY_HOME / MEM_LEDGER 都指向那儿，真实 data/ 一个字节都不碰。

钉住五件事：
  1. 命中后文件里多出一行，via="search"、weight=0.5、query 是原文
  2. 同一天同一个 key 重复搜（换个查询词也算）不新增行
  3. record=False / LAYERED_MEMORY_RECORD=0 → 一个字都不写
  4. 老格式记录（没有 via）照样按满值算权重，search 记录按 0.5 折
  5. 作废条目永远不进自动记录（带 include_deprecated 也不进）
"""
import datetime
import importlib.util
import io
import json
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
CORE_PY = REPO / "memory" / "core.py"


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
    """最小假仓库：三条事实 + 一条已经作废的旧版本。"""
    (root / "长期").mkdir(parents=True, exist_ok=True)
    (root / "日记").mkdir(parents=True, exist_ok=True)
    (root / "长期" / "事实库.md").write_text(
        "# 事实库（假）\n\n"
        "- 阿舟养了一只橘猫，叫团子，很胖。\n"
        "- 阿舟喜欢冰美式，不加糖。\n"
        "- 阿舟住在临川。 <!-- status: deprecated | since: 2026-10-01 | by: 搬家 -->\n",
        encoding="utf-8")
    return root


def read_log(home: Path):
    p = Path(home) / "使用记录.jsonl"
    if not p.exists():
        return []
    out = []
    with io.open(str(p), "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                out.append(json.loads(line))
    return out


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="layered-memory-use-"))
        self.home = make_home(self.tmp)
        self.core = load_core(self.tmp)
        self.cat = "阿舟养了一只橘猫，叫团子，很胖。"

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)
        os.environ.pop("LAYERED_MEMORY_HOME", None)
        os.environ.pop("MEM_LEDGER", None)
        os.environ.pop("LAYERED_MEMORY_RECORD", None)
        sys.modules.pop("core", None)


class Test自动记账(Base):
    def test_命中一条就多一行且字段齐全(self):
        hits = self.core.search("橘猫", limit=1)
        self.assertEqual(len(hits), 1)
        rows = read_log(self.home)
        self.assertEqual(len(rows), 1, "命中一条应该正好写一行")
        r = rows[0]
        self.assertEqual(r["via"], "search")
        self.assertEqual(r["weight"], 0.5)
        self.assertEqual(r["query"], "橘猫")
        self.assertTrue(r["used"])
        self.assertEqual(r["key"], self.core.body_sha(hits[0]["text"]))
        self.assertEqual(r["sha"], r["key"])
        self.assertEqual(r["at"][:10], datetime.date.today().isoformat())
        self.assertEqual(r["src"], "事实库.md")

    def test_同一天同一个条目只记一次(self):
        self.core.search("橘猫", limit=1)
        self.core.search("橘猫", limit=1)
        self.assertEqual(len(read_log(self.home)), 1, "同一天重复搜同一条不新增行")
        # 换个词还是同一条（团子 → 同一条正文），也不该再记
        self.core.search("团子", limit=1)
        self.assertEqual(len(read_log(self.home)), 1, "同 key 同日只计一次")

    def test_record_False_不写文件(self):
        self.core.search("橘猫", limit=1, record=False)
        self.assertEqual(read_log(self.home), [])
        self.assertFalse((self.home / "使用记录.jsonl").exists())

    def test_环境变量关掉就不写(self):
        os.environ["LAYERED_MEMORY_RECORD"] = "0"
        try:
            self.core.search("橘猫", limit=1)
        finally:
            os.environ.pop("LAYERED_MEMORY_RECORD", None)
        self.assertEqual(read_log(self.home), [])
        # 关掉之后再打开，照常记
        self.core.search("橘猫", limit=1)
        self.assertEqual(len(read_log(self.home)), 1)

    def test_作废条不进自动记录(self):
        self.core.search("临川", include_deprecated=True)   # 能返回，但不该记账
        rows = read_log(self.home)
        bad = self.core.body_sha("阿舟住在临川。")
        self.assertNotIn(bad, [r["sha"] for r in rows], "作废条不许出现在自动记录里")
        self.assertEqual(rows, [])


class Test权重折算(Base):
    def _write(self, rows):
        p = self.home / "使用记录.jsonl"
        p.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows),
                     encoding="utf-8")

    def test_老记录没有via按满值算(self):
        sha = self.core.body_sha(self.cat)
        self._write([{"ts": "2026-11-06T09:00:00", "sha": sha, "key": self.cat[:30],
                      "used": True, "source": "mark-used"}])
        u = self.core.load_usage()[sha]
        self.assertEqual(u["used"], 1.0, "老记录没有 via → 当人确认的满值")
        self.assertEqual(u["last_used"], "2026-11-06")

    def test_search记录按0点5折(self):
        sha = self.core.body_sha(self.cat)
        self._write([{"at": "2026-11-06T09:00:00", "ts": "2026-11-06T09:00:00",
                      "sha": sha, "key": sha, "used": True, "via": "search",
                      "weight": 0.5, "query": "橘猫", "source": "search"}])
        self.assertEqual(self.core.load_usage()[sha]["used"], 0.5)

    def test_缺weight时兜底用SEARCH_W(self):
        sha = self.core.body_sha(self.cat)
        self._write([{"at": "2026-11-06T09:00:00", "sha": sha, "used": True,
                      "via": "search", "query": "橘猫"}])
        self.assertEqual(self.core.load_usage()[sha]["used"], self.core.SEARCH_W)

    def test_manual来源算满值(self):
        sha = self.core.body_sha(self.cat)
        self._write([{"at": "2026-11-06T09:00:00", "sha": sha, "used": True,
                      "via": "manual", "weight": 0.5}])
        self.assertEqual(self.core.load_usage()[sha]["used"], 1.0)


if __name__ == "__main__":
    unittest.main()

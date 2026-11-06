# -*- coding: utf-8 -*-
"""tests\\test_同义扩展.py —— 可选插件「同义扩展」接进检索的底线。

要证的六件事：
  ① 双向命中：记忆写「头疼」，用「不舒服」搜得到；反过来也成
  ② 扩展命中折权重：同一个条目，「头疼」的分数必须高于「不舒服」
  ③ 插件缺失 → 不启用，走原来的字面检索
  ④ enabled:false → 不启用
  ⑤ JSON 坏了 → 静默回退，不打断检索
  ⑥ 空查询不炸

数据全在 tempfile 里造：LAYERED_MEMORY_HOME 指到假数据根，
LAYERED_MEMORY_PLUGINS 指到假插件目录，真仓库一个字节都不碰。
"""
import importlib.util
import json
import os
import shutil
import sys
import tempfile
import unittest
from datetime import date
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
CORE_PY = REPO / "memory" / "core.py"

GROUPS = [["头疼", "头痛", "不舒服", "难受", "不得劲"]]


def load_core(home, plugdir):
    os.environ["LAYERED_MEMORY_HOME"] = str(home)
    os.environ["MEM_LEDGER"] = str(Path(home) / "记忆账本.json")
    os.environ["LAYERED_MEMORY_PLUGINS"] = str(plugdir)
    sys.modules.pop("core", None)
    spec = importlib.util.spec_from_file_location("core", CORE_PY)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    sys.modules["core"] = mod
    return mod


class Base(unittest.TestCase):
    TEXT = "今天有点头疼。"

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="lm-syn-test-"))
        self.plugdir = self.tmp / "plugins"
        self.plugdir.mkdir()
        (self.tmp / "长期" / "主题").mkdir(parents=True)
        (self.tmp / "日记").mkdir()
        (self.tmp / "长期" / "事实库.md").write_text(
            "# 事实库（假）\n\n## 身体\n\n- " + self.TEXT + "\n", encoding="utf-8")
        self.write_plugin({"name": "同义扩展", "version": 1, "enabled": True,
                           "weight": 0.7, "groups": GROUPS})
        self.core = load_core(self.tmp, self.plugdir)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)
        for k in ("LAYERED_MEMORY_HOME", "MEM_LEDGER", "LAYERED_MEMORY_PLUGINS"):
            os.environ.pop(k, None)
        sys.modules.pop("core", None)

    def write_plugin(self, obj):
        text = obj if isinstance(obj, str) else json.dumps(obj, ensure_ascii=False)
        (self.plugdir / "同义扩展.json").write_text(text, encoding="utf-8")

    def drop_plugin(self):
        (self.plugdir / "同义扩展.json").unlink()

    def reload(self):
        self.core = load_core(self.tmp, self.plugdir)

    def score(self, q, needle="头疼"):
        for h in self.core.search(q, limit=5, record=False):   # 纯打分对比，不记账
            if needle in h["text"]:
                return round(h["score"], 4)
        return None


class TestSynonymExpansion(Base):
    def test_hit_both_ways(self):
        # ① 记忆写「头疼」，查询「不舒服」→ 命中
        self.assertIsNotNone(self.score("不舒服"), "「不舒服」该召回「头疼」那条")
        # 双向：记忆写「不舒服」，查询「头疼」也要命中
        (self.tmp / "长期" / "事实库.md").write_text(
            "# 事实库（假）\n\n- 今天有点不舒服。\n", encoding="utf-8")
        self.assertIsNotNone(self.score("头疼", needle="不舒服"), "反向也该命中")

    def test_expansion_weight_lower_than_direct(self):
        # ② 同一个条目：真命中 > 扩展命中，且比值就是 weight（0.35/1.0）
        direct = self.score("头疼")
        expanded = self.score("不舒服")
        self.assertIsNotNone(direct)
        self.assertIsNotNone(expanded)
        self.assertGreater(direct, expanded, "真命中必须压过同义扩展")
        ratio = expanded / direct
        self.assertAlmostEqual(ratio, 0.35, delta=0.02,
                               msg="扩展命中按 0.7 折、分母仍是原查询长度 → 0.35")

    def test_weight_is_configurable(self):
        self.write_plugin({"name": "同义扩展", "enabled": True, "weight": 0.2, "groups": GROUPS})
        self.reload()
        low = self.score("不舒服")
        self.write_plugin({"name": "同义扩展", "enabled": True, "weight": 0.7, "groups": GROUPS})
        self.reload()
        high = self.score("不舒服")
        self.assertGreater(high, low, "weight 调低，扩展命中的分数要跟着降")

    def test_missing_plugin_means_off(self):
        # ③ 没有插件文件 = 不启用，走原逻辑
        self.drop_plugin()
        self.reload()
        self.assertIsNone(self.score("不舒服"), "没插件时「不舒服」不该命中")
        self.assertIsNotNone(self.score("头疼"), "真命中不受影响")
        self.assertEqual(self.core.search("量子色动力学"), [])

    def test_disabled_plugin_means_off(self):
        # ④ enabled:false = 不启用
        self.write_plugin({"name": "同义扩展", "enabled": False, "weight": 0.7, "groups": GROUPS})
        self.reload()
        self.assertIsNone(self.score("不舒服"))
        self.assertAlmostEqual(self.score("头疼"), 0.7538, delta=0.01)

    def test_broken_json_falls_back(self):
        # ⑤ JSON 坏掉 → 静默回退，检索照跑
        self.write_plugin("{ 坏掉的 JSON ")
        self.reload()
        self.assertIsNone(self.score("不舒服"))
        self.assertIsNotNone(self.score("头疼"), "坏插件不许把检索带崩")
        self.assertIsNotNone(self.core.search("头疼", include_deprecated=True))

    def test_empty_query_is_safe(self):
        # ⑥ 空查询不炸
        for q in ("", "   ", "\n", None):
            self.assertEqual(self.core.search(q), [])

    def test_custom_group_works(self):
        # 换一组自定义词，照样双向
        (self.tmp / "长期" / "事实库.md").write_text(
            "# 事实库（假）\n\n- 我买了新球拍。\n", encoding="utf-8")
        self.write_plugin({"name": "同义扩展", "enabled": True, "weight": 0.7,
                           "groups": [["拍子", "球拍"]]})
        self.reload()
        hits = self.core.search("拍子")
        self.assertTrue(hits, "自定义组该生效")
        self.assertIn("球拍", hits[0]["text"])

    def test_diary_entries_also_expand(self):
        # 日记小节与长期库同一套打分，扩展照样生效
        (self.tmp / "日记" / (date.today().isoformat() + ".md")).write_text(
            "# 今天\n\n## 状态\n有点难受，早点睡。\n", encoding="utf-8")
        hits = self.core.search("头疼")
        self.assertTrue(any("难受" in h["text"] for h in hits), "日记条目也要被扩展命中")


if __name__ == "__main__":
    unittest.main()

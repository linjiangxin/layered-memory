# -*- coding: utf-8 -*-
"""tests\\test_插件.py —— 插件加载器的底线（零依赖，不碰真数据）。

规矩：每个用例都在 tempfile 造的目录里跑，LAYERED_MEMORY_PLUGINS 指过去，
      <仓库>/plugins 里的真插件一个字节都不读。
"""
import importlib.util
import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
PLUGIN_PY = REPO / "memory" / "插件.py"


def load_plugin_mod():
    sys.modules.pop("插件", None)
    spec = importlib.util.spec_from_file_location("插件", PLUGIN_PY)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    sys.modules["插件"] = mod
    return mod


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="lm-plugin-test-"))
        self.dir = self.tmp / "plugins"
        self.dir.mkdir()
        import os
        self._old = os.environ.get("LAYERED_MEMORY_PLUGINS")
        os.environ["LAYERED_MEMORY_PLUGINS"] = str(self.dir)
        self.mod = load_plugin_mod()

    def tearDown(self):
        import os
        if self._old is None:
            os.environ.pop("LAYERED_MEMORY_PLUGINS", None)
        else:
            os.environ["LAYERED_MEMORY_PLUGINS"] = self._old
        shutil.rmtree(self.tmp, ignore_errors=True)
        sys.modules.pop("插件", None)

    def write(self, name, obj):
        p = self.dir / (name + ".json")
        text = obj if isinstance(obj, str) else json.dumps(obj, ensure_ascii=False)
        p.write_text(text, encoding="utf-8")
        return p


class TestLoader(Base):
    def test_missing_file_is_none(self):
        self.assertIsNone(self.mod.load("同义扩展"))
        self.assertEqual(self.mod.list_all(), [])

    def test_enabled_false_is_none(self):
        self.write("同义扩展", {"name": "同义扩展", "enabled": False, "groups": [["a", "b"]]})
        self.assertIsNone(self.mod.load("同义扩展"))
        # require_enabled=False 时仍能读到（体检用）
        got = self.mod.load("同义扩展", require_enabled=False)
        self.assertIsNotNone(got)
        self.assertFalse(got["enabled"])

    def test_load_ok(self):
        self.write("同义扩展", {"name": "同义扩展", "version": 1, "enabled": True,
                              "weight": 0.7, "groups": [["头疼", "不舒服"]]})
        got = self.mod.load("同义扩展")
        self.assertEqual(got["name"], "同义扩展")
        self.assertEqual(got["weight"], 0.7)
        self.assertEqual(got["groups"], [["头疼", "不舒服"]])
        self.assertTrue(got["path"].endswith("同义扩展.json"))
        self.assertEqual([x["name"] for x in self.mod.list_all()], ["同义扩展"])

    def test_broken_json_is_silent(self):
        self.write("同义扩展", "{ 这不是 JSON ")
        self.assertIsNone(self.mod.load("同义扩展"), "坏 JSON 必须当没有，不能抛")
        self.assertEqual(self.mod.list_all(), [])
        self.assertTrue(self.mod.WARNINGS, "坏文件要留一条警告")

    def test_non_object_json_is_silent(self):
        self.write("同义扩展", "[1, 2, 3]")
        self.assertIsNone(self.mod.load("同义扩展"))


if __name__ == "__main__":
    unittest.main()

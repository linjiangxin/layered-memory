# -*- coding: utf-8 -*-
"""tests\\test_代理.py —— 适配层：默认假代理 / 三种回包 / 异常一律收成「败」。

跑法（仓库根）：
    python -m unittest discover -s tests -v

全程不联网：urllib.request.urlopen 全部用 unittest.mock 假造，
断言的是「发出去的那条请求」和「收回来那条 dict」，不是真端点。
"""
import json
import sys
import unittest
from pathlib import Path
from unittest import mock

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "memory"))
import 代理  # noqa: E402

密钥 = "sk-测试密钥-123456"
端点 = "http://127.0.0.1:9/v1/delegate"


def 环境(**kw):
    """一份 http 模式的环境变量；传 None 表示把这一项删掉。"""
    base = {"LAYERED_MEMORY_DELEGATE": "http",
            "LAYERED_MEMORY_DELEGATE_URL": 端点,
            "LAYERED_MEMORY_DELEGATE_KEY": 密钥,
            "LAYERED_MEMORY_DELEGATE_MODEL": "test-model"}
    for k, v in kw.items():
        if v is None:
            base.pop(k, None)
        else:
            base[k] = v
    return base


def 假回包(payload):
    """造一个 urlopen 的返回值：read() 给字节，close() 可关（contextlib.closing 要用）。"""
    m = mock.MagicMock()
    m.read.return_value = (payload if isinstance(payload, bytes)
                           else json.dumps(payload, ensure_ascii=False).encode("utf-8"))
    return m


class 适配层(unittest.TestCase):
    # ---- 默认：假代理 ----------------------------------------------------

    def test_默认不设变量就是假代理(self):
        out = 代理.make_delegate(env={})("随便一个活")
        self.assertEqual(out["result"], "成")
        self.assertEqual(out["path"], "fake-delegate（演示用）")
        self.assertEqual(out["conclusion"], "跑通了")

    def test_fake标志优先于环境变量(self):
        with mock.patch("urllib.request.urlopen") as m:
            out = 代理.make_delegate(result="败", cause="演示用", env=环境())("随便一个活")
        self.assertEqual(out["result"], "败")
        self.assertEqual(out["cause"], "演示用")
        m.assert_not_called()

    # ---- 三种回包 --------------------------------------------------------

    def test_openai兼容回包解析成功(self):
        内容 = json.dumps({"result": "成", "conclusion": "扫了 7 个文件，命中 3 处",
                           "loc": "memory/core.py:58", "path": "子代理搜了一遍"},
                          ensure_ascii=False)
        回包 = {"choices": [{"message": {"content": 内容}}]}
        with mock.patch("urllib.request.urlopen", return_value=假回包(回包)):
            out = 代理.make_delegate(env=环境())("扫一遍")
        self.assertEqual(out["result"], "成")
        self.assertEqual(out["conclusion"], "扫了 7 个文件，命中 3 处")
        self.assertEqual(out["loc"], "memory/core.py:58")

    def test_content字符串回包解析成功(self):
        内容 = json.dumps({"result": "半", "conclusion": "只跑完一半", "next_try": "缩小范围"},
                          ensure_ascii=False)
        with mock.patch("urllib.request.urlopen", return_value=假回包({"content": 内容})):
            out = 代理.make_delegate(env=环境())("跑一半")
        self.assertEqual(out["result"], "半")
        self.assertEqual(out["next_try"], "缩小范围")

    def test_直接dict回包解析成功(self):
        回包 = {"result": "成", "conclusion": "一句话结论",
                "loc": "memory/core.py:141, memory/经验.py:78", "cause": ""}
        with mock.patch("urllib.request.urlopen", return_value=假回包(回包)):
            out = 代理.make_delegate(env=环境())("直接给 dict")
        self.assertEqual(out["result"], "成")
        self.assertEqual(out["loc"], "memory/core.py:141, memory/经验.py:78")
        self.assertNotIn("cause", out)          # 空字段不进契约

    # ---- 收不住的，一律变「败」，不抛 ------------------------------------

    def test_非法result值变成败(self):
        with mock.patch("urllib.request.urlopen", return_value=假回包({"result": "成功"})):
            out = 代理.make_delegate(env=环境())("非法值")
        self.assertEqual(out["result"], "败")
        self.assertIn("result", out["cause"])

    def test_网络异常变成败不抛(self):
        with mock.patch("urllib.request.urlopen", side_effect=OSError("拒绝连接")):
            with self.assertLogs("代理", level="WARNING"):
                out = 代理.make_delegate(env=环境())("网络炸了")
        self.assertEqual(out["result"], "败")
        self.assertIn("调端点失败", out["cause"])

    def test_非json回包变成败(self):
        with mock.patch("urllib.request.urlopen", return_value=假回包(b"<html>502 bad gateway")):
            out = 代理.make_delegate(env=环境())("回了一页 HTML")
        self.assertEqual(out["result"], "败")
        self.assertIn("JSON", out["cause"])

    def test_败没写死因也补一句(self):
        with mock.patch("urllib.request.urlopen", return_value=假回包({"result": "败"})):
            out = 代理.make_delegate(env=环境())("败得没头没尾")
        self.assertEqual(out["result"], "败")
        self.assertTrue(out["cause"])           # 经验.record 要求败必须有 cause

    def test_位置不像file_line就丢掉(self):
        with mock.patch("urllib.request.urlopen",
                        return_value=假回包({"result": "成", "loc": "大概是 core.py 里"})):
            out = 代理.make_delegate(env=环境())("位置胡写")
        self.assertNotIn("loc", out)

    # ---- 请求与密钥 ------------------------------------------------------

    def test_请求体带task与model且超时30秒(self):
        seen = {}

        def 假urlopen(req, timeout=None):
            seen["req"], seen["timeout"] = req, timeout
            return 假回包({"result": "成"})

        with mock.patch("urllib.request.urlopen", side_effect=假urlopen):
            代理.make_delegate(env=环境())("把这句话发过去")
        body = json.loads(seen["req"].data.decode("utf-8"))
        self.assertEqual(body["task"], "把这句话发过去")
        self.assertEqual(body["model"], "test-model")
        self.assertEqual(seen["req"].get_method(), "POST")
        self.assertEqual(float(seen["timeout"]), 30.0)

    def test_密钥在header里且不出现在返回和日志里(self):
        seen = {}

        def 假urlopen(req, timeout=None):
            seen["req"] = req
            raise OSError("connection refused: %s 带着 Bearer %s" % (端点, 密钥))

        with mock.patch("urllib.request.urlopen", side_effect=假urlopen):
            with self.assertLogs("代理", level="WARNING") as log:
                out = 代理.make_delegate(env=环境())("查密钥会不会漏")

        self.assertEqual(seen["req"].get_header("Authorization"), "Bearer " + 密钥)
        blob = json.dumps(out, ensure_ascii=False) + "\n".join(log.output)
        self.assertNotIn(密钥, blob)            # 密钥不落进返回，也不进日志
        self.assertNotIn("/v1/delegate", blob)  # 端点路径同样抹掉

    def test_没配URL直接给败且不联网(self):
        with mock.patch("urllib.request.urlopen") as m:
            out = 代理.make_delegate(env=环境(LAYERED_MEMORY_DELEGATE_URL=None))("没配端点")
        self.assertEqual(out["result"], "败")
        self.assertIn("LAYERED_MEMORY_DELEGATE_URL", out["cause"])
        m.assert_not_called()


if __name__ == "__main__":
    unittest.main()

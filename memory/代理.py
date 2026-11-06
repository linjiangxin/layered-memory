# -*- coding: utf-8 -*-
"""代理.py —— 子代理适配层：默认假代理（不联网），设一个环境变量就挂真端点。

主循环 loop.py 只认一个签名 delegate(task) -> dict，这里负责「这个 delegate 从哪来」：

  什么都没设            → 假代理，结果可预测（成 + 演示用路子），测例一个字都不用改
  LAYERED_MEMORY_DELEGATE=http
      LAYERED_MEMORY_DELEGATE_URL    端点（必填；缺了就直接回「败」，不抛）
      LAYERED_MEMORY_DELEGATE_KEY    密钥 → Authorization: Bearer <key>
      LAYERED_MEMORY_DELEGATE_MODEL  可选，随请求体一起发
      LAYERED_MEMORY_DELEGATE_TIMEOUT 可选，秒，默认 30

请求：POST JSON  {"task": "...", "model": "..."}，超时 30s，只用标准库 urllib.request。

回包认三种形状，都能收成同一条契约：
  1. 直接就是 {"result": "成"|"败"|"半", "conclusion": ..., "cause": ..., "loc": "file:line", ...}
  2. OpenAI 兼容 {"choices": [{"message": {"content": "<json 字符串>"}}]}
  3. {"content": "<json 字符串>"}

硬规矩：
  - result 只认 成 / 败 / 半；字段不合法、JSON 坏了、网络炸了，一律
    {"result": "败", "cause": "一句人话"}，**不抛异常**——败也是经验，主循环照样落盘。
  - 密钥不落盘、不进日志：要写进原因里的 URL / 密钥先过 _脱敏()。
"""
import contextlib
import json
import logging
import os
import re
import urllib.request

结果集 = ("成", "败", "半")
默认超时 = 30.0

ENV_MODE = "LAYERED_MEMORY_DELEGATE"
ENV_URL = "LAYERED_MEMORY_DELEGATE_URL"
ENV_KEY = "LAYERED_MEMORY_DELEGATE_KEY"
ENV_MODEL = "LAYERED_MEMORY_DELEGATE_MODEL"
ENV_TIMEOUT = "LAYERED_MEMORY_DELEGATE_TIMEOUT"

_HTTP模式 = ("http", "https", "url")
_可选字段 = ("conclusion", "path", "cause", "loc", "next_try")

# NullHandler：默认一个字节都不往 stderr 冒；测试里用 assertLogs 自己接。
_log = logging.getLogger(__name__)
_log.addHandler(logging.NullHandler())


# ---- 脱敏 ----------------------------------------------------------------

def _脱敏(text, *secrets):
    """把已知的 URL / 密钥，以及长得像 URL / Bearer 的东西，一并抹掉。"""
    out = str(text or "")
    for s in secrets:
        s = (s or "").strip()
        if s:
            out = out.replace(s, "<已脱敏>")
    out = re.sub(r"(?i)(bearer\s+)[A-Za-z0-9._\-+/=]+", r"\1<已脱敏>", out)
    # 保留主机名（排障要看是不是打错域名），路径和查询串一律抹掉
    out = re.sub(r"(?i)(https?)://([^/\s'\"<>]+)(?:/[^\s'\"<>]*)?", r"\1://\2/***", out)
    return out


# ---- 回包解析 ------------------------------------------------------------

def _一句(v):
    if isinstance(v, dict):
        v = v.get("message") or v.get("reason") or v.get("detail") or json.dumps(v, ensure_ascii=False)
    return " ".join(str(v if v is not None else "").split())[:200]


def _败(原因):
    return {"result": "败", "cause": 原因}


def _去围栏(text):
    """模型爱把 JSON 包在 ```json ... ``` 里，先剥掉再解析。"""
    t = (text or "").strip()
    if t.startswith("```"):
        t = t.split("\n", 1)[1] if "\n" in t else t[3:]
        if "```" in t:
            t = t.rsplit("```", 1)[0]
    return t.strip()


def _干净位置(v):
    """loc 只认 file:line（可以是逗号分隔的多个），别的一律丢掉。"""
    if not isinstance(v, str):
        return ""
    parts = [p.strip() for p in v.replace("，", ",").split(",") if p.strip()]
    for p in parts:
        head, _, line = p.rpartition(":")
        if not head or not line.isdigit():
            return ""
    return ", ".join(parts)


def _契约(obj):
    """字段级校验：result 只认三个字，其余字段必须是人话字符串。"""
    result = obj.get("result")
    if result not in 结果集:
        return _败("回包的 result 只能是 成 / 败 / 半，收到：%r" % (result,))
    out = {"result": result}
    for k in _可选字段:
        v = obj.get(k)
        if k == "loc":
            v = _干净位置(v)
        elif isinstance(v, str):
            v = " ".join(v.split()).strip()[:500]
        else:
            v = ""
        if v:
            out[k] = v
    # 经验.record 规定「败必须写死因」，这里补一句，别让主循环在落盘那步炸
    if result == "败" and not out.get("cause"):
        out["cause"] = "子代理回了「败」，但没写死因"
    return out


def 归一(obj, depth=0):
    """把任何形状的回包收成契约 dict；收不出来就给「败」。depth 防自引用。"""
    if depth > 3:
        return _败("回包嵌套太深，判不清哪一层是结论")

    if isinstance(obj, (bytes, bytearray)):
        obj = obj.decode("utf-8", "replace")
    if isinstance(obj, str):
        text = _去围栏(obj)
        try:
            obj = json.loads(text)
        except (ValueError, TypeError):
            return _败("回包的内容不是 JSON：%s" % (_一句(text) or "（空）"))
        return 归一(obj, depth + 1)
    if not isinstance(obj, dict):
        return _败("回包不是一个 JSON 对象")

    if "result" in obj:
        return _契约(obj)

    if "error" in obj:
        return _败("端点报错：%s" % (_一句(obj.get("error")) or "没说原因"))

    choices = obj.get("choices")
    if isinstance(choices, list) and choices:
        first = choices[0] if isinstance(choices[0], dict) else {}
        msg = first.get("message") if isinstance(first.get("message"), dict) else {}
        content = msg.get("content") if msg else first.get("text")
        if content is None:
            return _败("OpenAI 兼容回包里没有 message.content")
        return 归一(content, depth + 1)

    if "content" in obj:
        return 归一(obj["content"], depth + 1)

    return _败("回包里既没有 result，也没有 choices / content")


def _解析(raw):
    """bytes / str → 契约 dict。永远不抛。"""
    text = raw.decode("utf-8", "replace") if isinstance(raw, (bytes, bytearray)) else str(raw or "")
    try:
        return 归一(json.loads(text))
    except (ValueError, TypeError):
        return _败("回包不是合法 JSON：%s" % (_一句(text) or "（空）"))


# ---- 三种代理 ------------------------------------------------------------

def fake_delegate(**kw):
    """假的子代理：返回给定结论。默认路径，测例和 demo 都靠它，行为与 loop.py 原来一致。"""
    def _d(task):
        out = {"result": "成", "path": "fake-delegate（演示用）", "conclusion": "跑通了"}
        out.update({k: v for k, v in kw.items() if v})
        return out
    return _d


def http_delegate(url, key="", model="", timeout=默认超时):
    """真端点：POST JSON，把回包收成契约；任何异常都收成「败」。"""
    url = (url or "").strip()
    key = (key or "").strip()
    model = (model or "").strip()

    if not url:
        def _没配端点(task):
            return _败("LAYERED_MEMORY_DELEGATE=http，但没配 LAYERED_MEMORY_DELEGATE_URL")
        return _没配端点

    def _d(task):
        body = {"task": str(task)}
        if model:
            body["model"] = model
        req = urllib.request.Request(
            url,
            data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
            headers={"Content-Type": "application/json; charset=utf-8",
                     "Accept": "application/json"},
            method="POST")
        if key:
            req.add_header("Authorization", "Bearer " + key)

        try:
            with contextlib.closing(urllib.request.urlopen(req, timeout=timeout)) as resp:
                raw = resp.read()
        except Exception as exc:            # 网络、超时、4xx/5xx 全在这一层收住
            reason = "调端点失败：%s" % _脱敏(exc, url, key)
            _log.warning("子代理端点调用失败：%s", reason)
            return _败(reason)

        out = _解析(raw)
        if out.get("result") == "败":
            _log.warning("端点回了「败」：%s", _脱敏(out.get("cause", ""), url, key))
        return out

    return _d


def _超时(v):
    try:
        t = float(str(v).strip())
    except (TypeError, ValueError):
        return 默认超时
    return t if t > 0 else 默认超时


def 已配(env=None):
    """环境变量里有没有指定代理模式（loop.py 用它决定「这活儿派不派得出去」）。"""
    e = os.environ if env is None else env
    return bool((e.get(ENV_MODE) or "").strip())


def make_delegate(*, result=None, cause="", env=None):
    """读环境变量决定返回哪种代理。

    result 给了（--fake 成|败|半）就直接假代理，优先级最高，连环境变量都不看。
    """
    e = os.environ if env is None else env
    if result:
        return fake_delegate(result=result, cause=cause)
    mode = (e.get(ENV_MODE) or "").strip().lower()
    if mode in _HTTP模式:
        return http_delegate(e.get(ENV_URL, ""), e.get(ENV_KEY, ""),
                             e.get(ENV_MODEL, ""), _超时(e.get(ENV_TIMEOUT)))
    return fake_delegate()

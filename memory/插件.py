# -*- coding: utf-8 -*-
"""插件.py —— 可选插件的加载器。零依赖，只用标准库 json / os / io。

插件就是一个 JSON 文件，放在 `plugins/`（和 memory/、tools/ 并列）：
    plugins/同义扩展.json

约定字段（都宽容，缺了就当默认）：
    name     插件名（缺省 = 文件名去 .json）
    version  版本号，随便
    enabled  false = 不启用（缺省 true）
    weight   插件自己解释的权重，比如同义扩展的折减系数
    groups   插件自己的数据，比如同义组

规矩：
  - 文件不存在 / JSON 坏了 / 不是对象 → load() 返回 None，**不抛异常**，
    坏原因记在 WARNINGS 里（检索照跑，只是没这层扩展）。
  - 插件目录：环境变量 LAYERED_MEMORY_PLUGINS 指了谁就只认谁（测试/搬家好用）；
    没指 → <仓库根>/plugins。

用法：
    import 插件
    插件.load("同义扩展")     # → dict 或 None
    插件.list_all()           # → [dict, ...]
"""
import io
import json
import os

WARNINGS = []          # 最近一次读失败的原因，给体检/CLI 看，不进异常


def plugin_dirs():
    """插件目录列表；环境变量一设就独占。"""
    env = (os.environ.get("LAYERED_MEMORY_PLUGINS") or "").strip()
    if env:
        return [os.path.abspath(os.path.expanduser(env))]
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    return [os.path.join(root, "plugins")]


def path_of(name):
    """按名字找插件文件，先到先得；找不到返回 None。"""
    name = str(name or "").strip()
    if not name:
        return None
    for d in plugin_dirs():
        p = os.path.join(d, name + ".json")
        if os.path.exists(p):
            return p
    return None


def _read(p):
    """读一个 JSON；坏了 → None（记一条警告）。"""
    try:
        with io.open(p, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception as e:
        WARNINGS.append("%s: %s" % (p, e))
        return None
    if not isinstance(data, dict):
        WARNINGS.append("%s: 顶层不是对象" % p)
        return None
    return data


def load(name, *, require_enabled=True):
    """读一个插件：不存在 / 坏 JSON / enabled:false（且 require_enabled）→ None。"""
    p = path_of(name)
    if p is None:
        return None
    data = _read(p)
    if data is None:
        return None
    if require_enabled and not data.get("enabled", True):
        return None
    out = dict(data)
    out.setdefault("name", os.path.basename(p)[:-5])
    out["path"] = p
    return out


def list_all():
    """plugins/ 下所有能读的插件（关掉的也列出来，方便看有哪些）。"""
    out, seen = [], set()
    for d in plugin_dirs():
        if not os.path.isdir(d):
            continue
        for fn in sorted(os.listdir(d)):
            if not fn.endswith(".json"):
                continue
            p = os.path.join(d, fn)
            data = _read(p)
            if data is None:
                continue
            name = str(data.get("name") or fn[:-5])
            if name in seen:
                continue
            seen.add(name)
            item = dict(data)
            item["name"] = name
            item["path"] = p
            out.append(item)
    return out

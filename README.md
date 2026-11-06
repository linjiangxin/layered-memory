# Layered Memory

一个**纯文件的 AI 记忆库**。记忆就是 markdown 里的一行行字，没有数据库、没有向量、不联网。

检索靠「中文字面重合 × 遗忘曲线 × 来源重要度」打分；过期的事实不用删，在行尾挂一截注释标成作废；每条记忆在账本里留一枚指纹，被改过还是真丢了能分开报。

![架构](docs/architecture.svg)

## 它解决什么

| 麻烦 | 这里的做法 |
| --- | --- |
| 上下文装不下全部历史 | 记忆落在文件里，按需检索，只把命中的 3 条拼进 prompt |
| 事实变了，模型答反 | 旧条目行尾标 `deprecated`，检索默认跳过；不删原文，可回溯 |
| 重要的事被日常噪音淹 | 打分里带来源权重（关系约定 > 事实 > 主题 > 日记），不靠关键词表 |
| 不知道记忆有没有丢 | 账本记 sha256 指纹，`--verify` 分三类报：改写过 / 已废弃 / 整条消失 |

## 30 秒跑起来

只要 Python 3.10+ 和 Node 18+（Node 只有召回脚本用），**没有第三方依赖**。

```bash
python demo.py          # 一条命令跑完整流程：检索 → 作废 → 再检索 → 归档 → 对账
```

想手动来：

```bash
python memory/检索.py 咖啡
python memory/作废.py 咖啡 --reason "改喝焙茶了" --dry
node tools/记忆召回.mjs "偏好"
python tools/记忆账本.py --verify
python -m unittest discover -s tests -v
```

仓库里带了一套虚构的示例数据（用户叫「阿舟」），可以直接跑。

中文 Windows 控制台默认 GBK，先 `chcp 65001` 或设 `PYTHONIOENCODING=utf-8`，否则中文显示成乱码（文件本身没事）。

## 三个函数

命令行只是壳。想接进自己的 agent，直接 import：

```python
import sys
sys.path.insert(0, "memory")
import core
```

### `add(text, *, source="事实库", tags=None) -> dict`

往 `data/长期/<source>.md` 追加一条 `- ` 条目；正文已经在库里就不重复写。

```python
>>> core.add("阿舟换了台 32 寸的显示器。", tags=["设备"])
{'key': 'a1b2c3d4e5f60718', 'file': 'data/长期/事实库.md', 'line': 30}
```

`key` = `sha256(正文)[:16]`，和账本、使用记录用的是同一把尺。

### `search(query, *, limit=3, include_deprecated=False) -> list[dict]`

```python
>>> core.search("咖啡")
[{'text': '阿舟下午三点后不喝咖啡，会睡不着。',
  'src': 'data/长期/事实库.md:12', 'file': 'data/长期/事实库.md',
  'line': 12, 'score': 0.75, 'deprecated': False}, ...]
```

- 分数降序，`limit` 截前几条。
- 日记条目按 `## ` 小节切，`line` 记 0。
- 公式：`重合度 × (0.35 + 0.65 × 可提取度) × (0.5 + 重要度/10)`，
  可提取度默认走 `S = 重要度 × (1 + 0.6 × 被用次数)`，次数来自 `data/使用记录.jsonl` 里 `used: true` 的行。

### `deprecate(keyword, *, reason, dry=False) -> dict`

在条目行尾挂 `<!-- status: deprecated | since: | by: -->`，**正文一个字不动**、不删行；顺手把账本里这条的指纹搬家。

```python
>>> core.deprecate("咖啡", reason="改喝焙茶", dry=True)["todo"]
[{'file': 'data/长期/事实库.md', 'line': 12, 'text': '阿舟下午三点后不喝咖啡，会睡不着。'}]

>>> core.search("咖啡")
[]
>>> core.search("咖啡", include_deprecated=True)
[{'text': '阿舟下午三点后不喝咖啡，会睡不着。', ..., 'deprecated': True}]
```

`keyword` 可以是一个字符串（空格分隔 = 必须同时出现），也可以是词列表。`dry=True` 只算不写。

## 命令行怎么用

```bash
python memory/检索.py 咖啡
python memory/检索.py 咖啡 --include-deprecated   # 连作废的也显示，前面带 [废]
python memory/检索.py 咖啡 --legacy-weights       # 用旧权重口径，做对照
python memory/作废.py 咖啡 --reason "改喝焙茶了"
node tools/记忆召回.mjs "他刚说的这句话"           # 拼 prompt 用的那 3 条
python tools/记忆账本.py --verify
python tools/记忆体检.py --line
```

身体上只有两条要记：**账本是先 `--sync` 再 `--verify`**；**作废只作用于 `长期/`，日记不作废**。

## 数据长什么样

```
├─ data/
│  ├─ 长期/
│  │  ├─ 事实库.md          # 一条一行 "- …"
│  │  ├─ 索引.md            # 主题索引
│  │  └─ 主题/*.md          # 关系与约定.md / 零碎见闻.md …
│  ├─ 日记/YYYY-MM-DD.md     # 一天一个文件，一条一个 "## " 小节
│  ├─ 短期记忆.md            # 一份 ≤600 字的交接卡
│  ├─ 记忆权重.json          # {正文: {src, imp, S, last}}
│  ├─ 使用记录.jsonl         # 每次召回/命中一行，只追加
│  └─ 记忆账本.json          # {sha256: {len, src, text}}
├─ memory/                   # core.py + 五个命令行脚本
├─ tools/                    # 召回(mjs)、账本、体检
└─ tests/                    # 五条底线测试
```

一条长期记忆就是一行；作废后只是尾巴长一点：

```markdown
- 阿舟住在城东，通勤靠地铁。
- 阿舟住在城东，通勤靠地铁。 <!-- status: deprecated | since: 2026-03-02 | by: 搬到城西 -->
```

没有标记的一律当 active。

## 换目录也能跑

所有脚本的根目录优先读环境变量 `LAYERED_MEMORY_HOME`，没设就按**脚本自身位置**往上推。
所以拷到别处、放进网盘、挂到移动硬盘，都不用改代码。

```bash
set LAYERED_MEMORY_HOME=E:\somewhere\layered-memory
python E:\somewhere\layered-memory\memory\检索.py 咖啡
```

## 已知限制

1. **字面检索，不是语义检索**。中文按 2 字滑窗算重合，换个说法就空手，这是设计取舍不是 bug。真需要语义就接一层 embedding，但那就不是「纯文件」了。
2. **没有并发锁**。两个进程同时写同一个 md 会互相覆盖。现在是「读全文 → 追加 → 整篇写回」，小文件风险低，但不是零。
3. **归档会删原行**，之后 `--verify` 会把它们报成「整条消失」。确认该删就用 `--adopt` 写一条处置记录。
4. **权重不会自己涨**。`used: true` 要显式标（`python memory/记忆曲线.py --mark-used 关键词`），否则所有条目停在初始重要度，只剩时间衰减。
5. **`使用记录.jsonl` 只增不减**，没有轮转，长期跑要自己按天切。
6. 中文 Windows 控制台要先 `chcp 65001`。

## License

MIT，见 [LICENSE](LICENSE)。

# 契约（contracts）

> 状态：**v0.1-draft** · 最后更新 2026-11-05

层与层之间传什么，这里说了算。表在 [docs/interfaces.md](../docs/interfaces.md) 里，**这里是机器可校验的定义**。

## 为什么不只用表格

表格是给人读的，schema 是给实现和 CI 读的。有了 schema：

- 谁实现 L3，输出对不对一跑就知道，不用来回解释；
- 契约改动会变成 diff，而不是"我记得好像说过"；
- 未来可以接 CI 校验样例，把"我们有契约"变成可执行的事实。

## 文件

| 文件 | 是什么 | 状态 |
|---|---|---|
| [`signals.schema.json`](signals.schema.json) | 通用信号信封 + `goal_candidate` / `semantic_plan_*` / `reflex_override` / `outcome_event` | 草案 |
| [`affect_vector.schema.json`](affect_vector.schema.json) | L3 → L2/L4 的情绪向量 | 草案 |
| [`homeostatic.schema.json`](homeostatic.schema.json) | L1 全局广播 | 草案 |
| [`examples/`](examples/) | 可直接用于校验的样例 | 草案 |

## 校验方式

```bash
# 用任一支持 JSON Schema 2020-12 的实现，例如 node + ajv：
node -e "
const Ajv = require('ajv/dist/2020');
const ajv = new Ajv({ allErrors: true, strict: false });
const schema = require('./contracts/signals.schema.json');
const validate = ajv.compile(schema);
const ok = validate(require('./contracts/examples/outcome_event.example.json'));
console.log(ok ? 'PASS' : validate.errors);
"
```

> 当前仓库**没有**接 CI 自动校验（P1，见 [roadmap](../docs/roadmap.md)）。
> 在接之前，改动 schema 请手动跑一次上面的命令并把结果贴进 PR。

## 版本策略

| 改动性质 | 版本位 | 需要什么 |
|---|---|---|
| 加可选字段、加枚举值（不改语义） | minor（v0.1 → v0.2） | PR + 更新 examples |
| 改字段含义、改取值范围、删字段、改必填性 | major（v0.x → v1.0） | **先 ADR**，再 PR |
| 纯文字澄清 | patch | PR |

**加字段不算破坏性改动，改语义算。** 一个字段以前是"建议"，现在变成"必须遵守"——这是 major。

## 设计约定

1. **字段名用 `snake_case`。** 时间戳统一 `*_ms`，单位毫秒，UTC。
2. **单位写进名字。** `timeout_ms` 而不是 `timeout`；不写单位的数值早晚会出错。
3. **枚举值用 `snake_case` 字符串，不用数字。** 数字枚举在日志里不可读。
4. **必填字段只放"没有它就没法处理"的。** 其余可选，避免把实现绑死。
5. **数值范围必须写 `minimum` / `maximum`。** 没范围的数值等于没契约。

## 已知的紧张点

- `affect_vector` 的取值范围（−1..1 / 0..1）目前是**提案**，没有生理学或工程标定依据。
  如果有人能给出更好的归一化方案，欢迎提 RFC——这正是 [ADR 0001](../docs/roadmap.md) 该记录的事。
- 序列化只定义了 JSON。DDS / 共享内存的二进制布局**尚未定义**，实时层大概率需要另做一套。

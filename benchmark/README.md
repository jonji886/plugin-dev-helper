# MCP 效果评测（Benchmark）

评估「Plugin Developer MCP Server」对本地 Coding Agent 的增益：Agent 能否借助 MCP
正确查找 SDK/API/类型、修改插件工程并通过 Build/Test。

## 目录结构

```
benchmark/
├── README.md            # 本文件：评测协议
├── tasks.json           # 任务集（10 个任务，含验收标准）
├── fixtures/            # 每个任务的最小插件工程（初始态，可 tsc 编译）
│   ├── tsconfig.base.json
│   └── T01-*/src/vm.ts
├── results/
│   └── comparison.md    # 无 MCP vs 有 MCP 对比结果
└── badcases/
    └── README.md        # Bad Case 收集规范
```

## 评测维度

1. **API 查找准确性**：能否找到正确的 API，不张冠李戴。
2. **参数/类型构造正确性**：能否按 `MiniappUploadDataOption` 等类型构造合法参数。
3. **UI/VM 通信正确性**：`postMessage` / `onMessageReceive` / `setContainerOptions` 用法。
4. **错误修复能力**：修复不存在的 API 名、错误的参数名。
5. **拒绝编造**：任务要求调用不存在的 API 时，明确说明而非编造。
6. **信息不足先查询**：需求模糊时，先查类型定义再写代码。

## 评测协议

对 `tasks.json` 中每个任务：

1. 把 `fixtures/<task>/` 作为 Coding Agent 的工作目录。
2. 使用同一份 `task_prompt` 驱动 Agent。
3. **基线（无 MCP）**：Agent 只能依赖自身知识，不能调用 MCP。
4. **实验（有 MCP）**：Agent 接入 MCP（`get_api` / `get_type` / `search_docs` / `validate_api_usage` 等）。
5. 采集指标：
   - `acceptance` 是否通过（typecheck / build / 字符串断言）；
   - 调用 MCP 工具的类型与次数；
   - 最终代码是否与 `expected_behavior` 一致；
   - token / 耗时（可选）。

结果汇总写入 `results/comparison.md`。

## 自动验收

```bash
# 验收单个任务
python scripts/check_benchmark_task.py T01

# 验收全部任务
python scripts/check_benchmark_task.py --all

# 清理 dist 产物
python scripts/check_benchmark_task.py --clean
```

验收 = `tsc --noEmit`（typecheck）+ `tsc --outDir dist`（build）+ 字符串断言
（`contains:` / `not_contains:`）。

> 说明：fixture 是最小工程，仅用于类型层面验收。真实插件运行还需要
> `manifest.json` + `page.html`，不在本评测范围内。

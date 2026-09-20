# ADR-001：项目级确定性 Validator 作为交付判定唯一事实来源

- 状态：已采纳
- 日期：2026-09
- 相关代码：`mcp_server/services/project_validator.py`、`mcp_server/tools/validate_plugin_project.py`

## 背景

Agent 产出的插件工程是否可交付，此前只能靠人工 review 或 LLM 判断，结果不可复现、
不可用于回归门禁，也无法支撑自动 Repair Loop 的收敛判定。

## 决策

1. 引入 `ProjectValidator`，把**可确定性检查**的判定全部收敛到代码：
   - STRUCTURE：目录与入口文件结构；
   - MANIFEST：manifest.json 字段与引用文件真实存在；
   - RULE：复用 `KujialeRuleEngine`（rules/kujiale/*.yaml，平台硬约束的事实来源）；
   - API：复用 `UsageValidator` + 知识索引（`data/knowledge`）做符号级幻觉检测；
   - BUILD / TYPE：可选接入 `tsc --noEmit`（无 tsconfig 或无 tsc 时如实跳过）。
2. 宿主运行才能验证的规则（CORS / OPTIONS 预检 / HTTP 探活）**不伪造结论**，
   统一输出 `runtime_required=True` 的 WARNING Issue，标明需要宿主环境验证。
3. 以 MCP 工具 `validate_plugin_project(project_dir)` 对外暴露，Issue 统一结构：
   `issue_id / severity / category / code / message / file / line / evidence / suggested_fix / repairable`。
4. 校验只看源文件：`dist/`、`build/`、`node_modules/` 不参与规则与 API 检测
   （fixture 中常残留过期的错误编译产物，扫到会产生无法修复的假问题）。

## 后果

- 正面：评分与门禁可复现；Repair Loop 有了明确的收敛信号；Benchmark 可以直接复用同一 Validator。
- 代价与约束：
  - 规则层与知识索引必须与平台演进同步（规则漏更 = 漏报；知识索引缺符号 = 误报，见 ADR-004 的教训记录）；
  - 「必须使用某模式」类规则（KJL-COMM-001/002）需要 require 语义 + trigger 条件，
    否则会把正确使用合法 API 的工程判为违规（P0 自检中发现并修复）。

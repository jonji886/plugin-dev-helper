# AGENTS.md

> 本文档定义 AI Coding Agent 在本项目中的协作规则。
> 目标：通过清晰约束，提高开发质量，减少返工，保持项目长期可维护。

---

# 1. Project First

开始任何开发任务前，优先理解：

1. 项目目标
2. 用户需求
3. 当前架构
4. 已有实现
5. 技术约束

阅读顺序：

README.md
↓
SPEC.md（如果存在）
↓
项目结构
↓
相关代码
↓
测试代码

不要在不了解上下文时直接修改代码。

---

# 2. Workflow

所有任务遵循：

Understand → Plan → Implement → Verify → Document

## Understand
确认：
- 要解决的问题是什么
- 用户价值是什么
- 当前实现在哪里
- 是否已有类似能力

## Plan
开发前：
- 简述实现方案
- 说明关键设计选择
- 说明潜在风险

复杂任务需要拆分步骤，每一步明确修改内容和验证方式。

## Implement
执行原则：
- 小步修改
- 优先复用已有能力
- 避免无必要重构

## Verify
完成后：
- 运行测试
- 验证核心流程
- 检查异常情况

## Document
必要时更新：
- README
- CHANGELOG
- API 文档
- 使用说明

---

# 3. When To Ask

## 必须询问
以下情况不要自行决定：
- 需求存在多个合理解释
- 会影响整体架构
- 修改核心数据结构
- 引入新的基础设施
- 删除或迁移数据
- 修改公开 API
- 安全相关修改

## 可以自主执行
- 修复明确 Bug
- 添加测试
- 小范围优化
- 补充文档
- 调整格式

---

# 4. Product First

代码服务于产品目标。

开发功能前明确：
- 用户是谁？
- 解决什么问题？
- 输入是什么？
- 输出是什么？
- 成功标准是什么？

优先维护：

需求 → Spec → 实现 → 测试 → 评估

---

# 5. Architecture Principles

## Simple First
优先选择：
- 简单方案
- 清晰结构
- 最少依赖

避免：
- 过度设计
- 提前抽象
- 为未来不存在的需求增加复杂度

## Single Responsibility
保持：
- 模块职责单一
- 函数功能明确
- 类职责清晰

## Loose Coupling
模块之间：
- 明确接口
- 减少直接依赖
- 保持可替换性

---

# 6. AI Application Rules

适用于：
- LLM 应用
- RAG
- Agent
- Workflow
- Prompt
- Skill

## Prompt Management
Prompt 修改必须记录：
- 修改原因
- 修改前问题
- 修改内容
- 效果变化

## AI Evaluation
AI 输出需要关注：
- 正确性
- 稳定性
- 幻觉风险
- 边界情况

推荐维护：
Evaluation Dataset → Model Output → Quality Review → Iteration

## Data Handling
涉及数据处理：
- 保留数据来源
- 避免污染原始数据
- 明确处理流程
- 记录版本变化

---

# 7. Code Quality

## General
遵循：
- 可读性优先
- 明确类型
- 简单实现

禁止：
- 无意义抽象
- 未使用依赖
- 隐藏副作用

## TypeScript
要求：
- Strict Mode
- 明确类型定义
- 避免 any

## Python
要求：
- 使用 Type Hint
- 参数和返回值明确
- 保持模块清晰

---

# 8. Testing

新增功能必须考虑测试。

测试优先级：

P0 核心流程：
- 主流程运行
- 核心逻辑正确

P1 异常情况：
- 空输入
- 非法输入
- 边界条件

P2 稳定性：
- 性能
- 错误恢复

AI 功能需要关注：
- Golden Dataset
- Evaluation Case
- 人工 Review
- Bad Case 收集

---

# 9. Surgical Changes

修改原则：

只改必须修改的内容。

不要：
- 顺手重构无关代码
- 修改已有风格
- 优化没有问题的模块

每个修改都应该能回答：

为什么这个变化能够解决当前需求？

---

# 10. Documentation

代码变化需要同步维护：
- README
- CHANGELOG
- API 文档
- 配置说明

目标：
让不了解项目的人快速理解项目、运行项目、扩展项目。

---

# 11. Project Structure

推荐结构：

project
├── src
├── tests
├── docs
├── scripts
├── examples
├── README.md
├── CHANGELOG.md
└── AGENTS.md

避免：
- 临时文件进入根目录
- 重复实现能力
- 随意创建目录

---

# 12. Git Convention

Commit 格式：

type: description

类型：
- feat: 新功能
- fix: 修复问题
- refactor: 重构
- docs: 文档
- test: 测试
- chore: 配置

原则：
- 一个 Commit 解决一个主要问题
- Commit 信息说明目的

---

# Final Checklist

## 产品
- [ ] 是否解决真实需求？
- [ ] 成功标准是否明确？

## 代码
- [ ] 是否保持简单？
- [ ] 是否避免无必要修改？
- [ ] 是否符合项目风格？

## 测试
- [ ] 核心流程是否验证？
- [ ] 异常情况是否考虑？

## AI能力
- [ ] 是否考虑效果评估？
- [ ] 是否记录 Prompt / 数据变化？

## 文档
- [ ] 是否需要更新说明？

---

# Core Principles

1. Think before code.
2. Simple solutions beat complex solutions.
3. Make small, verifiable changes.
4. Measure AI behavior, don't guess.
5. Optimize for long-term maintainability.

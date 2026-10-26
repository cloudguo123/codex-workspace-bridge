# Codex Workspace Bridge

**把项目、Git、聊天和 Goal 的迁移变成可检查的流程。**

A local-first migration toolkit for developer workspaces and Codex continuity. Python 3.11+, standard library only, MIT. No API key, telemetry, cloud upload or automatic Goal execution.

- 只读盘点项目体积、未提交/未跟踪文件、未首次提交仓库、分支与工作树。
- 本地 HTML 搜索、点选 SSD / 外置盘 / 暂缓，并导出计划。
- 内容寻址迁移包、SHA256、冲突预览、仅新增文件恢复与工作树绝对链接修复。
- **实验性** Codex 三库冷快照、只追加准备与提交、写前日志/回退；导入 Goal 保持暂停，目标旧工作保留。
- 随附 Codex Skill、完整 SPEC、匿名复盘和运行/Docker/扩展迁移手册。

这不是 OpenAI 官方迁移工具。App 的本地存储是版本相关的，schema 不匹配或目标同 ID 有新进展会拒绝。不是通用双向同步器，也不保证所有附件和私有 App 布局自动恢复。请先在隔离副本演练。数据库提交与界面验收分开记录。

## 开始

```sh
python3 -m workbridge --help
python3 -m workbridge scan --root "$HOME/Projects" --out /private/tmp/inventory.json
python3 -m workbridge dashboard /private/tmp/inventory.json --out /private/tmp/migration.html
open /private/tmp/migration.html
```

在源码根目录运行，无需安装依赖。可选：`python3 -m pip install .` 安装 `workbridge` 命令。支持 macOS，其他系统兼容性须单独验收。

**迁移包、清单、数据库、聊天和项目永远是私人数据，不要上传此仓库。** 默认排除少数常见凭据名，不能代替秘密审查；Git 历史也可能含秘密。

## 文档

- [SPEC：完整流程、实现边界、数据格式与验收](docs/SPEC.md)
- [RUNBOOK：逐步操作与故障恢复](docs/RUNBOOK.md)
- [案例复盘：成功、遗漏与未完成项](docs/RETROSPECTIVE.md)
- [Skill](skills/workspace-migration/SKILL.md) — 引导 Codex 使用已安装的 CLI
- [安全报告方式](SECURITY.md) / [贡献指南](CONTRIBUTING.md)

## 开发与验证

```sh
python3 -B -m unittest discover -s tests -v
```

测试使用临时目录、合成 profile 和测试 Git 仓库，不触碰真实 App 数据。本版本没有对真实生产 profile 执行新程序验收；先前迁移案例使用的专用脚本不等于本程序已获生产验证。

官方 [App Server 文档](https://learn.chatgpt.com/docs/app-server#api-overview)提供聊天和 Goal 读取接口；本程序的 SQLite 冷恢复适配器为社区实现，不应当作官方稳定 API。

# 操作手册

Python 3.11+，在仓库根运行 `python3 -m workbridge --help`，无需安装依赖。可用 `python3 -m pip install .` 安装命令 `workbridge`。先检查当前 Python 和执行主机，确认操作的是源机器还是目标机器。

## 1. 只读盘点与点选

```sh
python3 -m workbridge doctor
python3 -m workbridge scan --root "$HOME/Projects" --depth 1 --out /private/tmp/inventory.json
python3 -m workbridge dashboard /private/tmp/inventory.json --out /private/tmp/migration.html
open /private/tmp/migration.html
```

在页面选择 SSD、外置盘或暂不迁移，导出 `workbridge-plan.json`。扫描深度按目录结构选择；一个大型父项目会包含其内部工程。多个根可重复传 `--root`。无权限目录显示错误；不修改系统隐私权限。元数据体积盘点可能耗时，等待期间源目录仍只读。

普通资料目录无 Git 也能盘点。没有 Git 是缺少版本历史；已配置 remote 并不表示内容已推送。决定哪些项目应加入私有 GitHub 时，应先审查秘密、大文件、许可证和隐私；本程序不会替你创建提交或上传业务项目。

## 2. 打包、传输与恢复

```sh
python3 -m workbridge bundle /path/to/workbridge-plan.json --out /path/to/private-bundle
```

记录返回的 `manifest_sha256`。迁移包含 `manifest.json` 和 `blobs/`，必须整个传输。可以通过已验证 SSH、AirDrop、外置盘或可信远程工具的文件传输发送。哈希通过另一个可信通道核对；不要公开包内容。

目标机器上先预览：

```sh
python3 -m workbridge diff /path/to/private-bundle --ssd "$HOME/Projects" --hdd /Volumes/Data/Projects --out /path/to/diff.json
python3 -m workbridge restore /path/to/private-bundle --ssd "$HOME/Projects" --hdd /Volumes/Data/Projects --expect MANIFEST_SHA256 --apply --out /path/to/receipt.json
```

用真实返回的 SHA256 替换大写占位符。任何不同内容都报冲突，不自动覆盖。恢复可中断后重跑；已相同文件跳过，目标多余文件保留。写入期间应暂停目标目录编辑；检测无法防御恶意并发目录替换。

目标目录附加稳定 ID。返回的 `git_link_map` 可保存为 `mapping.json`（只保留该对象），先预览再修复复制后的 `.git` 绝对指针：

```sh
python3 -m workbridge git-links /path/to/mapping.json
python3 -m workbridge git-links /path/to/mapping.json --apply
```

对目标项目运行 `git status --porcelain`、`git for-each-ref`、`git worktree list --porcelain`，与源记录比较。链接修复会在目标保留带随机后缀的前像；不会运行 checkout/reset。submodule、alternates、相对控制链接、Git LFS 对象的额外存储均需人工核验。

默认不复制 `.env*`、常见私钥文件名和可重建缓存。`include_secrets` 不能绕过计划里显式 `exclude`；若确实需要迁移私人运行配置，单独使用加密/受信任渠道，不要关闭所有排除。Git 历史本身可能含秘密。

## 3. 聊天和 Goal：实验性冷恢复

先阅读 SPEC 第 6 节。优先使用当前 App 可用的官方交接；“打开一个同名目录”不会自动关联旧聊天，MacBook 的文件选择器也不能打开 Studio 的本地路径。草稿保护触发时保留草稿。

停止 **源 profile** 的全部 App/CLI/远程运行器，再在源机器执行：

```sh
python3 -m workbridge profile-snapshot --codex-home "$HOME/.codex" --app-stopped --out /path/to/private-profile
```

此步骤会拒绝仍在运行的默认本机 App；它不替你退出 App。没有某一个支持的数据库时会拒绝，不能将缺少 Goal 的包当完整恢复。快照不是密钥备份，外部历史图片也不会自动全盘搜集。

把整个 profile 包和返回摘要安全传到目标。在目标机器停止 App 和运行器后生成准备区：

```sh
python3 -m workbridge profile-prepare /path/to/private-profile --target "$HOME/.codex" --mapping /path/to/project-map.json --expect PROFILE_MANIFEST_SHA256 --app-stopped --out /path/to/stage
```

`project-map.json` 是源项目根→已验收目标项目根的 JSON 对象。源 Codex home 会自动映射到目标 home。准备区会包含私有目标前像，不可公开或作为新 profile 启动。App schema、已有行、UI 项目键、rollout 缺失发生冲突时停止，不覆盖；不要删除冲突行来让命令通过。

在新启动、无历史记录的目标 App 完成初始化后再退出，是较容易复核的首次导入方式。已有目标聊天会保留；若同一原 ID 已在目标继续开发，v0.1 报冲突，需专用三方合并，不能强制覆盖。

审核 prepare 返回的表级计数和阶段计划，确认冷窗口与第二备份位置：

```sh
python3 -m workbridge profile-commit /path/to/stage --expect PREPARE_PLAN_SHA256 --backup-root /Volumes/Backup/PrivateAppBackups --app-stopped
```

程序不会重启 App，也不会点击继续。提交后人工打开 App，检查项目下旧聊天、归档记录、图片/附件、至少几个原 Goal 卡片与累计量，以及权限重新审批。只有数据库计数通过，不算“无缝衔接已验收”。

发生提交中断，保持 App 停止，再使用同一准备区回退：

```sh
python3 -m workbridge profile-rollback /path/to/stage --app-stopped
```

回退保留后像。若有人已编辑目标，程序拒绝盲目回退。不得手工清除 journal 后重试 commit。副本请另存至另一物理盘/异地；`different_filesystems=true` 仅证明不同文件系统。

## 4. 运行环境、Docker、扩展与最终切换

逐项记录：源版本、目标版本、锁文件、数据位置、启动方式、测试命令与结果。依赖重建在独立目录，保留旧环境。Node/Python/Rust/TeX 等默认入口与项目专用入口分开核验。

Docker 至少包括 container/image、named volumes、bind mounts、Compose/env、数据库逻辑导出与磁盘容量。数据保全和恢复运行是两个批准范围。迁移卷不能自动启动旧容器；不要在本机和目标同时启动采集、定时任务或写服务。

Skills/模板可作为普通选定资料单元保全；插件从官方来源重新安装，MCP 凭据在目标重新登录。`doctor` 只列名字，不代表已经可调用。用只读、无费用或可控的工具调用验证，再查 App 界面。不要搬 auth/enrollment 来冒充另一主机身份。

最终业务库切换：获准中断窗口→停写→一致性快照/逻辑导出→两位置 SHA256 回读→隔离恢复→业务读写检查→保留目标旧库→切换→确认源不再写→观察。大型正在写入 SQLite 不可仅用普通文件复制。跨网络不可达时保留候选，不把传输进度当验收。

## 5. 暂停交接

记录每个域的来源、目标、最后一致性时点、哈希、原件是否仍在、服务实际运行位置、待处理冲突和下次增量范围。原机器在完整验收前保持原件。既有 Goal/调度恢复为暂停，由用户逐条决定何时继续。

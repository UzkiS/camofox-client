# Camofox Client

**让 agent 在同一个工作流中显式切换 `userId`，交替使用不同的浏览器用户上下文。**

一些 agent 接入的 Camofox 工具把用户身份固定在工具配置里，单次调用无法灵活更换 `userId`。这会让“用账号 A 查看，再用账号 B 操作，最后回到 A 确认”变得困难。本 skill 通过 Python 直接调用 Camofox REST 服务，每次 CLI 调用显式指定身份，或在同一 Python 进程中使用多个独立的 `Client`。

没有隐藏的“当前 profile”，不必修改宿主工具配置，也不依赖某一种 agent。页面读取、点击后观察、截图和自定义 REST 请求是辅助能力。

> 本客户端负责传递身份，实际 profile、cookie、登录态隔离与权限校验由 Camofox 服务端提供。`sessionKey` 只是同一用户内的标签分组，不是另一个 profile。不要把 `userId` 当作访问控制凭据，也不要混用不同身份的 `tabId`。

## 环境要求

- Python **3.10+**，客户端运行时**零第三方依赖**；也可通过 [uv](https://docs.astral.sh/uv/) 运行。
- 已运行且可访问的 [camofox-browser](https://github.com/jo-inc/camofox-browser) REST 服务，以及使用该服务所需的授权。
- 可选：Node.js/npm，用于 `npx skills` 安装；手动安装和运行客户端不需要 Node.js。

本仓库不安装或启动浏览器服务，也不是 MCP 服务或反验证工具。服务契约以你部署的 `/openapi.json` 为准。

## 安装

### 使用 skills CLI

从 GitHub 安装：

```sh
npx skills add UzkiS/camofox-client --skill camofox-client
```

选择目标 agent 和安装范围。也可明确指定（以 Claude Code 为例）：

```sh
npx skills add UzkiS/camofox-client --skill camofox-client --agent claude-code --copy
# 安装到用户范围，而非当前项目
npx skills add UzkiS/camofox-client --skill camofox-client --agent claude-code --global --copy
```

本地源码同样可以安装：在**目标项目**中运行，把路径替换为此仓库的绝对路径，不要在源码目录里维护另一份安装副本。

```sh
npx skills add /absolute/path/to/camofox-client --list
npx skills add /absolute/path/to/camofox-client --skill camofox-client --agent codex --copy
```

Windows 路径示例：`"C:/path/to/camofox-client"`。`--copy` 不要求符号链接权限。安装前检查目标位置已有 skill，不要覆盖本地修改。

已安装内容可用 `npx skills list` 查看，`npx skills remove camofox-client` 交互卸载；全局安装使用相应的 `--global`。`npx skills update` 检查/更新其管理的 skills（不只本 skill），执行前保留本地修改。手动复制的安装需手动更新；本地路径安装可从更新后的源码重新安装。

### 手动安装（无需 Node.js）

将本仓库的整个 `skills/camofox-client/` 目录复制到目标位置，必须同时保留 `SKILL.md`、`scripts/`、`references/` 和 `LICENSE`：

| 宿主        | 项目级目录                       | 用户级目录                         |
| ----------- | -------------------------------- | ---------------------------------- |
| Claude Code | `.claude/skills/camofox-client/` | `~/.claude/skills/camofox-client/` |
| Codex       | `.agents/skills/camofox-client/` | `~/.codex/skills/camofox-client/`  |

其他支持 Agent Skills 的宿主按其文档选择目录。不要只复制 `SKILL.md`。目标存在时先比较、备份，再明确替换；更新时移除旧安装中不再需要的文件。安装后按宿主要求重新加载技能，可以请求：“使用 camofox-client，分别以 userId A 和 B 查看各自的标签页”。

## 快速开始：同流程切换身份

以下命令在**本仓库根目录**执行。安装后的用户应将脚本路径换为**实际安装目录的绝对路径**，不需要切换工作目录。

先确认服务可连接：

```sh
python skills/camofox-client/scripts/main.py --base-url http://localhost:9377 --user-id profile-a doctor
```

`doctor` 只访问健康与接口描述端点，不打开标签页。它可能在 `healthy: false` 时仍正常返回退出码 0；应检查 `healthy`、`issues`、`connectError`，不能只看退出码。

然后在同一流程交替调用：

```sh
python skills/camofox-client/scripts/main.py --user-id profile-a tabs
python skills/camofox-client/scripts/main.py --user-id profile-b tabs
python skills/camofox-client/scripts/main.py --user-id profile-a tabs
```

要创建页面时，分别记录各次响应的 `tabId`。下面的 `TAB_A`、`TAB_B` 需替换为真实返回值，不是固定标签名：

```sh
python skills/camofox-client/scripts/main.py --user-id profile-a create --url https://example.com
python skills/camofox-client/scripts/main.py --user-id profile-b create --url https://example.com
python skills/camofox-client/scripts/main.py --user-id profile-a snapshot --tab-id TAB_A
python skills/camofox-client/scripts/main.py --user-id profile-b snapshot --tab-id TAB_B
```

显式 `--user-id` 优先于环境中的默认身份。切换身份时不要复用上一个身份的标签 ID。任务结束只关闭自己创建且不再需要的标签页。

只有 uv 时，将命令中的 `python` 换成 `uv run --no-project --python 3.14 python`，其余参数不变。

Python 多 Client 编排、点击后观察和自定义请求见 [接口与配置](skills/camofox-client/references/接口与配置.md)。完整动作参数以 CLI `--help` / `<动作> --help` 为准，不在此维护另一份参数清单。

## 配置

可以直接设置规范的 `CAMOFOX_*` 环境变量，或将 [.env.example](.env.example) 复制为本地 `.env`，再**显式**指定：

```sh
python skills/camofox-client/scripts/main.py --env-file .env --user-id profile-b tabs
```

- 优先级：显式 CLI/Python 配置 > 环境变量 > 指定的 `.env` > `Config` 默认值。
- 不搜索当前目录的 `.env`，不读取宿主配置，只接受规范的 `CAMOFOX_*` 配置项。
- `CAMOFOX_USER_ID` 默认留空：不设默认身份，访问用户资源时必须显式传 `--user-id`。多个 profile 平等使用时建议保持为空；单一常用身份可在本地配置默认值。
- `Config(...)` 本身不读环境；Python 如需加载环境或文件，应显式使用 `load_config`。
- 密钥通过环境、显式配置文件或 Python Config 传入，不放进命令行参数；不要提交真实 `.env`。
- 全局选项在动作之前；动作参数在动作之后。

## 失败处理

客户端不自动重试浏览器操作。带 `--output` 时会先检查输出位置，并通过临时文件替换目标；如果操作返回后才保存失败，退出码为 1，stdout 会包含 `phase: "output"` 和原始 JSON `result`。请保留其中的 `tabId` 或点击记录，**不要因为文件保存失败就重新创建或点击**。二进制保存失败只返回元数据，不包含文件内容。

请求超时按网络预算计时；系统 DNS 解析可能超出该预算。组合动作的失败、配置语法、trace 认证与下载细节见 [接口与配置](skills/camofox-client/references/接口与配置.md)。

## 开发与维护

```sh
python -m venv .venv
# 激活虚拟环境后
python -m pip install -r requirements-dev.txt
python -m ruff check .
python -m unittest discover -s tests -v
```

没有系统 Python 时的 uv 流程及发布检查见 [CONTRIBUTING.md](CONTRIBUTING.md)。测试使用模拟传输或本地 HTTP 服务，不连接真实 Camofox，不读取私人配置。

- [设计说明](docs/design.md)：多身份工作流、模块职责与事实源。
- [AGENTS.md](AGENTS.md)：维护本仓库的 agent 约束；不是安装给最终用户的技能说明。
- [SKILL.md](skills/camofox-client/SKILL.md)：安装后按需加载的使用指令。
- [CHANGELOG.md](CHANGELOG.md)：版本变更记录。

## 版本与下载

版本由 release-please 根据 Conventional Commits 管理。发布包与校验文件见 [GitHub Releases](https://github.com/UzkiS/camofox-client/releases)。下载 `camofox-client-vX.Y.Z.zip` 后解压，将其中的 `camofox-client/` 按上方手动安装步骤放到宿主技能目录。

`npx skills add UzkiS/camofox-client` 安装仓库默认分支中的内容；需要固定发布版本时，使用对应 Release 的压缩包。

## 规范与参考

本仓库采用 [Agent Skills 格式](https://agentskills.io/specification)，布局参考 [anthropics/skills](https://github.com/anthropics/skills)，安装使用 [vercel-labs/skills](https://github.com/vercel-labs/skills)。维护约定参考 [AGENTS.md](https://agents.md/)。

## 许可

本项目采用 [MIT License](LICENSE)。

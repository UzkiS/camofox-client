# 贡献指南

## 开发环境

需要 Python 3.10+。客户端仅使用标准库，开发工具单独安装：

```sh
python -m venv .venv
```

激活环境：

| 终端 | 命令 |
|---|---|
| Bash（Linux/macOS） | `source .venv/bin/activate` |
| Windows PowerShell | `.venv\Scripts\Activate.ps1` |
| Windows Git Bash | `source .venv/Scripts/activate` |

```sh
python -m pip install -r requirements-dev.txt
python -m ruff check .
python -m unittest discover -s tests -v
```

也可以使用 uv：

```sh
uv venv --python 3.14 .venv
uv pip install --python .venv -r requirements-dev.txt
uv run --no-project --python .venv python -m ruff check .
uv run --no-project --python .venv python -m unittest discover -s tests -v
```

开发依赖固定在 requirements-dev.txt，工具配置在 pyproject.toml。升级依赖需通过 CI 的 Linux/Windows、Python 3.10/3.14 矩阵。

## 修改原则

先说明用户流程与输入输出，再定义业务契约和职责，最后修改实现。阅读 [设计说明](docs/design.md) 和 [AGENTS.md](AGENTS.md)。

- 原子动作在 actions.py 声明规格与路由，公共身份规则由 Client 处理。
- 组合动作使用 Client 编排，在函数入口校验 COMPOSITE 规格，并在 main.COMPOSITE_HANDLERS 绑定实现。
- 新配置在 Config 声明；敏感字段设置 secret 元数据和 repr=False。环境名、CLI 与诊断从声明派生。
- 测试保护目标行为，不机械复制实现；重点覆盖 A→B→A 身份/分组/认证、参数错误、单次点击和失败路径。
- README 面向安装使用，SKILL.md 面向 agent 使用，docs 解释设计。动作参数以 CLI help 为准。

测试使用 fake transport、fake clock 或回环 HTTP 服务，不读取真实 `.env`、浏览器凭据或外部服务。分发测试会复制/解压 skill 到包含中文和空格的路径，从不同工作目录执行。

结构测试覆盖本仓库约束，不等同于完整的官方规范认证；可额外使用 [skills-ref](https://github.com/agentskills/agentskills/tree/main/skills-ref) 验证 Agent Skills 格式。

## 提交与 PR

使用 [Conventional Commits](https://www.conventionalcommits.org/)；PR squash 合并时，最终提交标题和正文必须保留约定：

- `feat: ...`：新增能力。
- `fix: ...`：修复问题。
- `docs: ...`、`test: ...`、`chore: ...`：文档、测试和维护。
- 不兼容变化使用 `!` 或正文中的 `BREAKING CHANGE:`，并说明调用方如何调整。

提交前运行 lint/tests，检查 `git diff --check` 与待提交文件，确保不包含凭据、缓存、重复源码或无关改动。涉及运行契约的变更要更新对应使用文档。

## 版本与发布

使用 [release-please](https://github.com/googleapis/release-please-action) 的 simple 策略：

1. Conventional Commits 合入 `main`，Release 工作流先执行 CI 矩阵。
2. release-please 创建或更新 Release PR，维护 `version.txt`、`CHANGELOG.md` 和发布 manifest。
3. 维护者检查版本号、变更记录与 CI 后合并 Release PR。
4. 工作流创建 `vX.Y.Z` tag 和 GitHub Release，从该 tag 构建 skill 压缩包及 SHA-256 并上传。

`version.txt` 是源码版本，`.release-please-manifest.json` 是工具管理的已发布基线，不手工双改版本。首次版本由配置中的 `initial-version` 指定；发布后由提交记录递增。0.x 阶段不兼容变化升 minor，稳定版不兼容变化升 major；feat 升 minor，fix 升 patch。docs/test/chore 本身不触发版本发布。

### GitHub 配置

在仓库 Settings → Actions → General 中允许 GitHub Actions 创建 PR。Release 工作流需要 contents、issues、pull-requests 写权限，CI 保持只读。

工作流优先使用仓库 secret `RELEASE_PLEASE_TOKEN`，否则使用 `GITHUB_TOKEN`。**启用 Release PR 必需的 CI 检查时，请配置 `RELEASE_PLEASE_TOKEN`**：使用仅授权此仓库的细粒度 PAT（Contents、Issues、Pull requests 读写）。默认 `GITHUB_TOKEN` 创建的 PR 不会触发新的 PR 工作流，不能把这一行为误判成 CI 已通过。Token 不写入仓库文件。

发布资产在同一工作流内上传，不依赖 bot 创建的 Release 再触发另一个工作流。发布失败可查看 Actions 日志；如 Release 已创建但资产上传失败，可按下述构建命令在对应 tag 上重新生成，再由维护者上传到同一 Release。

### 构建与验收

```sh
python scripts/build_release.py
```

输出为 `dist/camofox-client-vX.Y.Z.zip` 和同名 `.zip.sha256`。压缩包只有 `camofox-client/`，携带 SKILL.md、Python 脚本、参考文档、LICENSE 和版本。构建按明确文件范围收集内容，不包含 `.env`、缓存或开发依赖；已有同名产物时拒绝覆盖，可指定新的 `--output-dir`。

发布前确认：

- CI 矩阵通过，Release PR 版本及变更记录正确。
- 根 LICENSE 与 skill 内分发副本一致。
- 在临时项目发现、安装并执行 help：

```sh
npx skills add /absolute/path/to/camofox-client --list
npx skills add /absolute/path/to/camofox-client --skill camofox-client --agent claude-code --copy --yes
```

- 解压 Release 包，从其他工作目录执行脚本，确认不依赖源码仓库。
- 对需要服务端配合的改动，用授权的测试服务和 A/B 身份验证；只清理测试创建的标签，结果不含账号数据。

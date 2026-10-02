# 设计

## 目标与流程

同一个 agent 工作流需要交替操作多个 Camofox 浏览器用户上下文，而宿主工具可能把 userId 固定在配置里。

核心流程是：选择 A → 查看/创建 A 的标签 → 选择 B → 操作 B 的标签 → 回到 A 确认。每一步都应能从输入判断服务地址、身份和目标标签，不依赖上一步留下的隐藏状态。

因此，**每个 Client 绑定不可变 Config**。CLI 每次构造一个 Client，Python 编排可同时持有多个 Client。切换身份就是选择另一个配置实例，不是修改全局客户端。`CAMOFOX_USER_ID` 留空表示没有默认身份，访问用户资源必须显式选择；`sessionKey` 只是用户内标签分组。

## 业务契约

- 配置显式、来源可解释；业务调用不隐式读环境或文件。
- userId/sessionKey 由 Config 注入，request 不能另行覆盖；tabId 与其身份配对使用。
- 原子调用返回原始 JSON 或二进制响应，不加业务包装、不自动重试。
- 组合动作区分未执行、已执行但结果不确定、观察失败。点击超时不意味着可以安全重试。
- CLI 和 Python 使用同一套业务约束；入口只适配参数与呈现。
- 实际 profile、登录态隔离和访问控制由服务端提供，客户端不能据请求身份证明隔离成立。

## 职责与依赖

| 模块 | 职责 |
|---|---|
| `config.py` | 配置声明、校验、显式加载与来源 |
| `actions.py` | 动作参数规格、路由、校验；不导入实现 |
| `transport.py` | HTTP 编码、请求与传输错误；不认识用户业务 |
| `deadline.py` | 请求级网络预算，适配标准库连接与原始读取；不认识配置或动作 |
| `client.py` | 原子动作、身份/认证注入、响应业务判断 |
| `observe.py` | 单次点击及限时只读观察 |
| `doctor.py` | 只读健康/版本与配置诊断、脱敏 |
| `main.py` | CLI、显式实现分派、文件输入输出 |
| `errors.py` | 跨层错误数据与一致描述 |

依赖方向：CLI → Client/组合动作/声明；组合动作使用传入的 Client；Client → Transport/声明；Transport → Deadline。底层不反向导入入口。Client 的 transport 以及观察函数的 clock/sleep 可注入，足以离线测试，不需要额外依赖注入框架。

## 唯一事实源

| 事实 | 权威位置 | 使用方 |
|---|---|---|
| 配置字段、类型、默认值、敏感属性 | Config dataclass | 环境变量名、CLI 全局选项、doctor 摘要与脱敏名单 |
| 配置优先级 | resolve_config | build_config、load_config、CLI |
| 动作参数与路由 | ACTIONS / COMPOSITE | CLI help、原子与组合动作校验 |
| 观察默认时长 | observe.py 常量与函数签名 | CLI 不填时使用函数默认值 |
| 组合动作实现映射 | main.COMPOSITE_HANDLERS | CLI 分派；测试检查映射完整性 |
| 当前源码版本 | version.txt | release-please、分发包版本 |
| 已发布版本状态 | .release-please-manifest.json | release-please 的发布基线 |
| 授权文本 | 根 LICENSE | skill 分发副本；测试检查一致性 |

规格与实现映射分别表达“接受什么参数”和“执行什么函数”，共享动作名不等于重复业务逻辑。不能为了合并两个表，让 actions 导入 observe/doctor 形成循环依赖。

环境/文件中的 timeout 是文本，解析时转换；Python 显式值由 Config 校验。配置只合并一次，不能在多层重复覆盖而改变类型和优先级。敏感字段在 Config 声明 secret，CLI 与 doctor 派生行为，不各自维护密钥字段清单。

## 设计取舍

### 显式实例，而非 ProfileManager

不可变配置已经表达身份。全局“当前 profile”会让调用依赖顺序，并容易在交替任务中串用身份。新增 profile 管理层只有在出现具体的身份目录或生命周期需求时才有意义。

### 自包含脚本，而非 Python 发布包

完整 skill 目录就是安装单元。直接执行 main.py 时，Python 能从脚本目录导入同级模块，复制后即可运行。Python 编排需显式加入实际 scripts 路径；这些通用模块名不是稳定的可安装包命名空间。如需嵌入大型 Python 应用，应另行评估包命名隔离。

### 显式分派，而非兜底或插件框架

组合动作必须有对应 handler，不能把未知组合动作默认当成点击操作。doctor 接收配置来源等 CLI 上下文，是入口适配，不属于公共动作参数。当前规模用一个映射与完整性测试即可，不需要动态发现机制。

### 返回诊断结果，而非用退出码概括业务结果

原子失败输出 JSON error、退出码 1。组合动作形成结果时退出码 0，结果中可能包含失败或不确定；调用方必须检查结果字段。CLI 在业务调用前检查输出位置，保存使用同目录临时文件原子替换；即使业务返回后输出失败，也应以 `phase: output` 保留 JSON result，而不是把动作伪装成未执行。二进制错误恢复只携带未保存响应的元数据。

### 网络预算，而非无限延长的 socket 等待

Deadline 是每个请求独立的单调时钟截止时间。连接尝试、发送、响应头和正文的原始读取共享剩余预算，缓冲读取中的每次 socket 操作都重新设限，正常与错误响应遵循同一规则。适配留在传输下层，不让观察业务依赖 socket 实现；保留 urllib 的代理、TLS 校验及重定向策略。

系统同步 DNS 解析不能靠 socket 超时硬中断，因此不承诺绝对墙钟时限；解析返回后若已经过期，不再连接。观察层也检查返回结果是否落在窗口内，不接纳迟到候选。

## 边界与验证

Transport 拒绝跨主机 path、查询片段和路径穿越，不跟随重定向。Client 负责身份和认证，二者不互相承担职责。

Config repr 隐藏密钥；doctor 递归脱敏键和值，并优先匹配长密钥。诊断是展示结果，键替换可能折叠键，不能把它作为保真服务响应。原子响应保持原样，因此日志和输出仍需注意敏感信息。

测试覆盖 A→B→A 请求身份、分组、认证、tabId，配置来源、入口一致性、单次点击和失败路径。依赖检查只能发现显式导入越界，不能替代设计审查。回环 HTTP 测试也不能替代真实服务上的登录态隔离和版本兼容验证。

## 分发与版本

维护说明位于根目录，安装内容位于 `skills/camofox-client/`，不依赖仓库 tests/docs。目录遵循 [Agent Skills](https://agentskills.io/specification)，通过 [skills CLI](https://github.com/vercel-labs/skills) 或 Release 压缩包安装。

release-please 从 Conventional Commits 生成版本与变更记录。Release 工作流复用 CI，发布时从对应 tag 构建压缩包及 SHA-256；构建使用明确文件范围，不把开发环境或私人配置装进 skill。操作流程见 [贡献指南](../CONTRIBUTING.md)。

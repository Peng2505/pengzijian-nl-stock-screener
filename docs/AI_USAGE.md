# AI 使用与验证记录

## 使用的 AI 工具

| 工具 | 用途 |
|---|---|
| Cursor Agent（Composer） | 架构设计、代码生成、测试与文档起草 |
| DeepSeek（OpenAI 兼容接口） | 自然语言 → `ScreeningSpec` JSON；当前模型 `deepseek-flash` |
| 规则回退解析器 | 无 LLM Key 或 LLM 失败时保证主链路可演示 |

## AI 参与的环节

1. **意图结构化**：将「经营改善 / 估值合理 / 走势相对稳定」映射为可执行字段与默认阈值。
2. **澄清问题生成**：对模糊阈值询问用户偏好（PE 区间、波动上限等）。
3. **代码实现辅助**：仓库脚手架、FastAPI/React 页面、测试用例与 README。

## 我修正的不合理结果

1. **禁止 AI 直接出候选股名单**：入选判定只走 `ScreeningEngine`，避免模型幻觉票。
2. **Schema 白名单校验**：LLM 输出的 `field/op` 不在支持集则丢弃，防止编造指标。
3. **合规拒答**：含「保证挣钱 / 推荐买入 / 明天涨停」等语义时 API 返回 `compliance_reject`。
4. **冲突检测改为确定性规则**：高成长 + 极低估值等冲突由引擎规则产出，不依赖模型临场发挥。
5. **数据失败显式告警**：扶摇失败 / 字段 null 标记 `missing`/`error`，必填未满足则排除，不静默当通过。
6. **轻量「回测」改名条件稳定性**：避免被理解为收益承诺；接口文案强制 disclaimer。

## 我在调试中发现并已修正的问题

### 1. LLM 默认配置误用 OpenAI

- **问题**：初版 `.env` / 配置默认 `LLM_BASE_URL=https://api.openai.com/v1`、`LLM_MODEL=gpt-4o-mini`，与实际选用的 DeepSeek 不符。
- **修正**：改为 `LLM_BASE_URL=https://api.deepseek.com`，并同步更新 `.env.example`、`app/config.py`、`README.md`。

### 2. DeepSeek 模型名写错（`deepseek-v4flash`）

- **问题**：按习惯写成 `deepseek-v4flash` 后调用失败。接口返回明确错误：当前支持的模型名为 `deepseek-flash`、`deepseek-v4-pro`，传入 `deepseek-v4flash` 无效（`invalid_request_error`）。
- **验证**：Key 本身鉴权通过（非 401）；换模型名复测 `deepseek-flash` / `deepseek-v4-pro` / `deepseek-chat` 均可成功对话。
- **修正**：按 Flash 意图将配置统一改为 `LLM_MODEL=deepseek-flash`，并重建 API 容器使环境变量生效。

### 3. API Key 可用性实测结论

| Key | 结果 | 依据 |
|---|---|---|
| 扶摇 `FUYAO_API_KEY` | 可用 | `GET /api/meta/tickers/search` → HTTP 200、`code=0`，返回 `600519.SH` |
| DeepSeek `LLM_API_KEY` | 可用 | 鉴权通过；修正模型名后 `chat/completions` 成功 |

说明：密钥仅存放于本地 `.env`（已 gitignore），文档中不落明文。

### 4. Mock 开关与真数据易混淆

- **问题**：即便扶摇 Key 可用，若保持 `USE_MOCK_DATA=true`，产品仍走内置样本数据，UI 显示 MOCK，容易误判为「Key 无效」。
- **修正说明**：真实行情需显式设置 `USE_MOCK_DATA=false` 后重启 API；Key 探测应直连扶摇 REST，不经过 mock 短路。

### 5. iFinD MCP 解释增强接入

- **做法**：安装 `ifind-finance-data` Skill 后，将 MCP 密钥写入 `IFIND_MCP_TOKEN`；后端 `IFindClient` 调用 `stock_highfreq_quotes` / `search_news`。
- **边界**：增强结果 `kind=reference`，前端单独分区展示，**不进入** `ScreeningEngine` 的 pass/fail。
- **验证**：打开结果抽屉「查看」可拉取参考快照；失败时告警，不回写为筛选事实。

## 验证方式

- `pytest` 自动化：主链路、合规、排除逻辑、澄清答案应用、无 Token 时 enrich 降级。
- 手工：Web 端走完「解析 → 改条件 → 筛选 → 解释抽屉（含 iFinD 参考区）→ 对比/保存」。
- `/api/probe`：确认 live / mock 模式与连通性（含 ifind 子探测）。
- 直连探测：扶摇 tickers/search；DeepSeek `chat/completions`；iFinD `stock_highfreq_quotes`。

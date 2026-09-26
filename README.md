# 自然语言智能选股与策略解释器

**候选人：彭梓坚** · 同花顺入职考核提交版  

企业级交付：把模糊自然语言选股意图，转化为**可检查、可修改、可执行**的数据条件，用扶摇真实（或 mock）数据筛选，并解释入选/排除依据。本仓库为提交副本，**不含 API Key**（请本地复制 `.env.example` 为 `.env` 自行配置）。

## 产品选择与职责边界

| 角色 | 职责 | 不做什么 |
|---|---|---|
| **AI Interpreter** | 澄清意图、生成 `ScreeningSpec`、提示冲突 | 不直接决定谁入选 |
| **Deterministic Screener** | 对条件逐条求值，产出 pass/fail + evidence | 不调用 LLM |
| **Fuyao Data Gateway** | 行情/估值/财务/标的；统一错误与口径元数据 | 不静默填造“正常”结论 |

合规：不输出涨跌预测、收益承诺或买卖建议。事实 / 推断 / 不确定信息分区展示。

## 快速启动（本地）

### 1. 环境变量

```bash
cp .env.example .env
```

```env
USE_MOCK_DATA=false
FUYAO_API_KEY=你的扶摇密钥
LLM_API_KEY=你的DeepSeek密钥
LLM_BASE_URL=https://api.deepseek.com
LLM_MODEL=deepseek-flash
IFIND_MCP_TOKEN=你的iFinD_MCP密钥
```
 `.env` 若设置 `USE_MOCK_DATA=true`，无 Key 也可跑通主链路。接入真数据时：
 
### 2. 启动 API

```bash
cd apps/api
pip install -r requirements.txt
# Windows PowerShell
$env:PYTHONPATH="."
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

健康检查：http://127.0.0.1:8000/api/health  
OpenAPI：http://127.0.0.1:8000/docs  
扶摇探针：http://127.0.0.1:8000/api/probe

### 3. 启动 Web

```bash
cd apps/web
npm install --registry=https://registry.npmmirror.com
npm run dev
```

打开：http://127.0.0.1:5173  

Vite 已将 `/api` 代理到后端 `8000`。

### 4. Docker Compose（可选）

```bash
docker compose up --build
```

- Web: http://127.0.0.1:8088  
- API: http://127.0.0.1:8000  

## 主链路使用

1. 输入自然语言（示例：经营改善、估值合理、走势相对稳定）
2. **解析为条件** → 条件工作台编辑阈值 / 启用禁用
3. **多轮补充**：在对话区继续说「估值再严一点」等，上下文会保留并重解析；也可回答澄清单选
4. **执行筛选** → 查看入选/排除与 evidence（字段、来源、时点、状态）；抽屉内可看 iFinD 参考增强
5. 可 **保存策略**、**与解析快照对比**、**条件稳定性**、**转监控草稿**

## 数据来源

- 优先：扶摇 REST `https://fuyao.aicubes.cn`（估值 snapshot、利润表、K 线、标的列表/指数成分）
- 增强：iFinD MCP（实时快照 / 资讯参考，**不参与**入选判定）；需配置 `IFIND_MCP_TOKEN`
- 无 Key 或探针失败：内置 SAMPLE 宇宙 mock（UI 显示 `MOCK` 标签，告警不静默）

## 测试

```bash
cd apps/api
$env:PYTHONPATH="."
$env:USE_MOCK_DATA="true"
pytest -q
```

覆盖：主链路、必填条件排除、合规拒绝、保存与对比、澄清答案应用。详见 [docs/TEST_REPORT.md](docs/TEST_REPORT.md)。

## AI 使用记录

见 [docs/AI_USAGE.md](docs/AI_USAGE.md)。

## 已知边界与未做事项

- 全市场逐票财务暴力扫描：改为样本/指数成分限流宇宙，再算财务与波动
- 完整组合收益回测与实盘下单：未做；仅提供「条件稳定性」说明
- 监控：仅草稿落库，无推送
- iFinD：已接入解释增强（快照+新闻参考），未用于筛选条件求值
- 用户真实持仓画像：不采集

## 目录结构

```
apps/api/     FastAPI + 引擎 + 扶摇网关 + SQLite
apps/web/     React + Vite + Ant Design
docs/         AI 记录与测试说明
docker-compose.yml
```

## 可访问 URL

当前本机已验证：

| 入口 | URL |
|---|---|
| Docker 一体化产品 | http://127.0.0.1:8088 |
| 本地开发前端 | http://127.0.0.1:5173 |
| API / OpenAPI | http://127.0.0.1:8000/docs |

提交考核时若需公网 URL，可将本仓库部署至云主机 / Render / Railway，或用内网穿透转发 `8088`，并在材料中填写实际地址。

# 测试说明

## 环境

- Python 3.11+
- `USE_MOCK_DATA=true`（默认测试强制 mock，不依赖外网 Key）

```bash
cd apps/api
set PYTHONPATH=.
set USE_MOCK_DATA=true
pytest -q
```

## 用例覆盖

| 场景 | 用例 | 期望 |
|---|---|---|
| 健康检查 | `test_health` | `status=ok`，mock 模式 |
| 数据探针 | `test_probe_mock` | `ok=true`, `mode=mock` |
| 主链路 | `test_main_pipeline_parse_and_screen` | 解析出 ≥2 条件；筛选有宇宙；evidence 含 source |
| 条件排除 | `test_data_missing_required_excludes` | PE 不在区间则 `selected_count=0` |
| 接口/数据异常呈现 | 主链路 + mock 中 `601012` PE 缺失字段 | evidence `status=missing/ok` 可见，不假装完整 |
| 合规边界 | `test_compliance_reject` | 收益承诺/买卖建议 → HTTP 400 |
| 保存与对比 | `test_save_and_compare` | 策略入库；条件 diff 可计算 |
| 澄清闭环 | `test_clarification_answers_applied` | 答案改写 PE 区间为 5–25 |

## 手工回归清单

1. 输入「经营改善、估值合理、走势相对稳定」→ 解析 → 条件工作台可见 3 类条件。
2. 修改 PE 上限后执行筛选 → 入选数量变化。
3. 打开单票「解释」→ 可见字段值、来源、时点、命中状态。
4. 输入「保证挣钱推荐买入」→ 前端报错合规拒绝。
5. 保存策略 / 对比 / 条件稳定性 / 监控草稿均可点击且有反馈。
6. 将 `USE_MOCK_DATA=false` 并配置 `FUYAO_API_KEY` 后，`/api/probe` 应显示 live（若 Key 有效）。

## 极端与合规

- 空意图 / 无法映射：进入 clarifications，不编造字段。
- 上游 null：必填条件失败 → 排除，摘要标注缺失。
- 合规拒答：不进入筛选引擎。

import { useEffect, useState } from "react";
import {
  Alert,
  Button,
  Card,
  Col,
  Descriptions,
  Divider,
  Drawer,
  Form,
  Input,
  InputNumber,
  Layout,
  List,
  Modal,
  Radio,
  Row,
  Select,
  Space,
  Table,
  Tag,
  Typography,
  message,
} from "antd";
import {
  ApartmentOutlined,
  ExperimentOutlined,
  PlayCircleOutlined,
  SaveOutlined,
  SearchOutlined,
  SendOutlined,
  ClearOutlined,
  UserOutlined,
} from "@ant-design/icons";
import type { ColumnsType } from "antd/es/table";
import {
  api,
  type Condition,
  type ScreeningResult,
  type ScreeningSpec,
  type StockResult,
} from "./api";
import "./App.css";

const { Header, Content, Footer } = Layout;
const { Title, Paragraph, Text } = Typography;

const FIELD_OPTIONS = [
  { value: "pe_ttm", label: "市盈率（近一年）" },
  { value: "pb_mrq", label: "市净率" },
  { value: "revenue_yoy", label: "营收同比增速 %" },
  { value: "net_profit_yoy", label: "净利润同比增速 %" },
  { value: "volatility_60d", label: "近60日股价波动" },
  { value: "max_drawdown_60d", label: "近60日最大回撤" },
];

const OP_OPTIONS = [
  { value: "lt", label: "<" },
  { value: "lte", label: "≤" },
  { value: "gt", label: ">" },
  { value: "gte", label: "≥" },
  { value: "between", label: "区间" },
  { value: "eq", label: "=" },
];

type ChatMsg = { role: "user" | "assistant" | "system"; content: string; at: string };

const SESSION_KEY = "nl-screener-session-v1";

function App() {
  const [query, setQuery] = useState("经营改善、估值合理、走势相对稳定");
  const [followUp, setFollowUp] = useState("");
  const [loading, setLoading] = useState(false);
  const [screening, setScreening] = useState(false);
  const [spec, setSpec] = useState<ScreeningSpec | null>(null);
  const [baselineSpec, setBaselineSpec] = useState<ScreeningSpec | null>(null);
  const [result, setResult] = useState<ScreeningResult | null>(null);
  const [aiNotes, setAiNotes] = useState<string[]>([]);
  const [answers, setAnswers] = useState<Record<string, string>>({});
  const [chat, setChat] = useState<ChatMsg[]>([
    {
      role: "system",
      content: "会话级多轮澄清已启用：补充一句会保留上下文并重新结构化条件。不设登录，当前为匿名投研工作台。",
      at: new Date().toISOString(),
    },
  ]);
  const [health, setHealth] = useState<{
    use_mock_data: boolean;
    fuyao_configured: boolean;
    llm_configured: boolean;
    ifind_configured?: boolean;
  } | null>(null);
  const [drawerStock, setDrawerStock] = useState<StockResult | null>(null);
  const [enrichLoading, setEnrichLoading] = useState(false);
  const [enrichment, setEnrichment] = useState<{
    available: boolean;
    disclaimer: string;
    quote: {
      status: string;
      source: string;
      as_of?: string | null;
      fields: Record<string, string | number>;
      message?: string;
    } | null;
    news: Array<{ title: string; summary: string; source: string; as_of?: string | null }>;
    errors: string[];
  } | null>(null);
  const [compareOpen, setCompareOpen] = useState(false);
  const [compareInfo, setCompareInfo] = useState<string>("");
  const [aboutOpen, setAboutOpen] = useState(false);
  const [stability, setStability] = useState<string>("");

  useEffect(() => {
    api.health().then(setHealth).catch(() => setHealth(null));
    try {
      const raw = sessionStorage.getItem(SESSION_KEY);
      if (!raw) return;
      const saved = JSON.parse(raw) as {
        query?: string;
        answers?: Record<string, string>;
        chat?: ChatMsg[];
        spec?: ScreeningSpec | null;
      };
      if (saved.query) setQuery(saved.query);
      if (saved.answers) setAnswers(saved.answers);
      if (saved.chat?.length) setChat(saved.chat);
      if (saved.spec) {
        setSpec(saved.spec);
        setBaselineSpec(structuredClone(saved.spec));
      }
    } catch {
      /* ignore corrupt session */
    }
  }, []);

  useEffect(() => {
    const payload = JSON.stringify({ query, answers, chat, spec });
    sessionStorage.setItem(SESSION_KEY, payload);
  }, [query, answers, chat, spec]);

  const pushChat = (role: ChatMsg["role"], content: string) => {
    setChat((prev) => [...prev, { role, content, at: new Date().toISOString() }]);
  };

  const historyForApi = () =>
    chat
      .filter((m) => m.role === "user" || m.role === "assistant")
      .map((m) => ({ role: m.role, content: m.content }));

  const parseIntent = async (opts?: { followUpText?: string; fromClarify?: boolean }) => {
    const follow = (opts?.followUpText ?? "").trim();
    setLoading(true);
    try {
      if (!opts?.fromClarify && !follow) {
        pushChat("user", query);
      } else if (follow) {
        pushChat("user", follow);
      } else if (opts?.fromClarify) {
        pushChat("user", `已选择澄清：${JSON.stringify(answers)}`);
      }

      const res = await api.parseIntent(query, answers, historyForApi(), follow);
      setSpec(res.spec);
      setBaselineSpec(structuredClone(res.spec));
      setAiNotes(res.ai_notes);
      setResult(null);
      if (follow) setFollowUp("");

      const summaryParts = [
        res.used_fallback ? "规则/回退解析完成。" : "AI 结构化完成。",
        `条件 ${res.spec.conditions.length} 条`,
        res.spec.clarifications.length ? `待澄清 ${res.spec.clarifications.length} 项` : "暂无待澄清",
        ...res.ai_notes.slice(0, 2),
      ];
      if (res.spec.conditions.length) {
        summaryParts.push(
          "条件摘要：" +
            res.spec.conditions
              .map((c) => `${c.intent_label}(${c.field})`)
              .join("、"),
        );
      }
      pushChat("assistant", summaryParts.join(" "));
      message.success(res.used_fallback ? "已解析（含多轮上下文）" : "已解析（AI + 多轮上下文）");
    } catch (e) {
      const err = e instanceof Error ? e.message : "解析失败";
      pushChat("assistant", `解析失败：${err}`);
      message.error(err);
    } finally {
      setLoading(false);
    }
  };

  const clearSession = () => {
    sessionStorage.removeItem(SESSION_KEY);
    setChat([
      {
        role: "system",
        content: "会话已清空。可重新输入意图开始多轮澄清。",
        at: new Date().toISOString(),
      },
    ]);
    setAnswers({});
    setSpec(null);
    setBaselineSpec(null);
    setResult(null);
    setAiNotes([]);
    setFollowUp("");
    message.info("已清空本页会话上下文");
  };

  const runScreen = async () => {
    if (!spec) return;
    setScreening(true);
    try {
      const res = await api.runScreen(spec);
      setResult(res);
      message.success(`筛选完成：入选 ${res.meta.selected_count} / 宇宙 ${res.meta.universe_size}`);
    } catch (e) {
      message.error(e instanceof Error ? e.message : "筛选失败");
    } finally {
      setScreening(false);
    }
  };

  const updateCondition = (id: string, patch: Partial<Condition>) => {
    if (!spec) return;
    setSpec({
      ...spec,
      conditions: spec.conditions.map((c) => (c.id === id ? { ...c, ...patch } : c)),
    });
  };

  const openStockDrawer = async (row: StockResult) => {
    setDrawerStock(row);
    setEnrichment(null);
    setEnrichLoading(true);
    try {
      const data = await api.enrichStock(row.thscode, row.name);
      setEnrichment(data);
    } catch (e) {
      setEnrichment({
        available: false,
        disclaimer: "iFinD 解释增强暂时不可用。",
        quote: null,
        news: [],
        errors: [e instanceof Error ? e.message : "enrich failed"],
      });
    } finally {
      setEnrichLoading(false);
    }
  };

  const saveStrategy = async () => {
    if (!spec) return;
    const name = `策略-${new Date().toLocaleString()}`;
    try {
      await api.saveStrategy(name, spec, result ? { selected_count: result.meta.selected_count, run_id: result.meta.run_id } : undefined);
      message.success("策略已保存到本地 SQLite");
    } catch (e) {
      message.error(e instanceof Error ? e.message : "保存失败");
    }
  };

  const runCompare = async () => {
    if (!spec || !baselineSpec) return;
    try {
      const res = await api.compare(baselineSpec, spec);
      const lines = [
        `条件差异：${res.condition_diff.filter((d) => d.changed).length} 项`,
        ...res.condition_diff.filter((d) => d.changed).map((d) => `· ${d.field}: ${d.left} → ${d.right}`),
        `重叠入选：${res.overlap_selected.join(", ") || "无"}`,
        `仅原条件：${res.only_left.join(", ") || "无"}`,
        `仅新条件：${res.only_right.join(", ") || "无"}`,
      ];
      setCompareInfo(lines.join("\n"));
      setCompareOpen(true);
    } catch (e) {
      message.error(e instanceof Error ? e.message : "对比失败");
    }
  };

  const runStability = async () => {
    if (!spec) return;
    try {
      const res = await api.backtestLite(spec);
      if (res.message) {
        setStability(`${res.disclaimer}\n${res.message}`);
      } else {
        const lines = [
          res.disclaimer,
          `宇宙 ${res.universe_size}，入选 ${res.selected_count}`,
          ...(res.distribution || []).map((d) => `· ${d.field} 命中率 ${(d.hit_rate * 100).toFixed(1)}% (${d.hits}/${d.total})`),
        ];
        setStability(lines.join("\n"));
      }
    } catch (e) {
      message.error(e instanceof Error ? e.message : "稳定性分析失败");
    }
  };

  const createMonitor = async () => {
    if (!spec) return;
    try {
      await api.createMonitorDraft(`监控-${new Date().toLocaleString()}`, spec);
      message.success("已创建监控草稿（未接入实盘推送）");
    } catch (e) {
      message.error(e instanceof Error ? e.message : "创建失败");
    }
  };

  const selectedColumns: ColumnsType<StockResult> = [
      { title: "代码", dataIndex: "thscode", width: 120 },
      { title: "名称", dataIndex: "name", width: 120 },
      {
        title: "得分",
        dataIndex: "score",
        width: 80,
        render: (v: number) => v.toFixed(2),
      },
      {
        title: "数据质量",
        dataIndex: "data_quality",
        width: 100,
        render: (v: string) => (
          <Tag color={v === "complete" ? "green" : v === "partial" ? "orange" : "red"}>{v}</Tag>
        ),
      },
      { title: "摘要", dataIndex: "summary", ellipsis: true },
      {
        title: "解释",
        width: 90,
        render: (_, row) => (
          <Button type="link" onClick={() => openStockDrawer(row)}>
            查看
          </Button>
        ),
      },
    ];

  return (
    <Layout className="app-shell">
      <Header className="app-header">
        <div className="brand">
          <ApartmentOutlined />
          <span>智能选股与策略解释器</span>
        </div>
        <Space>
          {health && (
            <Tag color={health.use_mock_data ? "gold" : "blue"}>
              数据: {health.use_mock_data ? "MOCK" : "LIVE"}
            </Tag>
          )}
          {health && (
            <Tag color={health.llm_configured ? "blue" : "default"}>
              LLM: {health.llm_configured ? "ON" : "规则回退"}
            </Tag>
          )}
          {health && (
            <Tag color={health.ifind_configured ? "blue" : "default"}>
              iFinD: {health.ifind_configured ? "增强ON" : "OFF"}
            </Tag>
          )}
          <Tag icon={<UserOutlined />} color="default">
            匿名研究员
          </Tag>
          <Button type="text" style={{ color: "#fff" }} onClick={() => setAboutOpen(true)}>
            关于系统
          </Button>
        </Space>
      </Header>

      <Content className="app-content">
        <Alert
          type="warning"
          showIcon
          className="compliance-banner"
          message="合规声明"
          description="本产品将自然语言转化为可检查的数据条件，并基于授权数据做确定性筛选与解释。不构成投资建议，不承诺收益，不输出确定性涨跌预测。事实、推断与不确定信息将分区展示。"
        />

        <Row gutter={[16, 16]}>
          <Col xs={24} lg={10}>
            <Card title="1. 自然语言意图 · 多轮澄清" className="panel">
              <Input.TextArea
                rows={3}
                value={query}
                onChange={(e) => setQuery(e.target.value)}
                placeholder="例如：经营改善、估值合理、走势相对稳定"
              />
              <Space style={{ marginTop: 12 }} wrap>
                <Button type="primary" icon={<SearchOutlined />} loading={loading} onClick={() => parseIntent()}>
                  解析为条件
                </Button>
                <Button disabled={!spec} loading={screening} icon={<PlayCircleOutlined />} onClick={runScreen}>
                  执行筛选
                </Button>
                <Button icon={<ClearOutlined />} onClick={clearSession}>
                  清空会话
                </Button>
              </Space>

              <Divider style={{ margin: "12px 0" }} />
              <Text type="secondary">会话上下文（仅本页，刷新可恢复，不登录不落库）</Text>
              <div className="chat-box">
                {chat.map((m, idx) => (
                  <div key={`${m.at}-${idx}`} className={`chat-bubble chat-${m.role}`}>
                    <div className="chat-role">
                      {m.role === "user" ? "你" : m.role === "assistant" ? "助手" : "系统"}
                    </div>
                    <div className="chat-content">{m.content}</div>
                  </div>
                ))}
              </div>
              <Space.Compact style={{ width: "100%", marginTop: 8 }}>
                <Input
                  value={followUp}
                  onChange={(e) => setFollowUp(e.target.value)}
                  placeholder="补充一句，如：估值再严一点 / PE 用 5-25 / 波动更稳"
                  onPressEnter={() => followUp.trim() && parseIntent({ followUpText: followUp })}
                />
                <Button
                  type="primary"
                  icon={<SendOutlined />}
                  loading={loading}
                  disabled={!followUp.trim()}
                  onClick={() => parseIntent({ followUpText: followUp })}
                >
                  发送
                </Button>
              </Space.Compact>

              {aiNotes.length > 0 && (
                <Alert style={{ marginTop: 12 }} type="info" message={aiNotes.join(" ")} />
              )}
            </Card>

            {spec && spec.clarifications.length > 0 && (
              <Card title="澄清问题" className="panel" style={{ marginTop: 16 }}>
                {spec.clarifications.map((c) => (
                  <div key={c.id} style={{ marginBottom: 12 }}>
                    <Text>{c.question}</Text>
                    <Radio.Group
                      style={{ display: "block", marginTop: 8 }}
                      value={answers[c.id]}
                      onChange={(e) => setAnswers((prev) => ({ ...prev, [c.id]: e.target.value }))}
                      options={c.options.map((o) => ({ label: o, value: o }))}
                    />
                  </div>
                ))}
                <Button type="dashed" onClick={() => parseIntent({ fromClarify: true })} loading={loading}>
                  应用澄清并重新解析
                </Button>
              </Card>
            )}

            {spec && spec.conflicts.length > 0 && (
              <Alert
                style={{ marginTop: 16 }}
                type="error"
                showIcon
                message="条件冲突"
                description={
                  <ul>
                    {spec.conflicts.map((c, i) => (
                      <li key={i}>
                        {c.reason} 建议：{c.suggestion}
                      </li>
                    ))}
                  </ul>
                }
              />
            )}

            {spec && spec.unsupported.length > 0 && (
              <Alert
                style={{ marginTop: 16 }}
                type="warning"
                showIcon
                message="未能映射的意图（未编造指标）"
                description={spec.unsupported.map((u) => `${u.text} — ${u.reason}`).join("；")}
              />
            )}
          </Col>

          <Col xs={24} lg={14}>
            <Card
              title="2. 条件工作台（可检查 / 可修改）"
              className="panel"
              extra={spec ? <Text type="secondary">模型: {spec.meta.model}</Text> : null}
            >
              {!spec ? (
                <Paragraph type="secondary">先解析自然语言意图，生成可编辑 ScreeningSpec。</Paragraph>
              ) : (
                <>
                  {spec.meta.assumptions.length > 0 && (
                    <Alert
                      type="info"
                      showIcon
                      style={{ marginBottom: 12 }}
                      message="这些条件是怎么来的"
                      description={
                        <ul style={{ margin: "8px 0 0", paddingLeft: 18 }}>
                          {spec.meta.assumptions.map((a, i) => (
                            <li key={i} style={{ marginBottom: 6 }}>
                              {a}
                            </li>
                          ))}
                        </ul>
                      }
                    />
                  )}
                  <Form layout="vertical">
                    {spec.conditions.map((c) => (
                      <Card key={c.id} size="small" className="condition-card" title={c.intent_label}>
                        <Row gutter={8}>
                          <Col span={8}>
                            <Form.Item label="字段">
                              <Select
                                value={c.field}
                                options={FIELD_OPTIONS}
                                onChange={(v) => updateCondition(c.id, { field: v })}
                              />
                            </Form.Item>
                          </Col>
                          <Col span={6}>
                            <Form.Item label="算子">
                              <Select
                                value={c.op}
                                options={OP_OPTIONS}
                                onChange={(v) => updateCondition(c.id, { op: v as Condition["op"] })}
                              />
                            </Form.Item>
                          </Col>
                          <Col span={10}>
                            <Form.Item label="阈值">
                              {c.op === "between" && Array.isArray(c.value) ? (
                                <Space>
                                  <InputNumber
                                    value={c.value[0]}
                                    onChange={(v) =>
                                      updateCondition(c.id, {
                                        value: [Number(v ?? 0), Number((c.value as number[])[1] ?? 0)],
                                      })
                                    }
                                  />
                                  <span>~</span>
                                  <InputNumber
                                    value={c.value[1]}
                                    onChange={(v) =>
                                      updateCondition(c.id, {
                                        value: [Number((c.value as number[])[0] ?? 0), Number(v ?? 0)],
                                      })
                                    }
                                  />
                                </Space>
                              ) : (
                                <InputNumber
                                  style={{ width: "100%" }}
                                  value={typeof c.value === "number" ? c.value : 0}
                                  onChange={(v) => updateCondition(c.id, { value: Number(v ?? 0) })}
                                />
                              )}
                            </Form.Item>
                          </Col>
                        </Row>
                        <Space wrap>
                          <Tag>{c.source_hint || "source pending"}</Tag>
                          <Tag color={c.required ? "red" : "default"}>{c.required ? "必填" : "可选"}</Tag>
                          <Tag color={c.enabled ? "green" : "default"}>{c.enabled ? "启用" : "禁用"}</Tag>
                          <Button size="small" onClick={() => updateCondition(c.id, { enabled: !c.enabled })}>
                            {c.enabled ? "禁用" : "启用"}
                          </Button>
                        </Space>
                      </Card>
                    ))}
                  </Form>
                  <Space wrap style={{ marginTop: 8 }}>
                    <Button icon={<SaveOutlined />} onClick={saveStrategy}>
                      保存策略
                    </Button>
                    <Button onClick={runCompare}>与解析快照对比</Button>
                    <Button icon={<ExperimentOutlined />} onClick={runStability}>
                      条件稳定性
                    </Button>
                    <Button onClick={createMonitor}>转监控草稿</Button>
                  </Space>
                  {stability && (
                    <Alert style={{ marginTop: 12 }} type="info" message={<pre className="pre-block">{stability}</pre>} />
                  )}
                </>
              )}
            </Card>
          </Col>
        </Row>

        {result && (
          <Card
            className="panel"
            style={{ marginTop: 16 }}
            title="3. 筛选结果与解释"
            extra={
              <Space>
                <Tag>run: {result.meta.run_id}</Tag>
                <Tag color={result.meta.data_mode === "live" ? "blue" : "gold"}>{result.meta.data_mode}</Tag>
              </Space>
            }
          >
            <Alert type="info" showIcon message={result.meta.disclaimer} style={{ marginBottom: 12 }} />
            {result.meta.warnings.length > 0 && (
              <Alert
                type="warning"
                showIcon
                style={{ marginBottom: 12 }}
                message="数据告警（未静默忽略）"
                description={result.meta.warnings.join("；")}
              />
            )}
            <Descriptions size="small" bordered column={4} style={{ marginBottom: 16 }}>
              <Descriptions.Item label="宇宙规模">{result.meta.universe_size}</Descriptions.Item>
              <Descriptions.Item label="入选">{result.meta.selected_count}</Descriptions.Item>
              <Descriptions.Item label="排除">{result.meta.excluded_count}</Descriptions.Item>
              <Descriptions.Item label="完成时间">{result.meta.finished_at || "-"}</Descriptions.Item>
            </Descriptions>

            <Title level={5}>入选标的</Title>
            <Table
              rowKey="thscode"
              size="small"
              columns={selectedColumns}
              dataSource={result.selected}
              pagination={{ pageSize: 8 }}
            />

            <Divider />
            <Title level={5}>排除样本（展示前 {result.excluded_sample_limit}）</Title>
            <Table
              rowKey="thscode"
              size="small"
              columns={selectedColumns}
              dataSource={result.excluded}
              pagination={{ pageSize: 8 }}
            />
          </Card>
        )}
      </Content>

      <Footer className="app-footer">
        AI 解释意图 · 确定性引擎筛选 · 扶摇主数据 · iFinD 解释增强 · 结论可追溯
      </Footer>

      <Drawer
        width={480}
        open={!!drawerStock}
        onClose={() => {
          setDrawerStock(null);
          setEnrichment(null);
        }}
        title={drawerStock ? `${drawerStock.name}（${drawerStock.thscode}）入选/排除依据` : ""}
      >
        {drawerStock && (
          <>
            <Paragraph>
              <Text strong>判定：</Text>
              {drawerStock.selected ? <Tag color="green">入选</Tag> : <Tag color="red">排除</Tag>}
              <Text type="secondary"> 数据质量 {drawerStock.data_quality}</Text>
            </Paragraph>
            <List
              dataSource={drawerStock.condition_evals}
              renderItem={(ev) => (
                <List.Item>
                  <List.Item.Meta
                    title={
                      <Space>
                        <span>{ev.intent_label}</span>
                        {ev.passed === true && <Tag color="green">命中</Tag>}
                        {ev.passed === false && <Tag color="red">未命中</Tag>}
                        {ev.passed === null && <Tag>不确定</Tag>}
                        <Tag>事实</Tag>
                      </Space>
                    }
                    description={
                      <div>
                        <div>期望：{ev.expected}</div>
                        <div>
                          实际：{String(ev.actual.value ?? "null")} {ev.actual.unit} · 状态 {ev.actual.status}
                        </div>
                        <div>
                          来源：{ev.actual.source}
                          {ev.actual.as_of ? ` · 时点 ${ev.actual.as_of}` : ""}
                        </div>
                        {ev.actual.request_id && <div>request_id：{ev.actual.request_id}</div>}
                        {ev.actual.message && <div>说明：{ev.actual.message}</div>}
                      </div>
                    }
                  />
                </List.Item>
              )}
            />
            <Divider />
            <Title level={5}>iFinD 解释增强（参考，不参与筛选）</Title>
            {enrichLoading && <Paragraph type="secondary">正在拉取 iFinD 参考信息…</Paragraph>}
            {!enrichLoading && enrichment && (
              <>
                <Alert type="info" showIcon message={enrichment.disclaimer} style={{ marginBottom: 12 }} />
                {enrichment.errors.length > 0 && (
                  <Alert
                    type="warning"
                    showIcon
                    style={{ marginBottom: 12 }}
                    message="增强取数告警"
                    description={enrichment.errors.join("；")}
                  />
                )}
                {enrichment.quote && (
                  <Card size="small" title="实时/快照参考" style={{ marginBottom: 12 }}>
                    <div>来源：{enrichment.quote.source}</div>
                    {enrichment.quote.as_of && <div>时点：{enrichment.quote.as_of}</div>}
                    <Tag style={{ marginTop: 8 }}>参考</Tag>
                    <Descriptions size="small" column={1} style={{ marginTop: 8 }}>
                      {Object.entries(enrichment.quote.fields || {}).map(([k, v]) => (
                        <Descriptions.Item key={k} label={k}>
                          {String(v)}
                        </Descriptions.Item>
                      ))}
                    </Descriptions>
                  </Card>
                )}
                {enrichment.news.length > 0 && (
                  <Card size="small" title="相关资讯（参考）">
                    <List
                      size="small"
                      dataSource={enrichment.news}
                      renderItem={(n) => (
                        <List.Item>
                          <List.Item.Meta
                            title={
                              <Space>
                                <span>{n.title}</span>
                                <Tag>参考</Tag>
                              </Space>
                            }
                            description={
                              <div>
                                <div>{n.summary}</div>
                                <div>
                                  来源：{n.source}
                                  {n.as_of ? ` · ${n.as_of}` : ""}
                                </div>
                              </div>
                            }
                          />
                        </List.Item>
                      )}
                    />
                  </Card>
                )}
                {!enrichment.available && enrichment.errors.length === 0 && (
                  <Paragraph type="secondary">暂无可用增强信息。</Paragraph>
                )}
              </>
            )}
          </>
        )}
      </Drawer>

      <Modal open={compareOpen} onCancel={() => setCompareOpen(false)} onOk={() => setCompareOpen(false)} title="条件变化影响">
        <pre className="pre-block">{compareInfo}</pre>
      </Modal>

      <Modal open={aboutOpen} onCancel={() => setAboutOpen(false)} onOk={() => setAboutOpen(false)} title="职责边界" width={640}>
        <Paragraph>
          <Text strong>AI Interpreter：</Text>自然语言澄清与结构化为 ScreeningSpec，不直接决定入选名单。
        </Paragraph>
        <Paragraph>
          <Text strong>Deterministic Screener：</Text>对条件逐条求值，产出可复现的 pass/fail 与 evidence。
        </Paragraph>
        <Paragraph>
          <Text strong>Fuyao Data Gateway：</Text>行情 / 估值 / 财务 / 标的元数据；失败与缺失显式暴露，禁止静默编造。
        </Paragraph>
        <Paragraph>
          <Text strong>iFinD MCP（可选）：</Text>仅做解释增强（快照/资讯参考），<Text strong>不参与</Text>
          入选判定；与筛选事实分区展示。
        </Paragraph>
        <Paragraph>
          <Text strong>多轮上下文：</Text>同页会话保留 query / answers / 对话历史（sessionStorage），不设登录、不建用户画像。
        </Paragraph>
      </Modal>
    </Layout>
  );
}

export default App;

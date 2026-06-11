import { useState, useRef, useEffect } from "react";

// ── 샘플 초기 데이터 ──────────────────────────────
const SAMPLE_TRADES = [
  { id: 1, date: "2026-05-10", coin: "BTC", type: "buy",  price: 89500000, amount: 0.05, reason: "RSI 28 과매도 진입, 반등 기대" },
  { id: 2, date: "2026-05-18", coin: "BTC", type: "sell", price: 95200000, amount: 0.05, reason: "목표가 도달, RSI 72 과매수" },
  { id: 3, date: "2026-05-20", coin: "ETH", type: "buy",  price: 4850000,  amount: 0.5,  reason: "BTC 상승 후 알트 순환매 기대" },
  { id: 4, date: "2026-05-28", coin: "ETH", type: "sell", price: 4620000,  amount: 0.5,  reason: "시장 전반 조정, 손절" },
  { id: 5, date: "2026-06-01", coin: "XRP", type: "buy",  price: 780,      amount: 500,  reason: "거래량 급증 + MACD 골든크로스" },
  { id: 6, date: "2026-06-08", coin: "XRP", type: "sell", price: 920,      amount: 500,  reason: "+17% 달성, 저항선 근처" },
];

const COINS = ["BTC","ETH","XRP","SOL","ADA","DOGE","AVAX","DOT","MATIC","LINK"];
const UPBIT_TICKERS = ["KRW-BTC","KRW-ETH","KRW-XRP","KRW-SOL","KRW-ADA"];

function fmt(n) {
  if (n >= 1_000_000) return `₩${(n/1_000_000).toFixed(2)}M`;
  if (n >= 1_000)     return `₩${n.toLocaleString()}`;
  return `₩${n.toLocaleString()}`;
}
function fmtPrice(n) {
  if (!n && n !== 0) return "—";
  if (n >= 1_000_000) return `₩${(n/1_000_000).toFixed(2)}M`;
  if (n >= 1_000)     return `₩${n.toLocaleString()}`;
  if (n >= 1)         return `₩${n.toFixed(2)}`;
  return `₩${n.toFixed(4)}`;
}
function fmtPct(v) {
  const sign = v >= 0 ? "+" : "";
  return `${sign}${v.toFixed(2)}%`;
}

// ── 매매 통계 계산 ──────────────────────────────
function calcStats(trades) {
  const pairs = [];
  const buys = {};
  [...trades].sort((a,b) => a.date.localeCompare(b.date)).forEach(t => {
    if (t.type === "buy") {
      if (!buys[t.coin]) buys[t.coin] = [];
      buys[t.coin].push(t);
    } else {
      const buy = buys[t.coin]?.shift();
      if (buy) {
        const profit = (t.price - buy.price) * t.amount;
        const pct    = (t.price - buy.price) / buy.price * 100;
        pairs.push({ coin: t.coin, buyDate: buy.date, sellDate: t.date,
                     buyPrice: buy.price, sellPrice: t.price,
                     amount: t.amount, profit, pct,
                     buyReason: buy.reason, sellReason: t.reason });
      }
    }
  });
  const holding = [];
  Object.entries(buys).forEach(([coin, list]) => list.forEach(b => holding.push(b)));

  const totalProfit  = pairs.reduce((s,p) => s + p.profit, 0);
  const winCount     = pairs.filter(p => p.profit > 0).length;
  const winRate      = pairs.length ? winCount / pairs.length * 100 : 0;
  const avgPct       = pairs.length ? pairs.reduce((s,p) => s + p.pct, 0) / pairs.length : 0;
  return { pairs, holding, totalProfit, winRate, avgPct, tradeCount: pairs.length };
}

// ── 업비트 공개 API 시세 조회 ──────────────────
async function fetchUpbitTickers() {
  try {
    const markets = UPBIT_TICKERS.join(",");
    const res = await fetch(`https://api.upbit.com/v1/ticker?markets=${markets}`, {
      headers: { Accept: "application/json" },
    });
    if (!res.ok) throw new Error("업비트 API 오류");
    const data = await res.json();
    const result = {};
    data.forEach(item => { result[item.market] = item; });
    return result;
  } catch (e) {
    console.error("업비트 시세 조회 실패:", e);
    return {};
  }
}

// ── 챗봇 API 호출 (백엔드 프록시 또는 직접 호출) ──
async function askAI(messages, trades, tickerInfo) {
  const stats = calcStats(trades);
  const tradesSummary = trades.map(t =>
    `[${t.date}] ${t.type === "buy" ? "매수" : "매도"} ${t.coin} | 가격: ${fmt(t.price)} | 수량: ${t.amount} | 이유: ${t.reason}`
  ).join("\n");

  // 실시간 시세 요약
  const priceSummary = Object.entries(tickerInfo).map(([market, info]) => {
    const chg = (info.signed_change_rate * 100).toFixed(2);
    return `${market}: ${fmtPrice(info.trade_price)} (${chg >= 0 ? "+" : ""}${chg}%)`;
  }).join("\n");

  const systemPrompt = `당신은 코인 매매 일지를 분석해주는 전문 트레이딩 어시스턴트입니다.
사용자의 실제 매매 기록을 바탕으로 구체적이고 실용적인 분석을 제공하세요.

=== 매매 기록 ===
${tradesSummary}

=== 통계 요약 ===
- 완료된 거래: ${stats.tradeCount}건
- 총 수익: ${fmt(stats.totalProfit)}
- 승률: ${stats.winRate.toFixed(1)}%
- 평균 수익률: ${fmtPct(stats.avgPct)}
- 보유 중: ${stats.holding.map(h => h.coin).join(", ") || "없음"}

=== 실시간 시세 (업비트) ===
${priceSummary || "시세 정보 없음"}

위 데이터를 바탕으로 질문에 답하세요. 
- 구체적인 날짜, 코인명, 수익/손실 금액을 언급하세요
- 매매 패턴의 장단점을 솔직하게 분석하세요
- 개선 방향도 제안하세요
- 한국어로 답변하세요`;

  // 백엔드 프록시 엔드포인트 시도 (Streamlit 서버가 있는 경우)
  // 없으면 직접 호출 (API 키는 환경변수에서 주입 필요)
  const apiKey = window.__ANTHROPIC_API_KEY__ || "";

  if (!apiKey) {
    return "⚠️ API 키가 설정되지 않았습니다.\n\nStreamlit 앱(app.py)을 통해 챗봇을 사용하거나,\n환경변수 ANTHROPIC_API_KEY를 설정해주세요.";
  }

  const res = await fetch("https://api.anthropic.com/v1/messages", {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      "x-api-key": apiKey,
      "anthropic-version": "2023-06-01",
    },
    body: JSON.stringify({
      model: "claude-sonnet-4-20250514",
      max_tokens: 1000,
      system: systemPrompt,
      messages: messages.filter(m => m.role !== "system").map(m => ({
        role: m.role, content: m.content
      })),
    }),
  });
  const data = await res.json();
  if (!res.ok) {
    const errMsg = data?.error?.message || "알 수 없는 오류";
    throw new Error(errMsg);
  }
  return data.content?.[0]?.text || "응답을 받지 못했어요.";
}

// ── 실시간 시세 위젯 컴포넌트 ──────────────────
function PriceTicker({ tickerInfo, loading }) {
  const coinNames = { "KRW-BTC": "BTC", "KRW-ETH": "ETH", "KRW-XRP": "XRP", "KRW-SOL": "SOL", "KRW-ADA": "ADA" };

  if (loading) {
    return (
      <div style={{ display: "flex", gap: 8, overflowX: "auto", padding: "8px 20px" }}>
        {UPBIT_TICKERS.map(t => (
          <div key={t} style={{ background: "#21262d", borderRadius: 8, padding: "6px 12px", minWidth: 80, flexShrink: 0 }}>
            <div style={{ fontSize: 11, color: "#8b949e" }}>{coinNames[t]}</div>
            <div style={{ fontSize: 13, color: "#30363d", fontFamily: "monospace" }}>로딩중...</div>
          </div>
        ))}
      </div>
    );
  }

  return (
    <div style={{ display: "flex", gap: 8, overflowX: "auto", padding: "8px 20px", borderBottom: "1px solid #21262d" }}>
      {UPBIT_TICKERS.map(ticker => {
        const info = tickerInfo[ticker];
        if (!info) return null;
        const chg = info.signed_change_rate * 100;
        const color = chg >= 0 ? "#3fb950" : "#f85149";
        const arrow = chg >= 0 ? "▲" : "▼";
        return (
          <div key={ticker} style={{ background: "#161b22", border: "1px solid #30363d", borderRadius: 8, padding: "6px 12px", minWidth: 90, flexShrink: 0 }}>
            <div style={{ fontSize: 11, color: "#8b949e", marginBottom: 2 }}>{coinNames[ticker]}</div>
            <div style={{ fontSize: 12, fontFamily: "monospace", fontWeight: 700, color: "#e6edf3" }}>{fmtPrice(info.trade_price)}</div>
            <div style={{ fontSize: 11, color, marginTop: 1 }}>{arrow} {Math.abs(chg).toFixed(2)}%</div>
          </div>
        );
      })}
    </div>
  );
}

// ── 컴포넌트 ──────────────────────────────────
export default function App() {
  const [trades, setTrades]   = useState(SAMPLE_TRADES);
  const [tab, setTab]         = useState("chat"); // chat | journal | add
  const [messages, setMessages] = useState([
    { role: "assistant", content: "안녕하세요! 매매 일지를 분석해드리는 AI예요.\n\n\"이번 달 수익률 어때?\", \"BTC 매매 패턴 분석해줘\", \"내가 왜 손해봤어?\" 같은 질문을 해보세요 😊" }
  ]);
  const [input, setInput]     = useState("");
  const [loading, setLoading] = useState(false);
  const [showForm, setShowForm] = useState(false);
  const [form, setForm]       = useState({ date: "", coin: "BTC", type: "buy", price: "", amount: "", reason: "" });
  const [editId, setEditId]   = useState(null);
  const chatEndRef = useRef(null);

  // 업비트 실시간 시세 상태
  const [tickerInfo, setTickerInfo] = useState({});
  const [priceLoading, setPriceLoading] = useState(true);

  useEffect(() => { chatEndRef.current?.scrollIntoView({ behavior: "smooth" }); }, [messages]);

  // 업비트 시세 주기적 갱신 (30초)
  useEffect(() => {
    const loadPrices = async () => {
      setPriceLoading(true);
      const data = await fetchUpbitTickers();
      setTickerInfo(data);
      setPriceLoading(false);
    };
    loadPrices();
    const interval = setInterval(loadPrices, 30000);
    return () => clearInterval(interval);
  }, []);

  const stats = calcStats(trades);

  // 메시지 전송
  async function sendMessage() {
    if (!input.trim() || loading) return;
    const userMsg = { role: "user", content: input.trim() };
    const newMsgs = [...messages, userMsg];
    setMessages(newMsgs);
    setInput("");
    setLoading(true);
    try {
      const reply = await askAI(newMsgs, trades, tickerInfo);
      setMessages(prev => [...prev, { role: "assistant", content: reply }]);
    } catch(e) {
      setMessages(prev => [...prev, { role: "assistant", content: `오류: ${e.message}\n\n다시 시도해주세요.` }]);
    }
    setLoading(false);
  }

  // 거래 추가/수정
  function saveTrade() {
    if (!form.date || !form.price || !form.amount || !form.reason) return;
    const entry = {
      id: editId || Date.now(),
      date: form.date, coin: form.coin, type: form.type,
      price: Number(form.price), amount: Number(form.amount), reason: form.reason,
    };
    if (editId) {
      setTrades(prev => prev.map(t => t.id === editId ? entry : t));
      setEditId(null);
    } else {
      setTrades(prev => [...prev, entry]);
    }
    setForm({ date: "", coin: "BTC", type: "buy", price: "", amount: "", reason: "" });
    setShowForm(false);
    setTab("journal");
  }

  function deleteTrade(id) {
    setTrades(prev => prev.filter(t => t.id !== id));
  }

  function startEdit(t) {
    setForm({ date: t.date, coin: t.coin, type: t.type, price: String(t.price), amount: String(t.amount), reason: t.reason });
    setEditId(t.id);
    setShowForm(true);
    setTab("add");
  }

  // 빠른 질문
  const quickQs = ["이번 달 수익률 어때?", "왜 손해봤어?", "매매 패턴 분석해줘", "어떤 코인이 제일 수익?"];

  return (
    <div style={{ background: "#0d1117", minHeight: "100vh", color: "#e6edf3", fontFamily: "'Noto Sans KR', sans-serif", display: "flex", flexDirection: "column" }}>

      {/* 헤더 */}
      <div style={{ background: "#161b22", borderBottom: "1px solid #30363d", padding: "12px 20px", display: "flex", alignItems: "center", gap: 12 }}>
        <span style={{ fontSize: 22 }}>📒</span>
        <div>
          <div style={{ fontWeight: 700, fontSize: 16, letterSpacing: "-0.02em" }}>코인 매매 일지</div>
          <div style={{ fontSize: 11, color: "#8b949e" }}>AI 분석 챗봇 · 업비트 연동</div>
        </div>

        {/* 요약 뱃지 */}
        <div style={{ marginLeft: "auto", display: "flex", gap: 12, flexWrap: "wrap" }}>
          {[
            { label: "총 수익", value: fmt(stats.totalProfit), color: stats.totalProfit >= 0 ? "#3fb950" : "#f85149" },
            { label: "승률", value: `${stats.winRate.toFixed(0)}%`, color: "#58a6ff" },
            { label: "거래", value: `${stats.tradeCount}건`, color: "#8b949e" },
          ].map(b => (
            <div key={b.label} style={{ background: "#21262d", borderRadius: 8, padding: "4px 12px", textAlign: "center" }}>
              <div style={{ fontSize: 10, color: "#8b949e" }}>{b.label}</div>
              <div style={{ fontWeight: 700, fontSize: 13, color: b.color, fontFamily: "monospace" }}>{b.value}</div>
            </div>
          ))}
        </div>
      </div>

      {/* 업비트 실시간 시세 바 */}
      <PriceTicker tickerInfo={tickerInfo} loading={priceLoading} />

      {/* 탭 */}
      <div style={{ background: "#161b22", borderBottom: "1px solid #21262d", display: "flex", padding: "0 20px" }}>
        {[["chat","💬 AI 분석"],["journal","📋 매매 일지"],["add","➕ 기록 추가"]].map(([key, label]) => (
          <button key={key} onClick={() => { setTab(key); if (key==="add") setShowForm(true); }}
            style={{ background: "none", border: "none", color: tab===key ? "#e6edf3" : "#8b949e",
              borderBottom: tab===key ? "2px solid #58a6ff" : "2px solid transparent",
              padding: "10px 16px", cursor: "pointer", fontSize: 13, fontWeight: tab===key ? 600 : 400 }}>
            {label}
          </button>
        ))}
      </div>

      {/* 본문 */}
      <div style={{ flex: 1, overflow: "hidden", display: "flex", flexDirection: "column" }}>

        {/* ── 챗봇 탭 ── */}
        {tab === "chat" && (
          <div style={{ flex: 1, display: "flex", flexDirection: "column", overflow: "hidden" }}>
            {/* 메시지 영역 */}
            <div style={{ flex: 1, overflowY: "auto", padding: "16px 20px", display: "flex", flexDirection: "column", gap: 12 }}>
              {messages.map((m, i) => (
                <div key={i} style={{ display: "flex", justifyContent: m.role === "user" ? "flex-end" : "flex-start" }}>
                  {m.role === "assistant" && (
                    <div style={{ width: 28, height: 28, borderRadius: "50%", background: "#21262d", display: "flex", alignItems: "center", justifyContent: "center", fontSize: 14, marginRight: 8, flexShrink: 0, alignSelf: "flex-start", marginTop: 2 }}>🤖</div>
                  )}
                  <div style={{
                    maxWidth: "75%", padding: "10px 14px", borderRadius: m.role === "user" ? "16px 16px 4px 16px" : "16px 16px 16px 4px",
                    background: m.role === "user" ? "#1f6feb" : "#161b22",
                    border: m.role === "user" ? "none" : "1px solid #30363d",
                    fontSize: 14, lineHeight: 1.6, whiteSpace: "pre-wrap", color: "#e6edf3",
                  }}>
                    {m.content}
                  </div>
                </div>
              ))}
              {loading && (
                <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
                  <div style={{ width: 28, height: 28, borderRadius: "50%", background: "#21262d", display: "flex", alignItems: "center", justifyContent: "center", fontSize: 14 }}>🤖</div>
                  <div style={{ background: "#161b22", border: "1px solid #30363d", borderRadius: "16px 16px 16px 4px", padding: "10px 16px" }}>
                    <div style={{ display: "flex", gap: 4 }}>
                      {[0,1,2].map(i => (
                        <div key={i} style={{ width: 6, height: 6, borderRadius: "50%", background: "#58a6ff",
                          animation: "pulse 1.2s ease-in-out infinite", animationDelay: `${i*0.2}s` }}/>
                      ))}
                    </div>
                  </div>
                </div>
              )}
              <div ref={chatEndRef}/>
            </div>

            {/* 빠른 질문 */}
            <div style={{ padding: "8px 20px 0", display: "flex", gap: 8, flexWrap: "wrap" }}>
              {quickQs.map(q => (
                <button key={q} onClick={() => { setInput(q); }}
                  style={{ background: "#21262d", border: "1px solid #30363d", borderRadius: 20, padding: "5px 12px",
                    color: "#8b949e", fontSize: 12, cursor: "pointer", transition: "all 0.15s" }}
                  onMouseEnter={e => e.target.style.borderColor="#58a6ff"}
                  onMouseLeave={e => e.target.style.borderColor="#30363d"}>
                  {q}
                </button>
              ))}
            </div>

            {/* 입력창 */}
            <div style={{ padding: "12px 20px", display: "flex", gap: 10, borderTop: "1px solid #21262d", marginTop: 8 }}>
              <input value={input} onChange={e => setInput(e.target.value)}
                onKeyDown={e => e.key === "Enter" && !e.shiftKey && sendMessage()}
                placeholder="매매 기록에 대해 뭐든 물어보세요..."
                style={{ flex: 1, background: "#21262d", border: "1px solid #30363d", borderRadius: 10,
                  padding: "10px 14px", color: "#e6edf3", fontSize: 14, outline: "none" }}/>
              <button onClick={sendMessage} disabled={loading}
                style={{ background: loading ? "#21262d" : "#1f6feb", border: "none", borderRadius: 10,
                  padding: "10px 18px", color: "#fff", fontWeight: 600, fontSize: 14, cursor: loading ? "not-allowed" : "pointer" }}>
                전송
              </button>
            </div>
          </div>
        )}

        {/* ── 매매 일지 탭 ── */}
        {tab === "journal" && (
          <div style={{ flex: 1, overflowY: "auto", padding: 20 }}>

            {/* 완료 거래 */}
            <div style={{ marginBottom: 8, fontSize: 12, color: "#8b949e", fontWeight: 600, textTransform: "uppercase", letterSpacing: "0.08em" }}>완료된 거래</div>
            {stats.pairs.length === 0 && <div style={{ color: "#8b949e", fontSize: 13, padding: "12px 0" }}>아직 완료된 거래가 없어요.</div>}
            {stats.pairs.map((p, i) => (
              <div key={i} style={{ background: "#161b22", border: "1px solid #30363d", borderRadius: 10, padding: "12px 16px", marginBottom: 8 }}>
                <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 6 }}>
                  <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
                    <span style={{ fontWeight: 700, fontSize: 15 }}>{p.coin}</span>
                    <span style={{ background: "#21262d", borderRadius: 6, padding: "2px 8px", fontSize: 11, color: "#8b949e" }}>{p.buyDate} → {p.sellDate}</span>
                  </div>
                  <span style={{ fontWeight: 700, fontSize: 15, color: p.profit >= 0 ? "#3fb950" : "#f85149", fontFamily: "monospace" }}>
                    {p.profit >= 0 ? "+" : ""}{fmt(p.profit)} ({fmtPct(p.pct)})
                  </span>
                </div>
                <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: "4px 16px", fontSize: 12, color: "#8b949e", marginBottom: 8 }}>
                  <span>매수: {fmt(p.buyPrice)} × {p.amount}</span>
                  <span>매도: {fmt(p.sellPrice)} × {p.amount}</span>
                </div>
                <div style={{ fontSize: 12, marginBottom: 3 }}>
                  <span style={{ color: "#3fb950", marginRight: 6 }}>↑ 매수 이유:</span>
                  <span style={{ color: "#c9d1d9" }}>{p.buyReason}</span>
                </div>
                <div style={{ fontSize: 12 }}>
                  <span style={{ color: "#f85149", marginRight: 6 }}>↓ 매도 이유:</span>
                  <span style={{ color: "#c9d1d9" }}>{p.sellReason}</span>
                </div>
              </div>
            ))}

            {/* 보유 중 */}
            {stats.holding.length > 0 && (
              <>
                <div style={{ margin: "16px 0 8px", fontSize: 12, color: "#8b949e", fontWeight: 600, textTransform: "uppercase", letterSpacing: "0.08em" }}>보유 중</div>
                {stats.holding.map((h, i) => {
                  const ticker = `KRW-${h.coin}`;
                  const curPrice = tickerInfo[ticker]?.trade_price;
                  const pnlPct = curPrice && h.price ? (curPrice - h.price) / h.price * 100 : null;
                  return (
                    <div key={i} style={{ background: "#161b22", border: "1px solid #30363d", borderRadius: 10, padding: "12px 16px", marginBottom: 8, display: "flex", justifyContent: "space-between", alignItems: "center" }}>
                      <div>
                        <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 4 }}>
                          <span style={{ fontWeight: 700, fontSize: 15 }}>{h.coin}</span>
                          <span style={{ fontSize: 12, color: "#8b949e" }}>{h.date} 매수</span>
                          {pnlPct !== null && (
                            <span style={{ fontSize: 12, color: pnlPct >= 0 ? "#3fb950" : "#f85149", fontWeight: 700 }}>
                              {pnlPct >= 0 ? "▲" : "▼"} {Math.abs(pnlPct).toFixed(2)}%
                            </span>
                          )}
                        </div>
                        <div style={{ fontSize: 12, color: "#8b949e" }}>
                          매수가: {fmt(h.price)} × {h.amount}
                          {curPrice && <span style={{ marginLeft: 8, color: "#58a6ff" }}>현재: {fmtPrice(curPrice)}</span>}
                        </div>
                        <div style={{ fontSize: 12, color: "#8b949e", marginTop: 2 }}>{h.reason}</div>
                      </div>
                      <div style={{ display: "flex", gap: 6 }}>
                        <button onClick={() => startEdit(h)} style={{ background: "#21262d", border: "1px solid #30363d", borderRadius: 6, padding: "4px 10px", color: "#8b949e", fontSize: 12, cursor: "pointer" }}>수정</button>
                        <button onClick={() => deleteTrade(h.id)} style={{ background: "#3d1212", border: "1px solid #b91c1c", borderRadius: 6, padding: "4px 10px", color: "#f85149", fontSize: 12, cursor: "pointer" }}>삭제</button>
                      </div>
                    </div>
                  );
                })}
              </>
            )}

            {/* 전체 목록 */}
            <div style={{ margin: "16px 0 8px", fontSize: 12, color: "#8b949e", fontWeight: 600, textTransform: "uppercase", letterSpacing: "0.08em" }}>전체 거래 기록</div>
            {[...trades].sort((a,b) => b.date.localeCompare(a.date)).map(t => (
              <div key={t.id} style={{ background: "#161b22", border: "1px solid #21262d", borderRadius: 8, padding: "10px 14px", marginBottom: 6, display: "flex", alignItems: "center", gap: 10 }}>
                <span style={{ background: t.type==="buy" ? "#0d4429" : "#3d1212", color: t.type==="buy" ? "#3fb950" : "#f85149",
                  border: `1px solid ${t.type==="buy" ? "#238636" : "#b91c1c"}`,
                  borderRadius: 6, padding: "2px 8px", fontSize: 11, fontWeight: 700, flexShrink: 0 }}>
                  {t.type === "buy" ? "매수" : "매도"}
                </span>
                <span style={{ fontWeight: 600, fontSize: 13, width: 40 }}>{t.coin}</span>
                <span style={{ fontSize: 12, color: "#8b949e", width: 90 }}>{t.date}</span>
                <span style={{ fontSize: 12, fontFamily: "monospace", color: "#e6edf3" }}>{fmt(t.price)} × {t.amount}</span>
                <span style={{ fontSize: 12, color: "#8b949e", flex: 1, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{t.reason}</span>
                <div style={{ display: "flex", gap: 4, flexShrink: 0 }}>
                  <button onClick={() => startEdit(t)} style={{ background: "none", border: "1px solid #30363d", borderRadius: 5, padding: "2px 8px", color: "#8b949e", fontSize: 11, cursor: "pointer" }}>수정</button>
                  <button onClick={() => deleteTrade(t.id)} style={{ background: "none", border: "1px solid #30363d", borderRadius: 5, padding: "2px 8px", color: "#f85149", fontSize: 11, cursor: "pointer" }}>삭제</button>
                </div>
              </div>
            ))}
          </div>
        )}

        {/* ── 기록 추가 탭 ── */}
        {(tab === "add") && (
          <div style={{ flex: 1, overflowY: "auto", padding: 20, maxWidth: 520 }}>
            <div style={{ fontSize: 15, fontWeight: 700, marginBottom: 20 }}>{editId ? "거래 수정" : "새 매매 기록"}</div>

            {[
              { label: "날짜", key: "date", type: "date" },
            ].map(f => (
              <div key={f.key} style={{ marginBottom: 14 }}>
                <label style={{ fontSize: 12, color: "#8b949e", display: "block", marginBottom: 4 }}>{f.label}</label>
                <input type={f.type} value={form[f.key]} onChange={e => setForm(p => ({...p, [f.key]: e.target.value}))}
                  style={{ width: "100%", background: "#21262d", border: "1px solid #30363d", borderRadius: 8, padding: "9px 12px", color: "#e6edf3", fontSize: 14, boxSizing: "border-box" }}/>
              </div>
            ))}

            <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 12, marginBottom: 14 }}>
              <div>
                <label style={{ fontSize: 12, color: "#8b949e", display: "block", marginBottom: 4 }}>코인</label>
                <select value={form.coin} onChange={e => setForm(p => ({...p, coin: e.target.value}))}
                  style={{ width: "100%", background: "#21262d", border: "1px solid #30363d", borderRadius: 8, padding: "9px 12px", color: "#e6edf3", fontSize: 14 }}>
                  {COINS.map(c => <option key={c}>{c}</option>)}
                </select>
              </div>
              <div>
                <label style={{ fontSize: 12, color: "#8b949e", display: "block", marginBottom: 4 }}>유형</label>
                <select value={form.type} onChange={e => setForm(p => ({...p, type: e.target.value}))}
                  style={{ width: "100%", background: "#21262d", border: "1px solid #30363d", borderRadius: 8, padding: "9px 12px", color: "#e6edf3", fontSize: 14 }}>
                  <option value="buy">매수</option>
                  <option value="sell">매도</option>
                </select>
              </div>
            </div>

            <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 12, marginBottom: 14 }}>
              {[{label:"가격 (₩)", key:"price", ph:"89500000"},{label:"수량", key:"amount", ph:"0.05"}].map(f => (
                <div key={f.key}>
                  <label style={{ fontSize: 12, color: "#8b949e", display: "block", marginBottom: 4 }}>{f.label}</label>
                  <input type="number" value={form[f.key]} onChange={e => setForm(p => ({...p, [f.key]: e.target.value}))}
                    placeholder={f.ph}
                    style={{ width: "100%", background: "#21262d", border: "1px solid #30363d", borderRadius: 8, padding: "9px 12px", color: "#e6edf3", fontSize: 14, boxSizing: "border-box" }}/>
                </div>
              ))}
            </div>

            {/* 현재 시세 참고 */}
            {form.coin && tickerInfo[`KRW-${form.coin}`] && (
              <div style={{ background: "#0d2137", border: "1px solid #1f6feb", borderRadius: 8, padding: "8px 12px", marginBottom: 14, fontSize: 12 }}>
                <span style={{ color: "#58a6ff" }}>📊 {form.coin} 현재가: </span>
                <span style={{ fontFamily: "monospace", fontWeight: 700, color: "#e6edf3" }}>
                  {fmtPrice(tickerInfo[`KRW-${form.coin}`].trade_price)}
                </span>
                <span style={{ color: tickerInfo[`KRW-${form.coin}`].signed_change_rate >= 0 ? "#3fb950" : "#f85149", marginLeft: 8 }}>
                  {(tickerInfo[`KRW-${form.coin}`].signed_change_rate * 100).toFixed(2)}%
                </span>
              </div>
            )}

            <div style={{ marginBottom: 20 }}>
              <label style={{ fontSize: 12, color: "#8b949e", display: "block", marginBottom: 4 }}>
                {form.type === "buy" ? "매수 이유" : "매도 이유"}
              </label>
              <textarea value={form.reason} onChange={e => setForm(p => ({...p, reason: e.target.value}))}
                placeholder={form.type === "buy" ? "예: RSI 과매도, MACD 골든크로스 확인" : "예: 목표가 도달, 시장 불확실성"}
                rows={3}
                style={{ width: "100%", background: "#21262d", border: "1px solid #30363d", borderRadius: 8, padding: "9px 12px",
                  color: "#e6edf3", fontSize: 14, resize: "vertical", boxSizing: "border-box", fontFamily: "inherit" }}/>
            </div>

            <div style={{ display: "flex", gap: 10 }}>
              <button onClick={saveTrade}
                style={{ flex: 1, background: "#238636", border: "none", borderRadius: 8, padding: "11px", color: "#fff", fontWeight: 700, fontSize: 14, cursor: "pointer" }}>
                {editId ? "수정 완료" : "기록 저장"}
              </button>
              <button onClick={() => { setShowForm(false); setEditId(null); setForm({ date:"",coin:"BTC",type:"buy",price:"",amount:"",reason:"" }); setTab("journal"); }}
                style={{ background: "#21262d", border: "1px solid #30363d", borderRadius: 8, padding: "11px 16px", color: "#8b949e", fontSize: 14, cursor: "pointer" }}>
                취소
              </button>
            </div>
          </div>
        )}
      </div>

      <style>{`
        @keyframes pulse { 0%,100%{opacity:0.3} 50%{opacity:1} }
        input[type=date]::-webkit-calendar-picker-indicator { filter: invert(1); }
        ::-webkit-scrollbar { width: 6px; }
        ::-webkit-scrollbar-track { background: transparent; }
        ::-webkit-scrollbar-thumb { background: #30363d; border-radius: 3px; }
      `}</style>
    </div>
  );
}

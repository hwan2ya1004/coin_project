"""
app.py — 코인 매매 일지 AI 챗봇
실행: streamlit run app.py
"""
import warnings; warnings.filterwarnings("ignore")
import streamlit as st
import pandas as pd
import numpy as np
import os, json
from datetime import datetime
from coin_predictor_lgbm import fetch_ohlcv, make_features, COINS, MODEL_DIR
import joblib, ta

st.set_page_config(page_title="코인 매매 AI", page_icon="📒", layout="wide")

st.markdown("""
<style>
@import url('https://fonts.googleapis.com/css2?family=Noto+Sans+KR:wght@400;500;700&display=swap');
html,[class*="css"]{font-family:'Noto Sans KR',sans-serif}
.stApp{background:#0d1117;color:#e6edf3}
.block-container{padding:1.5rem 2rem;max-width:900px}
[data-testid="stSidebar"]{background:#161b22;border-right:1px solid #30363d}
.chat-user{background:#1f6feb;border-radius:16px 16px 4px 16px;padding:10px 14px;display:inline-block;max-width:75%;font-size:14px;line-height:1.6}
.chat-bot{background:#161b22;border:1px solid #30363d;border-radius:16px 16px 16px 4px;padding:10px 14px;display:inline-block;max-width:75%;font-size:14px;line-height:1.6;white-space:pre-wrap}
.card{background:#161b22;border:1px solid #30363d;border-radius:10px;padding:12px 16px;margin-bottom:8px}
.badge-up{background:#0d4429;color:#3fb950;border:1px solid #238636;border-radius:20px;padding:2px 10px;font-size:13px;font-weight:700}
.badge-down{background:#3d1212;color:#f85149;border:1px solid #b91c1c;border-radius:20px;padding:2px 10px;font-size:13px;font-weight:700}
.stButton>button{background:#238636;color:#fff;border:none;border-radius:8px;font-weight:600;width:100%}
</style>
""", unsafe_allow_html=True)

# ── 매매 기록 저장/로드 (JSON) ─────────────────
TRADE_FILE = os.path.join(os.environ.get("MODEL_DIR", "models"), "trades.json")

def load_trades():
    if os.path.exists(TRADE_FILE):
        with open(TRADE_FILE) as f:
            return json.load(f)
    return []

def save_trades(trades):
    os.makedirs(os.path.dirname(TRADE_FILE), exist_ok=True)
    with open(TRADE_FILE, "w") as f:
        json.dump(trades, f, ensure_ascii=False, indent=2)

# ── 수익 계산 ─────────────────────────────────
def calc_stats(trades):
    pairs, buys = [], {}
    for t in sorted(trades, key=lambda x: x["date"]):
        if t["type"] == "buy":
            buys.setdefault(t["coin"], []).append(t)
        else:
            buy = buys.get(t["coin"], [None])[0]
            if buy:
                buys[t["coin"]].pop(0)
                profit = (t["price"] - buy["price"]) * t["amount"]
                pct    = (t["price"] - buy["price"]) / buy["price"] * 100
                pairs.append({**t, "buyPrice": buy["price"], "profit": profit, "pct": pct,
                               "buyReason": buy["reason"], "sellReason": t["reason"],
                               "buyDate": buy["date"]})
    holding = [b for lst in buys.values() for b in lst]
    total   = sum(p["profit"] for p in pairs)
    wins    = sum(1 for p in pairs if p["profit"] > 0)
    wr      = wins / len(pairs) * 100 if pairs else 0
    avg_pct = sum(p["pct"] for p in pairs) / len(pairs) if pairs else 0
    return {"pairs": pairs, "holding": holding, "total": total,
            "winRate": wr, "avgPct": avg_pct, "count": len(pairs)}

def fmt(n):
    if n >= 1_000_000: return f"₩{n/1_000_000:.2f}M"
    if n >= 1_000:     return f"₩{n:,.0f}"
    return f"₩{n:.4f}"

# ── AI 예측 ────────────────────────────────────
@st.cache_data(ttl=3600)
def get_prediction(ticker):
    safe = ticker.replace("-","_")
    mf = f"{MODEL_DIR}/{safe}_model.pkl"
    sf = f"{MODEL_DIR}/{safe}_scaler.pkl"
    if not (os.path.exists(mf) and os.path.exists(sf)):
        return None
    model  = joblib.load(mf)
    scaler = joblib.load(sf)
    df     = fetch_ohlcv(ticker, count=500)
    ref_df = fetch_ohlcv("KRW-BTC", count=500) if ticker != "KRW-BTC" else None
    feat   = make_features(df, ref_df=ref_df)
    X      = feat.drop(columns=["label"])
    X_sc   = scaler.transform(X.iloc[[-1]])
    prob   = model.predict_proba(X_sc)[0][1]
    rsi    = ta.momentum.RSIIndicator(df["close"], 14).rsi().iloc[-1]
    macd_d = ta.trend.MACD(df["close"]).macd_diff().iloc[-1]
    chg    = df["close"].pct_change().iloc[-1] * 100
    return {"prob": prob, "rsi": rsi, "macd_diff": macd_d, "change": chg, "ticker": ticker}

# ── 챗봇 AI 응답 생성 ──────────────────────────
def build_context(trades):
    stats = calc_stats(trades)
    lines = [f"[{t['date']}] {'매수' if t['type']=='buy' else '매도'} {t['coin']} "
             f"가격:{fmt(t['price'])} 수량:{t['amount']} 이유:{t['reason']}"
             for t in trades]
    preds = []
    for coin in COINS:
        p = get_prediction(coin)
        if p:
            preds.append(f"{coin}: 상승확률 {p['prob']*100:.1f}% RSI:{p['rsi']:.1f} MACD:{p['macd_diff']:+.0f}")
    return f"""=== 매매 기록 ===
{chr(10).join(lines) if lines else '기록 없음'}

=== 통계 ===
완료거래:{stats['count']}건 | 총수익:{fmt(stats['total'])} | 승률:{stats['winRate']:.1f}% | 평균수익률:{stats['avgPct']:+.2f}%
보유중:{', '.join(h['coin'] for h in stats['holding']) or '없음'}

=== AI 예측 (오늘) ===
{chr(10).join(preds) if preds else '모델 없음 (학습 필요)'}"""

async def ask_claude(messages, trades):
    import requests as req
    ctx = build_context(trades)
    system = f"""당신은 코인 매매 일지를 분석하는 AI 트레이딩 어시스턴트입니다.
사용자의 실제 매매 기록과 AI 예측 데이터를 바탕으로 구체적으로 분석해주세요.
날짜/코인명/수익금액을 언급하고, 패턴의 장단점과 개선 방향을 솔직하게 알려주세요.
한국어로 답변하세요.

{ctx}"""
    history = [{"role": m["role"], "content": m["content"]}
               for m in messages if m["role"] != "system"]
    resp = req.post("https://api.anthropic.com/v1/messages",
        headers={"Content-Type": "application/json"},
        json={"model": "claude-sonnet-4-20250514", "max_tokens": 1000,
              "system": system, "messages": history}, timeout=30)
    data = resp.json()
    return data["content"][0]["text"] if resp.ok else f"오류: {data.get('error',{}).get('message','')}"

# ── 세션 초기화 ────────────────────────────────
if "trades"   not in st.session_state: st.session_state.trades   = load_trades()
if "messages" not in st.session_state: st.session_state.messages = [
    {"role":"assistant","content":"안녕하세요! 매매 일지 AI예요 😊\n\n매매 기록을 추가하거나, 아래 질문을 눌러보세요!"}]

# ── 사이드바 ────────────────────────────────────
with st.sidebar:
    st.markdown("## 📒 매매 일지 AI")
    stats = calc_stats(st.session_state.trades)
    for label, val, color in [
        ("총 수익", fmt(stats["total"]), "#3fb950" if stats["total"]>=0 else "#f85149"),
        ("승률",    f"{stats['winRate']:.0f}%", "#58a6ff"),
        ("거래 수", f"{stats['count']}건", "#8b949e"),
    ]:
        st.markdown(f"<div class='card' style='padding:8px 12px'>"
                    f"<span style='color:#8b949e;font-size:11px'>{label}</span>"
                    f"<span style='float:right;color:{color};font-weight:700;font-family:monospace'>{val}</span>"
                    f"</div>", unsafe_allow_html=True)

    st.divider()
    st.markdown("## ➕ 매매 기록 추가")
    with st.form("trade_form", clear_on_submit=True):
        c1, c2 = st.columns(2)
        date   = c1.date_input("날짜")
        coin   = c2.selectbox("코인", ["BTC","ETH","XRP","SOL","ADA","DOGE"])
        c3, c4 = st.columns(2)
        ttype  = c3.selectbox("유형", ["buy","sell"], format_func=lambda x: "매수" if x=="buy" else "매도")
        price  = c4.number_input("가격 (₩)", min_value=0, step=1000)
        amount = st.number_input("수량", min_value=0.0, step=0.001, format="%.4f")
        reason = st.text_input("이유", placeholder="예: RSI 과매도, 목표가 도달")
        if st.form_submit_button("💾 저장"):
            if price and amount and reason:
                new = {"id": int(datetime.now().timestamp()), "date": str(date),
                       "coin": coin, "type": ttype, "price": float(price),
                       "amount": float(amount), "reason": reason}
                st.session_state.trades.append(new)
                save_trades(st.session_state.trades)
                st.success("저장 완료!")
                st.rerun()

    st.divider()
    # AI 예측 요약
    st.markdown("## 🤖 오늘 예측")
    for coin in ["KRW-BTC","KRW-ETH","KRW-XRP"]:
        p = get_prediction(coin)
        if p:
            badge = "badge-up" if p["prob"] >= 0.5 else "badge-down"
            arrow = "📈" if p["prob"] >= 0.5 else "📉"
            st.markdown(
                f"<div style='display:flex;justify-content:space-between;align-items:center;"
                f"padding:6px 0;border-bottom:1px solid #21262d'>"
                f"<span style='font-weight:600;font-size:13px'>{coin.replace('KRW-','')}</span>"
                f"<span class='{badge}'>{arrow} {p['prob']*100:.0f}%</span></div>",
                unsafe_allow_html=True)
        else:
            st.markdown(f"<div style='color:#8b949e;font-size:12px;padding:4px 0'>"
                        f"{coin.replace('KRW-','')} — 모델 없음</div>", unsafe_allow_html=True)

# ── 메인: 챗봇 ─────────────────────────────────
st.markdown("### 💬 AI 분석 챗봇")

# 빠른 질문
quick = ["이번 달 수익률?", "왜 손해봤어?", "매매 패턴 분석해줘", "어떤 코인이 제일 수익?", "오늘 뭐 사야 해?"]
cols = st.columns(len(quick))
for i, q in enumerate(quick):
    if cols[i].button(q, key=f"q{i}"):
        st.session_state.messages.append({"role":"user","content":q})
        import asyncio
        reply = asyncio.run(ask_claude(st.session_state.messages, st.session_state.trades))
        st.session_state.messages.append({"role":"assistant","content":reply})
        st.rerun()

st.markdown("<div style='height:8px'></div>", unsafe_allow_html=True)

# 메시지 렌더링
for m in st.session_state.messages:
    if m["role"] == "user":
        st.markdown(f"<div style='text-align:right;margin:6px 0'><span class='chat-user'>{m['content']}</span></div>", unsafe_allow_html=True)
    else:
        st.markdown(f"<div style='margin:6px 0'><span class='chat-bot'>{m['content']}</span></div>", unsafe_allow_html=True)

# 입력창
with st.form("chat_form", clear_on_submit=True):
    c1, c2 = st.columns([5,1])
    user_input = c1.text_input("", placeholder="매매 기록에 대해 뭐든 물어보세요...", label_visibility="collapsed")
    sent = c2.form_submit_button("전송")
    if sent and user_input.strip():
        st.session_state.messages.append({"role":"user","content":user_input.strip()})
        import asyncio
        reply = asyncio.run(ask_claude(st.session_state.messages, st.session_state.trades))
        st.session_state.messages.append({"role":"assistant","content":reply})
        st.rerun()

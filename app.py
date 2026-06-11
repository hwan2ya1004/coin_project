"""
app.py — 코인 매매 일지 AI 챗봇
실행: streamlit run app.py
"""
import warnings; warnings.filterwarnings("ignore")
from dotenv import load_dotenv
load_dotenv()  # .env 파일에서 환경변수 로드 (로컬 개발용)
import streamlit as st
import pandas as pd
import numpy as np
import os, json
from datetime import datetime
from coin_predictor_lgbm import fetch_ohlcv, make_features, COINS, MODEL_DIR
import joblib, ta
import pyupbit

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
.price-card{background:#161b22;border:1px solid #30363d;border-radius:8px;padding:8px 12px;margin-bottom:6px}
</style>
""", unsafe_allow_html=True)

# ── 환경변수 ───────────────────────────────────
UPBIT_ACCESS  = os.environ.get("UPBIT_ACCESS_KEY", "")
UPBIT_SECRET  = os.environ.get("UPBIT_SECRET_KEY", "")
NCP_URL       = os.environ.get("NCP_PROXY_URL", "")       # 예: http://1.2.3.4:8080
NCP_SECRET    = os.environ.get("NCP_API_SECRET", "changeme")

# ── NCP 프록시 헬퍼 ───────────────────────────
def ncp_get(path, params=None):
    """NCP 서버 GET 요청"""
    if not NCP_URL:
        return None
    try:
        import requests
        r = requests.get(
            f"{NCP_URL.rstrip('/')}{path}",
            headers={"X-API-Secret": NCP_SECRET},
            params=params,
            timeout=10,
        )
        return r.json() if r.ok else None
    except Exception:
        return None

def ncp_post(path, data=None):
    """NCP 서버 POST 요청"""
    if not NCP_URL:
        return None
    try:
        import requests
        r = requests.post(
            f"{NCP_URL.rstrip('/')}{path}",
            headers={"X-API-Secret": NCP_SECRET, "Content-Type": "application/json"},
            json=data or {},
            timeout=10,
        )
        return r.json() if r.ok else None
    except Exception:
        return None

# ── 업비트 API 초기화 (직접 연결 — 로컬용) ────
@st.cache_resource
def get_upbit_client():
    if UPBIT_ACCESS and UPBIT_SECRET:
        return pyupbit.Upbit(UPBIT_ACCESS, UPBIT_SECRET)
    return None

# ── 업비트 실시간 시세 조회 ────────────────────
@st.cache_data(ttl=30)
def get_current_prices():
    tickers = ["KRW-BTC", "KRW-ETH", "KRW-XRP", "KRW-SOL", "KRW-ADA"]
    try:
        prices = pyupbit.get_current_price(tickers)
        return prices if prices else {}
    except Exception:
        return {}

@st.cache_data(ttl=60)
def get_ticker_info():
    """24시간 변동률 포함 시세 정보"""
    tickers = ["KRW-BTC", "KRW-ETH", "KRW-XRP", "KRW-SOL", "KRW-ADA"]
    try:
        import requests
        markets = ",".join(tickers)
        resp = requests.get(
            f"https://api.upbit.com/v1/ticker?markets={markets}",
            headers={"Accept": "application/json"},
            timeout=5
        )
        if resp.ok:
            data = resp.json()
            return {item["market"]: item for item in data}
    except Exception:
        pass
    return {}

# ── 업비트 잔고 조회 (NCP 프록시 우선) ────────
@st.cache_data(ttl=60)
def get_balances():
    # NCP 프록시가 설정된 경우 NCP를 통해 조회
    if NCP_URL:
        result = ncp_get("/balance")
        if result is None:
            return {"error": "NCP 서버 연결 실패"}
        if "error" in result:
            return {"error": result["error"]}
        return result.get("balances", [])

    # NCP 없으면 직접 연결 시도
    upbit = get_upbit_client()
    if not upbit:
        return []
    try:
        balances = upbit.get_balances()
        if isinstance(balances, dict) and "error" in balances:
            return {"error": balances["error"].get("message", "알 수 없는 오류")}
        if not balances:
            return []
        return [b for b in balances if isinstance(b, dict) and float(b.get("balance", 0)) > 0]
    except Exception as e:
        return {"error": str(e)}

# ── 자동매매 설정 조회 (NCP) ──────────────────
@st.cache_data(ttl=30)
def get_trade_config():
    if not NCP_URL:
        return None
    return ncp_get("/trade/config")

# ── 매매 로그 조회 (NCP) ──────────────────────
@st.cache_data(ttl=30)
def get_trade_log():
    if not NCP_URL:
        return []
    result = ncp_get("/trade/log", params={"limit": 20})
    return result.get("logs", []) if result else []

# ── 미확인 매매 알림 조회 ──────────────────────
def get_unnotified_trades():
    """notified=False 인 최신 매매 내역 반환"""
    logs = get_trade_log()
    return [l for l in logs if not l.get("notified", True)]

# ── 매매 알림 확인 처리 (NCP에 PATCH) ─────────
def mark_trades_notified():
    """NCP 서버에 알림 확인 처리 요청"""
    ncp_post("/trade/notify")

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

def fmt_price(n):
    if n is None: return "—"
    if n >= 1_000_000: return f"₩{n/1_000_000:.2f}M"
    if n >= 1_000:     return f"₩{n:,.0f}"
    if n >= 1:         return f"₩{n:,.2f}"
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

    ticker_info = get_ticker_info()
    price_lines = []
    for ticker, info in ticker_info.items():
        chg = info.get("signed_change_rate", 0) * 100
        price_lines.append(f"{ticker}: {fmt_price(info.get('trade_price'))} ({chg:+.2f}%)")

    return f"""=== 매매 기록 ===
{chr(10).join(lines) if lines else '기록 없음'}

=== 통계 ===
완료거래:{stats['count']}건 | 총수익:{fmt(stats['total'])} | 승률:{stats['winRate']:.1f}% | 평균수익률:{stats['avgPct']:+.2f}%
보유중:{', '.join(h['coin'] for h in stats['holding']) or '없음'}

=== 실시간 시세 ===
{chr(10).join(price_lines) if price_lines else '시세 조회 실패'}

=== AI 예측 (오늘) ===
{chr(10).join(preds) if preds else '모델 없음 (학습 필요)'}"""

def ask_groq(messages, trades):
    import requests as req
    api_key = os.environ.get("GROQ_API_KEY", "")
    if not api_key:
        return "오류: GROQ_API_KEY 환경변수가 설정되지 않았습니다.\n\nhttps://console.groq.com 에서 무료 API 키를 발급받으세요."
    ctx = build_context(trades)
    system = f"""당신은 코인 매매 일지를 분석하는 AI 트레이딩 어시스턴트입니다.
사용자의 실제 매매 기록과 AI 예측 데이터를 바탕으로 구체적으로 분석해주세요.
날짜/코인명/수익금액을 언급하고, 패턴의 장단점과 개선 방향을 솔직하게 알려주세요.
한국어로 답변하세요.

{ctx}"""
    history = [{"role": m["role"], "content": m["content"]}
               for m in messages if m["role"] != "system"]
    resp = req.post("https://api.groq.com/openai/v1/chat/completions",
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {api_key}"},
        json={"model": "llama-3.3-70b-versatile", "max_tokens": 1000,
              "messages": [{"role": "system", "content": system}] + history}, timeout=30)
    data = resp.json()
    if resp.ok:
        return data["choices"][0]["message"]["content"]
    return f"오류: {data.get('error', {}).get('message', '알 수 없는 오류')}"

# ── 자동매매 알림 메시지 생성 ──────────────────
def build_trade_alert_message(new_trades):
    """새 매매 내역을 챗봇 알림 메시지로 변환"""
    lines = []
    for t in new_trades:
        action  = t.get("action", "")
        ticker  = t.get("ticker", "").replace("KRW-", "")
        price   = t.get("price", 0)
        prob    = t.get("prob", 0)
        krw     = t.get("krw", 0)
        amount  = t.get("amount", 0)
        status  = t.get("status", "")
        time_str = t.get("time", "")[:16]
        trade_type = t.get("type", "")

        icon = "🟢" if action == "매수" else "🔴"
        if action == "매수":
            lines.append(
                f"{icon} **{action}** {ticker}  \n"
                f"  - 시각: {time_str}  \n"
                f"  - 주문가: {fmt_price(price)} ({trade_type})  \n"
                f"  - 금액: ₩{krw:,}  \n"
                f"  - AI 상승확률: {prob}%  \n"
                f"  - 상태: {status}"
            )
        else:
            lines.append(
                f"{icon} **{action}** {ticker}  \n"
                f"  - 시각: {time_str}  \n"
                f"  - 체결가: {fmt_price(price)}  \n"
                f"  - 수량: {amount}  \n"
                f"  - AI 상승확률: {prob}%  \n"
                f"  - 상태: {status}"
            )

    summary = "\n\n".join(lines)
    return f"🔔 **자동매매 알림** — 새로운 매매가 실행되었습니다!\n\n{summary}\n\n이 매매에 대해 분석이 필요하면 말씀해주세요."

# ── 세션 초기화 ────────────────────────────────
if "trades"        not in st.session_state: st.session_state.trades        = load_trades()
if "messages"      not in st.session_state: st.session_state.messages      = [
    {"role":"assistant","content":"안녕하세요! 매매 일지 AI예요 😊\n\n매매 기록을 추가하거나, 아래 질문을 눌러보세요!"}]
if "last_log_time" not in st.session_state: st.session_state.last_log_time = ""

# ── 새 매매 알림 체크 (NCP 연동) ──────────────
if NCP_URL:
    new_trades = get_unnotified_trades()
    if new_trades:
        # 가장 최신 매매 시각 확인 (중복 알림 방지)
        latest_time = new_trades[0].get("time", "")
        if latest_time != st.session_state.last_log_time:
            st.session_state.last_log_time = latest_time
            # 챗봇에 자동 알림 메시지 추가
            alert_msg = build_trade_alert_message(new_trades)
            st.session_state.messages.append({"role": "assistant", "content": alert_msg})
            # NCP 서버에 알림 확인 처리
            mark_trades_notified()
            st.cache_data.clear()

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

    # ── 업비트 실시간 시세 ──────────────────────
    st.markdown("## 📊 실시간 시세")
    ticker_info = get_ticker_info()
    if ticker_info:
        coin_names = {"KRW-BTC": "BTC", "KRW-ETH": "ETH", "KRW-XRP": "XRP",
                      "KRW-SOL": "SOL", "KRW-ADA": "ADA"}
        for ticker, info in ticker_info.items():
            price     = info.get("trade_price", 0)
            chg       = info.get("signed_change_rate", 0) * 100
            chg_price = info.get("signed_change_price", 0)
            color     = "#3fb950" if chg >= 0 else "#f85149"
            arrow     = "▲" if chg >= 0 else "▼"
            name      = coin_names.get(ticker, ticker.replace("KRW-",""))
            st.markdown(
                f"<div class='price-card'>"
                f"<div style='display:flex;justify-content:space-between;align-items:center'>"
                f"<span style='font-weight:700;font-size:13px'>{name}</span>"
                f"<span style='color:{color};font-size:12px;font-weight:700'>{arrow} {abs(chg):.2f}%</span>"
                f"</div>"
                f"<div style='font-family:monospace;font-size:14px;font-weight:700;margin-top:2px'>{fmt_price(price)}</div>"
                f"<div style='font-size:11px;color:{color}'>{arrow} {fmt_price(abs(chg_price))}</div>"
                f"</div>", unsafe_allow_html=True)
        if st.button("🔄 시세 새로고침", key="refresh_price"):
            st.cache_data.clear()
            st.rerun()
    else:
        st.markdown("<div style='color:#8b949e;font-size:12px'>시세 조회 중...</div>", unsafe_allow_html=True)

    st.divider()

    # ── 업비트 잔고 ─────────────────────────────
    st.markdown("## 💰 업비트 잔고")
    if NCP_URL or (UPBIT_ACCESS and UPBIT_SECRET):
        balances = get_balances()
        if isinstance(balances, dict) and "error" in balances:
            st.markdown(f"<div style='color:#f85149;font-size:12px'>⚠️ 조회 실패:<br>{balances['error']}</div>", unsafe_allow_html=True)
        elif balances:
            prices = get_current_prices()
            for b in balances:
                currency = b.get("currency", "")
                balance  = float(b.get("balance", 0))
                avg_buy  = float(b.get("avg_buy_price", 0))
                ticker   = f"KRW-{currency}" if currency != "KRW" else "KRW"
                if currency == "KRW":
                    st.markdown(
                        f"<div class='price-card'>"
                        f"<span style='font-weight:700'>KRW</span>"
                        f"<span style='float:right;font-family:monospace'>{fmt(balance)}</span>"
                        f"</div>", unsafe_allow_html=True)
                else:
                    cur_price = prices.get(ticker, 0) if prices else 0
                    if cur_price and avg_buy:
                        pnl_pct = (cur_price - avg_buy) / avg_buy * 100
                        color   = "#3fb950" if pnl_pct >= 0 else "#f85149"
                        pnl_str = f"<span style='color:{color};font-size:11px'>{pnl_pct:+.2f}%</span>"
                    else:
                        pnl_str = ""
                    st.markdown(
                        f"<div class='price-card'>"
                        f"<div style='display:flex;justify-content:space-between'>"
                        f"<span style='font-weight:700;font-size:13px'>{currency}</span>"
                        f"{pnl_str}</div>"
                        f"<div style='font-size:12px;color:#8b949e'>수량: {balance:.4f}</div>"
                        f"<div style='font-size:12px;color:#8b949e'>평균매수: {fmt_price(avg_buy)}</div>"
                        f"</div>", unsafe_allow_html=True)
        else:
            st.markdown("<div style='color:#8b949e;font-size:12px'>잔고 없음</div>", unsafe_allow_html=True)
    else:
        st.markdown("<div style='color:#8b949e;font-size:12px'>업비트 API 키 또는 NCP 서버 미설정</div>", unsafe_allow_html=True)

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

# ── 미확인 매매 알림 배너 (메인 화면 상단) ────
if NCP_URL and new_trades:
    count = len(new_trades)
    actions = [t.get("action","") for t in new_trades]
    buy_count  = actions.count("매수")
    sell_count = actions.count("매도")
    parts = []
    if buy_count:  parts.append(f"매수 {buy_count}건")
    if sell_count: parts.append(f"매도 {sell_count}건")
    summary_str = " · ".join(parts)
    tickers_str = ", ".join(set(t.get("ticker","").replace("KRW-","") for t in new_trades))
    st.markdown(
        f"<div style='background:#0d2137;border:1px solid #1f6feb;border-radius:10px;"
        f"padding:12px 16px;margin-bottom:12px;display:flex;align-items:center;gap:12px'>"
        f"<span style='font-size:20px'>🔔</span>"
        f"<div>"
        f"<div style='color:#58a6ff;font-weight:700;font-size:14px'>자동매매 실행됨 — {summary_str}</div>"
        f"<div style='color:#8b949e;font-size:12px;margin-top:2px'>코인: {tickers_str} | 💬 AI 분석 챗봇 탭에서 상세 내용을 확인하세요</div>"
        f"</div>"
        f"</div>",
        unsafe_allow_html=True
    )

# ── 메인 탭 ────────────────────────────────────
tab_chat, tab_auto = st.tabs(["💬 AI 분석 챗봇", "⚙️ 자동매매"])

# ════════════════════════════════════════════════
# 탭 1: AI 챗봇
# ════════════════════════════════════════════════
with tab_chat:
    quick = ["이번 달 수익률?", "왜 손해봤어?", "매매 패턴 분석해줘", "어떤 코인이 제일 수익?", "오늘 뭐 사야 해?"]
    cols = st.columns(len(quick))
    for i, q in enumerate(quick):
        if cols[i].button(q, key=f"q{i}"):
            st.session_state.messages.append({"role":"user","content":q})
            reply = ask_groq(st.session_state.messages, st.session_state.trades)
            st.session_state.messages.append({"role":"assistant","content":reply})
            st.rerun()

    st.markdown("<div style='height:8px'></div>", unsafe_allow_html=True)

    for m in st.session_state.messages:
        if m["role"] == "user":
            st.markdown(f"<div style='text-align:right;margin:6px 0'><span class='chat-user'>{m['content']}</span></div>", unsafe_allow_html=True)
        else:
            st.markdown(f"<div style='margin:6px 0'><span class='chat-bot'>{m['content']}</span></div>", unsafe_allow_html=True)

    with st.form("chat_form", clear_on_submit=True):
        c1, c2 = st.columns([5,1])
        user_input = c1.text_input("메시지 입력", placeholder="매매 기록에 대해 뭐든 물어보세요...", label_visibility="collapsed")
        sent = c2.form_submit_button("전송")
        if sent and user_input.strip():
            st.session_state.messages.append({"role":"user","content":user_input.strip()})
            reply = ask_groq(st.session_state.messages, st.session_state.trades)
            st.session_state.messages.append({"role":"assistant","content":reply})
            st.rerun()

# ════════════════════════════════════════════════
# 탭 2: 자동매매
# ════════════════════════════════════════════════
with tab_auto:
    if not NCP_URL:
        st.warning("⚠️ NCP 서버가 설정되지 않았습니다.\n\n환경변수 `NCP_PROXY_URL`에 NCP 서버 주소를 입력하세요.\n예: `http://1.2.3.4:8080`")
    else:
        col_cfg, col_log = st.columns([1, 1])

        # ── 자동매매 설정 ──────────────────────
        with col_cfg:
            st.markdown("### ⚙️ 자동매매 설정")

            cfg = get_trade_config() or {}
            enabled       = cfg.get("enabled", False)
            top_n         = cfg.get("top_n_coins", 20)
            buy_thr       = cfg.get("buy_threshold", 60.0)
            sell_thr      = cfg.get("sell_threshold", 40.0)
            buy_discount  = cfg.get("buy_discount", 2.0)
            buy_ratio     = cfg.get("buy_ratio", 10.0)

            # ON/OFF 토글
            new_enabled = st.toggle("자동매매 활성화", value=enabled)
            if new_enabled:
                st.markdown("<div style='color:#3fb950;font-size:12px;margin-bottom:8px'>🟢 자동매매 활성화 중 — 매일 새벽 2시 실행</div>", unsafe_allow_html=True)
            else:
                st.markdown("<div style='color:#8b949e;font-size:12px;margin-bottom:8px'>⚫ 자동매매 비활성화</div>", unsafe_allow_html=True)

            with st.form("auto_trade_form"):
                new_top_n = st.slider(
                    "대상 코인 수 (거래량 상위)",
                    min_value=5, max_value=50, value=int(top_n), step=5,
                    help="업비트 KRW 마켓 거래량 상위 N개 코인을 자동 선택합니다"
                )
                new_buy_thr = st.slider(
                    "매수 기준 상승확률 (%)",
                    min_value=50.0, max_value=90.0, value=float(buy_thr), step=1.0,
                    help="AI 예측 상승확률이 이 값 이상이면 매수 주문"
                )
                new_sell_thr = st.slider(
                    "매도 기준 상승확률 (%)",
                    min_value=10.0, max_value=50.0, value=float(sell_thr), step=1.0,
                    help="AI 예측 상승확률이 이 값 이하이면 시장가 매도"
                )
                new_discount = st.slider(
                    "매수 할인율 (현재가 대비 %)",
                    min_value=0.5, max_value=10.0, value=float(buy_discount), step=0.5,
                    help="현재가보다 이 비율만큼 낮은 가격으로 지정가 매수 주문"
                )
                new_ratio = st.slider(
                    "1회 매수 비율 (KRW 잔고의 %)",
                    min_value=1.0, max_value=50.0, value=float(buy_ratio), step=1.0,
                    help="보유 KRW 잔고의 이 비율만큼 1회 매수에 사용"
                )

                # 설정 요약
                st.markdown(
                    f"<div style='background:#0d2137;border:1px solid #1f6feb;border-radius:8px;"
                    f"padding:10px 12px;font-size:12px;margin-top:8px'>"
                    f"<b style='color:#58a6ff'>설정 요약</b><br>"
                    f"상위 {new_top_n}개 코인 | 매수 ≥{new_buy_thr:.0f}% | 매도 ≤{new_sell_thr:.0f}%<br>"
                    f"지정가 -{new_discount:.1f}% | 잔고의 {new_ratio:.0f}% 사용"
                    f"</div>", unsafe_allow_html=True
                )

                if st.form_submit_button("💾 설정 저장"):
                    result = ncp_post("/trade/config", {
                        "enabled":        new_enabled,
                        "top_n_coins":    new_top_n,
                        "buy_threshold":  new_buy_thr,
                        "sell_threshold": new_sell_thr,
                        "buy_discount":   new_discount,
                        "buy_ratio":      new_ratio,
                    })
                    if result and result.get("ok"):
                        st.success("✅ 설정 저장 완료!")
                        st.cache_data.clear()
                        st.rerun()
                    else:
                        st.error("❌ 저장 실패 — NCP 서버 연결을 확인하세요")

            # 수동 실행 버튼
            st.markdown("---")
            c1, c2 = st.columns(2)
            if c1.button("🚀 지금 매매 실행", key="run_trade"):
                result = ncp_post("/trade/run")
                if result and result.get("ok"):
                    st.success("자동매매 시작됨!")
                    st.cache_data.clear()
                else:
                    st.error("실행 실패")
            if c2.button("🧠 지금 학습 실행", key="run_train"):
                result = ncp_post("/train")
                if result and result.get("ok"):
                    st.success("학습 시작됨! (백그라운드)")
                else:
                    st.error("실행 실패")

        # ── 매매 로그 ──────────────────────────
        with col_log:
            st.markdown("### 📋 자동매매 로그")

            logs = get_trade_log()
            if logs:
                for log_entry in logs:
                    action  = log_entry.get("action", "")
                    ticker  = log_entry.get("ticker", "")
                    price   = log_entry.get("price", 0)
                    prob    = log_entry.get("prob", 0)
                    status  = log_entry.get("status", "")
                    time_str = log_entry.get("time", "")[:16]
                    trade_type = log_entry.get("type", "")
                    krw     = log_entry.get("krw", 0)

                    action_color = "#3fb950" if action == "매수" else "#f85149"
                    status_icon  = "✅" if status in ("체결", "주문완료") else "❌"

                    krw_str = f" | ₩{krw:,}" if krw else ""
                    st.markdown(
                        f"<div class='price-card' style='margin-bottom:6px'>"
                        f"<div style='display:flex;justify-content:space-between;align-items:center'>"
                        f"<span style='color:{action_color};font-weight:700;font-size:13px'>{action}</span>"
                        f"<span style='font-weight:700;font-size:13px'>{ticker.replace('KRW-','')}</span>"
                        f"<span style='font-size:11px;color:#8b949e'>{time_str}</span>"
                        f"</div>"
                        f"<div style='font-size:12px;color:#8b949e;margin-top:3px'>"
                        f"{trade_type} | {fmt_price(price)}{krw_str}"
                        f"</div>"
                        f"<div style='font-size:11px;margin-top:2px'>"
                        f"<span style='color:#58a6ff'>확률 {prob}%</span>"
                        f"<span style='margin-left:8px'>{status_icon} {status}</span>"
                        f"</div>"
                        f"</div>", unsafe_allow_html=True)

                if st.button("🔄 로그 새로고침", key="refresh_log"):
                    st.cache_data.clear()
                    st.rerun()
            else:
                st.markdown("<div style='color:#8b949e;font-size:13px;padding:20px 0'>아직 자동매매 기록이 없습니다.<br>설정 후 활성화하면 매일 새벽 2시에 실행됩니다.</div>", unsafe_allow_html=True)

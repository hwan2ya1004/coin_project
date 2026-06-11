"""
ncp_server.py — NCP 고정 IP 서버 (Flask API)
역할:
  - 업비트 잔고 조회 프록시 (고정 IP로 인증 통과)
  - AI 예측 기반 자동매매 실행
  - 매일 새벽 2시 모델 학습 (스케줄러)

실행: python ncp_server.py
포트: 8080
"""

import os
import json
import logging
import threading
import time
from datetime import datetime

from flask import Flask, jsonify, request
from dotenv import load_dotenv
import pyupbit
import requests as req
import schedule

load_dotenv()

# ── 로깅 설정 ──────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler("ncp_server.log", encoding="utf-8"),
    ],
)
log = logging.getLogger(__name__)

app = Flask(__name__)

# ── 환경변수 ───────────────────────────────────
UPBIT_ACCESS = os.environ.get("UPBIT_ACCESS_KEY", "")
UPBIT_SECRET = os.environ.get("UPBIT_SECRET_KEY", "")
API_SECRET   = os.environ.get("NCP_API_SECRET", "changeme")  # Render → NCP 인증용
MODEL_DIR    = os.environ.get("MODEL_DIR", "models")

# ── 설정 파일 경로 ─────────────────────────────
CONFIG_FILE = "trade_config.json"
TRADE_LOG   = "trade_log.json"

# ── 기본 자동매매 설정 ─────────────────────────
DEFAULT_CONFIG = {
    "enabled":        False,
    "top_n_coins":    20,      # 거래량 상위 N개 코인 대상
    "buy_threshold":  60.0,    # 매수 기준 상승확률 (%)
    "sell_threshold": 40.0,    # 매도 기준 상승확률 (%)
    "buy_discount":   2.0,     # 매수 할인율 (현재가 대비 %)
    "buy_ratio":      10.0,    # 1회 매수에 사용할 KRW 잔고 비율 (%)
}


# ── 설정 로드/저장 ─────────────────────────────
def load_config():
    if os.path.exists(CONFIG_FILE):
        with open(CONFIG_FILE, encoding="utf-8") as f:
            cfg = json.load(f)
            for k, v in DEFAULT_CONFIG.items():
                cfg.setdefault(k, v)
            return cfg
    return DEFAULT_CONFIG.copy()


def save_config(cfg):
    with open(CONFIG_FILE, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)


# ── 매매 로그 로드/저장 ────────────────────────
def load_trade_log():
    if os.path.exists(TRADE_LOG):
        with open(TRADE_LOG, encoding="utf-8") as f:
            return json.load(f)
    return []


def append_trade_log(entry):
    logs = load_trade_log()
    logs.insert(0, entry)
    logs = logs[:200]  # 최근 200건만 유지
    with open(TRADE_LOG, "w", encoding="utf-8") as f:
        json.dump(logs, f, ensure_ascii=False, indent=2)


# ── 업비트 클라이언트 ──────────────────────────
def get_upbit():
    if UPBIT_ACCESS and UPBIT_SECRET:
        return pyupbit.Upbit(UPBIT_ACCESS, UPBIT_SECRET)
    return None


# ── API 인증 미들웨어 ──────────────────────────
def check_auth():
    secret = request.headers.get("X-API-Secret", "")
    if secret != API_SECRET:
        return jsonify({"error": "Unauthorized"}), 401
    return None


# ── 거래량 상위 코인 조회 ──────────────────────
def get_top_krw_tickers(n=20):
    """업비트 KRW 마켓 거래량 상위 N개 티커 반환"""
    try:
        resp = req.get(
            "https://api.upbit.com/v1/market/all?isDetails=false",
            headers={"Accept": "application/json"},
            timeout=5,
        )
        markets = [m["market"] for m in resp.json() if m["market"].startswith("KRW-")]

        # 거래대금 조회 (100개씩 청크)
        ticker_data = []
        for i in range(0, len(markets), 100):
            chunk = markets[i:i + 100]
            r = req.get(
                f"https://api.upbit.com/v1/ticker?markets={','.join(chunk)}",
                headers={"Accept": "application/json"},
                timeout=5,
            )
            ticker_data.extend(r.json())

        # 24시간 거래대금 기준 정렬
        ticker_data.sort(key=lambda x: x.get("acc_trade_price_24h", 0), reverse=True)
        return [t["market"] for t in ticker_data[:n]]
    except Exception as e:
        log.error(f"거래량 상위 코인 조회 실패: {e}")
        return ["KRW-BTC", "KRW-ETH", "KRW-XRP", "KRW-SOL", "KRW-ADA"]


# ── 업비트 호가 단위 조정 ──────────────────────
def adjust_price_unit(price):
    """업비트 호가 단위에 맞게 가격 조정"""
    if price >= 2_000_000:   unit = 1000
    elif price >= 1_000_000: unit = 500
    elif price >= 500_000:   unit = 100
    elif price >= 100_000:   unit = 50
    elif price >= 10_000:    unit = 10
    elif price >= 1_000:     unit = 1
    elif price >= 100:       unit = 0.1
    elif price >= 10:        unit = 0.01
    elif price >= 1:         unit = 0.001
    else:                    unit = 0.0001
    return round(round(price / unit) * unit, 10)


# ── AI 예측 ────────────────────────────────────
def get_prediction(ticker):
    """저장된 모델로 예측, 없으면 None"""
    try:
        import joblib
        from coin_predictor_lgbm import fetch_ohlcv, make_features

        safe = ticker.replace("-", "_")
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
        return {"ticker": ticker, "prob": round(prob * 100, 2)}
    except Exception as e:
        log.warning(f"{ticker} 예측 실패: {e}")
        return None


# ── 자동매매 실행 ──────────────────────────────
def run_auto_trade():
    cfg = load_config()
    if not cfg.get("enabled"):
        log.info("자동매매 비활성화 상태 — 스킵")
        return

    upbit = get_upbit()
    if not upbit:
        log.error("업비트 클라이언트 초기화 실패")
        return

    log.info("=" * 50)
    log.info(f"자동매매 시작: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    log.info("=" * 50)

    top_n      = cfg.get("top_n_coins", 20)
    buy_thr    = cfg.get("buy_threshold", 60.0)
    sell_thr   = cfg.get("sell_threshold", 40.0)
    discount   = cfg.get("buy_discount", 2.0)
    buy_ratio  = cfg.get("buy_ratio", 10.0)  # KRW 잔고의 몇 % 사용

    # 1. 미체결 주문 취소
    try:
        orders = upbit.get_order("", state="wait")
        if orders and isinstance(orders, list):
            for o in orders:
                upbit.cancel_order(o["uuid"])
                log.info(f"미체결 주문 취소: {o.get('market')} {o.get('uuid', '')[:8]}")
    except Exception as e:
        log.warning(f"미체결 주문 취소 실패: {e}")

    # 2. 현재 잔고 조회
    try:
        balances = upbit.get_balances()
        if isinstance(balances, dict):
            log.error(f"잔고 조회 실패: {balances}")
            return

        krw_balance = 0.0
        holdings = {}
        for b in balances:
            if not isinstance(b, dict):
                continue
            cur = b.get("currency", "")
            bal = float(b.get("balance", 0))
            if cur == "KRW":
                krw_balance = bal
            elif bal > 0:
                holdings[f"KRW-{cur}"] = {
                    "balance": bal,
                    "avg_buy_price": float(b.get("avg_buy_price", 0)),
                }
        log.info(f"KRW 잔고: {krw_balance:,.0f}원 | 보유 코인: {list(holdings.keys())}")
    except Exception as e:
        log.error(f"잔고 조회 실패: {e}")
        return

    # 3. 거래량 상위 코인 조회
    tickers = get_top_krw_tickers(top_n)
    log.info(f"대상 코인 {len(tickers)}개: {tickers[:5]}...")

    # 4. 예측 및 매매
    for ticker in tickers:
        try:
            pred = get_prediction(ticker)
            if pred is None:
                continue

            prob      = pred["prob"]
            cur_price = pyupbit.get_current_price(ticker)
            if not cur_price:
                continue

            # ── 매도 판단 ──────────────────────
            if ticker in holdings and prob <= sell_thr:
                bal    = holdings[ticker]["balance"]
                result = upbit.sell_market_order(ticker, bal)
                entry  = {
                    "time":   datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                    "ticker": ticker,
                    "action": "매도",
                    "type":   "시장가",
                    "price":  cur_price,
                    "amount": bal,
                    "prob":   prob,
                    "status": "체결" if result and "uuid" in result else "실패",
                    "uuid":   result.get("uuid", "") if result else "",
                }
                append_trade_log(entry)
                log.info(f"[매도] {ticker} | 확률:{prob}% | 수량:{bal} | 상태:{entry['status']}")

            # ── 매수 판단 ──────────────────────
            elif ticker not in holdings and prob >= buy_thr:
                # 잔고의 buy_ratio% 사용 (최소 5,000원)
                buy_amount = krw_balance * (buy_ratio / 100) * 0.9995  # 수수료 고려
                if buy_amount < 5000:
                    log.info(f"[매수 스킵] {ticker} — 매수금액 부족: {buy_amount:,.0f}원 (잔고:{krw_balance:,.0f}원의 {buy_ratio}%)")
                    continue

                buy_price = adjust_price_unit(cur_price * (1 - discount / 100))
                volume    = buy_amount / buy_price

                result = upbit.buy_limit_order(ticker, buy_price, volume)
                entry  = {
                    "time":   datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                    "ticker": ticker,
                    "action": "매수",
                    "type":   f"지정가(-{discount}%)",
                    "price":  buy_price,
                    "amount": round(volume, 8),
                    "krw":    round(buy_amount),
                    "prob":   prob,
                    "status": "주문완료" if result and "uuid" in result else "실패",
                    "uuid":   result.get("uuid", "") if result else "",
                }
                append_trade_log(entry)
                krw_balance -= buy_amount  # 사용한 금액 차감
                log.info(f"[매수] {ticker} | 확률:{prob}% | 주문가:{buy_price:,} | 금액:{buy_amount:,.0f}원 | 상태:{entry['status']}")

        except Exception as e:
            log.error(f"{ticker} 매매 오류: {e}")

    log.info(f"자동매매 완료: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")


# ── 모델 학습 ──────────────────────────────────
def run_training():
    log.info("모델 학습 시작...")
    try:
        from coin_predictor_lgbm import fetch_ohlcv, make_features, train_model

        cfg    = load_config()
        top_n  = cfg.get("top_n_coins", 20)
        tickers = get_top_krw_tickers(top_n)

        os.makedirs(MODEL_DIR, exist_ok=True)
        btc_df = fetch_ohlcv("KRW-BTC", count=500)

        results = {}
        for ticker in tickers:
            try:
                df     = fetch_ohlcv(ticker, count=500)
                ref_df = btc_df if ticker != "KRW-BTC" else None
                feat   = make_features(df, ref_df=ref_df)
                _, _, acc, _ = train_model(feat, ticker)
                results[ticker] = round(acc * 100, 2)
                log.info(f"  {ticker} 완료 — 정확도: {acc*100:.2f}%")
            except Exception as e:
                log.warning(f"  {ticker} 학습 실패: {e}")

        log.info(f"학습 완료: {len(results)}개 코인")
        return results
    except Exception as e:
        log.error(f"학습 오류: {e}")
        return {}


# ── 스케줄러 (매일 UTC 17:00 = KST 02:00) ─────
def run_scheduler():
    def daily_job():
        log.info("스케줄 작업 시작: 학습 → 자동매매")
        run_training()
        run_auto_trade()

    schedule.every().day.at("17:00").do(daily_job)
    log.info("스케줄러 시작 — 매일 UTC 17:00 (KST 02:00) 학습+매매 실행")
    while True:
        schedule.run_pending()
        time.sleep(60)


# ════════════════════════════════════════════════
# Flask API 엔드포인트
# ════════════════════════════════════════════════

@app.route("/health", methods=["GET"])
def health():
    return jsonify({"status": "ok", "time": datetime.now().isoformat()})


@app.route("/balance", methods=["GET"])
def balance():
    auth = check_auth()
    if auth:
        return auth

    upbit = get_upbit()
    if not upbit:
        return jsonify({"error": "업비트 API 키 미설정"}), 500

    try:
        balances = upbit.get_balances()
        if isinstance(balances, dict) and "error" in balances:
            return jsonify({"error": balances["error"].get("message", "조회 실패")}), 403

        result = [b for b in balances if isinstance(b, dict) and float(b.get("balance", 0)) > 0]
        return jsonify({"balances": result})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/predict", methods=["GET"])
def predict():
    auth = check_auth()
    if auth:
        return auth

    ticker = request.args.get("ticker", "KRW-BTC")
    pred = get_prediction(ticker)
    if pred is None:
        return jsonify({"error": f"{ticker} 모델 없음"}), 404
    return jsonify(pred)


@app.route("/predict/all", methods=["GET"])
def predict_all():
    auth = check_auth()
    if auth:
        return auth

    cfg     = load_config()
    top_n   = cfg.get("top_n_coins", 20)
    tickers = get_top_krw_tickers(top_n)

    results = []
    for ticker in tickers:
        pred = get_prediction(ticker)
        if pred:
            results.append(pred)

    results.sort(key=lambda x: x["prob"], reverse=True)
    return jsonify({"predictions": results})


@app.route("/trade/config", methods=["GET"])
def get_trade_config():
    auth = check_auth()
    if auth:
        return auth
    return jsonify(load_config())


@app.route("/trade/config", methods=["POST"])
def set_trade_config():
    auth = check_auth()
    if auth:
        return auth

    data = request.get_json()
    if not data:
        return jsonify({"error": "JSON 데이터 필요"}), 400

    cfg = load_config()
    for key in DEFAULT_CONFIG:
        if key in data:
            cfg[key] = data[key]
    save_config(cfg)
    log.info(f"자동매매 설정 업데이트: {cfg}")
    return jsonify({"ok": True, "config": cfg})


@app.route("/trade/log", methods=["GET"])
def trade_log():
    auth = check_auth()
    if auth:
        return auth

    limit = int(request.args.get("limit", 50))
    logs  = load_trade_log()[:limit]
    return jsonify({"logs": logs})


@app.route("/train", methods=["POST"])
def train():
    auth = check_auth()
    if auth:
        return auth

    t = threading.Thread(target=lambda: (run_training(), run_auto_trade()), daemon=True)
    t.start()
    return jsonify({"ok": True, "message": "학습 시작됨 (백그라운드)"})


@app.route("/trade/run", methods=["POST"])
def trade_run():
    auth = check_auth()
    if auth:
        return auth

    t = threading.Thread(target=run_auto_trade, daemon=True)
    t.start()
    return jsonify({"ok": True, "message": "자동매매 시작됨 (백그라운드)"})


# ════════════════════════════════════════════════
if __name__ == "__main__":
    os.makedirs(MODEL_DIR, exist_ok=True)

    # 스케줄러 백그라운드 실행
    threading.Thread(target=run_scheduler, daemon=True).start()

    log.info("NCP 서버 시작 — 포트 8080")
    app.run(host="0.0.0.0", port=8080, debug=False)

"""
코인 예측 AI - LightGBM 앙상블 파이프라인
업비트 API 기반 | 상승/하락 방향 분류
"""

import warnings
warnings.filterwarnings("ignore")

import numpy as np
import pandas as pd
import pyupbit
import ta
from datetime import datetime, timedelta
from sklearn.model_selection import TimeSeriesSplit
from sklearn.preprocessing import RobustScaler
from sklearn.metrics import accuracy_score, classification_report
from sklearn.calibration import CalibratedClassifierCV
import lightgbm as lgb
import joblib
import os

# ────────────────────────────────────────────
# 설정
# ────────────────────────────────────────────
COINS = ["KRW-BTC", "KRW-ETH", "KRW-XRP", "KRW-SOL", "KRW-ADA"]
INTERVAL = "day"          # day / minute60
COUNT    = 500             # 학습에 사용할 캔들 수
PREDICT_DAYS = 1          # 몇 일 후 방향 예측
THRESHOLD = 0.005         # 상승/하락 판단 기준 (0.5%)
N_SPLITS  = 5             # 시계열 교차 검증 폴드 수
MODEL_DIR = os.environ.get("MODEL_DIR", "models")      # 모델 저장 경로
os.makedirs(MODEL_DIR, exist_ok=True)


# ────────────────────────────────────────────
# 1. 데이터 수집
# ────────────────────────────────────────────
def fetch_ohlcv(ticker: str, interval: str = INTERVAL, count: int = COUNT) -> pd.DataFrame:
    df = pyupbit.get_ohlcv(ticker, interval=interval, count=count)
    if df is None or df.empty:
        raise ValueError(f"{ticker} 데이터를 가져올 수 없습니다.")
    df.index = pd.to_datetime(df.index)
    df.columns = ["open", "high", "low", "close", "volume", "value"]
    return df


# ────────────────────────────────────────────
# 2. 피처 엔지니어링
# ────────────────────────────────────────────
def make_features(df: pd.DataFrame, ref_df: pd.DataFrame = None, ref_name: str = "btc") -> pd.DataFrame:
    feat = pd.DataFrame(index=df.index)
    close  = df["close"]
    high   = df["high"]
    low    = df["low"]
    volume = df["volume"]

    # ── 기술적 지표 ──────────────────────────
    # 모멘텀
    feat["rsi_14"]   = ta.momentum.RSIIndicator(close, window=14).rsi()
    feat["rsi_7"]    = ta.momentum.RSIIndicator(close, window=7).rsi()
    feat["stoch_k"]  = ta.momentum.StochasticOscillator(high, low, close).stoch()
    feat["stoch_d"]  = ta.momentum.StochasticOscillator(high, low, close).stoch_signal()
    feat["roc_5"]    = ta.momentum.ROCIndicator(close, window=5).roc()
    feat["roc_10"]   = ta.momentum.ROCIndicator(close, window=10).roc()

    # 추세
    macd = ta.trend.MACD(close)
    feat["macd"]       = macd.macd()
    feat["macd_signal"]= macd.macd_signal()
    feat["macd_diff"]  = macd.macd_diff()
    feat["ema_9"]      = ta.trend.EMAIndicator(close, window=9).ema_indicator()
    feat["ema_21"]     = ta.trend.EMAIndicator(close, window=21).ema_indicator()
    feat["ema_ratio"]  = feat["ema_9"] / feat["ema_21"]
    feat["adx"]        = ta.trend.ADXIndicator(high, low, close).adx()

    # 변동성
    bb = ta.volatility.BollingerBands(close)
    feat["bb_upper"]   = bb.bollinger_hband()
    feat["bb_lower"]   = bb.bollinger_lband()
    feat["bb_width"]   = (feat["bb_upper"] - feat["bb_lower"]) / close
    feat["bb_pos"]     = (close - feat["bb_lower"]) / (feat["bb_upper"] - feat["bb_lower"] + 1e-9)
    feat["atr"]        = ta.volatility.AverageTrueRange(high, low, close).average_true_range()
    feat["atr_pct"]    = feat["atr"] / close

    # ── 거래량 지표 ──────────────────────────
    feat["volume_sma5"]  = volume.rolling(5).mean()
    feat["volume_sma20"] = volume.rolling(20).mean()
    feat["volume_ratio"] = volume / feat["volume_sma20"]
    feat["obv"]          = ta.volume.OnBalanceVolumeIndicator(close, volume).on_balance_volume()
    feat["obv_sma10"]    = feat["obv"].rolling(10).mean()
    feat["vwap_ratio"]   = close / ta.volume.VolumeWeightedAveragePrice(high, low, close, volume).volume_weighted_average_price()

    # ── 가격 파생 피처 ───────────────────────
    for lag in [1, 2, 3, 5, 7]:
        feat[f"return_{lag}d"] = close.pct_change(lag)
    feat["high_low_ratio"]   = (high - low) / close
    feat["open_close_ratio"] = (close - df["open"]) / df["open"]

    # 롤링 통계
    for w in [5, 10, 20]:
        feat[f"vol_{w}d"]     = close.pct_change().rolling(w).std()
        feat[f"close_sma{w}"] = close.rolling(w).mean()
    feat["sma5_sma20_ratio"] = feat["close_sma5"] / feat["close_sma20"]

    # ── BTC 상관관계 피처 (BTC가 아닌 코인에만) ──
    if ref_df is not None:
        ref_close = ref_df["close"].reindex(df.index, method="ffill")
        feat[f"{ref_name}_return_1d"] = ref_close.pct_change(1)
        feat[f"{ref_name}_return_3d"] = ref_close.pct_change(3)
        # 5일 롤링 상관계수
        feat[f"corr_{ref_name}_5d"] = (
            close.pct_change().rolling(5)
            .corr(ref_close.pct_change())
        )

    # ── 레이블 (PREDICT_DAYS 후 방향) ───────
    future_return = close.pct_change(PREDICT_DAYS).shift(-PREDICT_DAYS)
    feat["label"] = (future_return > THRESHOLD).astype(int)

    feat = feat.replace([np.inf, -np.inf], np.nan).dropna()
    return feat


# ────────────────────────────────────────────
# 3. 모델 학습 (시계열 교차검증)
# ────────────────────────────────────────────
def train_model(feat_df: pd.DataFrame, ticker: str):
    X = feat_df.drop(columns=["label"])
    y = feat_df["label"]

    tscv = TimeSeriesSplit(n_splits=N_SPLITS)
    scaler = RobustScaler()

    lgb_params = {
        "objective":       "binary",
        "metric":          "binary_logloss",
        "boosting_type":   "gbdt",
        "n_estimators":    500,
        "learning_rate":   0.03,
        "num_leaves":      31,
        "max_depth":       6,
        "min_child_samples": 20,
        "subsample":       0.8,
        "colsample_bytree":0.8,
        "reg_alpha":       0.1,
        "reg_lambda":      0.1,
        "class_weight":    "balanced",
        "random_state":    42,
        "verbose":         -1,
        "n_jobs":          -1,
    }

    fold_scores = []
    models = []

    print(f"\n{'='*50}")
    print(f"  {ticker} 학습 시작")
    print(f"{'='*50}")

    for fold, (train_idx, val_idx) in enumerate(tscv.split(X), 1):
        X_tr, X_val = X.iloc[train_idx], X.iloc[val_idx]
        y_tr, y_val = y.iloc[train_idx], y.iloc[val_idx]

        X_tr_sc  = scaler.fit_transform(X_tr)
        X_val_sc = scaler.transform(X_val)

        model = lgb.LGBMClassifier(**lgb_params)
        model.fit(
            X_tr_sc, y_tr,
            eval_set=[(X_val_sc, y_val)],
            callbacks=[lgb.early_stopping(50, verbose=False),
                       lgb.log_evaluation(period=-1)],
        )

        preds = model.predict(X_val_sc)
        acc   = accuracy_score(y_val, preds)
        fold_scores.append(acc)
        models.append((model, scaler))
        print(f"  Fold {fold}/{N_SPLITS}  |  Accuracy: {acc:.4f}")

    mean_acc = np.mean(fold_scores)
    std_acc  = np.std(fold_scores)
    print(f"\n  평균 정확도: {mean_acc:.4f} ± {std_acc:.4f}")

    # 최고 폴드 모델 저장
    best_idx  = int(np.argmax(fold_scores))
    best_model, best_scaler = models[best_idx]

    safe_ticker = ticker.replace("-", "_")
    joblib.dump(best_model,  f"{MODEL_DIR}/{safe_ticker}_model.pkl")
    joblib.dump(best_scaler, f"{MODEL_DIR}/{safe_ticker}_scaler.pkl")
    print(f"  모델 저장 완료 → {MODEL_DIR}/{safe_ticker}_model.pkl")

    return best_model, best_scaler, mean_acc, X.columns.tolist()


# ────────────────────────────────────────────
# 4. 예측
# ────────────────────────────────────────────
def predict(ticker: str, feature_names: list, model=None, scaler=None):
    safe_ticker = ticker.replace("-", "_")

    if model is None:
        model  = joblib.load(f"{MODEL_DIR}/{safe_ticker}_model.pkl")
        scaler = joblib.load(f"{MODEL_DIR}/{safe_ticker}_scaler.pkl")

    df = fetch_ohlcv(ticker, count=COUNT)

    # BTC 참조 데이터
    ref_df = None
    if ticker != "KRW-BTC":
        ref_df = fetch_ohlcv("KRW-BTC", count=COUNT)

    feat_df = make_features(df, ref_df=ref_df)
    X = feat_df.drop(columns=["label"])

    # 피처 정렬 (학습 시와 동일 순서)
    X = X.reindex(columns=feature_names, fill_value=0)

    latest = X.iloc[[-1]]
    latest_sc = scaler.transform(latest)

    prob_up = model.predict_proba(latest_sc)[0][1]
    direction = "📈 상승" if prob_up >= 0.5 else "📉 하락"

    return {
        "ticker":    ticker,
        "prob_up":   round(prob_up * 100, 2),
        "prob_down": round((1 - prob_up) * 100, 2),
        "direction": direction,
        "date":      datetime.now().strftime("%Y-%m-%d %H:%M"),
    }


# ────────────────────────────────────────────
# 5. 피처 중요도 출력
# ────────────────────────────────────────────
def print_feature_importance(model, feature_names: list, top_n: int = 15):
    importance = pd.Series(
        model.feature_importances_,
        index=feature_names
    ).sort_values(ascending=False)

    print(f"\n  ── 피처 중요도 Top {top_n} ──")
    for i, (feat, val) in enumerate(importance.head(top_n).items(), 1):
        bar = "█" * int(val / importance.max() * 20)
        print(f"  {i:2}. {feat:<25} {bar} {val:.1f}")


# ────────────────────────────────────────────
# 6. 메인 실행
# ────────────────────────────────────────────
def main():
    print("\n🚀 코인 예측 AI (LightGBM 앙상블) 시작")
    print(f"   대상 코인: {', '.join(COINS)}")
    print(f"   예측 목표: {PREDICT_DAYS}일 후 방향 (기준: ±{THRESHOLD*100:.1f}%)")

    # BTC 데이터 미리 가져오기 (상관관계 피처용)
    print("\n[1/3] 데이터 수집 중...")
    btc_df = fetch_ohlcv("KRW-BTC", count=COUNT)

    results = []

    for ticker in COINS:
        try:
            # 데이터 로드
            df = fetch_ohlcv(ticker, count=COUNT)
            ref_df = btc_df if ticker != "KRW-BTC" else None

            # 피처 생성
            print(f"\n[2/3] {ticker} 피처 생성 중...")
            feat_df = make_features(df, ref_df=ref_df)
            print(f"      피처 수: {feat_df.shape[1]-1}개  |  샘플 수: {feat_df.shape[0]}개")

            # 학습
            model, scaler, acc, feature_names = train_model(feat_df, ticker)

            # 피처 중요도
            print_feature_importance(model, feature_names)

            # 최신 예측
            print(f"\n[3/3] {ticker} 현재 예측 중...")
            result = predict(ticker, feature_names, model=model, scaler=scaler)
            result["accuracy"] = round(acc * 100, 2)
            results.append(result)

        except Exception as e:
            print(f"  ⚠️  {ticker} 오류: {e}")

    # 결과 요약
    print(f"\n{'='*50}")
    print("  📊 예측 결과 요약")
    print(f"{'='*50}")
    print(f"  {'코인':<12} {'방향':<10} {'상승확률':>8} {'하락확률':>8} {'검증정확도':>10}")
    print(f"  {'-'*52}")
    for r in results:
        print(f"  {r['ticker']:<12} {r['direction']:<10} {r['prob_up']:>7.2f}% {r['prob_down']:>7.2f}% {r['accuracy']:>9.2f}%")

    print(f"\n  기준 시각: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"  ⚠️  본 예측은 참고용이며 투자 결정의 근거로 사용하지 마세요.")

    return results


if __name__ == "__main__":
    main()

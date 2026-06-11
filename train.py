"""
train.py — Render Cron Job 학습 스크립트
Render 설정: Command = python train.py
             Schedule  = 0 2 * * *  (매일 새벽 2시 KST = UTC 17:00)
"""

import os
import sys
import logging
from datetime import datetime

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
log = logging.getLogger(__name__)


def main():
    log.info("=" * 50)
    log.info(f"  자동 학습 시작: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    log.info("=" * 50)

    # coin_predictor_lgbm 임포트
    try:
        from coin_predictor_lgbm import (
            fetch_ohlcv, make_features, train_model, COINS, MODEL_DIR
        )
        import numpy as np
        import os
        os.makedirs(MODEL_DIR, exist_ok=True)
    except ImportError as e:
        log.error(f"임포트 실패: {e}")
        sys.exit(1)

    # BTC 데이터 사전 로드 (상관관계 피처용)
    log.info("[1/3] BTC 데이터 로드 중...")
    try:
        btc_df = fetch_ohlcv("KRW-BTC", count=500)
        log.info(f"  BTC 데이터 {len(btc_df)}개 로드 완료")
    except Exception as e:
        log.error(f"BTC 데이터 로드 실패: {e}")
        sys.exit(1)

    # 각 코인 학습
    log.info("[2/3] 코인별 모델 학습 중...")
    results = {}
    for ticker in COINS:
        try:
            df = fetch_ohlcv(ticker, count=500)
            ref_df = btc_df if ticker != "KRW-BTC" else None
            feat_df = make_features(df, ref_df=ref_df)
            _, _, acc, _ = train_model(feat_df, ticker)
            results[ticker] = round(acc * 100, 2)
            log.info(f"  {ticker} 완료 — 검증 정확도: {acc*100:.2f}%")
        except Exception as e:
            log.warning(f"  {ticker} 실패: {e}")

    # 결과 요약
    log.info("[3/3] 학습 완료 요약")
    log.info("-" * 40)
    for ticker, acc in results.items():
        log.info(f"  {ticker:<14} {acc:.2f}%")
    if results:
        mean_acc = sum(results.values()) / len(results)
        log.info(f"  {'평균':<14} {mean_acc:.2f}%")
    log.info("-" * 40)
    log.info(f"  완료 시각: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    log.info("=" * 50)


if __name__ == "__main__":
    main()

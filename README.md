# 코인 매매 AI 챗봇

LightGBM 앙상블 기반 코인 예측 + 매매 일지 AI 챗봇

## 파일 구조

```
├── app.py                  # Streamlit 챗봇 앱 (메인)
├── coin_predictor_lgbm.py  # LightGBM 학습/예측 파이프라인
├── train.py                # Cron Job용 자동 학습 스크립트
├── render.yaml             # Render 배포 설정
├── requirements.txt        # 패키지 목록
└── .gitignore
```

## Render 배포 방법

### 1단계 — GitHub 연동
1. 이 폴더를 GitHub 레포로 push
2. [render.com](https://render.com) 접속 → New → Blueprint
3. GitHub 레포 선택 → `render.yaml` 자동 감지

### 2단계 — Disk 설정 (중요)
Web Service와 Cron Job이 같은 `/data/models` 디스크를 공유해야 해요.
- Render Dashboard → coin-chatbot → Disks 탭 확인
- 마운트 경로: `/data/models`

### 3단계 — 첫 학습 실행
배포 직후엔 모델이 없으므로 수동으로 한 번 실행:
```
Render Dashboard → coin-daily-train → Trigger Run
```

### Cron Job 스케줄
- 매일 **새벽 2시 (KST)** 자동 실행
- UTC 기준: `0 17 * * *`
- 소요 시간: 약 5~15분 (코인 수에 따라 다름)

## 로컬 실행

```bash
pip install -r requirements.txt

# 첫 학습
python train.py

# 앱 실행
streamlit run app.py
```

## 환경변수 (선택)
Render Dashboard → Environment에서 설정:
- `MODEL_DIR` : 모델 저장 경로 (기본값: `/data/models`)

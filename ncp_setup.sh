#!/bin/bash
# ══════════════════════════════════════════════════════
# NCP 서버 초기 설치 스크립트
# Ubuntu 22.04 기준
# 실행: bash ncp_setup.sh
# ══════════════════════════════════════════════════════

set -e

echo "======================================"
echo "  NCP 코인 자동매매 서버 설치 시작"
echo "======================================"

# 1. 시스템 업데이트
echo "[1/7] 시스템 업데이트..."
sudo apt-get update -y
sudo apt-get install -y python3 python3-pip python3-venv git

# 2. 프로젝트 클론
echo "[2/7] 프로젝트 클론..."
cd ~
if [ -d "coin_project" ]; then
    cd coin_project && git pull
else
    git clone https://github.com/hwan2ya1004/coin_project.git
    cd coin_project
fi

# 3. 가상환경 생성 및 패키지 설치
echo "[3/7] Python 가상환경 설정..."
python3 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip
pip install flask python-dotenv pyupbit requests schedule \
    lightgbm scikit-learn pandas numpy ta joblib

# 4. .env 파일 생성
echo "[4/7] 환경변수 설정..."
if [ ! -f ".env" ]; then
    cat > .env << 'EOF'
UPBIT_ACCESS_KEY=여기에_업비트_Access_Key_입력
UPBIT_SECRET_KEY=여기에_업비트_Secret_Key_입력
NCP_API_SECRET=여기에_랜덤_비밀키_입력
MODEL_DIR=models
EOF
    echo "⚠️  .env 파일이 생성되었습니다. 실제 키를 입력하세요:"
    echo "    nano .env"
else
    echo "  .env 파일이 이미 존재합니다."
fi

# 5. models 디렉토리 생성
mkdir -p models

# 6. systemd 서비스 등록 (자동 시작)
echo "[5/7] systemd 서비스 등록..."
WORK_DIR=$(pwd)
VENV_PYTHON="$WORK_DIR/.venv/bin/python"

sudo tee /etc/systemd/system/coin-server.service > /dev/null << EOF
[Unit]
Description=Coin Auto Trade NCP Server
After=network.target

[Service]
Type=simple
User=$USER
WorkingDirectory=$WORK_DIR
ExecStart=$VENV_PYTHON ncp_server.py
Restart=always
RestartSec=10
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
EOF

sudo systemctl daemon-reload
sudo systemctl enable coin-server

# 7. 방화벽 설정 (8080 포트 허용)
echo "[6/7] 방화벽 설정..."
sudo ufw allow 8080/tcp 2>/dev/null || true

echo ""
echo "======================================"
echo "  설치 완료!"
echo "======================================"
echo ""
echo "📋 다음 단계:"
echo ""
echo "1. .env 파일에 실제 API 키 입력:"
echo "   nano $WORK_DIR/.env"
echo ""
echo "2. 업비트 Open API 관리에서 이 서버 IP를 등록:"
echo "   현재 서버 IP: $(curl -s https://api64.ipify.org)"
echo ""
echo "3. 서버 시작:"
echo "   sudo systemctl start coin-server"
echo "   sudo systemctl status coin-server"
echo ""
echo "4. Render 환경변수에 추가:"
echo "   NCP_PROXY_URL = http://$(curl -s https://api64.ipify.org):8080"
echo "   NCP_API_SECRET = (위에서 설정한 비밀키)"
echo ""
echo "5. 로그 확인:"
echo "   sudo journalctl -u coin-server -f"
echo ""

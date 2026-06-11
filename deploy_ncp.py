"""
NCP 서버 배포 스크립트 (paramiko 사용)
"""
import paramiko
import time
import sys

HOST = "101.79.21.199"
PORT = 22
USER = "root"
PASSWORD = "D3@eB$R=-2u"
KEY_FILE = r"C:\Users\hwan2\.ssh\coin-alert-key.pem"

def run(ssh, cmd, timeout=120):
    print(f"\n$ {cmd}")
    stdin, stdout, stderr = ssh.exec_command(cmd, timeout=timeout)
    out = stdout.read().decode("utf-8", errors="replace")
    err = stderr.read().decode("utf-8", errors="replace")
    if out: print(out.strip())
    if err: print("[STDERR]", err.strip())
    return out

def connect():
    ssh = paramiko.SSHClient()
    ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    
    # 1. PEM 키로 시도
    try:
        print(f"[1] PEM 키로 접속 시도: {USER}@{HOST}")
        key = paramiko.RSAKey.from_private_key_file(KEY_FILE)
        ssh.connect(HOST, port=PORT, username=USER, pkey=key, timeout=10)
        print("✅ PEM 키 인증 성공!")
        return ssh
    except Exception as e:
        print(f"  PEM 키 실패: {e}")

    # 2. ubuntu + PEM 키로 시도
    try:
        print(f"[2] ubuntu + PEM 키로 접속 시도")
        key = paramiko.RSAKey.from_private_key_file(KEY_FILE)
        ssh.connect(HOST, port=PORT, username="ubuntu", pkey=key, timeout=10)
        print("✅ ubuntu PEM 키 인증 성공!")
        return ssh
    except Exception as e:
        print(f"  ubuntu PEM 키 실패: {e}")

    # 3. root + 비밀번호로 시도
    try:
        print(f"[3] root + 비밀번호로 접속 시도")
        ssh.connect(HOST, port=PORT, username="root", password=PASSWORD, timeout=10,
                    look_for_keys=False, allow_agent=False)
        print("✅ root 비밀번호 인증 성공!")
        return ssh
    except Exception as e:
        print(f"  root 비밀번호 실패: {e}")

    # 4. ubuntu + 비밀번호로 시도
    try:
        print(f"[4] ubuntu + 비밀번호로 접속 시도")
        ssh.connect(HOST, port=PORT, username="ubuntu", password=PASSWORD, timeout=10,
                    look_for_keys=False, allow_agent=False)
        print("✅ ubuntu 비밀번호 인증 성공!")
        return ssh
    except Exception as e:
        print(f"  ubuntu 비밀번호 실패: {e}")

    return None

def deploy(ssh):
    print("\n" + "="*50)
    print("  NCP 서버 배포 시작")
    print("="*50)

    # 현재 상태 확인
    print("\n[1/6] 현재 서버 상태 확인...")
    run(ssh, "ls ~ && whoami && python3 --version")

    # 기존 프로세스 종료
    print("\n[2/6] 기존 서버 프로세스 종료...")
    run(ssh, "pkill -f ncp_server.py || true")
    run(ssh, "pkill -f 'python.*ncp' || true")
    run(ssh, "systemctl stop coin-server 2>/dev/null || true")

    # 기존 디렉토리 삭제
    print("\n[3/6] 기존 프로젝트 삭제...")
    run(ssh, "rm -rf ~/coin_project")

    # 프로젝트 클론
    print("\n[4/6] 프로젝트 클론...")
    run(ssh, "cd ~ && git clone https://github.com/hwan2ya1004/coin_project.git", timeout=60)
    run(ssh, "ls ~/coin_project/")

    # 패키지 설치
    print("\n[5/6] 패키지 설치 (시간이 걸릴 수 있습니다)...")
    run(ssh, "apt-get update -y 2>/dev/null | tail -3", timeout=60)
    run(ssh, "apt-get install -y python3-pip python3-venv 2>/dev/null | tail -3", timeout=60)
    run(ssh, "cd ~/coin_project && python3 -m venv .venv", timeout=60)
    run(ssh, "cd ~/coin_project && .venv/bin/pip install --upgrade pip -q", timeout=60)
    run(ssh, "cd ~/coin_project && .venv/bin/pip install flask python-dotenv pyupbit requests schedule lightgbm scikit-learn pandas numpy ta joblib -q", timeout=300)
    print("패키지 설치 완료!")

    # .env 파일 생성
    print("\n[6/6] .env 파일 생성...")
    env_content = """UPBIT_ACCESS_KEY=3InZnfjMwbgr9M9pEmiJ6Y1gWO2JQCkxEsYGL3eS
UPBIT_SECRET_KEY=kPxWf51dWNxE2nc0rem3PRvbet4jkEwxxmXExM5d
NCP_API_SECRET=coin2026secret!
MODEL_DIR=/root/coin_project/models"""
    run(ssh, f"cat > ~/coin_project/.env << 'ENVEOF'\n{env_content}\nENVEOF")
    run(ssh, "cat ~/coin_project/.env")

    # models 디렉토리 생성
    run(ssh, "mkdir -p ~/coin_project/models")

    # systemd 서비스 등록
    print("\n서비스 등록...")
    service = """[Unit]
Description=Coin Auto Trade NCP Server
After=network.target

[Service]
Type=simple
User=root
WorkingDirectory=/root/coin_project
ExecStart=/root/coin_project/.venv/bin/python ncp_server.py
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target"""
    run(ssh, f"cat > /etc/systemd/system/coin-server.service << 'SVCEOF'\n{service}\nSVCEOF")
    run(ssh, "systemctl daemon-reload && systemctl enable coin-server")
    run(ssh, "systemctl start coin-server")
    time.sleep(3)
    run(ssh, "systemctl status coin-server --no-pager")

    # 방화벽 설정
    run(ssh, "ufw allow 8080/tcp 2>/dev/null || true")

    # 서버 IP 확인
    print("\n" + "="*50)
    print("  배포 완료!")
    print("="*50)
    run(ssh, "curl -s https://api64.ipify.org && echo ''")
    print("\n✅ NCP 서버 배포 성공!")
    print(f"   서버 주소: http://{HOST}:8080")
    print(f"   NCP_PROXY_URL = http://{HOST}:8080")
    print(f"   NCP_API_SECRET = coin2026secret!")

if __name__ == "__main__":
    ssh = connect()
    if ssh:
        deploy(ssh)
        ssh.close()
    else:
        print("\n❌ SSH 접속 실패! NCP 콘솔에서 ACG(방화벽) 22번 포트가 열려있는지 확인하세요.")
        sys.exit(1)

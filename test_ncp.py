import requests

NCP_URL = "http://101.79.21.199:8080"
NCP_SECRET = "coin2026secret!"
headers = {"X-API-Secret": NCP_SECRET}

print("=== NCP 서버 외부 접근 테스트 ===")

# 1. health 체크
try:
    r = requests.get(f"{NCP_URL}/health", timeout=5)
    print(f"[health] {r.status_code}: {r.json()}")
except Exception as e:
    print(f"[health] 실패: {e}")

# 2. 잔고 조회
try:
    r = requests.get(f"{NCP_URL}/balance", headers=headers, timeout=10)
    print(f"[balance] {r.status_code}: {r.text[:200]}")
except Exception as e:
    print(f"[balance] 실패: {e}")

# 3. 설정 조회
try:
    r = requests.get(f"{NCP_URL}/trade/config", headers=headers, timeout=5)
    print(f"[config] {r.status_code}: {r.json()}")
except Exception as e:
    print(f"[config] 실패: {e}")

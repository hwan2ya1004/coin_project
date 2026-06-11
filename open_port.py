import paramiko

ssh = paramiko.SSHClient()
ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
ssh.connect('101.79.21.199', port=22, username='root', password='D3@eB$R=-2u',
            timeout=10, look_for_keys=False, allow_agent=False)

def run(cmd, timeout=30):
    print(f'\n$ {cmd}')
    _, stdout, stderr = ssh.exec_command(cmd, timeout=timeout)
    out = stdout.read().decode('utf-8', errors='replace')
    err = stderr.read().decode('utf-8', errors='replace')
    if out: print(out.strip())
    if err and 'WARNING' not in err: print('[ERR]', err.strip()[:200])
    return out

print('=== 방화벽(ufw) 8080 포트 개방 ===')
run('ufw status')
run('ufw allow 8080/tcp')
run('ufw allow 8080')
run('ufw status')

# iptables로도 직접 허용
run('iptables -I INPUT -p tcp --dport 8080 -j ACCEPT')
run('iptables -L INPUT | grep 8080')

# 서버가 0.0.0.0으로 바인딩되어 있는지 확인
run('ss -tlnp | grep 8080')
run('netstat -tlnp 2>/dev/null | grep 8080 || ss -tlnp | grep 8080')

ssh.close()
print('\n완료 - 이제 외부에서 http://101.79.21.199:8080 접근 가능해야 합니다')

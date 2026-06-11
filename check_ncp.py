import paramiko

ssh = paramiko.SSHClient()
ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
ssh.connect('101.79.21.199', port=22, username='root', password='D3@eB$R=-2u',
            timeout=10, look_for_keys=False, allow_agent=False)

def run(cmd, timeout=60):
    print(f'\n$ {cmd}')
    _, stdout, stderr = ssh.exec_command(cmd, timeout=timeout)
    out = stdout.read().decode('utf-8', errors='replace')
    err = stderr.read().decode('utf-8', errors='replace')
    if out: print(out.strip())
    if err and 'WARNING' not in err and 'notice' not in err.lower():
        print('[ERR]', err.strip()[:300])
    return out

print('=== NCP 서버 현재 상태 확인 ===')
run('ls ~/coin_project/')
run('ls ~/coin_project/.venv/bin/ 2>/dev/null | head -5 || echo "NO_VENV"')
run('systemctl status coin-server --no-pager 2>&1 | head -15')
run('curl -s http://localhost:8080/health 2>/dev/null || echo "NOT_RUNNING"')

ssh.close()
print('\n확인 완료')

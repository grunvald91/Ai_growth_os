import paramiko, os, sys
sys.stdout.reconfigure(encoding='utf-8')

ssh = paramiko.SSHClient()
ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
ssh.connect('155.212.162.47', username='root', password='***REMOVED***', timeout=30)
sftp = ssh.open_sftp()

base_local = 'C:/Users/MisterPaket/booking_bot/web'
base_remote = '/opt/booking_bot/web'

# Upload backend files
backend_files = [
    'api/main.py',
    'api/database.py',
    'api/schemas.py',
    'api/deps.py',
    'api/routes/admin.py',
    'api/routes/auth.py',
    'api/routes/client.py',
    'api/routes/subscriptions.py',
    'api/routes/ai.py',
]

for f in backend_files:
    local = os.path.join(base_local, f)
    remote = base_remote + '/' + f
    remote_dir = os.path.dirname(remote)
    try:
        sftp.stat(remote_dir)
    except FileNotFoundError:
        sftp.mkdir(remote_dir)
    sftp.put(local, remote)
    print('Uploaded ' + f)

# Upload frontend dist
dist_local = os.path.join(base_local, 'frontend/dist')
dist_remote = base_remote + '/frontend/dist'

# Clear old dist assets
stdin, stdout, stderr = ssh.exec_command('rm -rf ' + dist_remote + '/assets/*')
stdout.read()

for root, dirs, files in os.walk(dist_local):
    for d in dirs:
        local_dir = os.path.join(root, d)
        rel = os.path.relpath(local_dir, dist_local).replace(os.sep, '/')
        remote_dir = dist_remote + '/' + rel
        try:
            sftp.stat(remote_dir)
        except FileNotFoundError:
            sftp.mkdir(remote_dir)
    for f in files:
        local_path = os.path.join(root, f)
        rel = os.path.relpath(local_path, dist_local).replace(os.sep, '/')
        remote_path = dist_remote + '/' + rel
        sftp.put(local_path, remote_path)
        print('Uploaded dist/' + rel)

sftp.close()

# Restart API service
stdin, stdout, stderr = ssh.exec_command('systemctl restart booking-web-api 2>&1; sleep 2; systemctl is-active booking-web-api')
out = stdout.read().decode().strip()
err = stderr.read().decode().strip()
print('API service: ' + out)
if err:
    print(err)

ssh.close()
print('Deploy complete!')

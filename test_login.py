import sys, time, os
sys.path.insert(0, 'src')
os.environ['MONITOR_DATA_DIR'] = '/root/nsfocus-monitor/data'

print("1", flush=True)
from src.app import create_app
print("2", flush=True)
app = create_app()
print("3", flush=True)
with app.test_client() as client:
    t0 = time.time()
    resp = client.post('/api/auth/login', json={'username':'admin','password':'admin123'})
    print("login: %.3fs status=%d" % (time.time()-t0, resp.status_code), flush=True)
    if resp.status_code == 200:
        token = resp.get_json()['data']['token']
        # Simple GET
        t0 = time.time()
        r = client.get('/api/channels', headers={'Authorization': 'Bearer ' + token})
        print("GET /channels: %.3fs status=%d" % (time.time()-t0, r.status_code), flush=True)
print("done", flush=True)
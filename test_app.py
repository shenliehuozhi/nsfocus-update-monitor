import sys, time, os
sys.path.insert(0, 'src')

os.environ['MONITOR_DATA_DIR'] = '/root/nsfocus-monitor/data'

print("1", flush=True)
from src.app import create_app
print("2", flush=True)
t0 = time.time()
app = create_app()
print("3 create_app done: %.3fs" % (time.time()-t0), flush=True)
print("4 starting test client", flush=True)
with app.test_client() as client:
    t0 = time.time()
    resp = client.post('/api/auth/login', json={'username':'admin','password':'admin123'})
    print("5 login: %.3fs status=%d" % (time.time()-t0, resp.status_code), flush=True)
    if resp.status_code == 200:
        token = resp.get_json()['data']['token']
        t0 = time.time()
        r = client.post('/api/history/3308/resend-targeted',
            headers={'Authorization': 'Bearer ' + token},
            json={'channel_id': 1})
        print("6 resend-targeted: %.3fs status=%d body=%s" % (time.time()-t0, r.status_code, r.get_json()), flush=True)
print("7 done", flush=True)
import sys, time, os
sys.path.insert(0, 'src')
os.environ['MONITOR_DATA_DIR'] = '/root/nsfocus-monitor/data'

# Monkey-patch log_delivery to add tracing
import src.models.subscription as sub
_orig_log = sub.log_delivery

def traced_log(*args, **kwargs):
    sys.stderr.write("TRACE: log_delivery called\n")
    sys.stderr.flush()
    result = _orig_log(*args, **kwargs)
    sys.stderr.write("TRACE: log_delivery done\n")
    sys.stderr.flush()
    return result

sub.log_delivery = traced_log

print("1", flush=True)
from src.app import create_app
print("2", flush=True)
app = create_app()
print("3", flush=True)
with app.test_client() as client:
    resp = client.post('/api/auth/login', json={'username':'admin','password':'admin123'})
    print("login: status=%d" % resp.status_code, flush=True)
    if resp.status_code == 200:
        token = resp.get_json()['data']['token']
        sys.stderr.write("TRACE: before resend-targeted call\n"); sys.stderr.flush()
        r = client.post('/api/history/3308/resend-targeted',
            headers={'Authorization': 'Bearer ' + token},
            json={'channel_id': 1})
        sys.stderr.write("TRACE: after resend-targeted call\n"); sys.stderr.flush()
        print("resend-targeted: status=%d body=%s" % (r.status_code, r.get_json()), flush=True)
print("done", flush=True)
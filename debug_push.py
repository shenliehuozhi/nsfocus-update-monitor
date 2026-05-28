import sys, time
sys.path.insert(0, 'src')

print("Starting test...")

t0 = time.time()
print("Importing create_app...")
sys.stdout.flush()
from src.app import create_app
print("create_app imported in %.3fs" % (time.time()-t0))
sys.stdout.flush()

app = create_app()
print("app created in %.3fs" % (time.time()-t0))
sys.stdout.flush()

with app.test_client() as client:
    t0 = time.time()
    resp = client.post('/api/auth/login', json={'username':'admin','password':'admin123'})
    print("login: %.3fs, status=%d" % (time.time()-t0, resp.status_code))
    sys.stdout.flush()
    
    if resp.status_code == 200:
        token = resp.get_json()['data']['token']
        print("Token obtained")
        sys.stdout.flush()
        
        # Test POST with body to a simpler endpoint
        t0 = time.time()
        r = client.post('/api/history/3308/push',
            headers={'Authorization': 'Bearer ' + token},
            json={'mode': 'channel', 'target_id': 1})
        print("POST /push: %.3fs, status=%d, body=%s" % (time.time()-t0, r.status_code, r.get_json()))
        sys.stdout.flush()
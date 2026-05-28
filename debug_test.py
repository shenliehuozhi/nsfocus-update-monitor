import sys, time
sys.path.insert(0, 'src')

t0 = time.time()
from src.app import create_app
app = create_app()
print("create_app: %.3fs" % (time.time()-t0))

# Test login
with app.test_client() as client:
    t0 = time.time()
    resp = client.post('/api/auth/login', json={'username':'admin','password':'admin123'})
    print("login: %.3fs, status=%d" % (time.time()-t0, resp.status_code))
    
    if resp.status_code == 200:
        token = resp.get_json()['data']['token']
        
        # Test history
        t0 = time.time()
        r = client.get('/api/history?limit=1', headers={'Authorization': 'Bearer ' + token})
        print("GET /history: %.3fs, status=%d" % (time.time()-t0, r.status_code))
        
        # Test resend (faster, no wecom call)
        t0 = time.time()
        r = client.post('/api/history/3308/resend',
            headers={'Authorization': 'Bearer ' + token})
        print("POST /resend: %.3fs, status=%d" % (time.time()-t0, r.status_code))
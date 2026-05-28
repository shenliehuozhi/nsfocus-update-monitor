import sys, time
sys.path.insert(0, 'src')

print("Step 1: imports")
from flask import Flask, jsonify, request
from src.web.routes.auth_routes import bp as auth_bp
from src.web.routes.api_routes import bp_history
print("Step 2: bluepints imported")

app = Flask(__name__)
app.register_blueprint(auth_bp)
app.register_blueprint(bp_history, url_prefix='/api')

print("Step 3: registered blueprints")

with app.test_client() as client:
    t0 = time.time()
    resp = client.post('/api/auth/login', json={'username':'admin','password':'admin123'})
    print("login: %.3fs status=%d" % (time.time()-t0, resp.status_code))
    
    token = resp.get_json()['data']['token']
    
    # Test the problematic endpoint with just auth + parse
    t0 = time.time()
    r = client.post('/api/history/3308/resend-targeted',
        headers={'Authorization': 'Bearer ' + token},
        json={'channel_id': 1, 'customer_id': 0})
    print("resend-targeted: %.3fs status=%d body=%s" % (time.time()-t0, r.status_code, r.get_json()))
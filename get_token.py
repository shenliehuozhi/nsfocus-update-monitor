import sys, jwt, datetime
sys.path.insert(0, 'src')
from src.models.database import query
rows = query('SELECT id, username FROM users LIMIT 1')
uid, uname = rows[0]['id'], rows[0]['username']
SECRET = 'dev-jwt-secret-change-me'
token = jwt.encode({'user_id': uid, 'username': uname, 'exp': datetime.datetime.utcnow() + datetime.timedelta(hours=24), 'iat': datetime.datetime.utcnow()}, SECRET, algorithm='HS256')
print(token)

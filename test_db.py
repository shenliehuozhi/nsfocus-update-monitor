import sys, time, os
sys.path.insert(0, 'src')

print("A", flush=True)
from src.models.database import init_db, get_db
print("B", flush=True)
init_db('/root/nsfocus-monitor/data')
print("C", flush=True)
db = get_db()
print("D", flush=True)
rows = db.execute("SELECT * FROM channels WHERE id = ?", (1,)).fetchall()
print("E rows=%d" % len(rows), flush=True)
print("F done", flush=True)
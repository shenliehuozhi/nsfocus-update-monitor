import sys, time, os
# Disable .env loading and other init
os.environ['MONITOR_DATA_DIR'] = '/root/nsfocus-monitor/data'
sys.path.insert(0, 'src')

print("Starting isolated test")
t0 = time.time()

from src.models.channel import get_by_id
print("get_by_id imported: %.3fs" % (time.time()-t0))

t0 = time.time()
ch = get_by_id(1)
print("get_by_id(1): %.3fs result=%s" % (time.time()-t0, ch))

print("Done")
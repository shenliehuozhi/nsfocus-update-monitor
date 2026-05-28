import sys, time
sys.path.insert(0, 'src')

import os
os.environ['MONITOR_DATA_DIR'] = '/root/nsfocus-monitor/data'

print("1. get_snapshot")
t0 = time.time()
from src.models.snapshot import get_snapshot
snap = get_snapshot(3308)
print("  -> %.3fs, snap=%s" % (time.time()-t0, snap is not None))

print("2. get_by_id channel")
t0 = time.time()
from src.models.channel import get_by_id
ch = get_by_id(1)
print("  -> %.3fs, channel=%s" % (time.time()-t0, ch is not None))

print("3. NotificationMessage.from_snapshot")
t0 = time.time()
from src.notifiers.base import NotificationMessage
msg = NotificationMessage.from_snapshot(snap)
print("  -> %.3fs" % (time.time()-t0))

print("4. WecomNotifier.send")
t0 = time.time()
from src.notifiers.wecom import WecomNotifier
result = WecomNotifier().send(msg, ch['config'])
print("  -> %.3fs, success=%s" % (time.time()-t0, result.success))

print("5. log_delivery")
t0 = time.time()
from src.models.subscription import log_delivery
log_delivery(snapshot_id=3308, channel_id=1, channel_type='wecom', channel_name=ch.get('name',''), customer_id=0, status='sent')
print("  -> %.3fs" % (time.time()-t0))

print("DONE ALL IN SERIAL: success")
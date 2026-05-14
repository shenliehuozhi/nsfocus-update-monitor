#!/usr/bin/env python3
"""Sequential package type discovery for all active products."""
import sys, os, json, time, logging
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

LOG_FILE = '/root/nsfocus-monitor/logs/collect_all.log'
os.makedirs(os.path.dirname(LOG_FILE), exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    handlers=[
        logging.FileHandler(LOG_FILE),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger()

def main():
    from src.collectors.nsfocus import NsfocusCollector
    from src.models.user_session import get_active_sessions
    from src.models.database import query, execute

    sessions = get_active_sessions()
    if not sessions:
        logger.error('No session. Create one via web UI first.')
        return
    cookie = sessions[0]['cookie_value']

    collector = NsfocusCollector()

    rows = query("SELECT id, name FROM content_sources WHERE is_active=1 ORDER BY id")
    logger.info(f'Found {len(rows)} active products, starting sequential collection.')

    total = len(rows)
    for i, row in enumerate(rows, 1):
        source_id = row['id']
        name = row['name']
        logger.info(f'[{i}/{total}] source_id={source_id} name={name}')
        t0 = time.time()
        try:
            result = collector.discover_package_types(source_id, cookie)
            cfg = {
                'types': result['types'],
                'paths': result['paths'],
                'modes': result['modes']
            }
            execute(
                "UPDATE content_sources SET package_type=? WHERE id=?",
                (json.dumps(cfg, ensure_ascii=False), source_id)
            )
            logger.info(f'  -> OK: {len(result["types"])} types, {len(result["paths"])} paths in {time.time()-t0:.1f}s')
        except Exception as e:
            logger.error(f'  -> FAILED: {e}')

    logger.info('All done.')

if __name__ == '__main__':
    main()

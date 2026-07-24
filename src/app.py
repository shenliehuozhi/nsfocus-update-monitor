"""Flask Application Factory"""
import os
import sys
import time
import logging
import logging.handlers
from flask import Flask, request, g


def _migrate_email_channel_to_list():
    """Strip legacy to_list from email channel configs.

    The channel-level recipient field was removed when the email provider
    templates were added. Recipients now live on subscription rules
    (customer_emails) or are specified per manual push. This function is
    idempotent — running it on already-clean configs is a no-op.
    """
    import json
    from src.core.crypto import encrypt, decrypt
    from src.models.database import query, execute
    from src.core.logger import get_logger
    logger = get_logger('migration')

    rows = query("SELECT id, name, config FROM channels WHERE type = 'email' AND config IS NOT NULL")
    if not rows:
        return

    migrated = 0
    for row in rows:
        try:
            cfg_str = decrypt(row['config']) if row['config'] else ''
            cfg = json.loads(cfg_str) if cfg_str else {}
        except Exception:
            continue  # unreadable config — leave it alone, user will fix
        if not isinstance(cfg, dict):
            continue
        if 'to_list' not in cfg:
            continue
        old = cfg.pop('to_list')
        try:
            new_cfg_str = json.dumps(cfg, ensure_ascii=False)
            execute(
                "UPDATE channels SET config = ? WHERE id = ?",
                (encrypt(new_cfg_str), row['id'])
            )
            migrated += 1
            logger.info(
                f"Migrated email channel id={row['id']} name={row['name']!r}: "
                f"stripped to_list={old} (recipients must now be configured on subscription rules)"
            )
        except Exception as e:
            logger.warning(f"Failed to migrate channel id={row['id']}: {e}")

    if migrated:
        logger.info(f"email channel to_list migration: cleared {migrated} channel(s)")
    else:
        logger.debug("email channel to_list migration: nothing to do")


def _migrate_snapshots_url_based_if_needed():
    """One-time migration: snapshots.path_id 改成纯 URL hash + 部分 UNIQUE INDEX。

    背景:2026-07-16 前 snapshots.path_id 用
    MD5(source_url + JSON(chain))[:12],collector chain 文本一变就
    重新打 key,导致假 NEW / 假 WITHDRAWN 通知(IPS/IDS 实测 982 假 DELETED + 1019 假 NEW)。

    新算法:MD5(source_url)[:12](只看物理 URL,稳定)。
    UNIQUE INDEX 改为 (source_id, source_url, path_id, file_name, md5_hash)
    WHERE status='active' 部分唯一 — 撤回/历史可共存。

    升级时启动一次,跑完写 system_settings.snapshots_migration_v3='1'。
    已经迁移过(marker 已存在)→ noop。

    启动期跑迁移的风险:
      - 数据量大时启动会慢(扫 snapshots 全表 + 重建 INDEX)
      - 进程异常退出时部分状态可能不一致 — 但迁移脚本本身在一个事务里,
        SQLite 失败会回滚,且原始 row 不变
      - 用户数据:迁移不删 source_url='' 的孤立行,只删按
        (source_id, path_id, file, md5) 算出来的"同一物理包不同 chain"的副本
    """
    from src.core.logger import get_logger
    logger = get_logger('migration')

    # 1. 检查 marker — 已经迁移过就直接返回
    try:
        from src.models.database import query
        rows = query("SELECT value FROM system_settings WHERE key='snapshots_migration_v3'")
        if rows and rows[0].get('value') == '1':
            logger.debug("snapshots URL-based migration: already done (marker present)")
            return
    except Exception as e:
        logger.warning(f"snapshots URL-based migration: marker check skipped: {e}")
        return

    # 2. 导入迁移模块 — 失败不阻塞启动
    try:
        from scripts.migrate_snapshots_to_url_based import (
            plan as plan_migration,
            apply as apply_migration,
            already_migrated,
        )
        from src.models.database import get_db
    except ImportError:
        # PyInstaller onefile 兜底:脚本在 sys._MEIPASS/scripts/ 下,需要先把
        # 该路径加入 sys.path 才能 import。常规开发环境 / python3 run.py
        # 直接走默认 import 即可。
        import importlib.util as _ilu
        _meipass = getattr(sys, '_MEIPASS', None)
        _candidates = []
        if _meipass:
            _candidates.append(os.path.join(_meipass, 'scripts', 'migrate_snapshots_to_url_based.py'))
        _candidates.append(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                        '..', 'scripts', 'migrate_snapshots_to_url_based.py'))
        _loaded = False
        for _p in _candidates:
            if os.path.exists(_p):
                _spec = _ilu.spec_from_file_location(
                    'scripts.migrate_snapshots_to_url_based', _p
                )
                _mod = _ilu.module_from_spec(_spec)
                _spec.loader.exec_module(_mod)
                plan_migration = _mod.plan
                apply_migration = _mod.apply
                already_migrated = _mod.already_migrated
                _loaded = True
                break
        if not _loaded:
            logger.warning("snapshots URL-based migration: module not found, skipping")
            return
        from src.models.database import get_db
    except Exception as e:
        logger.warning(f"snapshots URL-based migration: import failed: {e}")
        return

    # 3. 准备进度 logger — 用户在 app.log 里能看到分阶段输出
    progress = get_logger('migration.progress')
    progress.info('=' * 70)
    progress.info('⏳ 检测到旧版 snapshots schema,启动迁移:')
    progress.info('   目的:消除"假通知"问题(path_id 因 chain 文本抖动被重算)')
    progress.info('   改动:path_id 改为 MD5(source_url)[:12],UNIQUE INDEX 改为')
    progress.info('        (source_id, source_url, path_id, file_name, md5_hash) 部分唯一')
    progress.info('   范围:仅当 system_settings.snapshots_migration_v3 缺失时执行,已迁移 noop')
    progress.info('=' * 70)

    db = get_db()

    # 4. 双重确认 marker(并发场景:另一个进程可能刚跑完)
    if already_migrated(db):
        progress.info('snapshots URL-based migration: marker appeared mid-check, skip')
        return

    # 5. dry-run 拿 plan
    t0 = time.time()
    try:
        plan_data = plan_migration(db)
    except Exception as e:
        progress.error(f'snapshots URL-based migration: plan() failed: {e}')
        logger.error(f'snapshots URL-based migration: plan() failed: {e}')
        return

    elapsed_plan = time.time() - t0
    n_rows = plan_data['row_total']
    n_dups = len(plan_data['delete_rows'])
    n_pid = len(plan_data['pathid_updates'])
    need_idx = plan_data['needs_index_rebuild']

    progress.info(f'📊 迁移计划 (dry-run,扫 {n_rows} 行,耗时 {elapsed_plan:.1f}s):')
    progress.info(f'   - 不同物理包数 (source_id, path_id, file, md5): {plan_data["row_unique"]}')
    progress.info(f'   - 需删除的重复行(同物理包不同 chain path_id): {n_dups}')
    progress.info(f'   - 需重算 path_id 的行: {n_pid}')
    progress.info(f'   - UNIQUE INDEX 需重建: {need_idx}')

    # 6. 应用迁移
    progress.info('⏳ 开始应用迁移...')
    t1 = time.time()
    try:
        result = apply_migration(db, plan_data)
    except Exception as e:
        elapsed_apply = time.time() - t1
        progress.error(f'❌ snapshots URL-based migration: apply() failed after {elapsed_apply:.1f}s: {e}')
        logger.error(f'snapshots URL-based migration: apply() failed after {elapsed_apply:.1f}s: {e}')
        return

    elapsed_apply = time.time() - t1
    progress.info('=' * 70)
    progress.info('✅ snapshots URL-based migration 完成:')
    progress.info(f'   - 删除重复行: {result["deleted"]}')
    progress.info(f'   - 引用改指(delivery_log/delayed_queue/digest_queue): {result["references_repointed"]}')
    progress.info(f'   - path_id 重算: {result["pathid_updated"]}')
    progress.info(f'   - UNIQUE INDEX 重建: {result["unique_index_rebuilt"]}')
    progress.info(f'   - 写 marker: system_settings.snapshots_migration_v3=1')
    progress.info(f'   - 总耗时: {elapsed_apply:.1f}s')
    progress.info('=' * 70)
    # summary logger 也打一行,方便监控聚合
    logger.info(
        f'snapshots URL-based migration: deleted={result["deleted"]} '
        f'repointed={result["references_repointed"]} '
        f'pathid_updated={result["pathid_updated"]} '
        f'index_rebuilt={result["unique_index_rebuilt"]} '
        f'took={elapsed_apply:.1f}s'
    )


def create_app(config_path=None):
    app = Flask(__name__,
                template_folder='src/web/templates',
                static_folder=os.path.join(os.path.dirname(__file__), 'web', 'static'))

    # ── CORS ─────────────────────────────────────────────────────────
    # Allow origins from env var, comma-separated. Empty = allow all (dev only).
    # Production should set: CORS_ORIGINS=https://app.example.com
    _cors_origins = os.getenv('CORS_ORIGINS', '')
    if _cors_origins:
        from flask_cors import CORS
        origins = [o.strip() for o in _cors_origins.split(',') if o.strip()]
        CORS(app, origins=origins, supports_credentials=True)
    else:
        # Dev mode: allow all origins but log warning
        from flask_cors import CORS
        CORS(app)
        app.logger.warning('⚠️  CORS_ORIGINS not set — allowing all origins. Set CORS_ORIGINS for production.')

    # ── gzip response compression ──────────────────────────────────
    # Activates Content-Encoding: gzip on JSON/text responses >= 500 bytes.
    # The /api/diff/fetch endpoint returns ~3 MB raw JSON for big rule pages;
    # gzip shrinks that to ~150 KB. Caveat: rules out bytes > 1024 are exempt
    # (the body is already compressed) — keep that out of the cache path.
    from flask_compress import Compress
    Compress(app)

    # ── Secrets validation ───────────────────────────────────────────
    _secret_key = os.getenv('MONITOR_SECRET_KEY', '')
    _jwt_secret = os.getenv('MONITOR_JWT_SECRET', '')
    if not _secret_key or len(_secret_key) != 64 or _secret_key == 'dev-secret-change-me':
        app.logger.warning('⚠️  MONITOR_SECRET_KEY is insecure or using dev fallback. Generate with: python3 -c "import secrets; print(secrets.token_hex(32))"')
    if not _jwt_secret or len(_jwt_secret) != 64 or _jwt_secret == 'dev-jwt-secret-change-me':
        app.logger.warning('⚠️  MONITOR_JWT_SECRET is insecure or using dev fallback. Generate with: python3 -c "import secrets; print(secrets.token_hex(32))"')

    # Config
    app.config['SECRET_KEY'] = os.getenv('MONITOR_SECRET_KEY', 'dev-secret-change-me')
    app.config['JWT_SECRET'] = os.getenv('MONITOR_JWT_SECRET', 'dev-jwt-secret-change-me')
    # Config — use env if set, otherwise probe for writable location
    def _data_dir():
        import sys as _sys
        if getattr(_sys, 'frozen', False):
            _exe_dir = os.path.dirname(_sys.executable)
            _probe = os.path.join(_exe_dir, 'data')
            try:
                os.makedirs(_probe, exist_ok=True)
                with open(os.path.join(_probe, '.probe'), 'w') as _f:
                    _f.write('')
                os.remove(os.path.join(_probe, '.probe'))
                return _probe
            except Exception:
                if _sys.platform == 'win32':
                    return os.environ.get('LOCALAPPDATA', os.path.expanduser('~/AppData/Local')) + '\\nsfocus-monitor-data'
                return os.path.join(os.path.expanduser('~/.local'), 'share', 'nsfocus-monitor-data')
        return os.getenv('MONITOR_DATA_DIR', os.path.join(os.path.dirname(__file__), '..', 'data'))

    app.config['DATA_DIR'] = _data_dir()
    app.config['LOG_DIR'] = os.getenv('MONITOR_LOG_DIR',
                                      os.path.join(os.path.dirname(__file__), '..', 'logs'))
    app.config['COLLECT_INTERVAL'] = int(os.getenv('MONITOR_COLLECT_INTERVAL', '4'))
    app.config['ROLLBACK_CONFIRM'] = int(os.getenv('MONITOR_ROLLBACK_CONFIRM', '2'))
    app.config['ATTACHMENT_MAX_SIZE'] = int(os.getenv('MONITOR_ATTACHMENT_MAX_SIZE', '10485760'))

    # Ensure directories exist
    os.makedirs(app.config['DATA_DIR'], exist_ok=True)
    os.makedirs(app.config['LOG_DIR'], exist_ok=True)

    # One-time migration: strip to_list from existing email channels.
    # Channel-level to_list was removed in the email provider template UI;
    # recipients now live on subscription rules (customer_emails) or are
    # specified per manual push. Idempotent — safe to run on every boot.
    try:
        _migrate_email_channel_to_list()
    except Exception as e:
        from src.core.logger import get_logger
        get_logger('migration').warning(f'email channel to_list migration skipped: {e}')

    # One-time migration: snapshots.path_id 改为纯 URL hash (消除假通知)
    # 升级后第一次启动跑,marker 写入 system_settings.snapshots_migration_v3 后 noop
    try:
        _migrate_snapshots_url_based_if_needed()
    except Exception as e:
        from src.core.logger import get_logger
        get_logger('migration').warning(f'snapshots URL-based migration skipped: {e}')

    # Hook Flask's own logger into our file handler so uncaught exceptions
    # and werkzeug messages appear in app.log
    from src.core.logger import get_logger
    monitor_logger = get_logger('flask')
    app.logger.handlers = monitor_logger.handlers  # inherit our handlers
    app.logger.setLevel(logging.DEBUG)

    # Also capture werkzeug access logs (suppress duplicate since we have access.log)
    werkzeug_logger = logging.getLogger('werkzeug')
    werkzeug_logger.handlers = monitor_logger.handlers
    werkzeug_logger.setLevel(logging.WARNING)  # Only warnings/errors, not every request

    # Health check
    @app.route('/api/health')
    def health():
        return {'code': 0, 'data': {'status': 'ok'}}

    # ---- Access log middleware ----
    _access_logger = None

    def _get_access_logger():
        nonlocal _access_logger
        if _access_logger is None:
            log_dir = app.config['LOG_DIR']
            os.makedirs(log_dir, exist_ok=True)
            _access_logger = logging.getLogger('access')
            _access_logger.setLevel(logging.INFO)
            _access_logger.propagate = False
            fh = logging.handlers.RotatingFileHandler(
                os.path.join(log_dir, 'access.log'),
                maxBytes=10 * 1024 * 1024,
                backupCount=10,
                encoding='utf-8'
            )
            fh.setFormatter(logging.Formatter('%(asctime)s %(message)s', datefmt='%Y-%m-%d %H:%M:%S'))
            _access_logger.addHandler(fh)
        return _access_logger

    @app.before_request
    def _access_start():
        g._req_start = time.time()

    @app.before_request
    def _api_localhost_guard():
        """If api_localhost_only is enabled, reject non-localhost /api/* requests.
        
        Uses request.remote_addr (TCP socket peer) — immune to Host/X-Forwarded-For forgery.
        """
        if not request.path.startswith('/api/'):
            return
        from src.models.database import query
        rows = query("SELECT value FROM system_settings WHERE key = 'api_localhost_only'")
        if not rows or rows[0]['value'] != '1':
            return
        # Check actual TCP peer address, not spoofable headers
        remote = request.remote_addr
        if remote not in ('127.0.0.1', '::1', 'localhost'):
            from flask import jsonify
            return jsonify({'code': 40300, 'message': 'API 仅限本地访问，当前已开启 localhost-only 模式'}), 403

    @app.after_request
    def _access_log(response):
        duration_ms = int((time.time() - g.get('_req_start', time.time())) * 1000)
        ip = request.headers.get('X-Forwarded-For', request.remote_addr) or '-'
        method = request.method
        path = request.path
        status = response.status_code
        _get_access_logger().info(f'{ip} {method} {path} {status} {duration_ms}ms')
        response.headers['X-Response-Time-ms'] = str(duration_ms)
        return response

    # Initialize database
    from src.models.database import init_db
    init_db(app.config['DATA_DIR'])
    from src.models import init_all_tables
    with app.app_context():
        init_all_tables()

    # Register routes
    from src.web.routes import register_routes
    register_routes(app)

    # Start scheduler
    from src.core.scheduler import start_scheduler
    app.scheduler = start_scheduler(app)

    # Start log scanner
    from src.core.log_scanner import start
    start()

    return app

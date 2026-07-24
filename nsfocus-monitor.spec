# -*-: utf-8 -*-
import sys
import os

block_cipher = None

a = Analysis(
    ['run.py'],
    pathex=[],
    binaries=[],
    datas=[
        ('data/initial_sources.json', 'data'),
        ('src/web/templates', 'src/web/templates'),
        ('src/web/static', 'src/web/static'),
        # 2026-07-24: 升级时启动期自动跑 snapshots 迁移,脚本必须进 exe 包
        ('scripts/migrate_snapshots_to_url_based.py', 'scripts'),
    ],
    hiddenimports=[
        'flask', 'flask_cors', 'flask_compress', 'apscheduler', 'requests',
        'beautifulsoup4', 'bs4', 'cryptography', 'jwt',
        'bcrypt', 'jinja2', 'markupsafe', 'werkzeug',
        'ordered_set', 'importlib_metadata',
    ],
    hookspath=[],
    hooksconfig={},
    key=block_cipher,
    runtime_hooks=[],
    excludes=[
        'tkinter', 'test', 'unittest', 'sqlite3.test',
        'email', 'xmlrpc', 'html.parser',
    ],
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    [],
    name='nsfocus-monitor',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    disable_windowed_traceback=False,
    console=False,
    cee_applies=False,
)

# Separate collection phase (needed for onefile to include datas correctly)
coll = COLLECT(
    exe,
    a.binaries,
    a.zipped_data,
    a.scripts,
    strip=False,
    upx=True,
    name='nsfocus-monitor-collected',
)
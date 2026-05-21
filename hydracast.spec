# hydracast.spec
import sys
from pathlib import Path
from PyInstaller.utils.hooks import collect_data_files, collect_submodules

block_cipher = None

# Collect all data files from google packages (they use pkg_resources)
google_datas = collect_data_files('google.auth')
google_datas += collect_data_files('google.oauth2')
holidays_datas = collect_data_files('holidays')

a = Analysis(
    ['hydracast.py'],
    pathex=['.'],
    binaries=[],
    datas=[
        # Your hc package non-py files (if any)
        ('hc', 'hc'),
        # resources folder
        ('resources', 'resources'),
        # bin folder (mediamtx.exe lives here)
        ('bin', 'bin'),
        # Collected package data
        *google_datas,
        *holidays_datas,
    ],
    hiddenimports=[
        # hc submodules (dynamic imports via __init__.py)
        'hc.compliance',
        'hc.constants',
        'hc.dependency',
        'hc.firewall',
        'hc.folder_scanner',
        'hc.folder_watcher',
        'hc.hc_system',
        'hc.json_manager',
        'hc.mailer',
        'hc.manager',
        'hc.mediamtx_cfg',
        'hc.models',
        'hc.resume_store',
        'hc.theme',
        'hc.tui',
        'hc.utils',
        'hc.watchdog',
        'hc.web',
        'hc.web_access_log',
        'hc.web_csvmanager',
        'hc.web_filemanager',
        'hc.web_handler',
        'hc.web_handlers_calendar',
        'hc.web_handlers_get',
        'hc.web_handlers_post',
        'hc.web_holiday_store',
        'hc.web_html',
        'hc.web_server',
        'hc.web_settings_manager',
        'hc.web_upload',
        'hc.worker',
        # google packages use namespace packages
        'google.auth.transport.requests',
        'google.oauth2.credentials',
        'googleapiclient.discovery',
        # Other likely-needed
        'pkg_resources.py2_warn',
        'holidays',
        'rich.console',
        'psutil',
        'ctypes',
        'ctypes.wintypes',
        'email.mime.multipart',
        'email.mime.text',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,        # one-dir mode (recommended)
    name='hydracast',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,                     # compress (optional, needs UPX installed)
    console=True,                 # keep console visible (TUI app)
    icon='resources/shourav.ico', # your existing icon
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='HydraCast',             # output folder name
)

# -*- mode: python ; coding: utf-8 -*-
"""
LocalAIAgent.spec - Production-Ready PyInstaller Configuration
==============================================================
Fixes ModuleNotFoundError for langchain_community and packaging.
"""

from PyInstaller.utils.hooks import collect_submodules, collect_data_files
import os
import sys

# ─────────────────────────────────────────────────────────────────────────────
# 1. COMPREHENSIVE HIDDEN IMPORTS
# ─────────────────────────────────────────────────────────────────────────────

hidden_imports = [
    'packaging',
    'packaging.version',
    'packaging.specifiers',
    'packaging.markers',
    'langchain_community',
    'langchain_community.embeddings',
    'langchain_community.embeddings.huggingface',
    'langchain_community.vectorstores',
    'langchain_community.vectorstores.chroma',
    'langchain_text_splitters',
    'sentence_transformers',
    'transformers',
    'torch',
    'numpy',
    'pydantic',
    'pydantic_core',
    'pydantic_settings',
]

# Auto-collect all submodules for complex packages
packages_to_collect = [
    'langchain_community.embeddings',
    'langchain_community.vectorstores',
    'transformers.models',
    'sentence_transformers.models'
]

for pkg in packages_to_collect:
    try:
        submodules = collect_submodules(pkg)
        hidden_imports.extend(submodules)
    except Exception:
        pass

# Deduplicate
hidden_imports = sorted(list(set(hidden_imports)))

# ─────────────────────────────────────────────────────────────────────────────
# 2. ANALYSIS
# ─────────────────────────────────────────────────────────────────────────────

a = Analysis(
    ['main.py'],
    pathex=[],
    binaries=[],
    datas=[],
    hiddenimports=hidden_imports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=['pytest', 'unittest', 'matplotlib', 'notebook'],
    noarchive=False,
    optimize=0,
)

# Include project sub-packages as data to ensure they are available in sys.path
project_dirs = [
    'configs', 'core', 'services', 'agents', 
    'engines', 'memory', 'pipelines', 'tools'
]
for d in project_dirs:
    if os.path.isdir(d):
        a.datas.append((d, d))

# ─────────────────────────────────────────────────────────────────────────────
# 3. BUILD
# ─────────────────────────────────────────────────────────────────────────────

pyz = PYZ(a.pure, a.zipped_data, cipher=None)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='LocalAIAgent',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

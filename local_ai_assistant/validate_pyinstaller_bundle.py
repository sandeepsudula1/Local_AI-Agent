#!/usr/bin/env python3
"""
validate_pyinstaller_bundle.py
==============================

Verify that PyInstaller .exe has all critical modules bundled.
Run this BEFORE deploying to production.

Usage:
    python validate_pyinstaller_bundle.py

This script:
1. Checks if app is frozen (running as .exe)
2. Lists bundled modules
3. Verifies critical modules are present
4. Tests import chains (packaging → transformers → huggingface)
5. Identifies potential runtime import failures
"""

import sys
import os
from pathlib import Path

def is_frozen():
    """Check if running as PyInstaller .exe."""
    return getattr(sys, 'frozen', False)

def get_bundle_path():
    """Get PyInstaller bundle directory."""
    if is_frozen():
        # sys.frozen=True means we're in the .exe
        bundle = sys._MEIPASS
        print(f"[FROZEN] Running as .exe - Bundle path: {bundle}")
        return Path(bundle)
    else:
        print("[SOURCE] Running as source code - not from .exe")
        return None

def list_bundled_modules(bundle_path, prefix=""):
    """List all bundled Python modules."""
    if not bundle_path:
        print("[INFO] Cannot list bundled modules (not frozen)")
        return set()
    
    bundled = set()
    
    # Look in base_library.zip (PyInstaller's module archive)
    import zipfile
    base_lib = bundle_path / 'base_library.zip'
    
    if base_lib.exists():
        try:
            with zipfile.ZipFile(base_lib, 'r') as zf:
                for name in zf.namelist():
                    # Extract module name (path/to/module.py -> path.to.module)
                    if name.endswith('.pyc') or name.endswith('.py'):
                        module_name = name.replace('/', '.').replace('.pyc', '').replace('.py', '')
                        if prefix and not module_name.startswith(prefix):
                            continue
                        bundled.add(module_name)
        except Exception as e:
            print(f"[WARNING] Could not read base_library.zip: {e}")
    
    return bundled

def check_module_imports():
    """Try importing critical modules and report results."""
    
    critical_modules = {
        'packaging': [
            'packaging',
            'packaging.version',
            'packaging.specifiers',
            'packaging.markers',
        ],
        'transformers': [
            'transformers',
            'transformers.models.auto',
            'transformers.models.auto.modeling_auto',
        ],
        'langchain_community': [
            'langchain_community',
            'langchain_community.embeddings',
            'langchain_community.embeddings.huggingface',
        ],
        'sentence_transformers': [
            'sentence_transformers',
        ],
        'chromadb': [
            'chromadb',
            'chromadb.api',
        ],
    }
    
    print("\n" + "="*70)
    print("CRITICAL MODULE IMPORT TEST")
    print("="*70 + "\n")
    
    results = {}
    failures = []
    
    for category, modules in critical_modules.items():
        print(f"[{category.upper()}]")
        results[category] = {}
        
        for module_name in modules:
            try:
                __import__(module_name)
                print(f"  OK   -> {module_name}")
                results[category][module_name] = True
            except ImportError as e:
                print(f"  FAIL -> {module_name}")
                print(f"         ERROR: {e}")
                results[category][module_name] = False
                failures.append((module_name, str(e)))
        
        print()
    
    return results, failures

def check_import_chains():
    """Test import chains (e.g., transformers -> packaging -> version)."""
    
    print("\n" + "="*70)
    print("IMPORT CHAIN TEST (Transitive Dependencies)")
    print("="*70 + "\n")
    
    chains = {
        'transformers -> packaging': [
            ('transformers', None),
            ('transformers.utils', None),
            ('transformers.models.auto.modeling_auto', 'packaging.version'),
        ],
        'sentence_transformers -> transformers': [
            ('sentence_transformers', 'transformers'),
            ('sentence_transformers.util', 'torch'),
        ],
        'langchain_community.embeddings.huggingface': [
            ('langchain_community.embeddings.huggingface', 'langchain_community'),
            ('langchain_community.embeddings.huggingface', 'sentence_transformers'),
        ],
    }
    
    failures = []
    
    for chain_name, imports in chains.items():
        print(f"[{chain_name}]")
        
        for module, check_import in imports:
            try:
                mod = __import__(module, fromlist=[''])
                if check_import:
                    # Verify expected submodule is accessible
                    try:
                        sub = __import__(check_import, fromlist=[''])
                        print(f"  OK   -> {module} (can access {check_import})")
                    except ImportError as e:
                        print(f"  FAIL -> {module} (cannot access {check_import})")
                        print(f"         ERROR: {e}")
                        failures.append((module, check_import, str(e)))
                else:
                    print(f"  OK   -> {module}")
            except ImportError as e:
                print(f"  FAIL -> {module}")
                print(f"         ERROR: {e}")
                failures.append((module, None, str(e)))
        
        print()
    
    return failures

def check_packaging_specifically():
    """Deep check of packaging module since it's the root issue."""
    
    print("\n" + "="*70)
    print("DEEP CHECK: 'packaging' MODULE")
    print("="*70 + "\n")
    
    try:
        import packaging
        print(f"OK   -> packaging imported")
        print(f"       Location: {packaging.__file__}")
        print(f"       Version: {packaging.__version__}")
        
        # Check critical submodules
        submodules = ['version', 'specifiers', 'markers', 'requirements']
        for sub in submodules:
            try:
                exec(f"from packaging import {sub}")
                print(f"OK   -> packaging.{sub}")
            except ImportError as e:
                print(f"FAIL -> packaging.{sub}: {e}")
                return False
        
        return True
    
    except ImportError as e:
        print(f"FAIL -> packaging not found: {e}")
        return False

def main():
    print("\n" + "="*70)
    print("PyInstaller Bundle Validation")
    print("="*70)
    
    # Check if frozen
    frozen = is_frozen()
    bundle_path = get_bundle_path()
    
    if not frozen:
        print("\n[WARNING] Not running as frozen .exe. To validate, run:")
        print("  .\dist\LocalAIAgent.exe")
        print("  (or copy to another folder and run there)")
    
    # Check packaging specifically (root issue)
    print("\n1. Validating PACKAGING module (root cause of previous errors):")
    packaging_ok = check_packaging_specifically()
    
    # Check critical modules
    print("\n2. Checking critical module imports:")
    import_results, import_failures = check_module_imports()
    
    # Check import chains
    print("\n3. Checking import chains (transitive dependencies):")
    chain_failures = check_import_chains()
    
    # Summary
    print("\n" + "="*70)
    print("VALIDATION SUMMARY")
    print("="*70 + "\n")
    
    total_failures = len(import_failures) + len(chain_failures)
    
    if total_failures == 0 and packaging_ok:
        print("SUCCESS! All critical modules are present and importable.")
        print("\nThe .exe should work correctly on other machines.")
        return 0
    else:
        print(f"ISSUES FOUND: {total_failures} failures\n")
        
        if not packaging_ok:
            print("CRITICAL: 'packaging' module is not properly bundled!")
            print("  Action: Add to .spec hiddenimports:")
            print("    'packaging', 'packaging.version', 'packaging.specifiers'")
        
        if import_failures:
            print(f"\nImport failures ({len(import_failures)}):")
            for module, error in import_failures:
                print(f"  - {module}: {error}")
        
        if chain_failures:
            print(f"\nImport chain failures ({len(chain_failures)}):")
            for module, check, error in chain_failures:
                print(f"  - {module}: {error}")
        
        return 1

if __name__ == '__main__':
    sys.exit(main())

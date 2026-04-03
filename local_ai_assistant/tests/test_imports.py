#!/usr/bin/env python
"""Quick test to check if main.py can be imported without errors."""
import sys
import traceback

try:
    print("Testing imports...")
    import main
    print("✓ main.py imported successfully")
except Exception as e:
    print(f"✗ Import failed: {e}")
    traceback.print_exc()
    sys.exit(1)

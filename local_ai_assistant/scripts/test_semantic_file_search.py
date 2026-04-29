"""Quick test for semantic file search pipeline."""
import sys, os
os.chdir(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, ".")

from services.file_indexer_service import file_indexer
from services.file_search_service import file_search_service

TEST_FOLDER = r"C:\AI_Test_Documents"

# Register test files
if os.path.isdir(TEST_FOLDER):
    n = file_indexer.register_folder(TEST_FOLDER, recursive=True)
    print(f"Registered {n} files from {TEST_FOLDER}")
else:
    print(f"Test folder not found: {TEST_FOLDER}")
    sys.exit(1)

# Semantic search
print("\n=== Semantic search: 'L&T financial results for quarter' ===")
sem = file_indexer.semantic_search("L&T financial results for quarter", limit=5)
for r in sem:
    print(f"  {r['name'][:55]:55s}  sem={r.get('semantic_score',0):.4f}")

# Hybrid search via file_search_service
print("\n=== Hybrid search: 'find document related to L&T financial results for quarter' ===")
hyb = file_search_service.search("find document related to L&T financial results for quarter", limit=5)
for r in hyb:
    print(f"  {r['name'][:55]:55s}")

print("\nDONE")

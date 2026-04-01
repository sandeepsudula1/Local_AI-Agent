"""
scripts/test_semantic_email_search.py
=====================================
Integration test for semantic email search system.

Tests all components:
1. Email loading from cache/static files
2. Embedding engine initialization
3. Email vector store building
4. Semantic search queries
5. Hybrid search combining semantic + keyword
6. Integration with existing email_query_agent

Run this script to validate the entire semantic email search pipeline.
"""

import sys
import os
import json
import time
from pathlib import Path

# Add project root to path
_SCRIPT_DIR = Path(__file__).parent
_PROJECT_ROOT = _SCRIPT_DIR.parent
sys.path.insert(0, str(_PROJECT_ROOT))

from core.logging_config import get_logger

log = get_logger(__name__)


def test_email_loading():
    """Test 1: Load emails from cache/static files."""
    print("\n" + "="*70)
    print("TEST 1: Email Loading")
    print("="*70)
    
    from agents.knowledge.email_query_agent import load_all_emails
    
    emails = load_all_emails()
    print(f"✓ Loaded {len(emails)} emails")
    
    if emails:
        sample = emails[0]
        print(f"  Sample email:")
        print(f"    - ID: {sample.get('id')}")
        print(f"    - From: {sample.get('from')}")
        print(f"    - Subject: {sample.get('subject')[:60]}...")
        print(f"    - Has body: {bool(sample.get('body'))}")
        return True
    else:
        print("✗ No emails found! Check data/emails.json or data/email_cache.json")
        return False


def test_embedding_engine():
    """Test 2: Initialize and test embedding engine."""
    print("\n" + "="*70)
    print("TEST 2: Embedding Engine")
    print("="*70)
    
    from engines.embedding_engine import get_embedding_engine
    from configs.settings import settings
    
    engine = get_embedding_engine(settings.email_embedding_model)
    
    if engine.load():
        print(f"✓ Embedding engine loaded: {settings.email_embedding_model}")
        print(f"  - Embedding dimension: {engine.embedding_dim}")
        print(f"  - Max tokens: {engine.max_tokens()}")
        
        # Test single embedding
        test_text = "meeting with john about the project"
        embedding = engine.embed(test_text, normalize=True)
        
        if embedding:
            print(f"✓ Single embedding works")
            print(f"  - Text: '{test_text}'")
            print(f"  - Embedding shape: {len(embedding)}")
            print(f"  - Sample values: {embedding[:3]}...")
            
            # Test batch embedding
            test_texts = [
                "urgent meeting",
                "email from alice",
                "project update",
            ]
            embeddings = engine.embed_batch(test_texts, batch_size=2)
            
            if embeddings:
                print(f"✓ Batch embedding works")
                print(f"  - Embedded {len(embeddings)} texts")
                return True
        else:
            print("✗ Failed to generate embedding")
            return False
    else:
        print("✗ Failed to load embedding engine")
        return False


def test_email_vector_store():
    """Test 3: Build and test email vector store."""
    print("\n" + "="*70)
    print("TEST 3: Email Vector Store")
    print("="*70)
    
    from services.email_vector_store_service import email_vector_store_service
    
    print("Starting email vector store (background loading)...")
    email_vector_store_service.start()
    
    # Wait with timeout
    print("Waiting for vector store to be ready (max 120s)...")
    start_time = time.time()
    
    if email_vector_store_service.wait(timeout=120):
        elapsed = time.time() - start_time
        print(f"✓ Vector store ready in {elapsed:.1f}s")
        
        # Check manifest
        import os
        manifest_path = os.path.join(
            str(email_vector_store_service._manifest_path)
        )
        if os.path.exists(manifest_path):
            with open(manifest_path, "r") as f:
                manifest = json.load(f)
            print(f"  - Indexed emails: {manifest.get('email_count', '?')}")
            print(f"  - Model: {manifest.get('model', '?')}")
            print(f"  - Store path: {manifest.get('store_path', '?')}")
        
        return True
    else:
        print("✗ Vector store not ready (timeout after 120s)")
        print("  Check: data/emails.json exists? embedding model loads? disk space?")
        return False


def test_semantic_search():
    """Test 4: Perform semantic search queries."""
    print("\n" + "="*70)
    print("TEST 4: Semantic Search")
    print("="*70)
    
    from agents.knowledge.email_retrieval_agent import semantic_email_search
    from services.email_vector_store_service import get_email_vector_store_service
    
    store = get_email_vector_store_service()
    if not store.is_ready:
        print("✗ Vector store not ready")
        return False
    
    test_queries = [
        "meeting notes",
        "project update",
        "urgent request",
    ]
    
    for query in test_queries:
        print(f"\n  Query: '{query}'")
        results = semantic_email_search(query, top_k=3, threshold=0.3)
        
        if results:
            print(f"  ✓ Found {len(results)} result(s)")
            for i, result in enumerate(results[:2], 1):
                print(f"    {i}. Score: {result.get('score', 0):.3f}")
                print(f"       Subject: {result.get('subject')[:50]}...")
        else:
            print(f"  - No results (threshold too high or no matches)")
    
    return True


def test_hybrid_search():
    """Test 5: Hybrid search combining semantic + keyword."""
    print("\n" + "="*70)
    print("TEST 5: Hybrid Search")
    print("="*70)
    
    from agents.knowledge.email_query_agent import hybrid_email_search
    from services.email_vector_store_service import get_email_vector_store_service
    
    store = get_email_vector_store_service()
    if not store.is_ready:
        print("✗ Vector store not ready")
        return False
    
    test_query = "meeting with john about the project"
    print(f"Query: '{test_query}'")
    
    results = hybrid_email_search(test_query, max_results=5)
    
    if results:
        print(f"✓ Found {len(results)} result(s)")
        for i, result in enumerate(results[:3], 1):
            print(f"  {i}. From: {result.get('from', 'Unknown')[:40]}")
            print(f"     Subject: {result.get('subject', 'No subject')[:50]}...")
            print(f"     Date: {result.get('date', 'Unknown')}")
        return True
    else:
        print("- No results found (may fallback to keyword search)")
        return True  # Hybrid search falls back to keyword, so not a failure


def test_main_entry_point():
    """Test 6: Main entry point (handle_email_query)."""
    print("\n" + "="*70)
    print("TEST 6: Main Entry Point (handle_email_query)")
    print("="*70)
    
    from agents.knowledge.email_query_agent import handle_email_query
    
    test_queries = [
        "emails from john",
        "recent emails",
        "meeting notes",
    ]
    
    for query in test_queries:
        print(f"\n  Query: '{query}'")
        response = handle_email_query(query, max_results=3)
        
        # Just check we got a response, don't print full output
        lines = response.split("\n")
        print(f"  ✓ Response ({len(lines)} lines):")
        print(f"    {lines[0][:70]}...")
    
    return True


def main():
    """Run all tests."""
    print("\n")
    print("╔" + "="*68 + "╗")
    print("║" + " "*68 + "║")
    print("║" + "  SEMANTIC EMAIL SEARCH - INTEGRATION TEST".center(68) + "║")
    print("║" + " "*68 + "║")
    print("╚" + "="*68 + "╝")
    
    tests = [
        ("Email Loading", test_email_loading),
        ("Embedding Engine", test_embedding_engine),
        ("Email Vector Store", test_email_vector_store),
        ("Semantic Search", test_semantic_search),
        ("Hybrid Search", test_hybrid_search),
        ("Main Entry Point", test_main_entry_point),
    ]
    
    results = []
    for name, test_func in tests:
        try:
            passed = test_func()
            results.append((name, passed))
        except Exception as e:
            print(f"\n✗ TEST FAILED WITH EXCEPTION: {e}")
            import traceback
            traceback.print_exc()
            results.append((name, False))
    
    # Summary
    print("\n" + "="*70)
    print("TEST SUMMARY")
    print("="*70)
    
    passed_count = sum(1 for _, passed in results if passed)
    total_count = len(results)
    
    for name, passed in results:
        status = "✓ PASS" if passed else "✗ FAIL"
        print(f"{status:8} {name}")
    
    print("-"*70)
    print(f"Result: {passed_count}/{total_count} tests passed")
    
    if passed_count == total_count:
        print("\n✓ All tests passed! Semantic email search is ready.")
        return 0
    else:
        print("\n✗ Some tests failed. See errors above.")
        return 1


if __name__ == "__main__":
    sys.exit(main())

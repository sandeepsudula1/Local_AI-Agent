"""
knowledge_graph/v2/demo.py
===========================
End-to-end demonstration of the minimal Knowledge Graph v2.

Run from this directory:
    python demo.py

Or from the project root:
    python knowledge_graph/v2/demo.py
"""

import sys
import os
import types

# ── Ensure we can import v2 without triggering the broken parent __init__.py ──
_v2_dir   = os.path.dirname(__file__)
_kg_dir   = os.path.dirname(_v2_dir)
_root_dir = os.path.dirname(_kg_dir)

if _root_dir not in sys.path:
    sys.path.insert(0, _root_dir)

# Register a stub for the parent package so Python doesn't run its __init__
if "knowledge_graph" not in sys.modules:
    stub = types.ModuleType("knowledge_graph")
    stub.__path__ = [_kg_dir]
    stub.__package__ = "knowledge_graph"
    sys.modules["knowledge_graph"] = stub

# Now we can safely import v2
from knowledge_graph.v2 import (  # noqa: E402
    extract_triples, query, graph_store,
    walk_graph, multihop_follow,
    get_context_for_llm, get_entity_summary,
    VALID_REASONING_PATHS, is_valid_chain,
)


def separator(title: str) -> None:
    print(f"\n{'-' * 55}")
    print(f"  {title}")
    print('-' * 55)


def main():
    graph_store.clear()  # fresh start each run

    # ── STEP 1: Extract triples ────────────────────────────────────────────
    separator("STEP 1 - Store triples")

    sentences = [
        "Sandeep is working on AI agent",
        "Sandeep is working on Local AI Assistant",
        "Sandeep owns Local AI Assistant",
        "Build feature is assigned to Sandeep",
        "AI agent uses AWS",
        "AI agent uses Python",
        "Local AI Assistant uses Streamlit",
        "Priya built recommendation engine",
        "Priya owns recommendation engine",
        "recommendation engine uses Redis",
    ]

    for sentence in sentences:
        triples = extract_triples(sentence)
        if triples:
            print(f"  OK  {triples[0]}")
        else:
            print(f"  [!] No match: {sentence!r}")

    # ── STEP 2: Direct + Reverse queries ──────────────────────────────────
    separator("STEP 2 - Direct and reverse queries")

    questions = [
        "What is Sandeep working on?",
        "What does Sandeep own?",
        "Who is working on AI agent?",         # reverse works_on
        "Who is working on Local AI Assistant?",
        "Who owns recommendation engine?",      # reverse owns
        "Who is assigned to Build feature?",    # reverse assigned_to
        "Who uses AWS?",                        # reverse uses
    ]

    for q in questions:
        print(f"  Q: {q}")
        print(f"  A: {query(q)}\n")

    # ── STEP 3: Multi-hop reasoning ────────────────────────────────────────
    separator("STEP 3 - Multi-hop: What does Sandeep use?")

    # Direct: Sandeep has no direct 'uses' edge
    # Multi-hop: Sandeep->works_on->AI agent->uses->AWS,Python
    #            Sandeep->works_on->Local AI Assistant->uses->Streamlit
    print(f"  Q: What does Sandeep use?")
    print(f"  A: {query('What does Sandeep use?')}\n")

    print(f"  Q: What does Priya use?")
    print(f"  A: {query('What does Priya use?')}\n")

    # ── STEP 4: BFS walk ──────────────────────────────────────────────────
    separator("STEP 4 - walk_graph(sandeep, max_hops=2)")

    paths = walk_graph("sandeep", max_hops=2)
    for subj, pred, obj in paths:
        print(f"  ({subj}) --[{pred}]--> ({obj})")

    # ── STEP 5: LLM context integration ───────────────────────────────────
    separator("STEP 5 - get_context_for_llm()")

    test_queries = [
        "What is Sandeep working on?",
        "Tell me about the AI agent project.",
        "What tools does Priya use?",
    ]

    for q in test_queries:
        context = get_context_for_llm(q)
        print(f"\n  Query  : {q!r}")
        print(f"  Context:\n{context}\n")

    # ── STEP 6: Entity summary ─────────────────────────────────────────────
    separator("STEP 6 - get_entity_summary()")

    for name in ["Sandeep", "AI agent", "Priya"]:
        print(f"\n  [{name}]")
        print(f"  {get_entity_summary(name)}")


    # ── STEP 7: Reasoning rules layer ─────────────────────────────────────
    separator("STEP 7 - Reasoning rules layer")

    print("  Active rules (VALID_REASONING_PATHS):")
    for rule in VALID_REASONING_PATHS:
        print(f"    {' -> '.join(rule)}")

    print()

    # Demonstrate: Priya owns recommendation engine -> uses -> Redis
    # This path (owns -> uses) is now in VALID_REASONING_PATHS
    print("  Q: What does Priya use?  (resolved via owns->uses rule)")
    print(f"  A: {query('What does Priya use?')}")
    print()

    # Demonstrate walk_graph with use_rules=True (filters to rule-allowed edges only)
    print("  walk_graph('priya', max_hops=2, use_rules=True):")
    for subj, pred, obj in walk_graph("priya", max_hops=2, use_rules=True):
        print(f"    ({subj}) --[{pred}]--> ({obj})")
    print()

    # Validate specific chains
    checks = [
        (["works_on", "uses"],  True),
        (["owns",     "uses"],  True),
        (["works_on", "owns"],  False),   # not in rules
    ]
    print("  is_valid_chain() checks:")
    for chain, expected in checks:
        result = is_valid_chain(chain)
        status = "OK" if result == expected else "FAIL"
        print(f"    [{status}] {' -> '.join(chain)} => {result}")


if __name__ == "__main__":
    main()


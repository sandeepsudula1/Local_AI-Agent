"""
knowledge_graph/v2/reasoning_rules.py
=======================================
Configurable reasoning rules for multi-hop graph traversal.

Instead of hardcoding "works_on → uses" everywhere, define all valid
predicate chains here.  The retriever and context builder consume these
rules automatically — extend this list to unlock new reasoning paths.

Format
------
Each entry in VALID_REASONING_PATHS is an ordered list of predicate strings
representing one valid multi-hop chain:

    ["A", "B"]  means:  entity -[A]-> middle -[B]-> result

Add new chains by appending rows; no other code needs to change.
"""

from __future__ import annotations
from typing import List, Set


# ── Configuration (edit here to extend reasoning) ──────────────────────────

VALID_REASONING_PATHS: List[List[str]] = [
    ["works_on", "uses"],          # person -[works_on]-> project -[uses]-> tool
    ["owns",     "uses"],          # person -[owns]->     project -[uses]-> tool
    ["assigned_to", "works_on"],   # task   -[assigned_to]-> person -[works_on]-> project
]


# ── Query helpers (consumed by graph_retriever) ─────────────────────────────

def paths_ending_with(predicate: str) -> List[List[str]]:
    """Return all valid chains whose last hop is `predicate`."""
    return [p for p in VALID_REASONING_PATHS if p[-1] == predicate]


def paths_starting_with(predicate: str) -> List[List[str]]:
    """Return all valid chains whose first hop is `predicate`."""
    return [p for p in VALID_REASONING_PATHS if p[0] == predicate]


def valid_predicates() -> Set[str]:
    """Return the flat set of all predicates that appear in any rule."""
    return {pred for path in VALID_REASONING_PATHS for pred in path}


def is_valid_chain(predicates: List[str]) -> bool:
    """Check whether an exact predicate sequence matches a known rule."""
    return list(predicates) in VALID_REASONING_PATHS

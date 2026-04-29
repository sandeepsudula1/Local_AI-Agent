"""
knowledge_graph/v2/__init__.py
================================
Public API surface for the minimal Knowledge Graph v2.
"""

from .entities import Entity, EntityType, make_entity
from .relationships import Triple, RelType
from .graph_store import graph_store
from .triple_extractor import extract_triples
from .graph_retriever import query, walk_graph, multihop_follow
from .llm_context import get_context_for_llm, get_entity_summary
from .reasoning_rules import (
    VALID_REASONING_PATHS,
    paths_ending_with,
    paths_starting_with,
    valid_predicates,
    is_valid_chain,
)

__all__ = [
    # Core types
    "Entity",
    "EntityType",
    "make_entity",
    "Triple",
    "RelType",
    # Storage
    "graph_store",
    # Extraction
    "extract_triples",
    # Retrieval
    "query",
    "walk_graph",
    "multihop_follow",
    # LLM integration
    "get_context_for_llm",
    "get_entity_summary",
    # Reasoning rules
    "VALID_REASONING_PATHS",
    "paths_ending_with",
    "paths_starting_with",
    "valid_predicates",
    "is_valid_chain",
]

"""
knowledge_graph/storage/
==========================
Storage layer using Neo4j for persistent knowledge graph.
"""

from .graph_store import GraphStore, graph_store

__all__ = ["GraphStore", "graph_store"]

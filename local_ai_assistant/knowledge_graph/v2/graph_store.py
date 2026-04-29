"""
knowledge_graph/v2/graph_store.py
==================================
Simple in-memory graph store with optional Neo4j backend.

Stores: entities (nodes) + triples (edges)
Falls back to pure Python dicts when Neo4j is unavailable.
"""

from __future__ import annotations

import os
import json
from pathlib import Path
from typing import Dict, List, Optional

from .entities import Entity, EntityType, make_entity
from .relationships import Triple, RelType


class GraphStore:
    """
    Minimal graph store.

    Priority:
        1. Neo4j (if bolt URI is configured and neo4j package is installed)
        2. In-memory Python dicts (default fallback)
    """

    def __init__(self):
        self._entities: Dict[str, Entity] = {}   # id -> Entity
        self._triples: List[Triple] = []
        self._save_path = Path("data/kg_store.json")

        self._driver = None
        self._neo4j_ok = False
        self._try_neo4j()
        self.load_graph()

    # ── Neo4j bootstrap ───────────────────────────────────────────────────

    def _try_neo4j(self) -> None:
        """Attempt a Neo4j connection; silently fall back on failure."""
        uri = os.getenv("NEO4J_URI", "")
        if not uri:
            return  # Not configured → skip entirely

        try:
            from neo4j import GraphDatabase  # type: ignore
            user = os.getenv("NEO4J_USER", "neo4j")
            pw   = os.getenv("NEO4J_PASSWORD", "")
            self._driver = GraphDatabase.driver(uri, auth=(user, pw))
            with self._driver.session() as s:
                s.run("RETURN 1")
            self._neo4j_ok = True
            print(f"[KG] Connected to Neo4j at {uri}")
            self._ensure_neo4j_constraints()
        except Exception as e:
            self._driver = None
            print(f"[KG] Neo4j not available ({e}). Using in-memory store.")

    def _ensure_neo4j_constraints(self) -> None:
        """Create basic uniqueness constraints once."""
        with self._driver.session() as s:
            s.run("CREATE CONSTRAINT IF NOT EXISTS FOR (e:Entity) REQUIRE e.id IS UNIQUE")

    # ── Entity operations ─────────────────────────────────────────────────

    def add_entity(self, entity: Entity) -> None:
        """Upsert an entity into the store."""
        if self._neo4j_ok:
            with self._driver.session() as s:
                s.run(
                    """
                    MERGE (e:Entity {id: $id})
                    SET e.type = $type, e.name = $name
                    """,
                    id=entity.id, type=entity.type.value, name=entity.name,
                )
        # Always keep local mirror for fast lookups
        self._entities[entity.id] = entity
        self.save_graph()

    def get_entity(self, entity_id: str) -> Optional[Entity]:
        return self._entities.get(entity_id)

    def all_entities(self) -> List[Entity]:
        return list(self._entities.values())

    # ── Triple operations ─────────────────────────────────────────────────

    def add_triple(self, triple: Triple) -> None:
        """Upsert a triple (deduplicates by subject-predicate-object)."""
        # Dedup in-memory
        if triple not in self._triples:
            self._triples.append(triple)

        if self._neo4j_ok:
            with self._driver.session() as s:
                s.run(
                    """
                    MATCH (a:Entity {id: $subj})
                    MATCH (b:Entity {id: $obj})
                    MERGE (a)-[r:REL {predicate: $pred}]->(b)
                    """,
                    subj=triple.subject,
                    pred=triple.predicate.value,
                    obj=triple.object,
                )
        self.save_graph()

    def all_triples(self) -> List[Triple]:
        return list(self._triples)

    # ── Query helpers ──────────────────────────────────────────────────────

    def triples_for_subject(self, entity_id: str) -> List[Triple]:
        """Return all triples where this entity is the subject."""
        return [t for t in self._triples if t.subject == entity_id]

    def triples_for_object(self, entity_id: str) -> List[Triple]:
        """Return all triples where this entity is the object."""
        return [t for t in self._triples if t.object == entity_id]

    def triples_by_predicate(self, predicate: RelType) -> List[Triple]:
        return [t for t in self._triples if t.predicate == predicate]

    # ── Utility ────────────────────────────────────────────────────────────

    def dump(self) -> None:
        """Print the current graph state (useful for debugging)."""
        print("\n-- Entities ----------------------------------------")
        for e in self._entities.values():
            print(f"  {e}")
        print("\n-- Triples -----------------------------------------")
        for t in self._triples:
            print(f"  {t}")
        print()

    def clear(self) -> None:
        """Wipe all data (useful in tests)."""
        self._entities.clear()
        self._triples.clear()
        if self._neo4j_ok:
            with self._driver.session() as s:
                s.run("MATCH (n) DETACH DELETE n")
        self.save_graph()

    def close(self) -> None:
        if self._driver:
            self._driver.close()

    # ── Persistence ────────────────────────────────────────────────────────

    def save_graph(self) -> None:
        """Save graph to JSON file."""
        try:
            self._save_path.parent.mkdir(parents=True, exist_ok=True)
            data = {
                "entities": [
                    {"id": e.id, "type": e.type.value, "name": e.name, "properties": e.properties}
                    for e in self._entities.values()
                ],
                "triples": [
                    {"subject": t.subject, "predicate": t.predicate.value, "object": t.object}
                    for t in self._triples
                ]
            }
            with open(self._save_path, 'w', encoding='utf-8') as f:
                json.dump(data, f, indent=2)
        except Exception as e:
            print(f"[KG] Failed to save graph: {e}")

    def load_graph(self) -> None:
        """Load graph from JSON file."""
        if not self._save_path.exists():
            return
        try:
            with open(self._save_path, 'r', encoding='utf-8') as f:
                data = json.load(f)
            
            for ed in data.get("entities", []):
                ent = Entity(id=ed["id"], type=EntityType(ed["type"]), name=ed["name"], properties=ed.get("properties", {}))
                self._entities[ent.id] = ent
                
            for td in data.get("triples", []):
                triple = Triple(subject=td["subject"], predicate=RelType(td["predicate"]), object=td["object"])
                if triple not in self._triples:
                    self._triples.append(triple)
                    
            print(f"[KG] Loaded graph from {self._save_path} ({len(self._entities)} entities, {len(self._triples)} triples)")
        except Exception as e:
            print(f"[KG] Failed to load graph: {e}")

# Singleton – import this from anywhere
graph_store = GraphStore()

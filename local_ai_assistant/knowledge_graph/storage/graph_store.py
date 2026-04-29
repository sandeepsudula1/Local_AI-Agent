"""
knowledge_graph/storage/graph_store.py
======================================
Neo4j-based graph store for the knowledge graph.

Provides: Entity creation, relationship management, graph querying
"""

from __future__ import annotations

import os
from typing import Optional, List, Dict, Any
from datetime import datetime

from core.logging_config import get_logger
from knowledge_graph.ontology import (
    Entity, Relationship, EntityType, RelationshipType,
    create_entity, create_relationship,
)

log = get_logger(__name__)


class GraphStore:
    """Neo4j-based graph database interface."""
    
    def __init__(self, uri: Optional[str] = None, user: str = "neo4j", password: str = ""):
        """Initialize Neo4j connection.
        
        Parameters
        ----------
        uri : str
            Neo4j connection URI (default: bolt://localhost:7687)
        user : str
            Neo4j username
        password : str
            Neo4j password
        """
        self.uri = uri or os.getenv("NEO4J_URI", "bolt://localhost:7687")
        self.user = user or os.getenv("NEO4J_USER", "neo4j")
        self.password = password or os.getenv("NEO4J_PASSWORD", "")
        
        self._driver = None
        self._connected = False
        
        try:
            self._connect()
        except Exception as e:
            log.warning(f"Neo4j not available: {e}. Using in-memory fallback.")
            self._use_fallback()
    
    def _connect(self) -> None:
        """Connect to Neo4j database."""
        try:
            from neo4j import GraphDatabase
            self._driver = GraphDatabase.driver(
                self.uri,
                auth=(self.user, self.password),
                encrypted=False,
            )
            # Verify connection
            with self._driver.session() as session:
                session.run("RETURN 1")
            self._connected = True
            log.info(f"Connected to Neo4j at {self.uri}")
        except ImportError:
            log.warning("neo4j package not installed. Using in-memory fallback.")
            self._use_fallback()
        except Exception as e:
            log.warning(f"Neo4j connection failed: {e}. Using in-memory fallback.")
            self._use_fallback()
    
    def _use_fallback(self) -> None:
        """Use in-memory storage when Neo4j is unavailable."""
        self._entities: Dict[str, Entity] = {}
        self._relationships: List[Relationship] = []
        self._connected = False
        log.info("Using in-memory fallback for knowledge graph")
    
    # ── Entity Operations ──────────────────────────────────────────────────
    
    def add_entity(self, entity: Entity) -> bool:
        """Add an entity to the graph."""
        if not self._connected:
            self._entities[entity.id] = entity
            return True
        
        try:
            with self._driver.session() as session:
                session.run(
                    """
                    CREATE (e:Entity {
                        id: $id,
                        type: $type,
                        name: $name,
                        status: $status,
                        confidence: $confidence,
                        created_at: $created_at,
                        updated_at: $updated_at,
                        source: $source
                    })
                    RETURN e
                    """,
                    id=entity.id,
                    type=entity.type.value,
                    name=entity.name,
                    status=entity.status.value,
                    confidence=entity.confidence,
                    created_at=entity.created_at.isoformat(),
                    updated_at=entity.updated_at.isoformat(),
                    source=entity.source,
                )
            log.debug(f"Added entity: {entity.id} ({entity.type.value})")
            return True
        except Exception as e:
            log.error(f"Failed to add entity: {e}")
            return False
    
    def get_entity(self, entity_id: str) -> Optional[Entity]:
        """Retrieve an entity by ID."""
        if not self._connected:
            return self._entities.get(entity_id)
        
        try:
            with self._driver.session() as session:
                result = session.run(
                    "MATCH (e:Entity {id: $id}) RETURN e",
                    id=entity_id,
                )
                record = result.single()
                if record:
                    data = dict(record["e"])
                    return self._record_to_entity(data)
        except Exception as e:
            log.error(f"Failed to get entity: {e}")
        return None
    
    def find_entities_by_type(self, entity_type: EntityType) -> List[Entity]:
        """Find all entities of a given type."""
        if not self._connected:
            return [e for e in self._entities.values() if e.type == entity_type]
        
        try:
            with self._driver.session() as session:
                result = session.run(
                    "MATCH (e:Entity {type: $type}) RETURN e",
                    type=entity_type.value,
                )
                entities = []
                for record in result:
                    data = dict(record["e"])
                    entity = self._record_to_entity(data)
                    if entity:
                        entities.append(entity)
                return entities
        except Exception as e:
            log.error(f"Failed to find entities: {e}")
        return []
    
    # ── Relationship Operations ────────────────────────────────────────────
    
    def add_relationship(self, relationship: Relationship) -> bool:
        """Add a relationship between two entities."""
        if not self._connected:
            self._relationships.append(relationship)
            return True
        
        try:
            with self._driver.session() as session:
                session.run(
                    """
                    MATCH (source:Entity {id: $source_id})
                    MATCH (target:Entity {id: $target_id})
                    CREATE (source)-[r:RELATIONSHIP {
                        type: $type,
                        confidence: $confidence,
                        created_at: $created_at,
                        source: $source
                    }]->(target)
                    RETURN r
                    """,
                    source_id=relationship.source_id,
                    target_id=relationship.target_id,
                    type=relationship.type.value,
                    confidence=relationship.confidence,
                    created_at=relationship.created_at.isoformat(),
                    source=relationship.source,
                )
            log.debug(f"Added relationship: {relationship.source_id} -[{relationship.type.value}]-> {relationship.target_id}")
            return True
        except Exception as e:
            log.error(f"Failed to add relationship: {e}")
            return False
    
    def get_related_entities(self, entity_id: str, rel_type: Optional[RelationshipType] = None, depth: int = 1) -> List[Relationship]:
        """Get relationships for an entity."""
        if not self._connected:
            results = [r for r in self._relationships if r.source_id == entity_id]
            if rel_type:
                results = [r for r in results if r.type == rel_type]
            return results
        
        try:
            rel_filter = f"type: '{rel_type.value}'" if rel_type else ""
            pattern = f"-[r{{{rel_filter}}}]->" if rel_type else "-[r]->"
            
            with self._driver.session() as session:
                result = session.run(
                    f"MATCH (e:Entity {{id: $id}}){pattern}(target) RETURN r, target",
                    id=entity_id,
                )
                relationships = []
                for record in result:
                    rel_data = dict(record["r"])
                    rel = Relationship(
                        source_id=entity_id,
                        target_id=rel_data.get("target_id", ""),
                        type=RelationshipType(rel_data["type"]),
                        confidence=rel_data.get("confidence", 1.0),
                    )
                    relationships.append(rel)
                return relationships
        except Exception as e:
            log.error(f"Failed to get relationships: {e}")
        return []
    
    def get_entity_context(self, entity_id: str, depth: int = 2) -> Dict[str, Any]:
        """Get full context around an entity (entity + related entities + relationships)."""
        entity = self.get_entity(entity_id)
        if not entity:
            return {}
        
        relationships = self.get_related_entities(entity_id, depth=depth)
        related_entities = [self.get_entity(r.target_id) for r in relationships]
        related_entities = [e for e in related_entities if e]
        
        return {
            "entity": entity,
            "relationships": relationships,
            "related_entities": related_entities,
            "context_depth": depth,
        }
    
    # ── Helper Methods ─────────────────────────────────────────────────────
    
    def _record_to_entity(self, data: Dict[str, Any]) -> Optional[Entity]:
        """Convert Neo4j record to Entity object."""
        try:
            entity_type = EntityType(data.get("type"))
            entity = create_entity(
                entity_type=entity_type,
                entity_id=data.get("id"),
                name=data.get("name"),
            )
            entity.confidence = data.get("confidence", 1.0)
            entity.source = data.get("source")
            return entity
        except Exception as e:
            log.error(f"Failed to convert record to entity: {e}")
            return None
    
    def close(self) -> None:
        """Close database connection."""
        if self._driver:
            try:
                self._driver.close()
                log.info("Closed Neo4j connection")
            except Exception as e:
                log.error(f"Error closing Neo4j: {e}")


# Module-level singleton
graph_store = GraphStore()

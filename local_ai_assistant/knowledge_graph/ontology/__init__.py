"""
knowledge_graph/ontology/
==========================
Ontology layer defining entities and relationships.

Exports:
- EntityType, Entity, User, Project, Task, Document, Action
- RelationshipType, Relationship
- Factory functions for creating typed instances
"""

from .entities import (
    EntityType,
    EntityStatus,
    Entity,
    User,
    Project,
    Task,
    Document,
    Action,
    create_entity,
    ENTITY_CLASSES,
)
from .relationships import (
    RelationshipType,
    Relationship,
    create_relationship,
    RELATIONSHIP_TEMPLATES,
)

__all__ = [
    "EntityType",
    "EntityStatus",
    "Entity",
    "User",
    "Project",
    "Task",
    "Document",
    "Action",
    "create_entity",
    "ENTITY_CLASSES",
    "RelationshipType",
    "Relationship",
    "create_relationship",
    "RELATIONSHIP_TEMPLATES",
]

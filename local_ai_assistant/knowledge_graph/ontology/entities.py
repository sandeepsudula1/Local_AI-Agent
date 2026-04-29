"""
knowledge_graph/ontology/entities.py
=====================================
Entity type definitions for the ontology.

Defines: User, Task, Project, Document, Action, Event
"""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional, Dict, Any
from enum import Enum


class EntityType(str, Enum):
    """Core entity types in the ontology."""
    USER = "User"
    PROJECT = "Project"
    TASK = "Task"
    EMAIL = "Email"
    DOCUMENT = "Document"
    ACTION = "Action"
    EVENT = "Event"
    CONCEPT = "Concept"
    LOCATION = "Location"
    ORGANIZATION = "Organization"


class EntityStatus(str, Enum):
    """Status of an entity."""
    ACTIVE = "active"
    ARCHIVED = "archived"
    DELETED = "deleted"
    PENDING = "pending"


@dataclass
class Entity:
    """Base entity in the knowledge graph."""
    
    id: str                                    # Unique identifier
    type: EntityType                           # Entity classification
    name: str                                  # Human-readable name
    properties: Dict[str, Any] = field(default_factory=dict)  # Additional properties
    status: EntityStatus = EntityStatus.ACTIVE
    created_at: datetime = field(default_factory=datetime.now)
    updated_at: datetime = field(default_factory=datetime.now)
    confidence: float = 1.0                    # Trust score (0-1)
    source: Optional[str] = None               # Where this entity came from
    
    def __hash__(self):
        return hash(self.id)
    
    def __eq__(self, other):
        if not isinstance(other, Entity):
            return False
        return self.id == other.id and self.type == other.type


@dataclass
class User(Entity):
    """User entity with personal information."""

    email: Optional[str] = None
    preferences: Dict[str, Any] = field(default_factory=dict)
    roles: list = field(default_factory=list)
    type: EntityType = field(default=EntityType.USER)

    def __post_init__(self):
        if not self.type:
            self.type = EntityType.USER


@dataclass
class Project(Entity):
    """Project entity with goals and scope."""

    description: Optional[str] = None
    goal: Optional[str] = None
    target_audience: Optional[str] = None
    tags: list = field(default_factory=list)
    type: EntityType = field(default=EntityType.PROJECT)
    status: EntityStatus = field(default=EntityStatus.ACTIVE)

    def __post_init__(self):
        if not self.type:
            self.type = EntityType.PROJECT


@dataclass
class Task(Entity):
    """Task entity with execution context."""

    description: Optional[str] = None
    priority: str = "medium"
    due_date: Optional[datetime] = None
    type: EntityType = field(default=EntityType.TASK)
    status: EntityStatus = field(default=EntityStatus.PENDING)

    def __post_init__(self):
        if not self.type:
            self.type = EntityType.TASK


@dataclass
class Document(Entity):
    """Document entity with content metadata."""

    path: Optional[str] = None
    content_preview: Optional[str] = None
    file_type: Optional[str] = None
    size_bytes: Optional[int] = None
    type: EntityType = field(default=EntityType.DOCUMENT)

    def __post_init__(self):
        if not self.type:
            self.type = EntityType.DOCUMENT


@dataclass
class Action(Entity):
    """Action entity representing what a user did."""

    action_type: str = "generic"
    target_id: Optional[str] = None
    timestamp: datetime = field(default_factory=datetime.now)
    type: EntityType = field(default=EntityType.ACTION)

    def __post_init__(self):
        if not self.type:
            self.type = EntityType.ACTION


# Entity factory for creating entities from type
ENTITY_CLASSES = {
    EntityType.USER: User,
    EntityType.PROJECT: Project,
    EntityType.TASK: Task,
    EntityType.DOCUMENT: Document,
    EntityType.ACTION: Action,
    EntityType.EVENT: Entity,
    EntityType.CONCEPT: Entity,
    EntityType.LOCATION: Entity,
    EntityType.ORGANIZATION: Entity,
}


def create_entity(entity_type: EntityType, entity_id: str, name: str, **kwargs) -> Entity:
    """Factory function to create appropriately typed entities."""
    entity_class = ENTITY_CLASSES.get(entity_type, Entity)
    return entity_class(id=entity_id, type=entity_type, name=name, **kwargs)

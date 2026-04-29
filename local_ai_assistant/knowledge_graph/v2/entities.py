"""
knowledge_graph/v2/entities.py
==============================
Minimal entity definitions.

Supported types: Person, Project, Task
"""

from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, Any


class EntityType(str, Enum):
    PERSON = "Person"
    PROJECT = "Project"
    TASK = "Task"


@dataclass
class Entity:
    """A node in the knowledge graph."""

    id: str           # Unique key, e.g. "sandeep"
    type: EntityType  # Person | Project | Task
    name: str         # Human-readable label
    properties: Dict[str, Any] = field(default_factory=dict)

    def __hash__(self):
        return hash(self.id)

    def __eq__(self, other):
        return isinstance(other, Entity) and self.id == other.id

    def __repr__(self):
        return f"Entity({self.type.value}:{self.name!r})"

import re

def normalize_entity(name: str) -> str:
    """Normalize entity name: lowercase, remove stop words, spaces to underscores."""
    low = name.lower()
    low = re.sub(r'\b(the|a|an)\b', '', low)
    low = re.sub(r'\s+', ' ', low).strip()
    return low.replace(" ", "_")

def make_entity(name: str, entity_type: EntityType) -> Entity:
    """Create an entity with a normalised id from its name."""
    entity_id = normalize_entity(name)
    return Entity(id=entity_id, type=entity_type, name=name)

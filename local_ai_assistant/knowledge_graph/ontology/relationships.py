"""
knowledge_graph/ontology/relationships.py
==========================================
Relationship type definitions for the ontology.

Defines relationships between entities:
- CREATED_BY, ASSIGNED_TO, PART_OF
- RELATED_TO, SIMILAR_TO, OPPOSITE_OF
- etc.
"""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional, Dict, Any
from enum import Enum


class RelationshipType(str, Enum):
    """Types of relationships between entities."""
    
    # Ownership/Assignment
    CREATED_BY = "CREATED_BY"
    ASSIGNED_TO = "ASSIGNED_TO"
    OWNED_BY = "OWNED_BY"
    
    # Structural
    PART_OF = "PART_OF"
    CONTAINS = "CONTAINS"
    COMPOSED_OF = "COMPOSED_OF"
    
    # Temporal
    PRECEDES = "PRECEDES"
    FOLLOWS = "FOLLOWS"
    CONCURRENT_WITH = "CONCURRENT_WITH"
    
    # Semantic
    RELATED_TO = "RELATED_TO"
    SIMILAR_TO = "SIMILAR_TO"
    OPPOSITE_OF = "OPPOSITE_OF"
    SPECIALIZES = "SPECIALIZES"
    GENERALIZES = "GENERALIZES"
    
    # Action
    EXECUTES = "EXECUTES"
    DEPENDS_ON = "DEPENDS_ON"
    BLOCKS = "BLOCKS"
    INFLUENCES = "INFLUENCES"
    
    # Reference
    REFERENCES = "REFERENCES"
    MENTIONED_IN = "MENTIONED_IN"
    ATTRIBUTED_TO = "ATTRIBUTED_TO"


@dataclass
class Relationship:
    """A relationship between two entities in the knowledge graph."""
    
    source_id: str                            # From entity ID
    target_id: str                            # To entity ID
    type: RelationshipType                    # Relationship type
    properties: Dict[str, Any] = field(default_factory=dict)  # Additional properties
    confidence: float = 1.0                   # Trust score (0-1)
    created_at: datetime = field(default_factory=datetime.now)
    source: Optional[str] = None              # Where this relationship came from
    
    def __hash__(self):
        return hash((self.source_id, self.target_id, self.type.value))
    
    def __eq__(self, other):
        if not isinstance(other, Relationship):
            return False
        return (
            self.source_id == other.source_id and
            self.target_id == other.target_id and
            self.type == other.type
        )
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for storage/display."""
        return {
            "source_id": self.source_id,
            "target_id": self.target_id,
            "type": self.type.value,
            "properties": self.properties,
            "confidence": self.confidence,
            "created_at": self.created_at.isoformat(),
            "source": self.source,
        }


# Predefined relationship templates
RELATIONSHIP_TEMPLATES = {
    RelationshipType.CREATED_BY: {
        "description": "Entity was created by someone",
        "reverse": "CREATES",
    },
    RelationshipType.ASSIGNED_TO: {
        "description": "Task/action assigned to a user",
        "reverse": "ASSIGNED_FROM",
    },
    RelationshipType.PART_OF: {
        "description": "Entity is part of another entity",
        "reverse": "CONTAINS",
    },
    RelationshipType.RELATED_TO: {
        "description": "General semantic relationship",
        "reverse": "RELATED_TO",  # Symmetric
    },
    RelationshipType.DEPENDS_ON: {
        "description": "Entity depends on another",
        "reverse": "DEPENDED_BY",
    },
}


def create_relationship(
    source_id: str,
    target_id: str,
    rel_type: RelationshipType,
    confidence: float = 1.0,
    **properties
) -> Relationship:
    """Factory function to create relationships."""
    return Relationship(
        source_id=source_id,
        target_id=target_id,
        type=rel_type,
        confidence=confidence,
        properties=properties,
    )

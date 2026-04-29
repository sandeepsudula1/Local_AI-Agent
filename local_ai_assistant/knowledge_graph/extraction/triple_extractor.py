"""
knowledge_graph/extraction/triple_extractor.py
===============================================
Extract structured triples (subject-predicate-object) from text.

This is the core of the memory extraction pipeline that converts
natural language into graph triples.

Types of triples:
- Episodic: Events that happened (User did X at time Y)
- Semantic: Knowledge facts (Plant is a type of Organism)
- Procedural: How to do something (Steps to build X)
"""

from __future__ import annotations

import re
from typing import List, Dict, Any, Optional, Tuple
from dataclasses import dataclass
from enum import Enum

from core.logging_config import get_logger
from knowledge_graph.ontology import (
    Entity, Relationship, EntityType, RelationshipType,
    create_entity, create_relationship,
)

log = get_logger(__name__)


class MemoryType(str, Enum):
    """Classification of memory types."""
    EPISODIC = "episodic"      # Events/experiences
    SEMANTIC = "semantic"      # Knowledge/facts
    PROCEDURAL = "procedural"  # How-to/processes


@dataclass
class Triple:
    """A subject-predicate-object triple."""
    
    subject: str
    predicate: str
    object: str
    memory_type: MemoryType = MemoryType.SEMANTIC
    confidence: float = 0.8
    context: Dict[str, Any] = None
    
    def __post_init__(self):
        if self.context is None:
            self.context = {}
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary."""
        return {
            "subject": self.subject,
            "predicate": self.predicate,
            "object": self.object,
            "memory_type": self.memory_type.value,
            "confidence": self.confidence,
            "context": self.context,
        }


class TripleExtractor:
    """Extract triples from conversational text."""
    
    # Patterns for different triple types
    SUBJECT_PATTERNS = [
        r"\b([A-Z][a-z]+(?:\s+[A-Z][a-z]+)*)\b",  # User names
        r"\b(?:I|user|assistant)\b",
        r"\b([\w\s]+?)\s+(?:is|has|wants|needs|created|built)",
    ]
    
    # Intent patterns for semantic relationships
    INTENT_PATTERNS = {
        "wants": ("wants_to", MemoryType.EPISODIC),
        "needs": ("needs", MemoryType.EPISODIC),
        "likes": ("likes", MemoryType.SEMANTIC),
        "dislikes": ("dislikes", MemoryType.SEMANTIC),
        "is": ("is_type_of", MemoryType.SEMANTIC),
        "contains": ("contains", MemoryType.SEMANTIC),
        "depends_on": ("depends_on", MemoryType.SEMANTIC),
        "works_with": ("works_with", MemoryType.SEMANTIC),
        "for": ("intended_for", MemoryType.SEMANTIC),
    }
    
    def __init__(self):
        """Initialize the triple extractor."""
        self.extracted_triples: List[Triple] = []
    
    def extract(self, text: str) -> List[Triple]:
        """Extract all triples from text.
        
        Parameters
        ----------
        text : str
            Input text to extract triples from
        
        Returns
        -------
        List[Triple]
            Extracted triples
        """
        self.extracted_triples = []
        
        # Normalize text
        text = text.strip()
        if not text:
            return []
        
        # Extract different types of triples
        self._extract_goal_triples(text)
        self._extract_property_triples(text)
        self._extract_relationship_triples(text)
        
        log.debug(f"Extracted {len(self.extracted_triples)} triples from text")
        return self.extracted_triples
    
    def _extract_goal_triples(self, text: str) -> None:
        """Extract goal/want/need triples.
        
        Example: "I want to build a plant identification app"
        → Triple(subject="User", predicate="wants", object="build plant identification app")
        """
        patterns = [
            r"(?:I|user|we)\s+(?:want|need|goal|plan|aim)\s+(?:to\s+)?(.+?)(?:\.|,|$)",
            r"(?:build|create|develop|make)\s+(?:a|an)?\s+(.+?)(?:\s+for|\s+to|\.|,|$)",
        ]
        
        for pattern in patterns:
            matches = re.finditer(pattern, text, re.IGNORECASE)
            for match in matches:
                obj = match.group(1).strip()
                if obj and len(obj) > 3:
                    triple = Triple(
                        subject="User",
                        predicate="wants_to_create",
                        object=obj,
                        memory_type=MemoryType.EPISODIC,
                        confidence=0.85,
                    )
                    self.extracted_triples.append(triple)
    
    def _extract_property_triples(self, text: str) -> None:
        """Extract property assignments.
        
        Example: "my project targets children"
        → Triple(subject="project", predicate="targets", object="children")
        """
        patterns = [
            r"(?:my|the)\s+(\w+)\s+(?:is|targets?|focuses?\s+on|aims?\s+at)\s+(.+?)(?:\.|,|$)",
            r"(\w+)\s+for\s+(.+?)(?:\s+audience|\s+users?|\.|,|$)",
        ]
        
        for pattern in patterns:
            matches = re.finditer(pattern, text, re.IGNORECASE)
            for match in matches:
                subject = match.group(1).strip()
                obj = match.group(2).strip()
                if subject and obj and len(obj) > 2:
                    triple = Triple(
                        subject=subject,
                        predicate="targets",
                        object=obj,
                        memory_type=MemoryType.SEMANTIC,
                        confidence=0.8,
                    )
                    self.extracted_triples.append(triple)
    
    def _extract_relationship_triples(self, text: str) -> None:
        """Extract semantic relationships.
        
        Example: "plants are organisms"
        → Triple(subject="plants", predicate="is_type_of", object="organisms")
        """
        for intent_word, (predicate, mem_type) in self.INTENT_PATTERNS.items():
            pattern = rf"(\w+(?:\s+\w+)?)\s+{intent_word}\s+(?:a|an|the)?\s*(.+?)(?:\.|,|$)"
            matches = re.finditer(pattern, text, re.IGNORECASE)
            
            for match in matches:
                subject = match.group(1).strip()
                obj = match.group(2).strip()
                
                if subject and obj and len(obj) > 2:
                    triple = Triple(
                        subject=subject,
                        predicate=predicate,
                        object=obj,
                        memory_type=mem_type,
                        confidence=0.75,
                    )
                    self.extracted_triples.append(triple)
    
    def to_entities_and_relationships(self, triples: List[Triple]) -> Tuple[List[Entity], List[Relationship]]:
        """Convert triples to Entity and Relationship objects.
        
        Parameters
        ----------
        triples : List[Triple]
            Triples to convert
        
        Returns
        -------
        Tuple[List[Entity], List[Relationship]]
            Entities and relationships suitable for graph storage
        """
        entities: Dict[str, Entity] = {}
        relationships: List[Relationship] = []
        
        for triple in triples:
            # Create subject entity
            subject_id = f"{triple.subject.lower().replace(' ', '_')}"
            if subject_id not in entities:
                entities[subject_id] = create_entity(
                    entity_type=self._infer_entity_type(triple.subject),
                    entity_id=subject_id,
                    name=triple.subject,
                    confidence=triple.confidence,
                )
            
            # Create object entity
            object_id = f"{triple.object.lower().replace(' ', '_')}"
            if object_id not in entities:
                entities[object_id] = create_entity(
                    entity_type=self._infer_entity_type(triple.object),
                    entity_id=object_id,
                    name=triple.object,
                    confidence=triple.confidence,
                )
            
            # Create relationship
            rel_type = self._predicate_to_relationship_type(triple.predicate)
            rel = create_relationship(
                source_id=subject_id,
                target_id=object_id,
                rel_type=rel_type,
                confidence=triple.confidence,
                memory_type=triple.memory_type.value,
            )
            relationships.append(rel)
        
        return list(entities.values()), relationships
    
    def _infer_entity_type(self, name: str) -> EntityType:
        """Infer entity type from name."""
        name_lower = name.lower()
        
        if any(word in name_lower for word in ["project", "app", "application", "system"]):
            return EntityType.PROJECT
        elif any(word in name_lower for word in ["task", "action", "step"]):
            return EntityType.TASK
        elif any(word in name_lower for word in ["email", "message"]):
            return EntityType.EMAIL
        elif any(word in name_lower for word in ["document", "file", "book"]):
            return EntityType.DOCUMENT
        elif any(word in name_lower for word in ["user", "person", "i", "me"]):
            return EntityType.USER
        else:
            return EntityType.CONCEPT
    
    def _predicate_to_relationship_type(self, predicate: str) -> RelationshipType:
        """Convert predicate string to relationship type."""
        predicate_lower = predicate.lower()
        
        mapping = {
            "wants": RelationshipType.RELATED_TO,
            "wants_to_create": RelationshipType.RELATED_TO,
            "targets": RelationshipType.RELATED_TO,
            "is_type_of": RelationshipType.SPECIALIZES,
            "contains": RelationshipType.CONTAINS,
            "depends_on": RelationshipType.DEPENDS_ON,
            "works_with": RelationshipType.RELATED_TO,
            "intended_for": RelationshipType.RELATED_TO,
        }
        
        return mapping.get(predicate_lower, RelationshipType.RELATED_TO)


# Module-level singleton
triple_extractor = TripleExtractor()

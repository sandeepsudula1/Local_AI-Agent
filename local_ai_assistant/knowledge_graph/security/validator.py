"""
knowledge_graph/security/validator.py
======================================
Validate and sanitize data before storing in the graph.

Prevents injection attacks, malformed data, and unsafe operations.
"""

from __future__ import annotations

import re
from typing import Optional, Tuple

from core.logging_config import get_logger
from knowledge_graph.ontology import Entity, Relationship

log = get_logger(__name__)


class DataValidator:
    """Validate data before storage in knowledge graph."""
    
    # Security constraints
    MAX_ENTITY_NAME_LENGTH = 256
    MAX_PROPERTY_VALUE_LENGTH = 1024
    MIN_CONFIDENCE = 0.0
    MAX_CONFIDENCE = 1.0
    
    # Unsafe patterns
    UNSAFE_PATTERNS = [
        r"<script",
        r"javascript:",
        r"onclick",
        r"onerror",
        r"'\s*or\s*'",  # SQL injection
        r"--",  # SQL comments
    ]
    
    # Restricted entity names
    RESTRICTED_WORDS = {
        "system", "admin", "root", "superuser",
        "delete", "drop", "truncate",
    }
    
    @classmethod
    def validate_entity(cls, entity: Entity) -> Tuple[bool, Optional[str]]:
        """Validate an entity before storage.
        
        Parameters
        ----------
        entity : Entity
            Entity to validate
        
        Returns
        -------
        Tuple[bool, Optional[str]]
            (is_valid, error_message)
        """
        # Check name
        if not entity.name or len(entity.name) == 0:
            return False, "Entity name cannot be empty"
        
        if len(entity.name) > cls.MAX_ENTITY_NAME_LENGTH:
            return False, f"Entity name too long (max {cls.MAX_ENTITY_NAME_LENGTH})"
        
        # Check for unsafe patterns
        unsafe = cls._check_unsafe_content(entity.name)
        if unsafe:
            return False, f"Entity name contains unsafe content: {unsafe}"
        
        # Check for restricted words
        name_lower = entity.name.lower()
        for restricted in cls.RESTRICTED_WORDS:
            if restricted in name_lower:
                log.warning(f"Entity name contains restricted word: {restricted}")
        
        # Check ID format
        if not cls._is_valid_id(entity.id):
            return False, "Entity ID contains invalid characters"
        
        # Check confidence score
        if not (cls.MIN_CONFIDENCE <= entity.confidence <= cls.MAX_CONFIDENCE):
            return False, f"Confidence must be between {cls.MIN_CONFIDENCE} and {cls.MAX_CONFIDENCE}"
        
        # Check properties
        for key, value in entity.properties.items():
            if isinstance(value, str) and len(value) > cls.MAX_PROPERTY_VALUE_LENGTH:
                return False, f"Property value too long for key '{key}'"
            
            unsafe = cls._check_unsafe_content(value)
            if unsafe:
                return False, f"Property '{key}' contains unsafe content: {unsafe}"
        
        return True, None
    
    @classmethod
    def validate_relationship(cls, relationship: Relationship) -> Tuple[bool, Optional[str]]:
        """Validate a relationship before storage.
        
        Parameters
        ----------
        relationship : Relationship
            Relationship to validate
        
        Returns
        -------
        Tuple[bool, Optional[str]]
            (is_valid, error_message)
        """
        # Check IDs
        if not cls._is_valid_id(relationship.source_id):
            return False, "Source ID contains invalid characters"
        
        if not cls._is_valid_id(relationship.target_id):
            return False, "Target ID contains invalid characters"
        
        # Check confidence
        if not (cls.MIN_CONFIDENCE <= relationship.confidence <= cls.MAX_CONFIDENCE):
            return False, f"Confidence must be between {cls.MIN_CONFIDENCE} and {cls.MAX_CONFIDENCE}"
        
        # Can't link to self
        if relationship.source_id == relationship.target_id:
            return False, "Cannot create self-referencing relationship"
        
        return True, None
    
    @classmethod
    def sanitize_string(cls, text: str, max_length: int = 256) -> str:
        """Sanitize string input.
        
        Parameters
        ----------
        text : str
            Text to sanitize
        max_length : int
            Maximum length after sanitization
        
        Returns
        -------
        str
            Sanitized text
        """
        if not isinstance(text, str):
            return str(text)[:max_length]
        
        # Remove leading/trailing whitespace
        text = text.strip()
        
        # Remove null bytes
        text = text.replace("\x00", "")
        
        # Limit length
        if len(text) > max_length:
            text = text[:max_length]
        
        return text
    
    @classmethod
    def _check_unsafe_content(cls, content: str) -> Optional[str]:
        """Check for unsafe content patterns.
        
        Returns first unsafe pattern found, or None.
        """
        if not isinstance(content, str):
            return None
        
        content_lower = content.lower()
        
        for pattern in cls.UNSAFE_PATTERNS:
            if re.search(pattern, content_lower, re.IGNORECASE):
                return pattern
        
        return None
    
    @classmethod
    def _is_valid_id(cls, entity_id: str) -> bool:
        """Check if ID format is valid."""
        # Allow alphanumeric, underscores, hyphens
        return bool(re.match(r"^[a-zA-Z0-9_\-]{1,128}$", entity_id))


class TrustScorer:
    """Score confidence/trust of graph data."""
    
    # Base scores for different sources
    SOURCE_SCORES = {
        "user_input": 0.7,
        "extraction": 0.65,
        "inference": 0.5,
        "external_api": 0.6,
        "validated": 1.0,
    }
    
    # Boost factors
    VALIDATION_BOOST = 0.15
    REPEATED_MENTION_BOOST = 0.1
    
    @classmethod
    def score_entity(cls, source: Optional[str], repeated: int = 1) -> float:
        """Score confidence for an entity.
        
        Parameters
        ----------
        source : str, optional
            Source of the entity
        repeated : int
            Number of times mentioned
        
        Returns
        -------
        float
            Confidence score (0-1)
        """
        base_score = cls.SOURCE_SCORES.get(source or "extraction", 0.5)
        
        # Boost for repeated mentions
        boost = min(cls.REPEATED_MENTION_BOOST * (repeated - 1), 0.2)
        
        final_score = min(base_score + boost, 1.0)
        return final_score
    
    @classmethod
    def adjust_confidence(cls, current: float, validation_result: bool) -> float:
        """Adjust confidence based on validation.
        
        Parameters
        ----------
        current : float
            Current confidence score
        validation_result : bool
            True if validation passed
        
        Returns
        -------
        float
            Updated confidence
        """
        if validation_result:
            return min(current + cls.VALIDATION_BOOST, 1.0)
        else:
            return max(current - cls.VALIDATION_BOOST, 0.0)


# Module-level singletons
data_validator = DataValidator()
trust_scorer = TrustScorer()

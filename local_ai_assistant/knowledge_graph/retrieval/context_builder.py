"""
knowledge_graph/retrieval/context_builder.py
=============================================
Build LLM-ready context from graph queries.

This layer converts graph data into natural language context
that the LLM can use for better understanding.
"""

from __future__ import annotations

from typing import Optional, List, Dict, Any

from core.logging_config import get_logger
from knowledge_graph.storage import graph_store
from knowledge_graph.ontology import Entity, Relationship, EntityType

log = get_logger(__name__)


class ContextBuilder:
    """Build natural language context from graph data."""
    
    def build_context_from_entity(self, entity_id: str, max_depth: int = 2) -> str:
        """Build context string for an entity.
        
        Parameters
        ----------
        entity_id : str
            Entity ID to build context for
        max_depth : int
            How many hops to include in the graph
        
        Returns
        -------
        str
            Natural language context
        """
        context_data = graph_store.get_entity_context(entity_id, depth=max_depth)
        
        if not context_data.get("entity"):
            return ""
        
        entity = context_data["entity"]
        relationships = context_data.get("relationships", [])
        related_entities = context_data.get("related_entities", [])
        
        lines = []
        
        # Main entity description
        lines.append(f"**{entity.name}** (Type: {entity.type.value})")
        if entity.properties:
            for key, value in entity.properties.items():
                lines.append(f"  - {key}: {value}")
        
        # Related information
        if relationships:
            lines.append("\n**Related:**")
            for rel in relationships:
                rel_target = next((e for e in related_entities if e.id == rel.target_id), None)
                if rel_target:
                    lines.append(f"  - {rel.type.value}: {rel_target.name}")
        
        return "\n".join(lines)
    
    def build_context_from_query(self, query: str) -> str:
        """Build context based on a query.
        
        Searches graph for relevant entities and builds context.
        
        Parameters
        ----------
        query : str
            User query
        
        Returns
        -------
        str
            Relevant context from graph
        """
        # Extract key terms from query
        key_terms = self._extract_key_terms(query)
        
        context_parts = []
        for term in key_terms:
            # Find matching entities
            entities = self._search_entities(term)
            for entity in entities[:3]:  # Top 3 matches
                context = self.build_context_from_entity(entity.id, max_depth=1)
                if context:
                    context_parts.append(context)
        
        if context_parts:
            return "\n\n".join(context_parts)
        return ""
    
    def _extract_key_terms(self, text: str) -> List[str]:
        """Extract key terms from text."""
        # Simple extraction: words longer than 3 chars, not common words
        import re
        common_words = {"the", "and", "for", "with", "that", "this", "from", "about"}
        
        words = re.findall(r"\b\w{4,}\b", text.lower())
        return [w for w in words if w not in common_words]
    
    def _search_entities(self, term: str) -> List[Entity]:
        """Search for entities matching a term."""
        # Search across all entity types
        results = []
        
        for entity_type in EntityType:
            entities = graph_store.find_entities_by_type(entity_type)
            matching = [e for e in entities if term.lower() in e.name.lower()]
            results.extend(matching)
        
        # Sort by confidence
        results.sort(key=lambda e: e.confidence, reverse=True)
        return results


class ContextInjector:
    """Inject graph context into LLM prompts."""
    
    def inject_context(self, system_prompt: str, context: str) -> str:
        """Add graph context to system prompt.
        
        Parameters
        ----------
        system_prompt : str
            Original system prompt
        context : str
            Graph-derived context
        
        Returns
        -------
        str
            Enhanced system prompt
        """
        if not context:
            return system_prompt
        
        enhancement = f"""

**Relevant Knowledge Graph Context:**
{context}

Use this context to provide more accurate and personalized responses. Consider relationships and past interactions when formulating answers."""
        
        return system_prompt + enhancement
    
    def build_conversation_context(self, entity_id: Optional[str] = None) -> str:
        """Build context for ongoing conversation.
        
        Parameters
        ----------
        entity_id : str, optional
            Primary entity being discussed
        
        Returns
        -------
        str
            Conversation context
        """
        builder = ContextBuilder()
        
        if entity_id:
            return builder.build_context_from_entity(entity_id, max_depth=2)
        
        return ""


# Module-level singletons
context_builder = ContextBuilder()
context_injector = ContextInjector()

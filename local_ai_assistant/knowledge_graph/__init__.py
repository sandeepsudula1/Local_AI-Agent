"""
knowledge_graph/
=================
Knowledge Graph layer for semantic memory and enhanced reasoning.

Provides structured storage and retrieval of facts, entities, and relationships
extracted from conversations, enabling the AI to maintain context and reason
over past interactions.

Core Modules:
  - ontology/    : Entity and relationship definitions
  - storage/     : Graph database interface (Neo4j + fallback)
  - extraction/  : Triple extraction from text
  - retrieval/   : Context building for LLM
  - security/    : Validation and trust scoring

Quick Start:

  from knowledge_graph.extraction import triple_extractor
  from knowledge_graph.storage import graph_store
  
  # Extract triples from text
  triples = triple_extractor.extract("I want to build an app for children")
  
  # Convert and store
  entities, rels = triple_extractor.to_entities_and_relationships(triples)
  for entity in entities:
      graph_store.add_entity(entity)
  for rel in rels:
      graph_store.add_relationship(rel)
  
  # Build context for LLM
  from knowledge_graph.retrieval import context_builder
  context = context_builder.build_context_from_query("Tell me about the app")
  
  # Use context with LLM...
"""

# ---------------------------------------------------------------------------
# Legacy sub-packages (ontology / storage / extraction / retrieval / security)
# These are the original v1 modules.  They are wrapped in try/except so that
# any import error in the old code NEVER prevents knowledge_graph.v2 from
# loading.  v2 is the active system used by the orchestrator.
# ---------------------------------------------------------------------------
try:
    from knowledge_graph.ontology import (
        Entity, Relationship,
        EntityType, RelationshipType,
    )

    from knowledge_graph.storage import graph_store

    from knowledge_graph.extraction import (
        Triple, TripleExtractor, MemoryType,
        triple_extractor,
    )

    from knowledge_graph.retrieval import (
        ContextBuilder, ContextInjector,
        context_builder, context_injector,
    )

    from knowledge_graph.security import (
        DataValidator, TrustScorer,
        data_validator, trust_scorer,
    )
except Exception as _kg_v1_err:
    import warnings
    warnings.warn(
        f"knowledge_graph v1 sub-packages failed to load ({_kg_v1_err}). "
        "Only knowledge_graph.v2 will be available.",
        ImportWarning,
        stacklevel=2,
    )


__version__ = "0.1.0"

__all__ = [
    # Ontology
    "Entity",
    "Relationship",
    "EntityType",
    "RelationshipType",
    
    # Storage
    "graph_store",
    
    # Extraction
    "Triple",
    "TripleExtractor",
    "MemoryType",
    "triple_extractor",
    
    # Retrieval
    "ContextBuilder",
    "ContextInjector",
    "context_builder",
    "context_injector",
    
    # Security
    "DataValidator",
    "TrustScorer",
    "data_validator",
    "trust_scorer",
]

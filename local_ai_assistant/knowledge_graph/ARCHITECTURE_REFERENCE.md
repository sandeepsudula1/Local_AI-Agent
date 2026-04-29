"""
KNOWLEDGE GRAPH SYSTEM - COMPLETE REFERENCE
==============================================

This is a comprehensive guide to the knowledge graph layer added to your
AI assistant. It explains architecture, components, and integration.

═══════════════════════════════════════════════════════════════════════
ARCHITECTURE OVERVIEW
═══════════════════════════════════════════════════════════════════════

The knowledge graph consists of 5 layers:

┌─────────────────────────────────────────────────────────┐
│  Layer 1: ONTOLOGY                                      │
│  ────────────────────────────────────────────────────   │
│  Defines entity types and relationship types            │
│  - EntityType: USER, PROJECT, TASK, EMAIL, DOCUMENT... │
│  - RelationshipType: CREATED_BY, ASSIGNED_TO, CONTAINS..
│  - Entity/Relationship dataclasses                      │
└─────────────────────────────────────────────────────────┘
                          ↓
┌─────────────────────────────────────────────────────────┐
│  Layer 2: EXTRACTION                                    │
│  ────────────────────────────────────────────────────   │
│  Convert conversations → Structured triples             │
│  - TripleExtractor: extract() method                    │
│  - Patterns: goals, properties, relationships           │
│  - Output: List[Triple] → convert to Entity/Rel         │
│  - MemoryType: episodic, semantic, procedural           │
└─────────────────────────────────────────────────────────┘
                          ↓
┌─────────────────────────────────────────────────────────┐
│  Layer 3: SECURITY & VALIDATION                         │
│  ────────────────────────────────────────────────────   │
│  Validate and score data before storage                 │
│  - DataValidator: validate_entity(), validate_rel()    │
│  - TrustScorer: score_entity(), adjust_confidence()    │
│  - Prevents injection, malformed data, unsafe ops      │
└─────────────────────────────────────────────────────────┘
                          ↓
┌─────────────────────────────────────────────────────────┐
│  Layer 4: STORAGE                                       │
│  ────────────────────────────────────────────────────   │
│  Persist data to Neo4j or in-memory                     │
│  - GraphStore: add_entity(), add_relationship()        │
│  - Neo4j support with automatic fallback               │
│  - CRUD and query methods                              │
└─────────────────────────────────────────────────────────┘
                          ↓
┌─────────────────────────────────────────────────────────┐
│  Layer 5: RETRIEVAL & CONTEXT                           │
│  ────────────────────────────────────────────────────   │
│  Build LLM-ready context from graph                     │
│  - ContextBuilder: build from entity or query           │
│  - ContextInjector: enhance LLM prompts                 │
│  - Natural language context synthesis                   │
└─────────────────────────────────────────────────────────┘


═══════════════════════════════════════════════════════════════════════
COMPONENT REFERENCE
═══════════════════════════════════════════════════════════════════════

1. ONTOLOGY (knowledge_graph/ontology/)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

entities.py:
  - EntityType: Enum of all entity types
    USER, PROJECT, TASK, EMAIL, DOCUMENT, ACTION, EVENT, CONCEPT, LOCATION, ORGANIZATION
  
  - Entity: Base dataclass with id, type, name, properties, status, confidence, created_at, source
  
  - User: Entity subclass with email, preferences, roles
  
  - Project: Entity subclass with description, goal, target_audience, tags
  
  - Task: Entity subclass with priority, due_date, assigned_to
  
  - Document: Entity subclass with path, content_preview, file_type, size_bytes
  
  - Action: Entity subclass with action_type, target_id, timestamp
  
  - create_entity(): Factory function for polymorphic creation

relationships.py:
  - RelationshipType: Enum of all relationship types
    CREATED_BY, ASSIGNED_TO, PART_OF, CONTAINS, PRECEDES, FOLLOWS,
    RELATED_TO, SIMILAR_TO, SPECIALIZES, EXECUTES, DEPENDS_ON, etc.
  
  - Relationship: Dataclass with source_id, target_id, type, properties, confidence
  
  - RELATIONSHIP_TEMPLATES: Descriptions and reverse relationships
  
  - create_relationship(): Factory function


2. EXTRACTION (knowledge_graph/extraction/)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

triple_extractor.py:
  - MemoryType: Enum (EPISODIC, SEMANTIC, PROCEDURAL)
  
  - Triple: Dataclass with subject, predicate, object, memory_type, confidence, context
  
  - TripleExtractor: Main class
    - extract(text) → List[Triple]
    - to_entities_and_relationships(triples) → (List[Entity], List[Relationship])
    - Pattern-based extraction for goals, properties, relationships
    - Automatic entity type inference
    - Automatic predicate → relationship_type mapping
  
  - triple_extractor: Module-level singleton instance

Pattern Examples:
  - "I want to build X" → Triple("User", "wants_to_create", "X")
  - "my project targets children" → Triple("project", "targets", "children")
  - "plants are organisms" → Triple("plants", "is_type_of", "organisms")


3. SECURITY & VALIDATION (knowledge_graph/security/)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

validator.py:
  - DataValidator: Validates entities and relationships
    - validate_entity(entity) → (bool, error_msg)
    - validate_relationship(relationship) → (bool, error_msg)
    - sanitize_string(text) → cleaned_text
    - Checks: name length, unsafe patterns, restricted words, ID format, confidence range
    - Prevents: XSS, SQL injection, self-referencing relationships
  
  - TrustScorer: Scores confidence of data
    - score_entity(source, repeated) → float
    - adjust_confidence(current, validation_result) → float
    - SOURCE_SCORES: Different confidence for user_input, extraction, inference, etc.
    - VALIDATION_BOOST: +0.15 confidence for validated data
    - REPEATED_MENTION_BOOST: +0.1 per mention
  
  - data_validator, trust_scorer: Module-level singletons


4. STORAGE (knowledge_graph/storage/)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

graph_store.py:
  - GraphStore: Main interface to graph database
    - Neo4j support (bolt protocol)
    - Automatic in-memory fallback if Neo4j unavailable
    - Connection config via environment variables:
      NEO4J_URI (default: bolt://localhost:7687)
      NEO4J_USER, NEO4J_PASSWORD
  
  - Methods:
    - add_entity(entity)
    - get_entity(entity_id)
    - find_entities_by_type(entity_type)
    - add_relationship(relationship)
    - get_related_entities(entity_id)
    - get_entity_context(entity_id, depth) → full neighborhood
  
  - graph_store: Module-level singleton


5. RETRIEVAL & CONTEXT (knowledge_graph/retrieval/)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

context_builder.py:
  - ContextBuilder: Build natural language context from graph
    - build_context_from_entity(entity_id, max_depth) → str
    - build_context_from_query(query) → str
    - _extract_key_terms(text) → List[str]
    - _search_entities(term) → List[Entity]
  
  - ContextInjector: Inject context into LLM prompts
    - inject_context(system_prompt, context) → enhanced_prompt
    - build_conversation_context(entity_id) → str
  
  - context_builder, context_injector: Module-level singletons


═══════════════════════════════════════════════════════════════════════
WORKFLOW: EXTRACTING AND STORING A CONVERSATION
═══════════════════════════════════════════════════════════════════════

Given conversation: "I want to build a plant identification app for children"

Step 1: Extract Triples
  triple_extractor.extract(text)
  → [Triple("User", "wants_to_create", "build plant identification app", ...)]

Step 2: Convert to Entities and Relationships
  triple_extractor.to_entities_and_relationships(triples)
  → [
      Entity(id="user", type=USER, ...),
      Entity(id="build_plant_id_app", type=PROJECT, ...)
    ]
    [
      Relationship(source="user", target="build_plant_id_app", type=RELATED_TO, ...)
    ]

Step 3: Validate
  data_validator.validate_entity(entity) → (True, None)
  data_validator.validate_relationship(rel) → (True, None)

Step 4: Score Confidence
  entity.confidence = trust_scorer.score_entity("user_input") → 0.7

Step 5: Store in Graph
  graph_store.add_entity(entity)
  graph_store.add_relationship(rel)

Step 6: Later - Build Context for LLM
  context_builder.build_context_from_entity("build_plant_id_app")
  → "**Plant Identification App** (Type: project)\n  - goal: Help children learn\n..."

Step 7: Inject Into Prompt
  context_injector.inject_context(system_prompt, context)
  → Enhanced prompt with graph knowledge


═══════════════════════════════════════════════════════════════════════
INTEGRATION WITH ORCHESTRATOR
═══════════════════════════════════════════════════════════════════════

In pipelines/orchestrator.py, add after memory.extract_and_store():

  # Extract and store triples
  from knowledge_graph.extraction import triple_extractor
  from knowledge_graph.storage import graph_store
  from knowledge_graph.security import data_validator, trust_scorer
  
  triples = triple_extractor.extract(user_input)
  entities, relationships = triple_extractor.to_entities_and_relationships(triples)
  
  for entity in entities:
      is_valid, _ = data_validator.validate_entity(entity)
      if is_valid:
          entity.confidence = trust_scorer.score_entity("user_input")
          graph_store.add_entity(entity)
  
  for rel in relationships:
      is_valid, _ = data_validator.validate_relationship(rel)
      if is_valid:
          graph_store.add_relationship(rel)

In handle_general(), get context before LLM call:

  from knowledge_graph.retrieval import context_builder, context_injector
  
  graph_context = context_builder.build_context_from_query(user_query)
  enhanced_system_prompt = context_injector.inject_context(system_prompt, graph_context)
  
  # Use enhanced_system_prompt with handle_general_ai()


═══════════════════════════════════════════════════════════════════════
PERFORMANCE NOTES
═══════════════════════════════════════════════════════════════════════

In-Memory Storage:
  - Fast for small graphs (< 10k entities)
  - All data lost on process restart
  - Good for development/testing

Neo4j Storage:
  - Recommended for production
  - Persistent across restarts
  - Scales to millions of entities
  - Requires Neo4j server running
  - Can add indexes for faster queries

Extraction Performance:
  - Triple extraction: ~10-50ms per sentence
  - Validation: <1ms per entity/relationship
  - Storage: 1-5ms per entity/relationship

Query Performance:
  - Entity lookup: <1ms
  - Entity context (depth=2): 5-20ms (Neo4j)
  - Full graph traversal: Expensive, use depth limits


═══════════════════════════════════════════════════════════════════════
TROUBLESHOOTING
═══════════════════════════════════════════════════════════════════════

Neo4j Not Found
  → System auto-falls back to in-memory storage
  → Install neo4j package: pip install neo4j
  → Run Neo4j server: docker run neo4j or download from neo4j.com

Extraction Not Working
  → Check patterns in triple_extractor.py
  → Add domain-specific regex patterns
  → Triples need: subject, predicate, object (3+ chars each)

Low Confidence Scores
  → Adjust SOURCE_SCORES in trust_scorer.py
  → Increase VALIDATION_BOOST
  → Or pass higher initial confidence

Memory Leaks
  → Graph stores data indefinitely
  → Implement cleanup: periodic deletion of old entities
  → Or use time-based expiration in properties

Context Not Injected
  → Check that entity_id exists in graph
  → Build context first, check if non-empty
  → Verify context_injector.inject_context() is called


═══════════════════════════════════════════════════════════════════════
NEXT STEPS
═══════════════════════════════════════════════════════════════════════

1. ✅ COMPLETE: Core layers (ontology, extraction, storage, security, retrieval)
2. ⏳ TODO: Orchestrator integration - extract and store triples after each turn
3. ⏳ TODO: Conversation history enhancement - pass graph context to LLM
4. ⏳ TODO: Test with real conversations
5. ⏳ TODO: Performance tuning and optimization
6. ⏳ TODO: Add audit logging for governance
"""

print(__doc__)

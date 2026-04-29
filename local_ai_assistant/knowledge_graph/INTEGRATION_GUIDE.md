"""
KNOWLEDGE GRAPH INTEGRATION GUIDE
==================================

This guide shows how to integrate the knowledge graph layer
into your existing AI assistant system.

STEP 1: Installation & Setup
-----------------------------

1.1 Install Neo4j (optional but recommended):
   - Download from: https://neo4j.com/download/
   - Or use Docker: docker run -p 7687:7687 -e NEO4J_AUTH=neo4j/password neo4j
   - Set environment variables:
     NEO4J_URI=bolt://localhost:7687
     NEO4J_USER=neo4j
     NEO4J_PASSWORD=your_password

1.2 Install Python package:
   pip install neo4j

1.3 The system will automatically use in-memory fallback if Neo4j is unavailable.


STEP 2: Extract Triples from Conversations
--------------------------------------------

In your conversation processing pipeline (after memory.extract_and_store):

    from knowledge_graph.extraction import triple_extractor
    from knowledge_graph.storage import graph_store
    from knowledge_graph.security import data_validator, trust_scorer

    user_message = "I want to build a plant identification app for children"
    
    # Extract triples
    triples = triple_extractor.extract(user_message)
    print(f"Extracted {len(triples)} triples")
    
    # Convert to entities and relationships
    entities, relationships = triple_extractor.to_entities_and_relationships(triples)
    
    # Validate and store
    for entity in entities:
        is_valid, error = data_validator.validate_entity(entity)
        if is_valid:
            # Adjust confidence based on validation
            entity.confidence = trust_scorer.score_entity("user_input")
            graph_store.add_entity(entity)
        else:
            print(f"Entity validation failed: {error}")
    
    for rel in relationships:
        is_valid, error = data_validator.validate_relationship(rel)
        if is_valid:
            graph_store.add_relationship(rel)
        else:
            print(f"Relationship validation failed: {error}")


STEP 3: Build Context for LLM Responses
-----------------------------------------

Modify your LLM call to include graph context:

    from knowledge_graph.retrieval import context_builder, context_injector
    from agents.core.general_agent import handle_general

    user_query = "Tell me more about the app"
    entity_id = "plant_identification_app"  # Primary entity being discussed
    
    # Build context from graph
    graph_context = context_builder.build_context_from_entity(entity_id)
    
    # Or search for relevant context from query
    graph_context = context_builder.build_context_from_query(user_query)
    
    # Inject into LLM system prompt
    enhanced_prompt = context_injector.inject_context(
        system_prompt="You are a helpful assistant.",
        context=graph_context
    )
    
    # Call LLM with context
    answer = handle_general(user_query, model_name="mistral")


STEP 4: Query the Graph
------------------------

Example queries:

    # Get an entity
    entity = graph_store.get_entity("plant_identification_app")
    
    # Find all projects
    projects = graph_store.find_entities_by_type(EntityType.PROJECT)
    
    # Get all relationships for an entity
    relationships = graph_store.get_related_entities("plant_identification_app")
    
    # Get full context (entity + relationships + related entities)
    context = graph_store.get_entity_context("plant_identification_app", depth=2)


STEP 5: Integration Points
---------------------------

5.1 In pipelines/orchestrator.py (_handle_general method):
    - After memory.extract_and_store()
    - Extract triples and add to graph
    - Get context from graph before LLM call

5.2 In agents/core/general_agent.py (handle_general_ai function):
    - Pass graph_context as part of system_extra parameter
    - LLM now has structured knowledge

5.3 In memory/conversation_memory.py:
    - Add graph_context to conversation history
    - Maintain link between memory turns and graph entities


IMPLEMENTATION CHECKLIST
-------------------------

□ Neo4j installation (optional but recommended)
□ Install neo4j Python package
□ Create extraction pipeline in orchestrator
□ Add validation and trust scoring
□ Add context retrieval before LLM calls
□ Test with sample conversations
□ Monitor query performance
□ Set up audit logs (optional)


TROUBLESHOOTING
---------------

Q: Neo4j not found - what should I do?
A: The system automatically falls back to in-memory storage.
   You can still use the knowledge graph, but it will be reset
   when the process ends. For production, install Neo4j.

Q: Triples don't seem to be extracted correctly?
A: Check the patterns in triple_extractor.py. You may need to
   add domain-specific patterns for your use case.

Q: Confidence scores are too low?
A: Adjust SOURCE_SCORES and VALIDATION_BOOST in trust_scorer.py
   or pass higher confidence when creating entities.

Q: Graph queries are slow?
A: Add Neo4j indexes on frequently queried properties.
   See Neo4j documentation for index creation.
"""

print(__doc__)

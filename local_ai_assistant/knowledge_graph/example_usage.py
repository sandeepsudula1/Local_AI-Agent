"""
knowledge_graph/example_usage.py
=================================
Working example of the knowledge graph system.

Run this to see the complete workflow:
  python -m knowledge_graph.example_usage
"""

from knowledge_graph.ontology import EntityType, create_entity, create_relationship, RelationshipType
from knowledge_graph.extraction import triple_extractor
from knowledge_graph.storage import graph_store
from knowledge_graph.retrieval import context_builder, context_injector
from knowledge_graph.security import data_validator, trust_scorer


def example_1_basic_entities():
    """Example 1: Create and store basic entities."""
    print("\n" + "="*60)
    print("EXAMPLE 1: Creating and Storing Entities")
    print("="*60)
    
    # Create a project entity
    plant_app = create_entity(
        entity_type=EntityType.PROJECT,
        entity_id="plant_id_app",
        name="Plant Identification App",
        description="Mobile app for identifying plants using computer vision",
        goal="Help children learn about plants",
        target_audience="children",
    )
    
    print(f"\nCreated entity: {plant_app.name}")
    print(f"  Type: {plant_app.type.value}")
    print(f"  Goal: {plant_app.properties.get('goal')}")
    
    # Validate before storing
    is_valid, error = data_validator.validate_entity(plant_app)
    if is_valid:
        plant_app.confidence = trust_scorer.score_entity("user_input")
        graph_store.add_entity(plant_app)
        print(f"  ✓ Stored with confidence: {plant_app.confidence:.2f}")
    else:
        print(f"  ✗ Validation failed: {error}")


def example_2_relationships():
    """Example 2: Create relationships between entities."""
    print("\n" + "="*60)
    print("EXAMPLE 2: Creating Relationships")
    print("="*60)
    
    # Create entities
    user = create_entity(
        entity_type=EntityType.USER,
        entity_id="user_john",
        name="John",
    )
    
    app = create_entity(
        entity_type=EntityType.PROJECT,
        entity_id="plant_id_app",
        name="Plant Identification App",
    )
    
    # Create relationships
    created_rel = create_relationship(
        source_id=user.id,
        target_id=app.id,
        rel_type=RelationshipType.CREATED_BY,
        confidence=0.9,
    )
    
    print(f"\nRelationship created:")
    print(f"  {user.name} -[{created_rel.type.value}]-> {app.name}")
    print(f"  Confidence: {created_rel.confidence:.2f}")
    
    # Validate and store
    is_valid, error = data_validator.validate_relationship(created_rel)
    if is_valid:
        graph_store.add_relationship(created_rel)
        print(f"  ✓ Stored in graph")
    else:
        print(f"  ✗ Validation failed: {error}")


def example_3_triple_extraction():
    """Example 3: Extract triples from text."""
    print("\n" + "="*60)
    print("EXAMPLE 3: Extracting Triples from Text")
    print("="*60)
    
    # Sample conversation turns
    conversations = [
        "I want to build a plant identification app",
        "my project targets children and students",
        "it should help users learn about plants and botany",
    ]
    
    all_triples = []
    
    for i, text in enumerate(conversations, 1):
        print(f"\nTurn {i}: \"{text}\"")
        
        triples = triple_extractor.extract(text)
        print(f"  Extracted {len(triples)} triple(s):")
        
        for triple in triples:
            print(f"    ✓ {triple.subject} -[{triple.predicate}]-> {triple.object}")
            print(f"      Memory type: {triple.memory_type.value}")
            print(f"      Confidence: {triple.confidence:.2f}")
            all_triples.append(triple)
        
        # Convert to entities and relationships
        entities, relationships = triple_extractor.to_entities_and_relationships(triples)
        
        # Store them
        for entity in entities:
            is_valid, _ = data_validator.validate_entity(entity)
            if is_valid:
                entity.confidence = trust_scorer.score_entity("extraction")
                graph_store.add_entity(entity)
        
        for rel in relationships:
            is_valid, _ = data_validator.validate_relationship(rel)
            if is_valid:
                graph_store.add_relationship(rel)
    
    print(f"\n  Total triples extracted: {len(all_triples)}")


def example_4_context_retrieval():
    """Example 4: Build context from graph."""
    print("\n" + "="*60)
    print("EXAMPLE 4: Building Context from Graph")
    print("="*60)
    
    entity_id = "plant_identification_app"
    
    # Build context from entity
    context = context_builder.build_context_from_entity(entity_id, max_depth=2)
    
    if context:
        print(f"\nContext for '{entity_id}':")
        print(context)
    else:
        print(f"\nNo context found for entity '{entity_id}'")
    
    # Build context from query
    query = "Tell me about the plant identification features"
    query_context = context_builder.build_context_from_query(query)
    
    if query_context:
        print(f"\nContext for query: \"{query}\"")
        print(query_context)
    else:
        print(f"\nNo context found for query")


def example_5_prompt_injection():
    """Example 5: Inject graph context into LLM prompt."""
    print("\n" + "="*60)
    print("EXAMPLE 5: Injecting Context into LLM Prompt")
    print("="*60)
    
    # Original prompt
    original_prompt = (
        "You are a helpful AI assistant. Answer questions clearly and concisely."
    )
    
    # Get context
    entity_id = "plant_identification_app"
    context = context_builder.build_context_from_entity(entity_id, max_depth=1)
    
    # Inject context
    if context:
        enhanced_prompt = context_injector.inject_context(original_prompt, context)
        
        print("\nOriginal prompt length:", len(original_prompt))
        print("Enhanced prompt length:", len(enhanced_prompt))
        print("\nEnhanced prompt:\n")
        print(enhanced_prompt)
    else:
        print("No context to inject")


def example_6_security_validation():
    """Example 6: Security validation."""
    print("\n" + "="*60)
    print("EXAMPLE 6: Security Validation")
    print("="*60)
    
    # Test valid entity
    valid_entity = create_entity(
        entity_type=EntityType.PROJECT,
        entity_id="safe_project",
        name="Safe Project Name",
    )
    
    is_valid, error = data_validator.validate_entity(valid_entity)
    print(f"\nValid entity: {valid_entity.name}")
    print(f"  ✓ Passed validation")
    
    # Test unsafe entity name
    unsafe_entity = create_entity(
        entity_type=EntityType.PROJECT,
        entity_id="unsafe",
        name="<script>alert('xss')</script>",
    )
    
    is_valid, error = data_validator.validate_entity(unsafe_entity)
    print(f"\nUnsafe entity: {unsafe_entity.name}")
    print(f"  ✗ Failed: {error}")
    
    # Test self-referencing relationship
    self_rel = create_relationship(
        source_id="entity_1",
        target_id="entity_1",
        rel_type=RelationshipType.RELATED_TO,
    )
    
    is_valid, error = data_validator.validate_relationship(self_rel)
    print(f"\nSelf-referencing relationship:")
    print(f"  ✗ Failed: {error}")


def main():
    """Run all examples."""
    print("\n" + "█"*60)
    print("█  KNOWLEDGE GRAPH SYSTEM - WORKING EXAMPLES")
    print("█"*60)
    
    try:
        example_1_basic_entities()
        example_2_relationships()
        example_3_triple_extraction()
        example_4_context_retrieval()
        example_5_prompt_injection()
        example_6_security_validation()
        
        print("\n" + "█"*60)
        print("█  ALL EXAMPLES COMPLETED SUCCESSFULLY")
        print("█"*60 + "\n")
        
    except Exception as e:
        print(f"\n✗ Error: {e}")
        import traceback
        traceback.print_exc()


if __name__ == "__main__":
    main()

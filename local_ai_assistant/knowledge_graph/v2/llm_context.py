"""
knowledge_graph/v2/llm_context.py
===================================
Integration layer: convert knowledge graph data into LLM-ready context.

Single public function:
    get_context_for_llm(query: str) -> str

Usage inside your agent
-----------------------
    from knowledge_graph.v2 import get_context_for_llm

    context = get_context_for_llm("What is Sandeep working on?")
    system_prompt = base_prompt + "\\n\\n[Knowledge Graph]\\n" + context
"""

from __future__ import annotations

import re
from typing import List, Optional, Set

from .entities import Entity, EntityType, normalize_entity
from .relationships import RelType, Triple
from .graph_store import graph_store
from .graph_retriever import _resolve_id, _entity_name, walk_graph


# ── Public API ──────────────────────────────────────────────────────────────

def get_context_for_llm(query: str) -> str:
    """
    Build a concise natural-language context block from the knowledge graph
    that is relevant to the given query.

    Strategy
    --------
    1. Extract candidate entity names from the query (proper nouns / key terms).
    2. For each entity found in the graph, do a 2-hop BFS walk.
    3. Summarise the collected triples as bullet-point sentences.
    4. Fall back to a full graph summary when no entities match.

    Parameters
    ----------
    query : str
        The user's question or message.

    Returns
    -------
    str
        A formatted context string (empty string if the graph is empty).

    Example
    -------
    >>> get_context_for_llm("What is Sandeep working on?")
    '- Sandeep (Person) is working on AI agent\\n- Sandeep (Person) is working on Local AI Assistant\\n- AI agent (Project) uses AWS'
    """
    if not graph_store.all_entities():
        return ""

    # Step 1: identify entities mentioned in the query
    mentioned_ids = _entities_from_query(query)

    # Step 2: collect triples around those entities (2-hop)
    triples_seen: Set[tuple] = set()
    relevant_triples: List[Triple] = []

    for eid in mentioned_ids:
        paths = walk_graph(eid, max_hops=2, use_rules=True)
        for subj_id, pred_val, obj_id in paths:
            key = (subj_id, pred_val, obj_id)
            if key not in triples_seen:
                triples_seen.add(key)
                relevant_triples.append(
                    Triple(subject=subj_id, predicate=RelType(pred_val), object=obj_id)
                )

    # Step 3: if nothing specific found, include all 1-hop triples for all entities
    if not relevant_triples:
        for triple in graph_store.all_triples():
            key = triple.to_tuple()
            if key not in triples_seen:
                triples_seen.add(key)
                relevant_triples.append(triple)

    if not relevant_triples:
        return ""

    # Step 4: format as natural language bullet points
    lines = _triples_to_sentences(relevant_triples)
    return "\n".join(f"- {line}" for line in lines)


def get_entity_summary(entity_name: str) -> str:
    """
    Return a short paragraph describing an entity and all its direct connections.

    Useful for injecting focused context about a specific person or project.

    Example
    -------
    >>> get_entity_summary("Sandeep")
    'Sandeep is a Person. Sandeep is working on AI agent and Local AI Assistant. Sandeep owns Local AI Assistant.'
    """
    eid = _resolve_id(entity_name)
    if not eid:
        return ""

    entity = graph_store.get_entity(eid)
    if not entity:
        return ""

    parts = [f"{entity.name} is a {entity.type.value}."]

    # Group outgoing triples by predicate
    pred_groups: dict = {}
    for t in graph_store.triples_for_subject(eid):
        pred_groups.setdefault(t.predicate, []).append(_entity_name(t.object))

    _PRED_PHRASES = {
        RelType.WORKS_ON:    "is working on",
        RelType.OWNS:        "owns",
        RelType.ASSIGNED_TO: "is assigned to",
        RelType.USES:        "uses",
    }

    for pred, objects in pred_groups.items():
        phrase = _PRED_PHRASES.get(pred, pred.value)
        parts.append(f"{entity.name} {phrase} {' and '.join(objects)}.")

    # Incoming: who assigned / works for this entity?
    reverse: dict = {}
    for t in graph_store.triples_for_object(eid):
        reverse.setdefault(t.predicate, []).append(_entity_name(t.subject))

    for pred, subjects in reverse.items():
        if pred == RelType.WORKS_ON:
            parts.append(f"{entity.name} has {' and '.join(subjects)} working on it.")
        elif pred == RelType.ASSIGNED_TO:
            parts.append(f"{entity.name} is assigned to {' and '.join(subjects)}.")

    return " ".join(parts)


# ── Internal helpers ────────────────────────────────────────────────────────

def _entities_from_query(query: str) -> List[str]:
    """
    Extract entity ids that are likely mentioned in the query.

    Uses two strategies:
    - Match capitalised proper-noun tokens against stored entity names
    - Match any stored entity name as a substring of the query
    """
    found: List[str] = []
    query_lower = query.lower()
    query_norm = normalize_entity(query)

    for entity in graph_store.all_entities():
        name_lower = entity.name.lower()
        # substring match
        if name_lower in query_lower or entity.id in query_norm:
            found.append(entity.id)

    return found


_PRED_SENTENCE = {
    RelType.WORKS_ON:    "{subj} ({stype}) is working on {obj}",
    RelType.OWNS:        "{subj} ({stype}) owns {obj}",
    RelType.ASSIGNED_TO: "{obj} is assigned to {subj} ({stype})",
    RelType.USES:        "{subj} ({stype}) uses {obj}",
}


def _triples_to_sentences(triples: List[Triple]) -> List[str]:
    """Convert a list of triples to readable English sentences."""
    sentences: List[str] = []

    for t in triples:
        subj_entity = graph_store.get_entity(t.subject)
        subj_name   = subj_entity.name if subj_entity else t.subject
        subj_type   = subj_entity.type.value if subj_entity else "Entity"
        obj_name    = _entity_name(t.object)

        template = _PRED_SENTENCE.get(t.predicate)
        if template:
            sentence = template.format(
                subj=subj_name, stype=subj_type, obj=obj_name
            )
        else:
            sentence = f"{subj_name} {t.predicate.value} {obj_name}"

        sentences.append(sentence)

    return sentences

"""
knowledge_graph/v2/graph_retriever.py
=======================================
Graph retrieval: direct queries, reverse queries, and multi-hop reasoning.

Supported query patterns
------------------------
Direct:
  "What is <name> working on?"       -> works_on targets
  "What does <name> own?"            -> owns targets
  "What does <name> use?"            -> uses targets (direct + via works_on chain)

Reverse:
  "Who is working on <thing>?"       -> reverse works_on
  "Who owns <thing>?"                -> reverse owns
  "Who is assigned to <thing>?"      -> reverse assigned_to
  "Who uses <thing>?"                -> reverse uses

Multi-hop:
  "What does <name> use?"            -> person->works_on->project->uses->tool
  walk_graph(entity_id, max_hops=2)  -> BFS over all predicates

Debug:
  "Show all triples"
"""

from __future__ import annotations

import re
import difflib
from collections import deque
from typing import Dict, List, Optional, Set, Tuple

from .entities import Entity, normalize_entity
from .relationships import RelType, Triple
from .graph_store import graph_store
from .reasoning_rules import paths_ending_with, valid_predicates


# ── Public query dispatcher ─────────────────────────────────────────────────

def query(question: str) -> str:
    """
    Answer a natural-language question using the knowledge graph.

    Returns a human-readable string answer.

    Examples
    --------
    >>> query("What is Sandeep working on?")
    'Sandeep is working on: AI agent, Local AI Assistant'

    >>> query("Who is working on AI agent?")
    'Working on AI agent: Sandeep'

    >>> query("What does Sandeep use?")
    'Sandeep uses: AWS (via AI agent)'
    """
    q = question.strip()

    # ── "What is/are <name> working on?" ────────────────────────────────
    m = re.search(r"what (?:is|are) (.+?) (?:working on|doing)", q, re.IGNORECASE)
    if m:
        return _works_on_by_person(m.group(1).strip())

    # ── "What does <name> own / build / create?" ────────────────────────
    m = re.search(r"what (?:does|did) (.+?) (?:own|build|create|made)", q, re.IGNORECASE)
    if m:
        return _owns_by_person(m.group(1).strip())

    # ── "What does <name> use / run / rely on?" (direct + multi-hop) ───
    m = re.search(r"what (?:does|did) (.+?) (?:use|utilize|run on|rely on)", q, re.IGNORECASE)
    if m:
        return _uses_by_entity(m.group(1).strip())

    # ── "Who is working on / works on <thing>?" (reverse) ──────────────
    m = re.search(r"who (?:is working on|works on) (.+?)[\?]?$", q, re.IGNORECASE)
    if m:
        return _reverse_lookup(m.group(1).strip(), RelType.WORKS_ON, "Working on")

    # ── "Who is assigned to <thing>?" (reverse) ─────────────────────────
    m = re.search(r"who is assigned to (.+?)[\?]?$", q, re.IGNORECASE)
    if m:
        return _reverse_lookup(m.group(1).strip(), RelType.ASSIGNED_TO, "Assigned to")

    # ── "Who owns / built / created <thing>?" (reverse) ─────────────────
    m = re.search(r"who (?:owns|built|created) (.+?)[\?]?$", q, re.IGNORECASE)
    if m:
        return _reverse_lookup(m.group(1).strip(), RelType.OWNS, "Owner of")

    # ── "Who uses <thing>?" (reverse) ───────────────────────────────────
    m = re.search(r"who (?:uses|utilizes|relies on) (.+?)[\?]?$", q, re.IGNORECASE)
    if m:
        return _reverse_lookup(m.group(1).strip(), RelType.USES, "Users of")

    # ── Debug: "Show all" / "List all" ──────────────────────────────────
    if re.search(r"(show|list|dump)\s+all", q, re.IGNORECASE):
        return _dump_all()

    return (
        "Sorry, I couldn't understand that query.\n"
        "Try: 'What is <name> working on?' / 'Who works on <thing>?' / "
        "'What does <name> use?'"
    )


# ── Multi-hop: BFS walk ─────────────────────────────────────────────────────

def walk_graph(
    start_id: str,
    max_hops: int = 2,
    predicates: Optional[List[RelType]] = None,
    use_rules: bool = False,
) -> List[Tuple[str, str, str]]:
    """
    BFS traversal from start_id up to max_hops edges.

    Parameters
    ----------
    start_id   : entity id to start from
    max_hops   : maximum number of edges to follow
    predicates : if given, only follow these specific predicate types
    use_rules  : if True, restrict traversal to predicates in VALID_REASONING_PATHS

    Returns
    -------
    List of (subject_id, predicate_value, object_id) tuples discovered
    along ALL paths from start.
    """
    visited: Set[str]    = set()
    queue:   deque       = deque([(start_id, 0)])
    paths:   List[Tuple] = []

    # Pre-compute allowed predicate set once (not per-node)
    _rules_allowed: Optional[Set[str]] = valid_predicates() if use_rules else None

    while queue:
        current_id, depth = queue.popleft()

        if current_id in visited or depth >= max_hops:
            continue
        visited.add(current_id)

        # When use_rules=True, only follow predicates that appear in a rule
        allowed: Optional[Set[str]] = _rules_allowed if use_rules else None

        for triple in graph_store.triples_for_subject(current_id):
            if predicates and triple.predicate not in predicates:
                continue
            if allowed is not None and triple.predicate.value not in allowed:
                continue
            paths.append((triple.subject, triple.predicate.value, triple.object))
            queue.append((triple.object, depth + 1))

    return paths


# ── Rule-based multi-hop follow ─────────────────────────────────────────────

def multihop_follow(
    start_id: str,
    target_predicate: str,
) -> List[Tuple[str, Optional[str]]]:
    """
    Follow all VALID_REASONING_PATHS that end with `target_predicate`,
    starting from `start_id`.

    Returns
    -------
    List of (result_entity_id, via_entity_id_or_None) tuples.
    via_entity_id is None for direct (1-hop) hits.

    Example
    -------
    multihop_follow("sandeep", "uses")
    # reads paths_ending_with("uses") => [["works_on","uses"],["owns","uses"]]
    # follows sandeep->works_on->ai_agent->uses->aws  etc.
    # returns [("aws", "ai_agent"), ("python", "ai_agent"), ...]
    """
    results: List[Tuple[str, Optional[str]]] = []
    seen:    Set[str] = set()

    for path in paths_ending_with(target_predicate):
        if len(path) == 1:
            # Direct: start -[target_predicate]-> result
            for t in graph_store.triples_for_subject(start_id):
                if t.predicate.value == path[0] and t.object not in seen:
                    seen.add(t.object)
                    results.append((t.object, None))

        elif len(path) == 2:
            # Two-hop: start -[path[0]]-> mid -[path[1]]-> result
            first_pred, second_pred = path[0], path[1]
            for t1 in graph_store.triples_for_subject(start_id):
                if t1.predicate.value != first_pred:
                    continue
                mid_id = t1.object
                for t2 in graph_store.triples_for_subject(mid_id):
                    if t2.predicate.value == second_pred and t2.object not in seen:
                        seen.add(t2.object)
                        results.append((t2.object, mid_id))

        # Extending to 3-hop paths is straightforward — add an elif len==3 block here.

    return results


# ── Internal handlers ───────────────────────────────────────────────────────

def _resolve_id(name: str) -> Optional[str]:
    """Fuzzy-match a name string to a stored entity id."""
    # 1. Exact normalised match
    exact = normalize_entity(name)
    if graph_store.get_entity(exact):
        return exact

    # 2. Substring match (both directions)
    name_l = name.lower()
    for entity in graph_store.all_entities():
        if name_l in entity.name.lower() or entity.name.lower() in name_l:
            return entity.id
            
    # 3. Fuzzy match
    existing_ids = [e.id for e in graph_store.all_entities()]
    matches = difflib.get_close_matches(exact, existing_ids, n=1, cutoff=0.7)
    if matches:
        return matches[0]

    return None


def _entity_name(entity_id: str) -> str:
    e = graph_store.get_entity(entity_id)
    return e.name if e else entity_id


def _works_on_by_person(name: str) -> str:
    eid = _resolve_id(name)
    if not eid:
        return f"I don't know who or what '{name}' is."

    triples = [t for t in graph_store.triples_for_subject(eid) if t.predicate == RelType.WORKS_ON]
    if not triples:
        return f"I have no record of {_entity_name(eid)} working on anything."

    objects = [_entity_name(t.object) for t in triples]
    return f"{_entity_name(eid)} is working on: {', '.join(objects)}"


def _owns_by_person(name: str) -> str:
    eid = _resolve_id(name)
    if not eid:
        return f"I don't know who or what '{name}' is."

    triples = [t for t in graph_store.triples_for_subject(eid) if t.predicate == RelType.OWNS]
    if not triples:
        return f"I have no record of {_entity_name(eid)} owning anything."

    objects = [_entity_name(t.object) for t in triples]
    return f"{_entity_name(eid)} owns: {', '.join(objects)}"


def _uses_by_entity(name: str) -> str:
    """
    Find what an entity uses — directly, or via any valid reasoning chain
    that ends with 'uses' (driven by VALID_REASONING_PATHS).
    """
    eid = _resolve_id(name)
    if not eid:
        return f"I don't know who or what '{name}' is."

    label = _entity_name(eid)

    # multihop_follow covers both direct (1-hop) and indirect (2-hop) hits
    hits = multihop_follow(eid, "uses")

    if not hits:
        return f"I have no record of {label} using anything."

    parts: List[str] = []
    for obj_id, via_id in hits:
        obj_name = _entity_name(obj_id)
        parts.append(f"{obj_name} (via {_entity_name(via_id)})" if via_id else obj_name)

    return f"{label} uses: {', '.join(parts)}"


def _reverse_lookup(thing: str, predicate: RelType, label: str) -> str:
    """
    Generic reverse lookup: find all subjects that have (subject -[predicate]-> thing).
    """
    eid = _resolve_id(thing)
    if not eid:
        return f"I don't know about '{thing}'."

    triples = [t for t in graph_store.triples_for_object(eid) if t.predicate == predicate]
    if not triples:
        return f"No one recorded as '{predicate.value}' for '{_entity_name(eid)}'."

    subjects = [_entity_name(t.subject) for t in triples]
    return f"{label} {_entity_name(eid)}: {', '.join(subjects)}"


def _dump_all() -> str:
    entities = graph_store.all_entities()
    triples  = graph_store.all_triples()

    if not entities and not triples:
        return "The knowledge graph is empty."

    lines = ["=== Knowledge Graph ==="]
    lines.append(f"\nEntities ({len(entities)}):")
    for e in entities:
        lines.append(f"  [{e.type.value}] {e.name} (id={e.id})")

    lines.append(f"\nTriples ({len(triples)}):")
    for t in triples:
        lines.append(f"  ({_entity_name(t.subject)}) --[{t.predicate.value}]--> ({_entity_name(t.object)})")

    return "\n".join(lines)

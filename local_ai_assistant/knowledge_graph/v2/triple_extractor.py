"""
knowledge_graph/v2/triple_extractor.py
========================================
Rule-based triple extractor.

Converts natural language sentences into (subject, predicate, object) triples
using simple regex patterns.  No NLP library required.

Supported patterns
------------------
works_on   : "<Name> is working on <thing>"
             "<Name> works on <thing>"
             "<Name> is building <thing>"
             "<Name> is developing <thing>"
owns       : "<Name> owns <thing>"
             "<Name> created <thing>"
             "<Name> built <thing>"
assigned_to: "<thing> is assigned to <Name>"
             "assign <thing> to <Name>"
"""

from __future__ import annotations

import re
import difflib
from typing import List, Optional, Tuple

from .entities import Entity, EntityType, make_entity, normalize_entity
from .relationships import Triple, RelType
from .graph_store import graph_store


# ── Pattern table ──────────────────────────────────────────────────────────
#  Each entry: (compiled regex, predicate, subject_group, object_group)
#
#  Groups are 1-indexed (match.group(n)).
#  subject_group is the *actor* entity; object_group is the *target* entity.

_PATTERNS: List[Tuple[re.Pattern, RelType, int, int]] = [
    # works_on ---------------------------------------------------------------
    (re.compile(
        r"(.+?)\s+(?:is\s+(?:working|building|developing)\s+on|works\s+on|is\s+(?:building|developing))\s+(.+)",
        re.IGNORECASE,
    ), RelType.WORKS_ON, 1, 2),

    # owns / created / built -------------------------------------------------
    (re.compile(
        r"(.+?)\s+(?:owns|created|built|made)\s+(.+)",
        re.IGNORECASE,
    ), RelType.OWNS, 1, 2),

    # assigned_to ------------------------------------------------------------
    (re.compile(
        r"(.+?)\s+is\s+assigned\s+to\s+(.+)",
        re.IGNORECASE,
    ), RelType.ASSIGNED_TO, 2, 1),   # note: subject=person, object=task

    (re.compile(
        r"assign\s+(.+?)\s+to\s+(.+)",
        re.IGNORECASE,
    ), RelType.ASSIGNED_TO, 2, 1),   # assign <task> to <person>

    # uses -------------------------------------------------------------------
    (re.compile(
        r"(.+?)\s+(?:uses|is\s+using|utilizes|runs\s+on|is\s+built\s+on|relies\s+on)\s+(.+)",
        re.IGNORECASE,
    ), RelType.USES, 1, 2),
]


# ── Entity type heuristics ─────────────────────────────────────────────────

def _guess_type(name: str) -> EntityType:
    """Simple keyword heuristic to classify an entity."""
    low = name.lower()
    
    # Tool/Project check
    if any(k in low for k in [
        "project", "app", "system", "platform", "tool", "agent", "bot",
        "aws", "azure", "gcp", "cloud", "api", "db", "database", "service",
        "engine", "framework", "library", "sdk", "llm", "model", "redis", "docker", "python", "java"
    ]):
        return EntityType.PROJECT
        
    if any(k in low for k in ["task", "feature", "bug", "issue", "ticket", "todo"]):
        return EntityType.TASK
        
    # Explicit Person check
    if any(k in low for k in ["sandeep", "rahul", "john", "user", "admin", "manager"]):
        return EntityType.PERSON
        
    # Default to PROJECT for general concepts like "biology" (NOT a person)
    return EntityType.PROJECT

def _is_valid_factual_statement(text: str) -> bool:
    """Guard against questions, requests, and conversational noise."""
    text_lower = text.lower().strip()
    
    if text_lower.endswith("?"):
        return False
        
    invalid_prefixes = [
        "what ", "how ", "who ", "when ", "why ", "where ",
        "can you ", "could you ", "do you ", "are you ", "would you ",
        "i need ", "i want ", "give me ", "tell me ", "show me ", "please "
    ]
    if any(text_lower.startswith(p) for p in invalid_prefixes):
        return False
        
    return True


def _clean(text: str) -> str:
    """Strip trailing punctuation and whitespace."""
    return re.sub(r"[.,;!?]+$", "", text.strip())


# ── Public API ──────────────────────────────────────────────────────────────

def extract_triples(text: str) -> List[Triple]:
    """
    Extract triples from a sentence and store them in the graph.

    Returns the list of triples that were extracted.

    Example
    -------
    >>> triples = extract_triples("Sandeep is working on AI agent")
    >>> triples[0]
    (sandeep)-[works_on]->(ai_agent)
    """
    text = text.strip()
    
    if not _is_valid_factual_statement(text):
        print(f"[KG] Skipped invalid extraction: Not a factual statement ('{text}')")
        return []
        
    found: List[Triple] = []

    for pattern, predicate, subj_grp, obj_grp in _PATTERNS:
        match = pattern.match(text)
        if not match:
            continue

        subj_name = _clean(match.group(subj_grp))
        obj_name  = _clean(match.group(obj_grp))

        if not subj_name or not obj_name:
            continue

        invalid_entities = {
            "i", "you", "it", "he", "she", "they", "we", "me", "him", "her", "us", "them", 
            "this", "that", "subject", "information", "something", "anything", "nothing", "someone",
            "do", "see", "any", "which", "related"
        }
        if subj_name.lower() in invalid_entities or obj_name.lower() in invalid_entities:
            print(f"[KG] Skipped invalid extraction: Contains generic pronoun or invalid entity ('{subj_name}', '{obj_name}')")
            continue

        # Resolve / create entities
        subj = _get_or_create(subj_name)
        obj  = _get_or_create(obj_name)

        triple = Triple(
            subject=subj.id,
            predicate=predicate,
            object=obj.id,
        )

        graph_store.add_triple(triple)
        found.append(triple)
        print(f"[KG] Stored triple: {triple}")
        break  # Stop at the first matching pattern for a sentence

    # --- LLM Fallback Disabled Temporarily ---
    # if not found:
    #     found = _extract_via_llm(text)

    if found:
        print(f"[KG] Extracted: {found}")
    else:
        print(f"[KG] Skipped invalid extraction: No rule patterns matched")

    return found


def _get_or_create(name: str) -> Entity:
    """Return an existing entity or create one in the graph store."""
    entity_id = normalize_entity(name)
    existing  = graph_store.get_entity(entity_id)
    if existing:
        return existing

    print(f"[KG] Normalized entity '{name}' -> '{entity_id}'")

    # Fuzzy matching for deduplication
    existing_ids = [e.id for e in graph_store.all_entities()]
    matches = difflib.get_close_matches(entity_id, existing_ids, n=1, cutoff=0.85)
    if matches:
        matched_id = matches[0]
        print(f"[KG] Merged '{entity_id}' -> '{matched_id}' (fuzzy match)")
        return graph_store.get_entity(matched_id)

    entity = make_entity(name, _guess_type(name))
    graph_store.add_entity(entity)
    return entity


def _extract_via_llm(text: str) -> List[Triple]:
    """Lightweight LLM fallback for triple extraction when regex fails."""
    try:
        import ollama
        from configs.llm_config import MODEL
        prompt = (
            "Extract the primary subject, predicate, and object from the sentence as a knowledge graph triple.\n"
            "Respond ONLY with a single line in the format: subject | predicate | object\n"
            "Use basic predicates like WORKS_ON, OWNS, USES, ASSIGNED_TO, IS_A.\n"
            f"Sentence: {text}\n"
        )
        print(f"[LLM] Using model: {MODEL}")
        resp = ollama.chat(
            model=MODEL,
            messages=[{"role": "user", "content": prompt}],
            options={"temperature": 0.1, "num_predict": 50}
        )
        content = resp.get("message", {}).get("content", "").strip()
        
        if "|" in content:
            parts = [p.strip() for p in content.split("|")]
            if len(parts) == 3:
                s_name, p_str, o_name = parts
                
                # Best-effort map predicate string to RelType
                pred = RelType.WORKS_ON # fallback
                p_upper = p_str.upper()
                for rt in RelType:
                    if rt.name in p_upper or p_upper in rt.name:
                        pred = rt
                        break
                
                s = _get_or_create(s_name)
                o = _get_or_create(o_name)
                
                triple = Triple(subject=s.id, predicate=pred, object=o.id)
                graph_store.add_triple(triple)
                print(f"[KG] Stored triple (via LLM): {triple}")
                return [triple]
    except Exception as e:
        print(f"[KG] LLM fallback extraction failed: {e}")
    return []


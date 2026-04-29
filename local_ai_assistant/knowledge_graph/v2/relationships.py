"""
knowledge_graph/v2/relationships.py
====================================
Minimal relationship definitions.

Supported predicates: works_on, owns, assigned_to, uses
"""

from dataclasses import dataclass
from enum import Enum


class RelType(str, Enum):
    WORKS_ON    = "works_on"
    OWNS        = "owns"
    ASSIGNED_TO = "assigned_to"
    USES        = "uses"         # enables multi-hop: person→works_on→project→uses→tool


@dataclass
class Triple:
    """
    A subject-predicate-object triple.

    Example:
        Triple(subject="sandeep", predicate=RelType.WORKS_ON, object="ai_agent")
    """

    subject: str    # Entity id of the subject
    predicate: RelType
    object: str     # Entity id of the object

    def __repr__(self):
        return f"({self.subject})-[{self.predicate.value}]->({self.object})"

    def to_tuple(self):
        return (self.subject, self.predicate.value, self.object)

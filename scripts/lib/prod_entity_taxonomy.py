"""Production entity taxonomy: the 25 subtypes the production NER model emits, grouped by type.

Single source of truth is `scripts/lib/taxonomy.py::ENTITY_SUBTYPES`; this module only reshapes it and adds the
two small maps stage 21 needs. Importing this module has no side effects (no model, no database).
"""
from __future__ import annotations

from .taxonomy import ENTITY_SUBTYPES

# type -> {subtype key: readable label}, e.g. PROD_ENTITY_TAXONOMY["PERSON"]["political_leader"]
PROD_ENTITY_TAXONOMY: dict[str, dict[str, str]] = {t: dict(rows) for t, rows in ENTITY_SUBTYPES.items()}

# Entity type codes seen in production / NER outputs -> our four types
TYPE_MAP: dict[str, str] = {
    "PER": "PERSON", "PERSON": "PERSON", "POI": "PERSON",
    "ORG": "ORG", "ORGANIZATION": "ORG", "OOI": "ORG",
    "LOC": "LOCATION", "LOCATION": "LOCATION", "LOI": "LOCATION",
    "EVT": "EVENT", "EVENT": "EVENT", "EOI": "EVENT", "MISC": "EVENT",
}

# Subtypes used by the earlier (Babelscape-era) taxonomy -> the production subtype that replaced them
SUBTYPE_MIGRATION_MAP: dict[str, str] = {
    "political_leader": "political_leader", "military_leader": "military_personnel",
    "diplomat": "political_leader", "business_executive": "civilian",
    "port": "naval_base_port", "border_town": "disputed_border", "economic_zone": "strategic_corridor",
    "pipeline": "strategic_corridor", "railway": "strategic_corridor",
    "ministry": "government_body", "state_owned_enterprise": "commercial_company",
    "armed_group": "militant_group_non_state", "international_org": "ngo", "business_org": "commercial_company",
}


def parent_type_of(subtype: str) -> str:
    """PERSON / LOCATION / ORG / EVENT for a production subtype, or 'UNMAPPED' if it is not one of the 25."""
    for parent, subs in PROD_ENTITY_TAXONOMY.items():
        if subtype in subs:
            return parent
    return "UNMAPPED"

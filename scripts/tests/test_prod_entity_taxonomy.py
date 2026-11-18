"""The 25 production entity subtypes: one source of truth, no overlaps, every key resolves to one type."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from lib.prod_entity_taxonomy import PROD_ENTITY_TAXONOMY, SUBTYPE_MIGRATION_MAP, TYPE_MAP, parent_type_of  # noqa: E402
from lib.taxonomy import ENTITY_SUBTYPES  # noqa: E402


def test_25_subtypes_in_four_types():
    assert {t: len(v) for t, v in PROD_ENTITY_TAXONOMY.items()} == {"PERSON": 5, "LOCATION": 10, "ORG": 8, "EVENT": 2}


def test_subtype_keys_are_unique_across_types():
    keys = [k for subs in PROD_ENTITY_TAXONOMY.values() for k in subs]
    assert len(keys) == len(set(keys)) == 25


def test_same_source_as_the_dashboard_taxonomy():
    assert {t: [k for k, _ in rows] for t, rows in ENTITY_SUBTYPES.items()} == \
           {t: list(v) for t, v in PROD_ENTITY_TAXONOMY.items()}


def test_parent_type_of_and_unknown_is_not_dropped():
    assert parent_type_of("country") == "LOCATION" and parent_type_of("civilian") == "PERSON"
    assert parent_type_of("not_a_subtype") == "UNMAPPED"      # kept and flagged, never silently dropped


def test_old_subtypes_migrate_to_valid_new_ones():
    valid = {k for subs in PROD_ENTITY_TAXONOMY.values() for k in subs}
    assert set(SUBTYPE_MIGRATION_MAP.values()) <= valid
    assert TYPE_MAP["POI"] == "PERSON" and TYPE_MAP["LOI"] == "LOCATION" and TYPE_MAP["OOI"] == "ORG" and TYPE_MAP["EOI"] == "EVENT"

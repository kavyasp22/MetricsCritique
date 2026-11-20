"""Unified taxonomy contract -- the single source of truth for every label the
product displays, across both the legacy (risk/theme) and the new CAMEO axes.

WHY THIS MODULE EXISTS
----------------------
Before this, the effective UI schema was four JavaScript object literals in
`UI/assets/dash.js` (`RISK_META`, `RISK_ORDER`, `THEME_META`, `THEME_ORDER`).
That was a real defect, not just duplication:

  * The `*_ORDER` arrays were used as `ORDER.filter(c => catSet[c])`, so any
    category present in the data but absent from the array was **silently
    dropped from the UI**. A taxonomy change would have failed invisibly --
    the panel would simply render fewer rows, with no error anywhere.
  * `riskName()`/`themeName()` fall back to `|| k`, so an unknown key renders
    as a raw snake_case string that looks like a label. Fail-soft in the one
    place where fail-loud was needed.
  * The long, exclusion-aware descriptions the *classifier* actually saw lived
    in `12_risk_theme_classification.py`, while the *user* saw different short
    blurbs in dash.js. Nothing kept the two key sets in agreement.

So this module owns display metadata and ordering, composes in the CAMEO
taxonomy from `taxonomy_cameo`, and -- critically -- **verifies against the
classifier source** rather than trusting a hand-copied list.

THE DRIFT CHECK
---------------
`_classifier_keys()` parses `12_risk_theme_classification.py` with `ast` and
extracts the keys of `CATEGORY_DESCRIPTIONS` and `NLI_HYPOTHESES` without
importing it. Parsing rather than importing is deliberate: script 12 pulls
duckdb/pandas (and, in the classification path, torch) at module scope, so an
import would make a cheap metadata build depend on the ML stack. `validate()`
then asserts the key sets match **in both directions** -- a category added to
the classifier but not here, or vice versa, is an error, not a silent
mismatch.

The long model descriptions and NLI hypotheses are lifted from that same parse
and emitted alongside the short UI blurbs. That is a feature: the drill panel
can show the operator the exact prompt the classifier was asked, which is the
difference between "the model said sanctions" and an auditable claim.

EMITTED CONTRACT
----------------
`data/taxonomy.json`:

    {
      "axes": {
        "<axis>": {
          "label":  "...",             # human name for the axis itself
          "kind":   "legacy"|"cameo",
          "hierarchical": bool,
          "order":  ["key", ...],      # canonical display order
          "entries": {
            "<key>": {
              "display": "...",        # what the user sees
              "description": "...",    # short UI blurb
              "model_description":     # long prompt the bi-encoder saw (or null)
              "hypothesis":            # the NLI hypothesis (or null)
              "code": "05.2"|null, "parent_key": ..., "level": int,
              "alertable": bool, "alertable_reason": str|null,
              "crosswalk_from": [...], "crosswalk_to": [...]
            }, ...
          }
        }, ...
      },
      "crosswalk": {"<legacy key>": ["<cameo key>", ...]},
      ...provenance...
    }

Axes are separated by *grain*, not merged: `theme_code` (10 parents) and
`theme_subcode` (41 children) are distinct axes because the metrics layer
computes `share_V` as a fraction of the panel total. Parent 05 and child 05.2
in one panel would double-count and make every published share wrong.

Run directly to (re)emit:  python scripts/lib/taxonomy.py
"""
from __future__ import annotations

import ast
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve()
EDA_ROOT = HERE.parent.parent.parent
OUT_PATH = EDA_ROOT / "data" / "taxonomy.json"
CLASSIFIER_SRC = EDA_ROOT / "scripts" / "12_risk_theme_classification.py"

sys.path.insert(0, str(HERE.parent))
import taxonomy_cameo  # noqa: E402


# ==========================================================================
# legacy axes -- display metadata
# ==========================================================================
# Ordering is editorial, not alphabetical: within `risk` the categories run
# from domestic/political through economic to kinetic; within `theme` they
# follow the order the analysts use in briefings. Order is data here so it can
# change without touching JavaScript.
#
# The short `description` is the tooltip blurb. It is intentionally NOT the
# same text as the classifier's `model_description` -- the model needs long
# exclusion-aware prose ("Not a naval blockade, ..."), the operator needs six
# words. Both are emitted so the difference is visible rather than lost.

LEGACY_AXES: dict[str, dict] = {
    "risk": {
        "label": "Risk category",
        "entries": [
            ("domestic_instability", "Domestic Instability",
             "Internal political or social unrest"),
            ("policy_shifts", "Policy Shifts",
             "Sudden changes in government policy"),
            ("project_delays", "Project Delays",
             "Named infrastructure or investment projects behind schedule"),
            ("sanctions", "Sanctions",
             "Economic or diplomatic sanctions"),
            ("border_conflicts", "Border Conflicts",
             "State-on-state border disputes or clashes"),
            ("insurgencies", "Insurgencies",
             "Non-state-actor armed insurgency"),
            ("shipping_disruptions", "Shipping Disruptions",
             "Maritime trade / shipping-route disruption"),
            ("pipeline_vulnerabilities", "Pipeline Vulnerabilities",
             "Threats to energy pipeline infrastructure"),
            ("international_pressure", "International Pressure",
             "Diplomatic or economic pressure from other states"),
        ],
    },
    "theme": {
        "label": "Thematic dimension",
        "entries": [
            ("political_relations", "Political Relations",
             "Bilateral / multilateral political ties"),
            ("economic_relations", "Economic Relations",
             "Trade, investment, economic cooperation"),
            ("bri", "Belt and Road Initiative (BRI)",
             "China-led infrastructure / investment corridor activity"),
            ("energy_security", "Energy Security",
             "Energy supply, access and resilience"),
            ("maritime_security", "Maritime Security",
             "Naval / military activity at sea"),
            ("maritime_dimensions", "Maritime Dimensions",
             "Commercial, non-military maritime activity"),
            ("security_military_relations", "Security & Military Relations",
             "Defence cooperation, arms deals, joint exercises"),
            ("border_issues", "Border Issues",
             "Territorial and boundary questions"),
            ("internal_conflict_china_role", "Internal Conflict & China’s Role",
             "Domestic conflict where China’s role is a factor"),
            ("tech_digital_influence", "Technology & Digital Influence",
             "Digital infrastructure, tech transfer, cyber influence"),
            ("humanitarian_dimension", "Humanitarian Dimension",
             "Refugees, disaster relief, humanitarian crises"),
            ("international_relations", "International Relations",
             "Broader multilateral / global diplomatic activity"),
        ],
    },
}

# Entity subtypes taxonomy: 25 official subtypes across PERSON, LOCATION, ORG, EVENT.
# Aligned with production database schema (POI, LOI, OOI, EOI).
ENTITY_SUBTYPES: dict[str, list[tuple[str, str]]] = {
    "PERSON": [
        ("political_leader", "Political Leader / Head of State"),
        ("civilian", "Civilian / Public Figure"),
        ("military_personnel", "Military Commander / Officer"),
        ("journalist_analyst", "Journalist / Defense Analyst"),
        ("legal_actor", "Judicial / Legal Official"),
    ],
    "LOCATION": [
        ("country", "Sovereign State / Nation"),
        ("geographic_location", "Geographic Location / City / Province"),
        ("conflict_zone", "Active Conflict Zone / Hostile Sector"),
        ("disputed_border", "Disputed Border / Line of Actual Control"),
        ("military_installation", "Military Installation / Airbase / Garrison"),
        ("naval_base_port", "Naval Base / Strategic Port Facility"),
        ("strategic_corridor", "Strategic Transit Corridor / Choke Point"),
        ("restricted_operational_zone", "Restricted Operational Zone"),
        ("territorial_waters", "Territorial Waters / Maritime EEZ"),
        ("refugee_camp_displacement_zone", "Displacement / Humanitarian Zone"),
    ],
    "ORG": [
        ("government_body", "Government Body / Executive Agency"),
        ("political_party", "Political Party / Coalition"),
        ("commercial_company", "Commercial Corporation / Defense Contractor"),
        ("military_branch_coalition", "Armed Forces Branch / Military Coalition"),
        ("militant_group_non_state", "Militant / Insurgent Group"),
        ("ngo", "Non-Governmental Organization / Agency"),
        ("military_hardware", "Military Weapon System / Hardware"),
        ("naval_vessel", "Naval Warship / Combatant Vessel"),
    ],
    "EVENT": [
        ("military_exercise", "Joint Military Exercise / Maneuver"),
        ("protest_demonstration", "Protest / Mass Demonstration"),
    ],
}


# ==========================================================================
# classifier-source parse (drift check)
# ==========================================================================
def _dict_literal(tree: ast.Module, name: str) -> dict:
    """Return an assigned dict literal from a parsed module, by variable name.

    Only literal values are recovered (`ast.literal_eval`); anything computed
    raises, which is the correct outcome -- a taxonomy that is computed at
    import time cannot be verified statically and should not pretend to be.
    """
    for node in tree.body:
        if not isinstance(node, (ast.Assign, ast.AnnAssign)):
            continue
        targets = node.targets if isinstance(node, ast.Assign) else [node.target]
        for t in targets:
            if isinstance(t, ast.Name) and t.id == name:
                if node.value is None:
                    raise ValueError(f"{name} is annotated but unassigned")
                return ast.literal_eval(node.value)
    raise KeyError(f"{name} not found in {CLASSIFIER_SRC.name}")


def classifier_metadata() -> dict[str, dict[str, dict[str, str]]]:
    """Long descriptions + NLI hypotheses exactly as the classifier saw them.

    Returns {axis: {key: {"model_description": ..., "hypothesis": ...}}}.
    Parsed, not imported -- see module docstring.
    """
    tree = ast.parse(CLASSIFIER_SRC.read_text(encoding="utf-8"))
    descs = _dict_literal(tree, "CATEGORY_DESCRIPTIONS")   # nested: axis -> key -> str
    hyps = _dict_literal(tree, "NLI_HYPOTHESES")           # flat: key -> str
    out: dict[str, dict[str, dict[str, str]]] = {}
    for axis, per_key in descs.items():
        out[axis] = {
            k: {"model_description": v, "hypothesis": hyps.get(k)}
            for k, v in per_key.items()
        }
    return out


# ---- the A01-A10 event topics (theme_code, 41 sub-codes in theme_subcode) placed under the D01-D09 security themes -------------------
# One primary D theme per code (the filter hierarchy: D theme -> A code -> sub-code) and, where a code plainly straddles two themes, a
# secondary one (documentation only, not used to count). Keyed by the CAMEO code string. A parent code takes the theme most of its
# sub-codes share; a sub-code that belongs elsewhere is mapped on its own (08.x is the mixed one). Decided 2026-11-20; a change is a spec change.
CODE_TO_DOMAIN: dict[str, tuple[str, tuple[str, ...]]] = {
    "01": ("D02", ()), "01.1": ("D02", ()), "01.2": ("D02", ()), "01.3": ("D02", ()), "01.4": ("D02", ("D05",)),
    "02": ("D03", ()), "02.1": ("D03", ()), "02.2": ("D03", ()), "02.3": ("D03", ()), "02.4": ("D03", ("D02",)),
    "03": ("D03", ()), "03.1": ("D03", ()), "03.2": ("D03", ()), "03.3": ("D03", ()), "03.4": ("D03", ("D02",)),
    "04": ("D03", ()), "04.1": ("D03", ()), "04.2": ("D03", ()), "04.3": ("D03", ("D01",)), "04.4": ("D03", ("D04",)),
    "05": ("D04", ()), "05.1": ("D04", ("D05",)), "05.2": ("D04", ("D06",)), "05.3": ("D04", ()), "05.4": ("D04", ("D03",)), "05.5": ("D04", ()),
    "06": ("D05", ()), "06.1": ("D05", ()), "06.2": ("D05", ("D03",)), "06.3": ("D05", ()), "06.4": ("D05", ("D02",)),
    "07": ("D06", ()), "07.1": ("D06", ()), "07.2": ("D06", ()), "07.3": ("D06", ("D02",)), "07.4": ("D06", ("D01",)),
    "08": ("D09", ("D01",)), "08.1": ("D02", ()), "08.2": ("D01", ("D09",)), "08.3": ("D09", ("D02",)), "08.4": ("D08", ("D09",)),
    "09": ("D07", ()), "09.1": ("D07", ("D03",)), "09.2": ("D07", ()), "09.3": ("D07", ("D01",)), "09.4": ("D07", ("D03",)),
    "10": ("D08", ()), "10.1": ("D08", ()), "10.2": ("D08", ()), "10.3": ("D08", ("D01",)), "10.4": ("D08", ()),
}


# ==========================================================================
# build
# ==========================================================================
def build() -> dict:
    """Compose legacy + CAMEO axes into the emitted contract."""
    cls_meta = classifier_metadata()
    cameo = taxonomy_cameo.build()

    # Reverse the CAMEO crosswalk so a legacy category knows where it went.
    crosswalk: dict[str, list[str]] = {}
    for c in cameo["codes"]:
        for old in c.get("crosswalk_from") or []:
            crosswalk.setdefault(old, []).append(c["key"])

    axes: dict[str, dict] = {}

    for axis, spec in LEGACY_AXES.items():
        meta = cls_meta.get(axis, {})
        entries = {}
        for key, display, desc in spec["entries"]:
            m = meta.get(key, {})
            entries[key] = {
                "display": display,
                "description": desc,
                "model_description": m.get("model_description"),
                "hypothesis": m.get("hypothesis"),
                "code": None,
                "parent_key": None,
                "level": 1,
                "alertable": True,
                "alertable_reason": None,
                "crosswalk_from": [],
                "crosswalk_to": sorted(crosswalk.get(key, [])),
            }
        axes[axis] = {
            "label": spec["label"],
            "kind": "legacy",
            "hierarchical": False,
            "status": "in_production",
            "order": [k for k, _, _ in spec["entries"]],
            "entries": entries,
        }

    # entity_subtype is hierarchical by NER type rather than by code, so it
    # carries a `groups` block instead of a parent_key chain.
    st_entries = {}
    st_order = []
    for ner_type, subs in ENTITY_SUBTYPES.items():
        for key, display in subs:
            st_entries[key] = {
                "display": display, "description": ner_type,
                "model_description": None, "hypothesis": None,
                "code": None, "parent_key": ner_type, "level": 2,
                "alertable": True, "alertable_reason": None,
                "crosswalk_from": [], "crosswalk_to": [],
            }
            st_order.append(key)
    axes["entity_subtype"] = {
        "label": "Entity subtype",
        "kind": "legacy",
        "hierarchical": True,
        "status": "in_production",
        "order": st_order,
        "groups": {t: [k for k, _ in subs] for t, subs in ENTITY_SUBTYPES.items()},
        "entries": st_entries,
    }

    # --- security domains D01..D09 (the hybrid theme filter, stage 27). Names come from data/entity_schema.json, the file the
    #     routing stage uses, so the dashboard and the routing never disagree. Automatic and provisional: not alertable. ---
    sd = json.loads((EDA_ROOT / "data" / "entity_schema.json").read_text(encoding="utf-8"))
    sd_entries = {
        d: {"display": sd["l2"][d]["name"], "description": sd["l2"][d]["name"],
            "model_description": None, "hypothesis": None, "code": d, "parent_key": None, "level": 1,
            "alertable": False,
            "alertable_reason": "automatic provisional label (stage 27, about 74% right on its high tier); alerts are keyed on topic_id",
            "crosswalk_from": [], "crosswalk_to": []}
        for d in sd["domain_ids"]
    }
    axes["security_domain"] = {
        "label": "Security theme (automatic, provisional)",
        "kind": "legacy",
        "hierarchical": False,
        "status": "provisional",
        "order": list(sd["domain_ids"]),
        "entries": sd_entries,
    }

    # --- CAMEO axes: split by grain, see module docstring on share_V ---
    def _cameo_entry(c: dict) -> dict:
        return {
            "display": c["display"],
            # The CAMEO `description` IS the bi-encoder prompt, so it is both
            # the model description and (truncated by the UI) the tooltip.
            "description": c["description"],
            "model_description": c["description"],
            "hypothesis": c["hypothesis"],
            "code": c["code"],
            "parent_key": c["parent_key"],
            "level": c["level"],
            "alertable": c["alertable"],
            "alertable_reason": c.get("alertable_reason"),
            "crosswalk_from": c.get("crosswalk_from", []),
            "crosswalk_to": [],
        }

    groups: dict[str, list[dict]] = {"theme_code": [], "theme_subcode": [], "action": []}
    for c in cameo["codes"]:
        if c["axis"] == "action":
            groups["action"].append(c)
        elif c["level"] == 1:
            groups["theme_code"].append(c)
        else:
            groups["theme_subcode"].append(c)

    cameo_labels = {
        "theme_code": "Event topic (Section A, parent)",
        "theme_subcode": "Event topic (Section A, sub-code)",
        "action": "Event action (Section B)",
    }
    for axis, items in groups.items():
        items = sorted(items, key=lambda c: c["code"])
        axes[axis] = {
            "label": cameo_labels[axis],
            "kind": "cameo",
            "hierarchical": axis == "theme_subcode",
            # Nothing is populated until phases 2-3 run. Saying so in the data
            # means the UI can render an honest "not yet coded" state instead
            # of an empty panel that looks like "no events".
            "status": "declared_not_yet_populated",
            "order": [c["key"] for c in items],
            "entries": {c["key"]: _cameo_entry(c) for c in items},
        }
    axes["theme_subcode"]["groups"] = {}
    for c in groups["theme_subcode"]:
        axes["theme_subcode"]["groups"].setdefault(c["parent_key"], []).append(c["key"])

    # event topics placed under the security themes (theme_code / theme_subcode entries get `domain` + `domain_also`; the theme gets its codes)
    dom_groups: dict[str, list[str]] = {d: [] for d in axes["security_domain"]["order"]}
    for axis in ("theme_code", "theme_subcode"):
        for k, e in axes[axis]["entries"].items():
            dom, also = CODE_TO_DOMAIN.get(e["code"], (None, ()))
            e["domain"], e["domain_also"] = dom, list(also)
            if dom in dom_groups:
                dom_groups[dom].append(k)
    axes["security_domain"]["groups"] = dom_groups

    return {
        "$schema_version": "1.0",
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "generator": "scripts/lib/taxonomy.py",
        "framework": cameo["framework"],
        "framework_version": cameo["framework_version"],
        "reserved_codes": cameo["reserved_codes"],
        "reserved_note": cameo["reserved_note"],
        "axis_notes": {
            "theme_code": cameo["axes"]["theme"],
            "action": cameo["axes"]["action"],
        },
        "axes": axes,
        "crosswalk": {k: sorted(v) for k, v in sorted(crosswalk.items())},
    }


# ==========================================================================
# validate
# ==========================================================================
def validate(tax: dict) -> list[str]:
    """Invariants the UI and the metrics layer rely on. Returns error strings."""
    errs: list[str] = []
    axes = tax["axes"]

    # 1. Order and entries must agree exactly. A key in `entries` but not in
    #    `order` is the exact defect this module was written to kill: it would
    #    be dropped from every ordered panel while still existing in the data.
    for name, ax in axes.items():
        order, entries = ax["order"], ax["entries"]
        if len(order) != len(set(order)):
            errs.append(f"{name}: duplicate keys in order")
        missing = sorted(set(entries) - set(order))
        extra = sorted(set(order) - set(entries))
        if missing:
            errs.append(f"{name}: in entries but not order (would be dropped from UI): {missing}")
        if extra:
            errs.append(f"{name}: in order but not entries (would render blank): {extra}")
        for k, e in entries.items():
            if not e.get("display"):
                errs.append(f"{name}.{k}: no display name (UI would show the raw key)")

    # 2. Legacy axes must match the classifier source exactly, both ways.
    try:
        cls_meta = classifier_metadata()
    except Exception as exc:                                    # noqa: BLE001
        errs.append(f"cannot parse {CLASSIFIER_SRC.name} for the drift check: {exc}")
        cls_meta = {}
    for axis in ("risk", "theme"):
        if axis not in cls_meta:
            errs.append(f"{axis}: absent from CATEGORY_DESCRIPTIONS in the classifier")
            continue
        here, there = set(axes[axis]["entries"]), set(cls_meta[axis])
        if here - there:
            errs.append(f"{axis}: declared here but not in the classifier: {sorted(here - there)}")
        if there - here:
            errs.append(f"{axis}: in the classifier but undeclared here "
                        f"(would render as a raw key): {sorted(there - here)}")
        for k in here & there:
            if not cls_meta[axis][k].get("hypothesis"):
                errs.append(f"{axis}.{k}: no NLI hypothesis in the classifier")

    # 3. Every legacy risk/theme category must have somewhere to go in CAMEO.
    #    An uncrosswalked category means the migration would lose it.
    for axis in ("risk", "theme"):
        for k, e in axes.get(axis, {}).get("entries", {}).items():
            if not e["crosswalk_to"]:
                errs.append(f"{axis}.{k}: no CAMEO crosswalk target -- migration would drop it")

    # 4. Hierarchy integrity: every sub-code's parent must exist as a parent code.
    parents = set(axes["theme_code"]["entries"])
    for k, e in axes["theme_subcode"]["entries"].items():
        if e["parent_key"] not in parents:
            errs.append(f"theme_subcode.{k}: parent {e['parent_key']!r} is not a parent code")
    #    ...and the two grains must never be merged into one axis (share_V).
    if set(axes["theme_code"]["entries"]) & set(axes["theme_subcode"]["entries"]):
        errs.append("theme_code and theme_subcode share keys -- share_V would double-count")

    # 5. Non-alertable classes must say why, in the data, not in a comment.
    for name, ax in axes.items():
        for k, e in ax["entries"].items():
            if not e["alertable"] and not e.get("alertable_reason"):
                errs.append(f"{name}.{k}: alertable=false with no stated reason")

    # 6. Every event topic sits under exactly one security theme, every theme has at least one event topic, secondaries are real themes.
    doms = set(axes["security_domain"]["order"])
    for axis in ("theme_code", "theme_subcode"):
        for k, e in axes[axis]["entries"].items():
            if e.get("domain") not in doms:
                errs.append(f"{axis}.{k}: no security theme (domain={e.get('domain')!r}); add its code to CODE_TO_DOMAIN")
            for x in e.get("domain_also", []):
                if x not in doms or x == e.get("domain"):
                    errs.append(f"{axis}.{k}: bad secondary theme {x!r}")
    for d, ks in axes["security_domain"].get("groups", {}).items():
        if not ks:
            errs.append(f"security_domain.{d}: no event topic placed under it")

    return errs


def main() -> int:
    tax = build()
    errs = validate(tax)
    for e in errs:
        print(f"  INVALID: {e}", file=sys.stderr)
    if errs:
        print(f"taxonomy validation FAILED with {len(errs)} error(s)", file=sys.stderr)
        return 1

    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUT_PATH.write_text(json.dumps(tax, ensure_ascii=False, indent=1), encoding="utf-8")

    for name, ax in tax["axes"].items():
        n_alert = sum(1 for e in ax["entries"].values() if e["alertable"])
        print(f"  {name:<15} n={len(ax['order']):>3}  alertable={n_alert:>3}  "
              f"kind={ax['kind']:<6} status={ax['status']}")
    print(f"  crosswalk: {len(tax['crosswalk'])} legacy categories -> CAMEO")
    print(f"  wrote {OUT_PATH.relative_to(EDA_ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

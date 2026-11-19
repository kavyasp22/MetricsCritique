"""Per-theme keyword cues (D01..D09) for the hybrid theme label.

The pretrained bge-m3 zero-shot domain (lib/domain_zeroshot.py) is only ~42% exactly right and 40% of its labelled posts are not
security posts (216 hand-judged posts, 2026-11-19). The hybrid keeps a theme only when the zero-shot choice is backed by a cue of
that same theme in the post text; otherwise the post is "Unassigned" (never a guess).

English cues only: the stored text is the translated text where a translation exists. Posts in other scripts without a
translation stay Unassigned (stated limit). A cue is a NECESSARY condition, not a proof: it removes posts with no vocabulary of
the theme, it does not make the label right (see the precision record in collaboration/04_CURRENT_STATE.md).
"""
from __future__ import annotations

import re

DOMAINS = ["D01", "D02", "D03", "D04", "D05", "D06", "D07", "D08", "D09"]

_CUES = {
    # conventional conflict: fighting between armies, strikes, shelling, ceasefires
    "D01": r"missile|drone strike|air ?strike|shelling|shelled|ceasefire|cease-fire|offensive|counter-?offensive|invasion|invaded|"
           r"artillery|bombard|bombing|bombed|ballistic|geran|shahed|kamikaze|frontline|front line|battlefield|hostilities|"
           r"troops|casualties|strikes? on|struck|destroyed|intercepted|rocket attack|war in|war between|war on",
    # diplomacy
    "D02": r"foreign minist|foreign ministry|ministry of foreign|ambassador|embassy|consulate|bilateral|summit|diplomat|envoy|"
           r"state visit|joint statement|\btalks\b|negotiat|security council|general assembly|multilateral|delegation|"
           r"\bmou\b|sidelines|high commissioner|foreign affairs|mediation|mediator",
    # strategic trade, sanctions, energy, infrastructure
    "D03": r"sanction|tariff|trade (deal|war|agreement|ties|talks|deficit|partner)|export controls?|embargo|rare earth|supply chain|"
           r"crude|oil (price|export|import|embargo|supply)|\blng\b|pipeline|energy (security|supply|crisis)|belt and road|\bbri\b|railway|port project|"
           r"semiconductor|\bopec\b|hydropower|trade (route|corridor)|critical minerals?",
    # maritime
    "D04": r"\bnavy\b|naval|warship|coast guard|maritime|aircraft carrier|submarine|destroyer|frigate|\bfleet\b|\bstrait\b|"
           r"sea lane|shipping lane|south china sea|west philippine sea|red sea|hormuz|merchant (ship|vessel)|cargo ship|"
           r"carrier strike group|\beez\b|undersea|tanker",
    # military operations and cooperation
    "D05": r"\barmy\b|armed forces|military|air force|defen[cs]e (ministry|minister|budget|pact|cooperation|procurement|industry|"
           r"forces|force|advis)|joint (exercise|drill)|exercise|\bdrills?\b|procure|fighter (jet|aircraft)|brigade|regiment|"
           r"commander|soldier|air defen[cs]e|counter-?uas|\bpla\b|\bplaaf\b|\bplarf\b|nuclear|warhead|weapon|armament|"
           r"missile test|\bnato\b|pentagon",
    # borders and territory
    "D06": r"\bborder|\blac\b|line of control|\bloc\b|disputed|sovereignty|territorial|territory|annex|boundary|occupied|enclave|"
           r"ladakh|arunachal|doklam|kashmir|aksai chin|taiwan strait|buffer zone|demarcat|indus water|standoff|"
           r"stand-off|incursion|transgress|separatis",
    # cyber, technology, espionage, influence
    "D07": r"cyber|hack|malware|ransomware|phishing|vulnerab|espionage|\bspy\b|spies|spying|surveillance|disinformation|"
           r"propaganda|influence operation|deepfake|data breach|breach|zero-day|encryption|electronic warfare|jamming|"
           r"spyware|botnet|infosec|artificial intelligence|\bai\b|5g|quantum|satellite|space force|cyber ?attack",
    # humanitarian crises
    "D08": r"humanitarian|refugee|displac|famine|\baid\b|asylum|migrant|epidemic|outbreak|cholera|relief|unhcr|\bwfp\b|ocha|"
           r"civilians|starvation|evacuat|food insecurity|disaster|earthquake|flood|\bunrwa\b|human rights",
    # terrorism, insurgency, organised crime
    "D09": r"terror|militant|insurgen|jihad|suicide (bomb|attack)|\bied\b|extremis|\bttp\b|\bisis\b|\biskp\b|al-?qaeda|"
           r"al-?shabaab|boko haram|\bhamas\b|hezbollah|houthi|rebel|cartel|narco|drug traffick|trafficking|counter-?terror|"
           r"hostage|assassinat|kidnap|\bbla\b|\bpkk\b|\bsdf\b|hideout|gunmen|bombing|explosion",
}
CUE_RE = {d: re.compile(p, re.I) for d, p in _CUES.items()}


def cue_hits(text: str, domain: str) -> int:
    """Number of DISTINCT cue terms of the domain found in the text."""
    return len({m.group(0).lower() for m in CUE_RE[domain].finditer(text if isinstance(text, str) else "")})


MIN_SCORE = 0.48          # best zero-shot domain score; below it the post stays unassigned
HIGH_HITS = 2             # distinct cue terms for the high tier


def hybrid_theme(text: str, best: str, best_score: float, confident_none: bool) -> tuple[str, str]:
    """(theme, tier). The zero-shot best domain is kept only when its own cue appears in the text AND its score >= MIN_SCORE.
    tier 'high' = >= HIGH_HITS distinct cue terms, 'medium' = 1 cue term; otherwise ('unassigned', '')."""
    if confident_none or best_score < MIN_SCORE or best not in CUE_RE:
        return "unassigned", ""
    h = cue_hits(text, best)
    if h >= HIGH_HITS:
        return best, "high"
    if h == 1:
        return best, "medium"
    return "unassigned", ""

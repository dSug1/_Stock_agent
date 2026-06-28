"""The scoring rubric — what each Claude call reads and returns (spec §9).

This module is provider-agnostic: it builds the compact per-company **evidence bundle** Claude scores
over, the **system prompt** (§9.5 rubric + the controlled mechanism vocabulary so D_mechanism maps to
valid ids), the **JSON output schema** (§9.3), and a semantic **validator**. The Anthropic wiring is
in ``clients.anthropic_client``; the tiering in ``stage4``.

The call's job, in one line: parse a ticker's evidence, map its activity onto the in-scope mechanisms,
and score how genuinely it embodies the Acrivon pattern (proprietary data engine + inference +
external validation + in-scope mechanism + translational bridge) — distinguishing a real platform
from press-release vapor.
"""

from __future__ import annotations

from typing import Any

AXES = ["A_proprietary_data", "B_compute_engine", "C_validation", "D_mechanism", "E_translation"]


# ── evidence bundle (the per-company input, spec §9.2) ──────────────────────

def build_bundle(company, evidence: dict[str, Any]) -> dict:
    """Assemble the compact evidence bundle for one company from store rows.

    ``evidence`` maps source -> payload (from ``store.get_evidence``). Kept small (~600 tokens):
    identity + cap + description + Stage-1 tags + harvested publication/clinical/patent summaries.
    """
    from ..clients.clinicaltrials import scan_designations
    oa = evidence.get("openalex") or {}
    ct = evidence.get("ctgov") or {}
    pv = evidence.get("patents") or {}
    ped = evidence.get("pedigree") or {}
    bundle: dict[str, Any] = {
        "company": company.name,
        "ticker": company.primary_ticker,
        "exchange": company.exchange,
        "country": company.country,
        "mktcap_usd_fd": company.mktcap_usd_fd,
        "business_description": company.business_description,
        "stage1_mechanism_tags": company.ta_tags,         # coarse keyword candidates (D refines these)
    }
    if oa:
        bundle["publications"] = {
            "works_count": oa.get("works_count"), "h_index": oa.get("h_index"),
            "cited_by_count": oa.get("cited_by_count"), "top_concepts": oa.get("top_concepts"),
        }
    if ped:
        bundle["pedigree"] = {                            # founder / scientific pedigree (§5.6.1)
            "top_authors": ped.get("top_authors"),
            "prestige_recognitions": ped.get("prestige_recognitions"),
        }
    if pv:
        bundle["patent_estate"] = {
            "patent_count": pv.get("patent_count"),
            "method_platform_titles": pv.get("method_platform_titles"),
            "composition_titles": pv.get("composition_titles"),
        }
    if ct:
        bundle["clinical"] = {
            "trial_count": ct.get("trial_count"), "phases": ct.get("phases"),
            "top_conditions": ct.get("top_conditions"),
            "biomarker_or_companion_dx_language": ct.get("biomarker_or_cdx_language"),
        }
    # FDA / regulatory designations (awards & recognitions) — from trials + the description
    designations = sorted(set((ct.get("fda_designations") or []))
                          | set(scan_designations(company.business_description)))
    if designations:
        bundle["fda_designations"] = designations
    return bundle


# ── system prompts ──────────────────────────────────────────────────────────

_RUBRIC_PREFACE = """\
You are a skeptical biotech equity analyst screening for the "Acrivon pattern": a proprietary
high-dimensional DATA-GENERATION engine + a COMPUTATIONAL INFERENCE layer (optionally generative/LLM)
+ EXTERNAL or wet-lab VALIDATION + anchoring to an in-scope biological MECHANISM + a TRANSLATIONAL
bridge (companion Dx or biomarker-defined clinical assets).

Score ONLY from the supplied evidence (publications, patents, trial registrations, descriptions).
Treat investor-relations adjectives as near-worthless absent peer-reviewed / patent / wet-lab backing.

Rules:
(1) The AI / foundation-model axis (B) is high-value but NOT required — strong proprietary data +
    validation + mechanism can score well with non-LLM ML.
(2) Distinguish a proprietary-DATA moat from a commoditizable-ARCHITECTURE one; an impressive model
    trained only on public databases is a weak moat.
(3) Reward validation that uses fresh/EXTERNAL ground truth (wet-lab loops, prospective clinical,
    independent benchmarks) over held-out metrics on the model's own training database.
(4) For every company, state the strongest DISCONFIRMING evidence; if the "platform" is unbacked
    vocabulary, say so. B and C cannot score high without external evidence.
(5) Carry inferred values as inferences, not facts; never invent citations — if evidence is absent,
    score low and say why. Scores are 0-5 integers (composite/confidence are 0-1).
(6) FOUNDER / SCIENTIFIC PEDIGREE is a HIGH-PRECISION signal: prestige-lab lineage, high-h-index
    authors, and major recognitions (Nobel / NAS / Lasker / Breakthrough Prize / foundation-model-in-
    biology authors) materially raise confidence that the proprietary-data engine (A) is real and
    deep. Weigh the `pedigree` field accordingly and name any prestige recognition in the memo. A
    near-empty author list / no recognized names is itself informative (lower A confidence).
(7) AWARDS & FDA BREAKTHROUGHS corroborate translation/validation: FDA designations (Breakthrough
    Therapy/Device, RMAT, Fast Track, Orphan, PRIME) and external recognitions lift E (and sometimes
    C). Treat them as CORROBORATION, not proof — they don't substitute for the underlying proprietary
    data + wet-lab/external validation. A genuine PLATFORM shows pedigree + data + validation together.
(8) Read the `patent_estate`: weight METHOD/PLATFORM patents (the data engine) over composition-of-
    matter (a single molecule) when judging the moat.
(9) DO NOT penalize a YOUNG / early-stage company for thin clinical or translational evidence. A
    pre-clinical or recently-public platform legitimately has few or no trials yet — score the
    PLATFORM on its merits (A proprietary data, B inference, pedigree, and whatever validation
    exists). A low E (no clinical assets yet) for a clearly early company should lower the score only
    modestly, not dominate it; the absence of late-stage trials is NOT evidence against a real data
    engine. Reserve low A/C scores for companies that lack the data/validation, not for youth.

Calibration anchor (Acrivon Therapeutics — desired behaviour): A=5 (proprietary ~120k-phosphosite
drug-response dataset is the moat), B=3 capped-not-maxed (ESM-2 ensemble is disclosed/replicable —
high capability, weak architectural moat), C=5 (InViKA wet-lab loop + external benchmarks + CPTAC/FLT4
origination — fresh ground truth, not held-out AUPRC alone), E=5 (OncoSignature companion Dx),
moat_location=data, substance=substantive. Transfer the PATTERN, not the phosphoproteomics modality.

D_mechanism.mechanism_ids must be drawn from this controlled vocabulary (id — branch — label):
"""


def build_rubric_system(taxonomy: dict) -> str:
    """The full scoring system prompt + the controlled mechanism vocabulary (cached across companies)."""
    lines = [f"  {m['id']} — {m['branch']} — {m.get('label', '')}"
             for m in (taxonomy.get("mechanisms") or [])]
    return (_RUBRIC_PREFACE + "\n".join(lines) +
            "\n\nReturn ONLY the JSON object specified by the schema, no preamble.")


ADVERSARIAL_SUFFIX = ("\n\nFINALIZE PASS: this company is in the contested band. Argue the case AGAINST "
                      "it first — what is the single strongest reason this is NOT an Acrivon-pattern "
                      "platform? Only then score. Be conservative; default a borderline B/C downward.")

TRIAGE_SYSTEM = """\
You are a fast biotech screener. Decide whether a company is PLAUSIBLY an "Acrivon-pattern" platform:
a proprietary high-dimensional data-generation engine + a computational inference layer + external
validation + an in-scope mechanism (oncology / autoimmune / GPCR) + a translational bridge.

This is a cheap recall-safe triage, NOT a final score. KEEP anything that could plausibly fit on the
available evidence — only KILL obvious non-matches (pure CRO/manufacturing/services, a single-asset
drug with no platform, a diversified non-biotech, or a company with no data-engine signal at all).
When unsure, KEEP. Return ONLY the JSON object: keep (bool), prior (0-1), reason (one sentence)."""


# ── output schemas (structured outputs, spec §9.3) ──────────────────────────

def _axis(extra: dict) -> dict:
    props = {"score": {"type": "integer"}, "citation": {"type": "string"}}
    props.update(extra)
    return {"type": "object", "additionalProperties": False,
            "required": list(props.keys()), "properties": props}


RUBRIC_SCHEMA: dict = {
    "type": "object", "additionalProperties": False,
    "required": ["company", "ticker", "A_proprietary_data", "B_compute_engine", "C_validation",
                 "D_mechanism", "E_translation", "moat_location", "substance_check", "memo"],
    "properties": {
        "company": {"type": "string"}, "ticker": {"type": "string"},
        "A_proprietary_data": _axis({"modality": {"type": "string"},
                                     "scale_evidence": {"type": "string"}}),
        "B_compute_engine": _axis({"is_foundation_model": {"type": "boolean"},
                                   "generative_evidence": {"type": "string"}}),
        "C_validation": _axis({"validation_type": {"type": "string"}}),
        "D_mechanism": {"type": "object", "additionalProperties": False,
                        "required": ["score", "mechanism_ids", "branch", "disruption_rationale"],
                        "properties": {"score": {"type": "integer"},
                                       "mechanism_ids": {"type": "array", "items": {"type": "string"}},
                                       "branch": {"type": "string"},
                                       "disruption_rationale": {"type": "string"}}},
        "E_translation": _axis({"companion_dx": {"type": "boolean"},
                                "lead_phase": {"type": "string"}}),
        "moat_location": {"type": "object", "additionalProperties": False,
                          "required": ["data_vs_architecture", "rationale"],
                          "properties": {"data_vs_architecture":
                                         {"type": "string",
                                          "enum": ["data", "architecture", "mixed"]},
                                         "rationale": {"type": "string"}}},
        "substance_check": {"type": "object", "additionalProperties": False,
                            "required": ["verdict", "disconfirming_evidence"],
                            "properties": {"verdict": {"type": "string",
                                                       "enum": ["substantive", "marketing", "mixed"]},
                                           "disconfirming_evidence": {"type": "string"}}},
        "memo": {"type": "string"},
    },
}

TRIAGE_SCHEMA: dict = {
    "type": "object", "additionalProperties": False,
    "required": ["keep", "prior", "reason"],
    "properties": {"keep": {"type": "boolean"}, "prior": {"type": "number"},
                   "reason": {"type": "string"}},
}


# ── validation (defense-in-depth on top of structured outputs) ──────────────

class RubricError(ValueError):
    """Raised when a rubric response is structurally or semantically invalid."""


def validate_rubric(data: Any) -> dict:
    """Check the rubric JSON: required axes present, scores integers in 0-5, enums valid."""
    if not isinstance(data, dict):
        raise RubricError("rubric is not an object")
    for axis in AXES + ["moat_location", "substance_check"]:
        if axis not in data:
            raise RubricError(f"missing section: {axis}")
    for axis in AXES:
        score = (data.get(axis) or {}).get("score")
        if not isinstance(score, int) or not (0 <= score <= 5):
            raise RubricError(f"{axis}.score must be an int 0-5, got {score!r}")
    if (data["moat_location"].get("data_vs_architecture")
            not in ("data", "architecture", "mixed")):
        raise RubricError("moat_location.data_vs_architecture invalid")
    if data["substance_check"].get("verdict") not in ("substantive", "marketing", "mixed"):
        raise RubricError("substance_check.verdict invalid")
    return data


def validate_triage(data: Any) -> dict:
    if not isinstance(data, dict) or "keep" not in data:
        raise RubricError("triage missing 'keep'")
    return data

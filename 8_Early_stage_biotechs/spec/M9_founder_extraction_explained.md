# M9 — Founder-lineage extraction (first Claude spend), explained

*House-style milestone note. Companion to `decisions.md` D8 and overall spec §5.1/§5.5. This is the
first Claude API call in Module 8 — it turns the empty `founder` table (schema v3) into real data and
sets up the module's thesis signal.*

---

## What it does
For each active-universe entity, Claude researches the company's **scientific founders, key inventors,
and Scientific Advisory Board** via web_search and returns a structured roster
(name / role / institution / is_company_officer) + academic affiliations. Results land in the `founder`
table. CLI: `scripts/8_extract.py`. This is a **gated spend** — it costs money, behind a `[y/N]`
confirmation and a hard `max_usd_per_run` cap.

## Why this is the first Claude call
Module 8's whole thesis is *independently-corroborated but under-recognized mechanisms*. The strongest
form of that signal — an independent lab citing a founder's foundational paper (§5.2) — needs to know
**who the founders are** and **which papers are theirs**. That's the §5.1 extraction. So founder
lineage is the prerequisite for the literature/citation layer, which is why it comes before the
OpenAlex signal and the scoring rubric.

## Every accumulated Claude-dispatch lesson, applied
The client (`clients/anthropic_client.py`) is ported from Module 6's proven wrapper, with current-fact
corrections, and bakes in the memory lessons:

- **Cheap tier, right web_search variant.** Extraction is a Haiku job (`claude-haiku-4-5`, §5.5). It
  uses the **basic `web_search_20250305`** — it honors `max_uses`, is ~10× faster than the dynamic
  `web_search_20260209` (which ignored max_uses and timed out >600s), *and* is the only web_search
  variant valid on Haiku. Verified against the claude-api skill.
- **Structured output, full size.** Forced `output_config.format` json_schema, validated at the
  tool-call layer; `max_output_tokens` sized to the full response so the JSON isn't truncated → dropped
  (an undersized cap silently produced invalid JSON in a prior module).
- **Batch API is the production default** (50% cost) for a bulk sweep; realtime async fan-out (bounded
  concurrency) for small/interactive runs.
- **Crash-safe.** Batch mode writes the `batch_id` to `data/extract_batch_id.txt` **before** the long
  poll, so `--resume` re-attaches after a crash (Anthropic keeps results ~29 days). Both modes
  **persist per entity** as each result arrives — a mid-sweep kill keeps prior work.
- **Skip-cache on identity + prompt version.** `entities_for_extraction` returns only entities not yet
  done at the current `extraction_prompt_version`; bumping the version re-opens everyone. No paid
  re-work on a re-run.
- **Prompt caching.** The static system prompt is cached (`cache_control: ephemeral`), byte-stable
  across the run.
- **Cost discipline.** A mandatory `[y/N]` gate shows a pre-dispatch estimate (scaled by
  `cost_calibration_factor = 0.10` to match real invoices), and a hard `max_usd_per_run` guard raises
  `BudgetExceeded` if spend would cross the cap. The key is read from `.env`, never logged.

## The one discipline that matters most: never invent
The system prompt is emphatic that the model must report **only** people/institutions it actually found
via search, and return an **empty** roster with `confidence: "low"` when it can't — an empty answer is
correct; a fabricated one is harmful. The company identity in the user message is delimited as **data
to research, not instructions** (prompt-injection containment; the model output only writes the DB,
drives no unsafe action).

**Live validation (2 entities, ~$0.01, 22s) confirmed the discipline holds on a real call:** it
returned real, verifiable founders — Stuart Rich (Northwestern Feinberg) as Tenax's CMO/board, Michael
Hays as NRC Health's founder — and where it couldn't find an institution it wrote "Unknown" / "None
identified" rather than inventing one.

## How to run / verify
```
cd 8_Early_stage_biotechs   # needs ANTHROPIC_API_KEY in repo-root .env
PYTHONPATH=src ..\.venv\Scripts\python.exe -m pytest tests\test_extraction.py -q   # 7 offline tests (no spend)
PYTHONPATH=src ..\.venv\Scripts\python.exe scripts\8_extract.py --limit 3 --realtime   # tiny live smoke
PYTHONPATH=src ..\.venv\Scripts\python.exe scripts\8_extract.py --limit 200            # batch sweep (gated)
PYTHONPATH=src ..\.venv\Scripts\python.exe scripts\8_extract.py --resume               # re-attach a batch
PYTHONPATH=src ..\.venv\Scripts\python.exe scripts\8_extract.py --stats
```

## Deferred (honest scope)
- **Full sweep** — only a 2-entity smoke has run; the ~890 active-universe sweep (~$0.10–0.50 on Haiku
  Batch) is gated and not yet executed.
- **Literature signal (§3.1)** — OpenAlex/PubMed queries keyed on the extracted founder names; this is
  the immediate next milestone and what makes founder lineage pay off.
- **Independence classification (§5.2)** and the **D3 stack-convergence scoring rubric (§5.4)** — the
  expensive, high-value calls, run only on pre-filtered candidates.
- Founder de-duplication across entities, and confidence-weighting of low-confidence extractions.

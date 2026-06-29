# Acrivon-Pattern Listed-Biotech Screener — Shortlist

*Run `2026-06-29T04:11:14+00:00` · generated 2026-06-29 · market-cap band $50,000,000–$3,000,000,000 · 6 scored companies (of 589 in store).*

Composite is the code-computed weighted rubric score (auditable; §10). `rank_score = composite` (lifecycle age-weighting OFF — `stage5.lifecycle.enabled`).

## Ranked shortlist

| # | Ticker | Company | Composite | Rank | Conf | Moat | Substance | Mechanism |
|--:|:--|:--|--:|--:|--:|:--|:--|:--|
| 1 | **ACRV** | Acrivon Therapeutics, Inc. | 0.904 | 0.904 | 0.50 | data | substantive | — |
| 2 | **GRAL** | GRAIL, Inc. | 0.760 | 0.760 | 0.50 | data | substantive | — |
| 3 | **RXRX** | RECURSION PHARMACEUTICALS, INC. | 0.744 | 0.744 | 0.35 | data | mixed | lsd1_demethylase, menin_kmt2a, ubiquitination_degradation |
| 4 | **ABSI** | Absci Corp | 0.704 | 0.704 | 0.50 | mixed | substantive | tnf_il_axis, treg_modulation |
| 5 | **SDGR** | Schrodinger, Inc. | 0.672 | 0.672 | 0.30 | mixed | substantive | — |
| 6 | **SANA** | Sana Biotechnology, Inc. | 0.402 | 0.402 | 0.35 | architecture | mixed | b_cell_depletion_degrader, tolerogenic_antigen_specific |

## Memos

### 1. Acrivon Therapeutics, Inc. (ACRV) — composite 0.904

*exchange Nasdaq · country US · mkt cap $72M · ~4 yr since IPO · confidence 0.50*

**Axes:** A (proprietary data) 5/5 · B (compute) 3/5 · C (validation) 5/5 · D (mechanism) 4/5 · E (translation) 5/5

**Moat (data):** The core moat is the proprietary phosphoproteomic drug-response dataset (~120k phosphosites, unique experimental profiling across drug-cell line-patient matrices). The computational architecture (ESM-2 ensemble, generative ML) is high-capability but largely replicable by well-resourced competitors; the dataset is not. OncoSignature companion Dx value derives from data uniqueness, not model novelty.

Acrivon is the CALIBRATION ANCHOR for this scoring system. Core moat: ~120,000-phosphosite proprietary drug-response phosphoproteomic dataset, generated internally via mass spectrometry and the InViKA wet-lab loop — this is not reconstructable from public databases. The AP3 platform uses ESM-2 protein language model ensembles (disclosed, replicable architecture) to infer kinase activity states and generate drug-specific OncoSignature companion diagnostics. Validation is multi-layered and external: InViKA wet-lab loop, CPTAC external benchmarking, and prospective clinical enrichment in ACR-368 Phase 1/2 trials. Lead asset ACR-368 (CHK1/CHK2 inhibitor) carries FDA Fast Track designation, reinforcing translational credibility. Scientific pedigree: founded by proteomics/systems biology researchers with peer-reviewed publication record in phosphoproteomics; approach originated from academic phosphoproteomic drug-response work. Strongest disconfirming evidence: tiny market cap, single-asset clinical risk, open-source ML architecture, and 'generative' label may slightly overstate AI novelty. Nonetheless, data + wet-lab validation + companion Dx combination is genuine and pattern-matching to the Acrivon archetype by definition.

**Disconfirming:** Small market cap (~$72M) and limited revenue suggest commercial validation is nascent. ACR-368 is not yet approved; OncoSignature clinical utility is prospective/enrichment-based rather than confirmed pivotal-trial validated. ESM-2 architecture is open-source, reducing architectural moat. Company is single-asset-risk dependent on ACR-368 Phase 2 readouts. Some 'generative' platform language in IR materials may overstate current generative AI sophistication relative to conventional phosphoproteomic ML pipelines.

*(merged duplicate company-ids: fd1881898c215386)*


### 2. GRAIL, Inc. (GRAL) — composite 0.760

*exchange Nasdaq · country US · mkt cap $2,829M · ~2 yr since IPO · confidence 0.50*

**Axes:** A (proprietary data) 5/5 · B (compute) 3/5 · C (validation) 4/5 · D (mechanism) 1/5 · E (translation) 5/5

**Moat (data):** GRAIL's durable competitive advantage rests almost entirely on the scale and longitudinal depth of its proprietary methylation training datasets (CCGA, STRIVE, PATHFINDER) — not on a unique algorithmic architecture. The ML classifiers, while sophisticated, are built on conventional supervised learning; any competitor with comparable annotated cfDNA methylation data could replicate the approach. The multi-year, multi-site biobanking effort with biopsy-confirmed outcomes is the asset that is structurally hard to replicate.

GRAIL is the canonical large-scale proprietary-data moat in liquid biopsy — the Acrivon pattern maps clearly onto the DATA axis. The CCGA/STRIVE/PATHFINDER biobanking effort (tens of thousands to ~140,000 participants with longitudinally annotated, biopsy-confirmed outcomes) represents a dataset scale that is genuinely irreplaceable in the near term. Scientific pedigree is strong: GRAIL was co-founded with backing from Illumina and leading cancer genomics researchers; the CCGA study design reflects rigorous epidemiological and clinical collaboration. FDA Breakthrough Device Designation for Galleri corroborates the translational credibility. The compute layer (B=3) is real but not architecturally novel — the moat is unambiguously data. The primary investment risk is clinical: if the NHS-Galleri RCT (140,000-person prospective RCT, results expected ~2026) fails to demonstrate stage shift or mortality benefit, the screening paradigm faces an existential challenge. Sensitivity for early-stage cancer (~16-40% for stage I) is the key technical limitation — the platform excels at late-stage detection where screening value is lower. Reimbursement (no CMS coverage) remains the dominant commercial risk. No mechanism tag applies — the platform is pan-cancer and mechanism-agnostic, which is a feature for screening breadth but limits the D score.

**Disconfirming:** Key disconfirming risks: (1) Sensitivity for early-stage (stage I-II) cancers remains limited (~20-40% at high specificity), which is the clinically critical use case for screening; (2) The NHS-Galleri RCT primary endpoint (stage shift) results are not yet published — if the trial fails to show meaningful clinical benefit, the entire screening thesis is challenged; (3) No FDA PMA/510(k) clearance yet — Galleri is LDT-status, not FDA-cleared, limiting broad insurance coverage; (4) Medicare/CMS coverage is not established, creating reimbursement risk; (5) Competitors (Exact Sciences, Illumina's Grail spinout dynamics, Guardant, Personal Genome Diagnostics) are building competing methylation-based MCED tests; (6) The company has been loss-making with high cash burn, and its Illumina entanglement created significant regulatory and corporate governance uncertainty.


### 3. RECURSION PHARMACEUTICALS, INC. (RXRX) — composite 0.744

*exchange Nasdaq · country US · mkt cap $1,868M · ~5 yr since IPO · confidence 0.35*

**Axes:** A (proprietary data) 5/5 · B (compute) 4/5 · C (validation) 3/5 · D (mechanism) 3/5 · E (translation) 3/5

**Moat (data):** Recursion's core defensibility lies in its proprietary petabyte-scale cellular phenomics dataset and the associated wet-lab automation infrastructure generating it. The ML architectures (transformers, CNNs, GNNs) are state-of-the-art but replicable; the dataset is not. Competitors cannot easily replicate ~10 years of continuous high-throughput experimentation and the resulting phenotypic maps across thousands of genetic and chemical perturbations.

Recursion is the highest-profile 'industrialized drug discovery' platform company, co-founded by Chris Gibson and Dean Li (former Merck CSO, strong pedigree) and backed by significant institutional capital including a landmark NVIDIA partnership. The core thesis is a DATA moat: a proprietary petabyte-scale cellular phenomics corpus generated by ~2.5M experiments/week through heavily automated lab infrastructure, which no academic or smaller biotech can replicate easily. The LOWE foundation model trained on this corpus adds a genuine AI layer. However, the moat scores 'mixed' on substance because (a) clinical validation of the platform's predictive accuracy remains unproven — no program has generated positive Phase 2/3 efficacy data yet; (b) no companion diagnostic exists despite years of biomarker-adjacent language; and (c) external partnerships have not publicly produced validated leads. The mechanism portfolio is broad and opportunistic (LSD1, CDK, RAS/MAPK, FAP), consistent with a phenotypics-first approach but weakening any single mechanistic moat. Overall: the DATA ENGINE is real and large-scale; the INFERENCE layer is sophisticated and includes a genuine foundation model; VALIDATION is partial (wet-lab confirmed hits, clinical stage programs); TRANSLATION is early-stage. This is a platform company with genuine substance at the data/compute layer but meaningful clinical execution risk and no yet-demonstrated translational proof-of-concept.

**Disconfirming:** (1) Despite significant capital deployed and years of operation, Recursion has not yet demonstrated a clinical success directly attributable to the platform — all clinical programs are Phase 1/2 with no efficacy read-outs validating platform-derived predictions. (2) Several early partnerships (e.g., Bayer) were not renewed or disclosed as unsuccessful, raising questions about external validation of predictive accuracy. (3) The Exscientia acquisition added chemistry capabilities but also integration risk and cost. (4) No companion diagnostic or biomarker strategy is formally disclosed, limiting the 'precision medicine' narrative. (5) The phenomics-to-mechanism translation step remains a known bottleneck — high-content imaging hits do not always map to tractable drug targets, and the company has been criticized for generating large datasets without proportionate translational throughput.

*(merged duplicate company-ids: a4748b360106ad9a)*


### 4. Absci Corp (ABSI) — composite 0.704

*exchange Nasdaq · country US · mkt cap $1,845M · ~5 yr since IPO · confidence 0.50*

**Axes:** A (proprietary data) 4/5 · B (compute) 4/5 · C (validation) 4/5 · D (mechanism) 2/5 · E (translation) 3/5

**Moat (mixed):** Absci's moat is partially in proprietary sequence-function data generated by its cell-free high-throughput expression engine (data moat) and partially in the generative AI architecture trained on that data (architecture). The cell-free expression platform generating large-scale proprietary training data is the harder-to-replicate component; the generative model architecture itself is based on protein language model paradigms increasingly accessible in academia, but the proprietary training corpus and wet-lab integration create genuine differentiation.

Absci represents a genuine but early-stage 'Acrivon-pattern' company where the moat is a high-throughput cell-free protein expression engine generating proprietary sequence-function training data for generative AI antibody design. The scientific pedigree is credible: a 2023 Nature paper on zero-shot generative antibody design is a meaningful external validation signal, placing Absci among the small cohort of companies with peer-reviewed foundation-model-in-biology publications. The platform data engine (cell-free expression + deep mutational scanning at scale) is the primary moat, with the generative AI layer being high-capability but increasingly contested architecturally. Clinical translation is early-stage (Phase 1 only, no companion Dx), limiting E score. The mechanism anchoring is weak — pipeline assets address conventional indications (IBD, alopecia, IO) without a single deeply proprietary biological mechanism. Collaborations with MSK, Twist, and Owkin add external validation but also signal platform dependencies. The strongest disconfirming factor is competitive intensity in generative protein design: Generate Biomedicines, Profluent, and EvolutionaryScale (ESM3) are well-funded rivals with comparable or superior foundation models, making architectural moat durability questionable. Data moat durability depends on continued throughput leadership in cell-free expression. Overall: substantive AI-bio platform with real wet-lab validation, but not yet a fully closed precision-medicine loop (no companion Dx, no clinical POC).

**Disconfirming:** Key disconfirming evidence: (1) The Nature 2023 zero-shot antibody design paper, while impressive, showed functional but not necessarily best-in-class binders — generative AI for antibodies is an intensely competitive field (Dyno, Generate Biomedicines, Profluent, EvolutionaryScale all active); (2) ABS-101 and ABS-201 are early Phase 1 — no clinical proof-of-concept yet; (3) No companion diagnostic strategy disclosed, reducing the precision-medicine differentiation; (4) The cell-free expression system, while proprietary in scale, is not a fully unique modality — competitors use yeast display, phage display at comparable scale; (5) Partnerships with Twist, Owkin, Oracle suggest platform is not entirely self-contained and may require external data/compute infrastructure.


### 5. Schrodinger, Inc. (SDGR) — composite 0.672

*exchange Nasdaq · country US · mkt cap $1,260M · ~6 yr since IPO · confidence 0.30*

**Axes:** A (proprietary data) 4/5 · B (compute) 3/5 · C (validation) 4/5 · D (mechanism) 2/5 · E (translation) 3/5

**Moat (mixed):** The moat is partly in the proprietary physics-based simulation infrastructure and OPLS force-field datasets (data/methodology), which are not easily replicated, and partly in the FEP+ computational architecture, which is the industry benchmark for binding free energy calculations. Neither alone is sufficient — the combination of 30+ years of force-field parameterization data plus validated FEP+ workflow is the true differentiator. Architecture is well-disclosed in publications but operationally deep.

Schrödinger (SDGR) is one of the most technically credible computational drug discovery platforms publicly traded. Founded in 1990, the company has 30+ years of physics-based simulation development anchored by the OPLS force-field (parameterized from extensive experimental and QM data) and the FEP+ engine, which remains an industry benchmark for binding free energy calculations. Scientific pedigree is strong: founders include academic luminaries in computational chemistry, and the company counts Richard Friesner (Columbia, NAS member) as a key scientific architect — a high-precision signal that the data engine is real and deep. The platform is genuinely proprietary in its depth: OPLS4 trained on >100k experimental data points, decades of simulation runs across 1700+ pharma collaborator projects, and an internally generated Drug Discovery pipeline that provides wet-lab ground truth. Validation is substantive: FEP+ has prospective clinical-stage validation (SGR-1505 in Phase 2; SGR-2921 in Phase 1) and extensive peer-reviewed benchmarks in JCTC/JCIM/JACS. The primary disconfirming evidence is competitive: open-source force-fields (OpenFF) and academic FEP implementations are improving; the architectural moat is partially published away; and ML surrogate models rely partly on public databases. The absence of an approved drug and no companion diagnostic strategy weakens the E score. The platform is therapeutic-area-agnostic (no specific mechanism from the controlled vocabulary dominates), which is both a breadth strength and a focus weakness. Overall pattern: strong physics-data + computation moat, substantive external validation, moderate translation progress. Not the Acrivon pattern (no companion Dx, not biomarker-stratified) but a legitimate deep-tech platform.

**Disconfirming:** Key risks: (1) FEP+ methodology is extensively published and academically replicable in principle — open-source alternatives (OpenFF, OpenFE) and cloud-based competitors (OpenEye/Cadence, Schrödinger clones) are narrowing the gap. (2) The ML surrogate models are trained partly on public data (ChEMBL, PDB) diluting the proprietary edge. (3) Drug Discovery segment has not yet produced an approved drug, so translational validation is incomplete. (4) No companion diagnostic or biomarker-defined patient selection strategy disclosed. (5) Market cap decline suggests investor skepticism about the timeline to drug approvals sustaining the software business.

*(merged duplicate company-ids: c7611de8ed06e459)*


### 6. Sana Biotechnology, Inc. (SANA) — composite 0.402

*exchange Nasdaq · country US · mkt cap $952M · ~5 yr since IPO · confidence 0.35*

**Axes:** A (proprietary data) 3/5 · B (compute) 1/5 · C (validation) 3/5 · D (mechanism) 3/5 · E (translation) 3/5

**Moat (architecture):** Sana's moat is primarily architectural/biological: the HIP immune-evasion platform (licensed from Harvard, peer-reviewed), the fusosome in vivo delivery technology (proprietary lipid-envelope mechanism for cell-type-specific gene delivery), and CRISPR editing access. There is no disclosed large proprietary dataset engine. The moat depends on biological platform exclusivity and IP, not a data-generation flywheel.

Sana Biotechnology is a cell therapy platform company with two principal biological innovations: (1) HIP (hypoimmune) cell modification, originally developed in the Bhanu lab / Tobias Deuse / Sonja Schrepfer group at UCSF (published Nature Biomedical Engineering 2019) — providing allogeneic cell products with immune-evasion properties without systemic immunosuppression; and (2) fusosomes — proprietary engineered lipid-enveloped particles derived from viral fusion proteins that can deliver genetic cargo to specific cell types (e.g., CD8+ T cells) in vivo without ex vivo cell manipulation. Schrepfer (co-founder) has strong academic pedigree in transplant immunology; fusosome technology originated from internal R&D. The lead clinical asset UP421 (HIP-modified donor islets) is in Phase 1 for T1D in collaboration with Mayo Clinic. SG293/SG299 are in vivo fusosome programs targeting B-cell malignancies and autoimmune disease. DISCONFIRMING: No AI/ML data engine — this is a biological platform, not a data+compute moat. Core HIP IP is licensed (Harvard), not owned. Fusosome clinical translation unproven. No companion Dx. Pipeline breadth is modest relative to capital intensity. The pattern does NOT match the Acrivon archetype: there is no proprietary high-dimensional data-generation engine and no computational inference layer. Substance is real (peer-reviewed biology, genuine clinical programs) but the moat is biological/IP rather than data-computational.

**Disconfirming:** 1) HIP core IP is licensed from Harvard, not internally generated — dependency risk and limited architectural exclusivity. 2) Fusosome technology is early-stage with no clinical readouts yet for in vivo programs; NHP data is promising but limited. 3) No disclosed AI/ML computational layer — the 'platform' is biological engineering, not a data+compute engine in the Acrivon sense. 4) CRISPR editing relies on a Beam Therapeutics license, adding another external dependency. 5) Clinical pipeline is very early (Phase 1 / preclinical) with no efficacy data released. 6) The trial conditions listed (ALD, AGS, Alexander Disease) appear mismatched with the company's described focus areas, suggesting the clinical metadata may reflect historical or partner programs rather than Sana's core pipeline.


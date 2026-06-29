# Acrivon-Pattern Listed-Biotech Screener — Shortlist

*Run `2026-06-29T05:02:17+00:00` · generated 2026-06-29 · market-cap band $50,000,000–$3,000,000,000 · 7 scored companies (of 589 in store).*

Composite is the code-computed weighted rubric score (auditable; §10). `rank_score = composite` (lifecycle age-weighting OFF — `stage5.lifecycle.enabled`).

## Ranked shortlist

| # | Ticker | Company | Composite | Rank | Conf | Moat | Substance | Mechanism |
|--:|:--|:--|--:|--:|--:|:--|:--|:--|
| 1 | **ACRV** | Acrivon Therapeutics, Inc. | 0.904 | 0.904 | 0.50 | data | substantive | — |
| 2 | **BOLD** | Boundless Bio | 0.800 | 0.800 | 0.90 | mixed | substantive | ecdna |
| 3 | **GRAL** | GRAIL, Inc. | 0.760 | 0.760 | 0.50 | data | substantive | — |
| 4 | **RXRX** | RECURSION PHARMACEUTICALS, INC. | 0.744 | 0.744 | 0.35 | data | mixed | lsd1_demethylase, menin_kmt2a, ubiquitination_degradation |
| 5 | **ABSI** | Absci Corp | 0.704 | 0.704 | 0.50 | mixed | substantive | tnf_il_axis, treg_modulation |
| 6 | **SDGR** | Schrodinger, Inc. | 0.568 | 0.568 | 0.55 | mixed | mixed | — |
| 7 | **SANA** | Sana Biotechnology, Inc. | 0.402 | 0.402 | 0.35 | architecture | mixed | b_cell_depletion_degrader, tolerogenic_antigen_specific |

## Memos

### 1. Acrivon Therapeutics, Inc. (ACRV) — composite 0.904

*exchange Nasdaq · country US · mkt cap $72M · ~4 yr since IPO · confidence 0.50*

**Axes:** A (proprietary data) 5/5 · B (compute) 3/5 · C (validation) 5/5 · D (mechanism) 4/5 · E (translation) 5/5

**Moat (data):** The irreplaceable asset is the proprietary ~120,000-phosphosite drug-response dataset generated from patient tumor samples via mass spectrometry. The ML/generative inference layer (ESM-2 ensemble) is capable but built on a public foundation model and is architecturally replicable. The OncoSignature companion Dx pipeline is only defensible because of the underlying data engine.

Acrivon is the calibration anchor and scores accordingly. The AP3 platform is the canonical example of the target pattern: a proprietary high-dimensional data-generation engine (phosphoproteomics mass-spec, ~120k phosphosites, patient tumor samples) feeding a computational inference layer (ESM-2 ensemble, labeled 'generative' by the company) with genuine wet-lab external validation (InViKA loop, CPTAC benchmarks) and a fully operationalized translational bridge (OncoSignature companion Dx in biomarker-stratified Phase 1/2 trials for ACR-368 in endometrial/ovarian cancer and SCCs; ACR-2316 in development). Moat is firmly in DATA, not architecture. The ESM-2 model is public; the phosphosite drug-response dataset is not. No Nobel/NAS-level pedigree flags identified in the supplied record, but the Watertown, MA clinical-stage positioning and platform architecture are consistent with serious academic-to-industry translation. FDA biomarker language in trial design corroborates translational seriousness. Key risks: sub-$100M market cap reflects real commercial uncertainty; 'generative' branding slightly overstates the architecture; prospective companion Dx registration-level evidence still pending.

**Disconfirming:** Strongest disconfirming considerations: (1) Market cap of ~$72M suggests market skepticism about near-term commercial viability; (2) ESM-2 architecture is publicly available — a well-funded competitor could replicate the inference layer if they acquired comparable phosphoproteomics data; (3) The 'generative' label in the business description may be partially aspirational marketing — the core engine is discriminative ML on mass-spec data rather than a true generative model in the LLM sense; (4) Clinical validation is still ongoing (Phase 1/2), so prospective companion Dx utility is not yet proven at registration level; (5) Small trial count (n=3) limits statistical power of biomarker stratification evidence so far.

*(merged duplicate company-ids: fd1881898c215386)*


### 2. Boundless Bio (BOLD) — composite 0.800

*exchange NASDAQ · country US · mkt cap $58M · ~2 yr since IPO · confidence 0.90*

**Axes:** A (proprietary data) 4/5 · B (compute) 3/5 · C (validation) 4/5 · D (mechanism) 5/5 · E (translation) 4/5

**Moat (mixed):** The primary moat is in the proprietary biological insight and clinical-grade ecDNA detection/profiling capability (data/knowledge), anchored by world-class founders (Mischel h=110, Bafna h=81). The computational tools (AmpliconArchitect lineage) are partially open-source but clinically integrated versions are proprietary. Moat is therefore mixed: data/knowledge/detection pipeline plus computational architecture, with data/biology insight being the stronger component.

Boundless Bio represents a high-conviction scientific platform anchored by two of the world's foremost ecDNA biologists: Paul Mischel (h=110, seminal Nature/Science ecDNA papers) and Vineet Bafna (h=81, creator of AmpliconArchitect). This is among the strongest scientific pedigrees in the dataset for a micro-cap company. The ecDNA mechanism is genuinely novel and disruptive — ecDNA-amplified tumors exhibit non-Mendelian oncogene inheritance, transcriptional hub formation, and accelerated drug resistance, making them a distinct and underserved oncology target class. The platform translates from large-scale ecDNA profiling of thousands of human tumors (Nature 2020 landmark study) into clinical-stage drug development (BBI-940, Phase 1/2) with biomarker-defined patient selection, consistent with the Acrivon pattern. Key risks: (1) AmpliconArchitect is open-source, limiting the computational moat; (2) no clinical efficacy data yet; (3) extremely small market cap (~$58M) reflects substantial development-stage risk; (4) the therapeutic modality targeting ecDNA (as opposed to detecting it) is mechanistically novel but clinically unproven. The substance verdict is substantive — this is not marketing vocabulary but a real platform with peer-reviewed, externally validated biological foundations and genuine mechanism innovation.

**Disconfirming:** Core computational tools (AmpliconArchitect) are open-source, weakening the architectural moat claim. Market cap of ~$58M suggests significant clinical and commercial risk. No Phase 2 readout yet; BBI-940 mechanism (targeting ecDNA-dependent transcriptional hubs via CHK1 inhibition or related) is unproven in the clinic. The ecDNA patient-selection biomarker for clinical trials is conceptually strong but prospective companion Dx validation is still ongoing. Some pedigree authors (e.g., Sylvia Richardson, Reginald Penner) appear tangential to the core ecDNA biology.

*(merged duplicate company-ids: be9404838ac3ae36)*


### 3. GRAIL, Inc. (GRAL) — composite 0.760

*exchange Nasdaq · country US · mkt cap $2,829M · ~2 yr since IPO · confidence 0.50*

**Axes:** A (proprietary data) 5/5 · B (compute) 3/5 · C (validation) 4/5 · D (mechanism) 1/5 · E (translation) 5/5

**Moat (data):** GRAIL's durable competitive advantage rests almost entirely on the scale and longitudinal depth of its proprietary methylation training datasets (CCGA, STRIVE, PATHFINDER) — not on a unique algorithmic architecture. The ML classifiers, while sophisticated, are built on conventional supervised learning; any competitor with comparable annotated cfDNA methylation data could replicate the approach. The multi-year, multi-site biobanking effort with biopsy-confirmed outcomes is the asset that is structurally hard to replicate.

GRAIL is the canonical large-scale proprietary-data moat in liquid biopsy — the Acrivon pattern maps clearly onto the DATA axis. The CCGA/STRIVE/PATHFINDER biobanking effort (tens of thousands to ~140,000 participants with longitudinally annotated, biopsy-confirmed outcomes) represents a dataset scale that is genuinely irreplaceable in the near term. Scientific pedigree is strong: GRAIL was co-founded with backing from Illumina and leading cancer genomics researchers; the CCGA study design reflects rigorous epidemiological and clinical collaboration. FDA Breakthrough Device Designation for Galleri corroborates the translational credibility. The compute layer (B=3) is real but not architecturally novel — the moat is unambiguously data. The primary investment risk is clinical: if the NHS-Galleri RCT (140,000-person prospective RCT, results expected ~2026) fails to demonstrate stage shift or mortality benefit, the screening paradigm faces an existential challenge. Sensitivity for early-stage cancer (~16-40% for stage I) is the key technical limitation — the platform excels at late-stage detection where screening value is lower. Reimbursement (no CMS coverage) remains the dominant commercial risk. No mechanism tag applies — the platform is pan-cancer and mechanism-agnostic, which is a feature for screening breadth but limits the D score.

**Disconfirming:** Key disconfirming risks: (1) Sensitivity for early-stage (stage I-II) cancers remains limited (~20-40% at high specificity), which is the clinically critical use case for screening; (2) The NHS-Galleri RCT primary endpoint (stage shift) results are not yet published — if the trial fails to show meaningful clinical benefit, the entire screening thesis is challenged; (3) No FDA PMA/510(k) clearance yet — Galleri is LDT-status, not FDA-cleared, limiting broad insurance coverage; (4) Medicare/CMS coverage is not established, creating reimbursement risk; (5) Competitors (Exact Sciences, Illumina's Grail spinout dynamics, Guardant, Personal Genome Diagnostics) are building competing methylation-based MCED tests; (6) The company has been loss-making with high cash burn, and its Illumina entanglement created significant regulatory and corporate governance uncertainty.


### 4. RECURSION PHARMACEUTICALS, INC. (RXRX) — composite 0.744

*exchange Nasdaq · country US · mkt cap $1,868M · ~5 yr since IPO · confidence 0.35*

**Axes:** A (proprietary data) 5/5 · B (compute) 4/5 · C (validation) 3/5 · D (mechanism) 3/5 · E (translation) 3/5

**Moat (data):** Recursion's core defensibility lies in its proprietary petabyte-scale cellular phenomics dataset and the associated wet-lab automation infrastructure generating it. The ML architectures (transformers, CNNs, GNNs) are state-of-the-art but replicable; the dataset is not. Competitors cannot easily replicate ~10 years of continuous high-throughput experimentation and the resulting phenotypic maps across thousands of genetic and chemical perturbations.

Recursion is the highest-profile 'industrialized drug discovery' platform company, co-founded by Chris Gibson and Dean Li (former Merck CSO, strong pedigree) and backed by significant institutional capital including a landmark NVIDIA partnership. The core thesis is a DATA moat: a proprietary petabyte-scale cellular phenomics corpus generated by ~2.5M experiments/week through heavily automated lab infrastructure, which no academic or smaller biotech can replicate easily. The LOWE foundation model trained on this corpus adds a genuine AI layer. However, the moat scores 'mixed' on substance because (a) clinical validation of the platform's predictive accuracy remains unproven — no program has generated positive Phase 2/3 efficacy data yet; (b) no companion diagnostic exists despite years of biomarker-adjacent language; and (c) external partnerships have not publicly produced validated leads. The mechanism portfolio is broad and opportunistic (LSD1, CDK, RAS/MAPK, FAP), consistent with a phenotypics-first approach but weakening any single mechanistic moat. Overall: the DATA ENGINE is real and large-scale; the INFERENCE layer is sophisticated and includes a genuine foundation model; VALIDATION is partial (wet-lab confirmed hits, clinical stage programs); TRANSLATION is early-stage. This is a platform company with genuine substance at the data/compute layer but meaningful clinical execution risk and no yet-demonstrated translational proof-of-concept.

**Disconfirming:** (1) Despite significant capital deployed and years of operation, Recursion has not yet demonstrated a clinical success directly attributable to the platform — all clinical programs are Phase 1/2 with no efficacy read-outs validating platform-derived predictions. (2) Several early partnerships (e.g., Bayer) were not renewed or disclosed as unsuccessful, raising questions about external validation of predictive accuracy. (3) The Exscientia acquisition added chemistry capabilities but also integration risk and cost. (4) No companion diagnostic or biomarker strategy is formally disclosed, limiting the 'precision medicine' narrative. (5) The phenomics-to-mechanism translation step remains a known bottleneck — high-content imaging hits do not always map to tractable drug targets, and the company has been criticized for generating large datasets without proportionate translational throughput.

*(merged duplicate company-ids: a4748b360106ad9a)*


### 5. Absci Corp (ABSI) — composite 0.704

*exchange Nasdaq · country US · mkt cap $1,845M · ~5 yr since IPO · confidence 0.50*

**Axes:** A (proprietary data) 4/5 · B (compute) 4/5 · C (validation) 4/5 · D (mechanism) 2/5 · E (translation) 3/5

**Moat (mixed):** Absci's moat is partially in proprietary sequence-function data generated by its cell-free high-throughput expression engine (data moat) and partially in the generative AI architecture trained on that data (architecture). The cell-free expression platform generating large-scale proprietary training data is the harder-to-replicate component; the generative model architecture itself is based on protein language model paradigms increasingly accessible in academia, but the proprietary training corpus and wet-lab integration create genuine differentiation.

Absci represents a genuine but early-stage 'Acrivon-pattern' company where the moat is a high-throughput cell-free protein expression engine generating proprietary sequence-function training data for generative AI antibody design. The scientific pedigree is credible: a 2023 Nature paper on zero-shot generative antibody design is a meaningful external validation signal, placing Absci among the small cohort of companies with peer-reviewed foundation-model-in-biology publications. The platform data engine (cell-free expression + deep mutational scanning at scale) is the primary moat, with the generative AI layer being high-capability but increasingly contested architecturally. Clinical translation is early-stage (Phase 1 only, no companion Dx), limiting E score. The mechanism anchoring is weak — pipeline assets address conventional indications (IBD, alopecia, IO) without a single deeply proprietary biological mechanism. Collaborations with MSK, Twist, and Owkin add external validation but also signal platform dependencies. The strongest disconfirming factor is competitive intensity in generative protein design: Generate Biomedicines, Profluent, and EvolutionaryScale (ESM3) are well-funded rivals with comparable or superior foundation models, making architectural moat durability questionable. Data moat durability depends on continued throughput leadership in cell-free expression. Overall: substantive AI-bio platform with real wet-lab validation, but not yet a fully closed precision-medicine loop (no companion Dx, no clinical POC).

**Disconfirming:** Key disconfirming evidence: (1) The Nature 2023 zero-shot antibody design paper, while impressive, showed functional but not necessarily best-in-class binders — generative AI for antibodies is an intensely competitive field (Dyno, Generate Biomedicines, Profluent, EvolutionaryScale all active); (2) ABS-101 and ABS-201 are early Phase 1 — no clinical proof-of-concept yet; (3) No companion diagnostic strategy disclosed, reducing the precision-medicine differentiation; (4) The cell-free expression system, while proprietary in scale, is not a fully unique modality — competitors use yeast display, phage display at comparable scale; (5) Partnerships with Twist, Owkin, Oracle suggest platform is not entirely self-contained and may require external data/compute infrastructure.


### 6. Schrodinger, Inc. (SDGR) — composite 0.568

*exchange Nasdaq · country US · mkt cap $1,260M · ~6 yr since IPO · confidence 0.55*

**Axes:** A (proprietary data) 3/5 · B (compute) 3/5 · C (validation) 3/5 · D (mechanism) 2/5 · E (translation) 3/5

**Moat (mixed):** Schrödinger's moat is split: (1) architectural — the OPLS force field and FEP+ workflow represent decades of physics-based parameterization that is hard to replicate quickly; (2) data — proprietary Drug Discovery SAR datasets and simulation outputs accumulated internally. Neither alone is a pure data moat in the Acrivon sense; the physics engine (architecture) may be more defensible than the datasets, but both contribute.

Schrödinger (SDGR) is the canonical physics-based computational drug discovery platform, founded 1990, with an extraordinary publication record (h-index 268, 620k citations). The moat is real but mixed: the OPLS force field and FEP+ workflow represent genuine proprietary parameterization developed over decades, and the Drug Discovery segment accumulates internal SAR data. However, the core scientific methods are published and the underlying structural/chemical databases are largely public, distinguishing it from a true closed-loop proprietary data engine like Acrivon. The ML/AI layer is meaningful but not a foundation-model-in-biology breakthrough. External validation via prospective FEP+ benchmarks and partner co-publications is credible but not as tight as a wet-lab validation loop. The Drug Discovery pipeline (SGR-1505 MALT1 inhibitor in Phase 1 being the lead) is promising but early, with no companion Dx strategy. Prestige recognitions are absent from disclosed data; the top-author list reflects field collaborators rather than internal leadership, slightly tempering confidence in a deep proprietary data engine. The strongest disconfirming signal is open-source/competitive commoditization of physics-based methods and AlphaFold's disruption of structure prediction. Schrödinger remains a high-quality platform but scores as 'mixed' substance — the physics engine is real, the data moat is partial, and the translational bridge is emerging rather than established.

**Disconfirming:** The core physics methods (FEP, MD, docking) are published and increasingly accessible via open-source tools (OpenMM, OpenFE, AutoDock-GPU) and competing commercial platforms (OpenEye, CCG, Cresset). AlphaFold2/3 and RoseTTAFold commoditize structure prediction, reducing one key barrier. Revenue growth has been slow and the software business faces pricing pressure. Drug Discovery pipeline is early with no Phase 2 readouts yet. The top-author pedigree list does not cleanly map to Schrödinger's internal scientific leadership (Josef Penninger, Stephen Lippard are not Schrödinger core staff), suggesting the publication corpus reflects collaborative/field work rather than a tightly controlled internal data engine. No FDA Breakthrough designations reported.

*(merged duplicate company-ids: c7611de8ed06e459)*


### 7. Sana Biotechnology, Inc. (SANA) — composite 0.402

*exchange Nasdaq · country US · mkt cap $952M · ~5 yr since IPO · confidence 0.35*

**Axes:** A (proprietary data) 3/5 · B (compute) 1/5 · C (validation) 3/5 · D (mechanism) 3/5 · E (translation) 3/5

**Moat (architecture):** Sana's moat is primarily architectural/biological: the HIP immune-evasion platform (licensed from Harvard, peer-reviewed), the fusosome in vivo delivery technology (proprietary lipid-envelope mechanism for cell-type-specific gene delivery), and CRISPR editing access. There is no disclosed large proprietary dataset engine. The moat depends on biological platform exclusivity and IP, not a data-generation flywheel.

Sana Biotechnology is a cell therapy platform company with two principal biological innovations: (1) HIP (hypoimmune) cell modification, originally developed in the Bhanu lab / Tobias Deuse / Sonja Schrepfer group at UCSF (published Nature Biomedical Engineering 2019) — providing allogeneic cell products with immune-evasion properties without systemic immunosuppression; and (2) fusosomes — proprietary engineered lipid-enveloped particles derived from viral fusion proteins that can deliver genetic cargo to specific cell types (e.g., CD8+ T cells) in vivo without ex vivo cell manipulation. Schrepfer (co-founder) has strong academic pedigree in transplant immunology; fusosome technology originated from internal R&D. The lead clinical asset UP421 (HIP-modified donor islets) is in Phase 1 for T1D in collaboration with Mayo Clinic. SG293/SG299 are in vivo fusosome programs targeting B-cell malignancies and autoimmune disease. DISCONFIRMING: No AI/ML data engine — this is a biological platform, not a data+compute moat. Core HIP IP is licensed (Harvard), not owned. Fusosome clinical translation unproven. No companion Dx. Pipeline breadth is modest relative to capital intensity. The pattern does NOT match the Acrivon archetype: there is no proprietary high-dimensional data-generation engine and no computational inference layer. Substance is real (peer-reviewed biology, genuine clinical programs) but the moat is biological/IP rather than data-computational.

**Disconfirming:** 1) HIP core IP is licensed from Harvard, not internally generated — dependency risk and limited architectural exclusivity. 2) Fusosome technology is early-stage with no clinical readouts yet for in vivo programs; NHP data is promising but limited. 3) No disclosed AI/ML computational layer — the 'platform' is biological engineering, not a data+compute engine in the Acrivon sense. 4) CRISPR editing relies on a Beam Therapeutics license, adding another external dependency. 5) Clinical pipeline is very early (Phase 1 / preclinical) with no efficacy data released. 6) The trial conditions listed (ALD, AGS, Alexander Disease) appear mismatched with the company's described focus areas, suggesting the clinical metadata may reflect historical or partner programs rather than Sana's core pipeline.


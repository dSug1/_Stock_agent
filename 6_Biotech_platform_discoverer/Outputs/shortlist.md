# Acrivon-Pattern Listed-Biotech Screener — Shortlist

*Run `2026-06-29T02:27:13+00:00` · generated 2026-06-29 · market-cap band $50,000,000–$3,000,000,000 · 6 scored companies (of 589 in store).*

Composite is the code-computed weighted rubric score (auditable; §10). `rank_score = composite` (lifecycle age-weighting OFF — `stage5.lifecycle.enabled`).

## Ranked shortlist

| # | Ticker | Company | Composite | Rank | Conf | Moat | Substance | Mechanism |
|--:|:--|:--|--:|--:|--:|:--|:--|:--|
| 1 | **ACRV** | Acrivon Therapeutics, Inc. | 0.904 | 0.904 | 0.50 | data | substantive | adp_ribosylation_parp_tankyrase |
| 2 | **GRAL** | GRAIL, Inc. | 0.760 | 0.760 | 0.50 | data | substantive | — |
| 3 | **ABSI** | Absci Corp | 0.704 | 0.704 | 0.50 | mixed | substantive | tnf_il_axis, treg_modulation |
| 4 | **RXRX** | RECURSION PHARMACEUTICALS, INC. | 0.656 | 0.656 | 0.35 | mixed | mixed | lsd1_demethylase, menin_kmt2a, adp_ribosylation_parp_tankyrase |
| 5 | **SDGR** | Schrodinger | 0.462 | 0.462 | 0.15 | architecture | mixed | — |
| 6 | **SANA** | Sana Biotechnology, Inc. | 0.402 | 0.402 | 0.35 | architecture | mixed | b_cell_depletion_degrader, tolerogenic_antigen_specific |

## Memos

### 1. Acrivon Therapeutics, Inc. (ACRV) — composite 0.904

*exchange Nasdaq · country US · mkt cap $72M · confidence 0.50*

**Axes:** A (proprietary data) 5/5 · B (compute) 3/5 · C (validation) 5/5 · D (mechanism) 4/5 · E (translation) 5/5

**Moat (data):** The irreplaceable asset is the ~120k-phosphosite proprietary dataset and the wet-lab InViKA loop that continuously enriches it. The computational architecture (ESM-2 ensemble) is disclosed and replicable; competitors could run similar models on public data but cannot replicate the phosphoproteomic training corpus without equivalent years of mass-spec experimentation.

Acrivon is the calibration anchor for this scoring system and scores at ceiling on data (A=5), validation (C=5), and translation (E=5). The AP3 platform exemplifies the 'Acrivon pattern' by design: a massive internally generated phosphoproteomic dataset (~120k phosphosites) that cannot be replicated from public sources, a wet-lab InViKA validation loop providing continuous fresh ground truth (not just held-out metrics), external benchmarking against CPTAC and FLT4, and a drug-specific OncoSignature companion Dx co-developed with each clinical asset. Computational layer (B=3) is deliberately capped: the ESM-2 ensemble is capable but architecturally commoditizable — the moat is unambiguously in the data. Mechanism score (D=4) reflects genuine biological depth (CHK1/CHK2 and WEE1/PKMYT1 checkpoint kinases in replication-stressed tumors) but the controlled vocabulary does not contain a dedicated 'replication stress / DNA damage checkpoint' entry; adp_ribosylation_parp_tankyrase is the closest in-scope neighbor given synthetic lethality context. Scientific pedigree: founded with roots in phosphoproteomics mass spectrometry; platform concept aligns with academic leaders in systems-level kinase signaling. Key risk: ACR-368 is a CHK1/2 inhibitor class with historical toxicity issues in unselected patients — the entire investment thesis hinges on OncoSignature successfully enriching responders, which is currently being tested prospectively. Low market cap (~$72M) reflects binary clinical risk, not platform quality doubt.

**Disconfirming:** Strongest disconfirming evidence: (1) ESM-2 model architecture is public and replicable — if a competitor generates comparable phosphoproteomic data, the inference layer provides no durable barrier; (2) 'generative' label in business description is arguably marketing — the platform is better characterized as a deep-learning predictive model over proprietary proteomics, not a true generative model in the LLM/diffusion sense; (3) market cap (~$72M) suggests market skepticism about near-term commercial translation despite strong scientific design; (4) CHK1/CHK2 inhibitor class (ACR-368, formerly prexasertib-class) has a well-documented history of toxicity and prior clinical failures in unselected populations — biomarker selection is the key differentiator but clinical validation is still maturing.

*(merged duplicate company-ids: fd1881898c215386)*


### 2. GRAIL, Inc. (GRAL) — composite 0.760

*exchange Nasdaq · country US · mkt cap $2,829M · confidence 0.50*

**Axes:** A (proprietary data) 5/5 · B (compute) 3/5 · C (validation) 4/5 · D (mechanism) 1/5 · E (translation) 5/5

**Moat (data):** GRAIL's durable competitive advantage rests almost entirely on the scale and longitudinal depth of its proprietary methylation training datasets (CCGA, STRIVE, PATHFINDER) — not on a unique algorithmic architecture. The ML classifiers, while sophisticated, are built on conventional supervised learning; any competitor with comparable annotated cfDNA methylation data could replicate the approach. The multi-year, multi-site biobanking effort with biopsy-confirmed outcomes is the asset that is structurally hard to replicate.

GRAIL is the canonical large-scale proprietary-data moat in liquid biopsy — the Acrivon pattern maps clearly onto the DATA axis. The CCGA/STRIVE/PATHFINDER biobanking effort (tens of thousands to ~140,000 participants with longitudinally annotated, biopsy-confirmed outcomes) represents a dataset scale that is genuinely irreplaceable in the near term. Scientific pedigree is strong: GRAIL was co-founded with backing from Illumina and leading cancer genomics researchers; the CCGA study design reflects rigorous epidemiological and clinical collaboration. FDA Breakthrough Device Designation for Galleri corroborates the translational credibility. The compute layer (B=3) is real but not architecturally novel — the moat is unambiguously data. The primary investment risk is clinical: if the NHS-Galleri RCT (140,000-person prospective RCT, results expected ~2026) fails to demonstrate stage shift or mortality benefit, the screening paradigm faces an existential challenge. Sensitivity for early-stage cancer (~16-40% for stage I) is the key technical limitation — the platform excels at late-stage detection where screening value is lower. Reimbursement (no CMS coverage) remains the dominant commercial risk. No mechanism tag applies — the platform is pan-cancer and mechanism-agnostic, which is a feature for screening breadth but limits the D score.

**Disconfirming:** Key disconfirming risks: (1) Sensitivity for early-stage (stage I-II) cancers remains limited (~20-40% at high specificity), which is the clinically critical use case for screening; (2) The NHS-Galleri RCT primary endpoint (stage shift) results are not yet published — if the trial fails to show meaningful clinical benefit, the entire screening thesis is challenged; (3) No FDA PMA/510(k) clearance yet — Galleri is LDT-status, not FDA-cleared, limiting broad insurance coverage; (4) Medicare/CMS coverage is not established, creating reimbursement risk; (5) Competitors (Exact Sciences, Illumina's Grail spinout dynamics, Guardant, Personal Genome Diagnostics) are building competing methylation-based MCED tests; (6) The company has been loss-making with high cash burn, and its Illumina entanglement created significant regulatory and corporate governance uncertainty.


### 3. Absci Corp (ABSI) — composite 0.704

*exchange Nasdaq · country US · mkt cap $1,845M · confidence 0.50*

**Axes:** A (proprietary data) 4/5 · B (compute) 4/5 · C (validation) 4/5 · D (mechanism) 2/5 · E (translation) 3/5

**Moat (mixed):** Absci's moat is partially in proprietary sequence-function data generated by its cell-free high-throughput expression engine (data moat) and partially in the generative AI architecture trained on that data (architecture). The cell-free expression platform generating large-scale proprietary training data is the harder-to-replicate component; the generative model architecture itself is based on protein language model paradigms increasingly accessible in academia, but the proprietary training corpus and wet-lab integration create genuine differentiation.

Absci represents a genuine but early-stage 'Acrivon-pattern' company where the moat is a high-throughput cell-free protein expression engine generating proprietary sequence-function training data for generative AI antibody design. The scientific pedigree is credible: a 2023 Nature paper on zero-shot generative antibody design is a meaningful external validation signal, placing Absci among the small cohort of companies with peer-reviewed foundation-model-in-biology publications. The platform data engine (cell-free expression + deep mutational scanning at scale) is the primary moat, with the generative AI layer being high-capability but increasingly contested architecturally. Clinical translation is early-stage (Phase 1 only, no companion Dx), limiting E score. The mechanism anchoring is weak — pipeline assets address conventional indications (IBD, alopecia, IO) without a single deeply proprietary biological mechanism. Collaborations with MSK, Twist, and Owkin add external validation but also signal platform dependencies. The strongest disconfirming factor is competitive intensity in generative protein design: Generate Biomedicines, Profluent, and EvolutionaryScale (ESM3) are well-funded rivals with comparable or superior foundation models, making architectural moat durability questionable. Data moat durability depends on continued throughput leadership in cell-free expression. Overall: substantive AI-bio platform with real wet-lab validation, but not yet a fully closed precision-medicine loop (no companion Dx, no clinical POC).

**Disconfirming:** Key disconfirming evidence: (1) The Nature 2023 zero-shot antibody design paper, while impressive, showed functional but not necessarily best-in-class binders — generative AI for antibodies is an intensely competitive field (Dyno, Generate Biomedicines, Profluent, EvolutionaryScale all active); (2) ABS-101 and ABS-201 are early Phase 1 — no clinical proof-of-concept yet; (3) No companion diagnostic strategy disclosed, reducing the precision-medicine differentiation; (4) The cell-free expression system, while proprietary in scale, is not a fully unique modality — competitors use yeast display, phage display at comparable scale; (5) Partnerships with Twist, Owkin, Oracle suggest platform is not entirely self-contained and may require external data/compute infrastructure.


### 4. RECURSION PHARMACEUTICALS, INC. (RXRX) — composite 0.656

*exchange Nasdaq · country US · mkt cap $1,868M · confidence 0.35*

**Axes:** A (proprietary data) 4/5 · B (compute) 4/5 · C (validation) 3/5 · D (mechanism) 3/5 · E (translation) 2/5

**Moat (mixed):** The primary moat is the proprietary phenomics dataset (>50M images from controlled perturbation experiments in an automated wet lab) — this is data-driven and difficult to replicate quickly. However, Recursion has invested heavily in foundation model architecture (BioHive-1, LOWE, phenomics FM), making this a mixed moat. The data edge is stronger than the architectural edge since Cell Painting is a public assay, but the scale and systematic coverage of perturbations is proprietary.

Recursion represents one of the most capital-intensive phenomics-AI platforms in biotech, with genuine scale (>50M proprietary images, BioHive-1 supercomputer, automated wet lab) and multiple foundation models (phenomics FM, LOWE chemical FM via Valence). The data engine scores high (A=4) because the systematic perturbation coverage and automation infrastructure are real and costly to replicate, even though Cell Painting itself is public. The compute engine (B=4) is credible given disclosed foundation model architecture and BioHive-1, though architectural moat is limited since transformers/graph NNs are commoditizing. Validation (C=3) is the key weakness: prospective wet-lab loop publications akin to Acrivon's InViKA are absent; validation largely rests on IND progression and partner co-validation. No companion Dx (E=2) further limits the translational precision score. The platform is phenotype-first and mechanism-agnostic, which is scientifically coherent but makes mechanism anchoring (D=3) secondary. Chris Gibson (CEO/co-founder) has built a recognized AI-bio company and attracted Nvidia investment and top-tier pharma partnerships, providing pedigree signal, but no Nobel/NAS-level scientific founder is at the core. The dominant disconfirming risk is that the platform's clinical output (several Phase 1/2 assets, no Phase 3 efficacy readout) has not yet demonstrated that AI-driven phenomics translates to superior clinical success rates versus conventional drug discovery.

**Disconfirming:** 1) Cell Painting is an open, community-standard assay — the modality itself is not proprietary; competitors (Broad, Vividion, Phenomic AI) use similar approaches. 2) Published peer-reviewed validation of AI-to-hit predictions via prospective wet-lab loops is sparse relative to the marketing claims of 'industrialized drug discovery.' 3) Pipeline attrition: most assets are still Phase 1/2 with no clinical proof-of-concept readout tying AI origin to efficacy. 4) Major partnerships (Sanofi $150M, Bayer, Roche) provide business validation but have not yet produced disclosed clinical successes traceable to the platform. 5) No companion Dx strategy disclosed, weakening the translational precision medicine narrative.

*(merged duplicate company-ids: a4748b360106ad9a)*


### 5. Schrodinger (SDGR) — composite 0.462

*exchange NASDAQ · country US · mkt cap $1,260M · confidence 0.15*

**Axes:** A (proprietary data) 3/5 · B (compute) 3/5 · C (validation) 4/5 · D (mechanism) 2/5 · E (translation) 3/5

**Moat (architecture):** Schrödinger's moat resides primarily in its proprietary physics-based simulation architecture (FEP+, force fields, integrated workflow) and the expertise/validation accumulated over 30+ years of platform development, rather than in a proprietary experimental data-generation engine. The computational methods are scientifically differentiated but the underlying molecular data is largely public. Competitors (OpenEye/Cadence, Atomwise, Relay) are narrowing the gap with ML-native approaches.

Schrödinger (est. 1990) is a pioneering computational chemistry platform company led by co-founder Ramy Farid (Ph.D. Columbia) and guided by Nobel laureate David Baker-adjacent scientific networks; the company's scientific pedigree in physics-based simulation is among the strongest in the industry. The platform's FEP+ free-energy perturbation engine represents genuine scientific depth validated across D3R Grand Challenges and prospective pharma collaborations. HOWEVER, the Acrivon pattern fit is partial: the moat is architectural/methodological rather than rooted in a proprietary experimental data-generation loop. There is no high-dimensional closed-loop wet-lab engine producing unique training signal analogous to Acrivon's phosphoproteomics mass-spec dataset. The ML augmentation layers (generative design, ADMET ML) use standard architectures on largely public data. Translational assets are real (SGR-1505 Phase 1/2, Fast Track Designation) but lack companion Dx biomarker strategy. The strongest disconfirming evidence is that the physics simulation stack, while sophisticated, is reproducible in academic/open-source settings and the data feeding it is not proprietary. Platform value is real but derives from integration depth, UI/workflow, and 30-year parameterization effort — a meaningful but gradually eroding architectural moat.

**Disconfirming:** (1) Core training/simulation data (PDB structures, known ligand activity) is public — no closed-loop proprietary wet-lab data engine. (2) ML layers use standard architectures trainable on public data; no disclosed foundation model with unique biological data moat. (3) FEP+ is scientifically rigorous but well-described in literature, enabling academic and commercial replication (OpenMM, GROMACS, etc.). (4) Drug discovery pipeline is early-stage and small relative to the software business; platform validation via internal pipeline is indirect. (5) Revenue concentration and software renewal risk from large pharma partners who could internalize or switch platforms.


### 6. Sana Biotechnology, Inc. (SANA) — composite 0.402

*exchange Nasdaq · country US · mkt cap $952M · confidence 0.35*

**Axes:** A (proprietary data) 3/5 · B (compute) 1/5 · C (validation) 3/5 · D (mechanism) 3/5 · E (translation) 3/5

**Moat (architecture):** Sana's moat is primarily architectural/biological: the HIP immune-evasion platform (licensed from Harvard, peer-reviewed), the fusosome in vivo delivery technology (proprietary lipid-envelope mechanism for cell-type-specific gene delivery), and CRISPR editing access. There is no disclosed large proprietary dataset engine. The moat depends on biological platform exclusivity and IP, not a data-generation flywheel.

Sana Biotechnology is a cell therapy platform company with two principal biological innovations: (1) HIP (hypoimmune) cell modification, originally developed in the Bhanu lab / Tobias Deuse / Sonja Schrepfer group at UCSF (published Nature Biomedical Engineering 2019) — providing allogeneic cell products with immune-evasion properties without systemic immunosuppression; and (2) fusosomes — proprietary engineered lipid-enveloped particles derived from viral fusion proteins that can deliver genetic cargo to specific cell types (e.g., CD8+ T cells) in vivo without ex vivo cell manipulation. Schrepfer (co-founder) has strong academic pedigree in transplant immunology; fusosome technology originated from internal R&D. The lead clinical asset UP421 (HIP-modified donor islets) is in Phase 1 for T1D in collaboration with Mayo Clinic. SG293/SG299 are in vivo fusosome programs targeting B-cell malignancies and autoimmune disease. DISCONFIRMING: No AI/ML data engine — this is a biological platform, not a data+compute moat. Core HIP IP is licensed (Harvard), not owned. Fusosome clinical translation unproven. No companion Dx. Pipeline breadth is modest relative to capital intensity. The pattern does NOT match the Acrivon archetype: there is no proprietary high-dimensional data-generation engine and no computational inference layer. Substance is real (peer-reviewed biology, genuine clinical programs) but the moat is biological/IP rather than data-computational.

**Disconfirming:** 1) HIP core IP is licensed from Harvard, not internally generated — dependency risk and limited architectural exclusivity. 2) Fusosome technology is early-stage with no clinical readouts yet for in vivo programs; NHP data is promising but limited. 3) No disclosed AI/ML computational layer — the 'platform' is biological engineering, not a data+compute engine in the Acrivon sense. 4) CRISPR editing relies on a Beam Therapeutics license, adding another external dependency. 5) Clinical pipeline is very early (Phase 1 / preclinical) with no efficacy data released. 6) The trial conditions listed (ALD, AGS, Alexander Disease) appear mismatched with the company's described focus areas, suggesting the clinical metadata may reflect historical or partner programs rather than Sana's core pipeline.


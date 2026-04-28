const QUARTER = "2025Q4";
const CLI_COMMAND = "python scripts/6_score.py --selection-from-html Outputs/final_ranking_2025Q4.html -v";
const SERVE_COMMAND = "python scripts/6_serve_report.py --quarter 2025Q4";
// D47 / D50 — modifier configuration + per-ticker model factors. Driven
// by the hamburger panel; recomputes table cells on every slider tick.
const MODIFIER_DATA = {"components": ["crowding", "financing", "dilution", "insider", "mgmt", "acquisition", "moat", "failures", "concentration", "big_pharma_validation", "catalyst_density", "tech_uniqueness", "dilution_overhang", "cash_floor"], "labels": {"crowding": "Competitive crowding", "financing": "Financing risk (runway)", "dilution": "Recent dilution", "insider": "Insider conviction", "mgmt": "Mgmt track record", "acquisition": "Acquisition optionality", "moat": "Moat durability", "failures": "Recent operational failure", "concentration": "rNPV concentration", "big_pharma_validation": "Big-pharma validation", "catalyst_density": "Catalyst density (12mo)", "tech_uniqueness": "Tech uniqueness", "dilution_overhang": "Dilution overhang", "cash_floor": "Cash floor / asymm setup"}, "weights": {"crowding": 0.0, "financing": 0.0, "dilution": 0.0, "insider": 0.0, "mgmt": 0.0, "acquisition": 0.0, "moat": 0.0, "failures": 0.0, "concentration": 0.0, "big_pharma_validation": 0.0, "catalyst_density": 0.0, "tech_uniqueness": 0.0, "dilution_overhang": 0.0, "cash_floor": 0.0}, "weights_ui": {"min": 0.0, "max": 2.0, "step": 0.05, "default": 1.0}, "bounds": {"min": 0.5, "max": 1.5}, "ticker_factors": {"ABCL": {"crowding": 0.9, "financing": 1.0, "dilution": 1.0, "insider": 1.1, "mgmt": 1.05, "acquisition": 1.0, "moat": 1.0, "failures": 1.0, "concentration": 0.95, "big_pharma_validation": 1.05, "catalyst_density": 1.1, "tech_uniqueness": 1.0, "dilution_overhang": 1.0, "cash_floor": 1.0}, "ABEO": {"crowding": 0.95, "financing": 1.0, "dilution": 1.0, "insider": 1.0, "mgmt": 0.9, "acquisition": 1.05, "moat": 1.0, "failures": 1.0, "concentration": 0.85, "big_pharma_validation": 1.0, "catalyst_density": 1.0, "tech_uniqueness": 1.05, "dilution_overhang": 1.0, "cash_floor": 1.0}, "ABOS": {"crowding": 0.9, "financing": 0.95, "dilution": 0.93, "insider": 1.1, "mgmt": 1.05, "acquisition": 1.05, "moat": 1.0, "failures": 1.0, "concentration": 0.85, "big_pharma_validation": 1.0, "catalyst_density": 1.0, "tech_uniqueness": 1.05, "dilution_overhang": 1.0, "cash_floor": 1.0}, "ABSI": {"crowding": 0.8, "financing": 1.0, "dilution": 1.0, "insider": 1.1, "mgmt": 1.0, "acquisition": 1.0, "moat": 1.0, "failures": 1.0, "concentration": 0.95, "big_pharma_validation": 1.1, "catalyst_density": 1.05, "tech_uniqueness": 1.05, "dilution_overhang": 0.97, "cash_floor": 1.0}, "ABUS": {"crowding": 1.0, "financing": 1.0, "dilution": 1.0, "insider": 1.0, "mgmt": 1.0, "acquisition": 1.0, "moat": 1.05, "failures": 1.0, "concentration": 1.0, "big_pharma_validation": 1.0, "catalyst_density": 1.0, "tech_uniqueness": 1.05, "dilution_overhang": 1.0, "cash_floor": 1.0}, "ACET": {"crowding": 0.95, "financing": 0.7, "dilution": 0.97, "insider": 1.1, "mgmt": 1.0, "acquisition": 1.05, "moat": 1.0, "failures": 1.0, "concentration": 0.95, "big_pharma_validation": 1.0, "catalyst_density": 1.0, "tech_uniqueness": 1.05, "dilution_overhang": 0.88, "cash_floor": 1.0}, "ACIU": {"crowding": 0.9, "financing": 0.95, "dilution": 1.0, "insider": 1.0, "mgmt": 1.0, "acquisition": 1.05, "moat": 1.0, "failures": 1.0, "concentration": 0.95, "big_pharma_validation": 1.1, "catalyst_density": 1.0, "tech_uniqueness": 1.05, "dilution_overhang": 0.97, "cash_floor": 1.0}, "ACRV": {"crowding": 0.9, "financing": 1.0, "dilution": 1.0, "insider": 1.1, "mgmt": 0.9, "acquisition": 1.0, "moat": 1.0, "failures": 1.0, "concentration": 0.95, "big_pharma_validation": 1.05, "catalyst_density": 1.1, "tech_uniqueness": 1.0, "dilution_overhang": 0.88, "cash_floor": 1.08}, "ADCT": {"crowding": 0.9, "financing": 1.0, "dilution": 0.97, "insider": 1.0, "mgmt": 1.0, "acquisition": 1.05, "moat": 0.95, "failures": 1.0, "concentration": 0.95, "big_pharma_validation": 1.0, "catalyst_density": 1.1, "tech_uniqueness": 1.0, "dilution_overhang": 0.97, "cash_floor": 1.0}, "ADMA": {"crowding": 0.9, "financing": 1.0, "dilution": 1.0, "insider": 1.1, "mgmt": 0.9, "acquisition": 1.0, "moat": 1.0, "failures": 1.0, "concentration": 0.85, "big_pharma_validation": 1.0, "catalyst_density": 1.0, "tech_uniqueness": 1.0, "dilution_overhang": 1.0, "cash_floor": 1.0}, "ADPT": {"crowding": 0.95, "financing": 1.0, "dilution": 1.0, "insider": 1.0, "mgmt": 1.0, "acquisition": 1.05, "moat": 1.05, "failures": 1.0, "concentration": 0.95, "big_pharma_validation": 1.1, "catalyst_density": 1.05, "tech_uniqueness": 1.05, "dilution_overhang": 1.0, "cash_floor": 1.0}, "AGIO": {"crowding": 0.9, "financing": 1.0, "dilution": 1.0, "insider": 1.0, "mgmt": 1.0, "acquisition": 1.05, "moat": 1.05, "failures": 0.9, "concentration": 0.95, "big_pharma_validation": 1.0, "catalyst_density": 1.1, "tech_uniqueness": 1.05, "dilution_overhang": 1.0, "cash_floor": 1.03}, "AKBA": {"crowding": 0.95, "financing": 1.0, "dilution": 1.0, "insider": 1.1, "mgmt": 0.9, "acquisition": 1.0, "moat": 0.95, "failures": 1.0, "concentration": 0.95, "big_pharma_validation": 1.0, "catalyst_density": 1.1, "tech_uniqueness": 1.0, "dilution_overhang": 0.97, "cash_floor": 1.0}, "ALEC": {"crowding": 0.95, "financing": 0.95, "dilution": 0.97, "insider": 1.0, "mgmt": 0.9, "acquisition": 1.0, "moat": 0.95, "failures": 0.9, "concentration": 0.85, "big_pharma_validation": 1.05, "catalyst_density": 1.0, "tech_uniqueness": 1.0, "dilution_overhang": 0.88, "cash_floor": 1.03}, "ALMS": {"crowding": 0.9, "financing": 1.0, "dilution": 0.97, "insider": 1.1, "mgmt": 1.05, "acquisition": 1.05, "moat": 1.0, "failures": 1.0, "concentration": 0.85, "big_pharma_validation": 1.0, "catalyst_density": 1.1, "tech_uniqueness": 1.0, "dilution_overhang": 1.0, "cash_floor": 1.0}, "ANAB": {"crowding": 0.95, "financing": 1.0, "dilution": 1.0, "insider": 1.0, "mgmt": 1.0, "acquisition": 1.05, "moat": 1.0, "failures": 0.9, "concentration": 0.95, "big_pharma_validation": 1.05, "catalyst_density": 1.05, "tech_uniqueness": 1.0, "dilution_overhang": 1.0, "cash_floor": 1.0}, "ANNX": {"crowding": 0.9, "financing": 0.85, "dilution": 0.97, "insider": 1.1, "mgmt": 0.9, "acquisition": 1.05, "moat": 1.0, "failures": 1.0, "concentration": 0.95, "big_pharma_validation": 1.0, "catalyst_density": 1.1, "tech_uniqueness": 1.05, "dilution_overhang": 1.0, "cash_floor": 1.0}, "ANRO": {"crowding": 0.9, "financing": 1.0, "dilution": 0.93, "insider": 1.0, "mgmt": 0.9, "acquisition": 1.0, "moat": 0.95, "failures": 0.9, "concentration": 0.85, "big_pharma_validation": 1.0, "catalyst_density": 1.05, "tech_uniqueness": 1.0, "dilution_overhang": 1.0, "cash_floor": 1.0}, "ARDX": {"crowding": 0.9, "financing": 1.0, "dilution": 1.0, "insider": 1.1, "mgmt": 1.0, "acquisition": 1.05, "moat": 1.05, "failures": 1.0, "concentration": 0.85, "big_pharma_validation": 1.0, "catalyst_density": 0.95, "tech_uniqueness": 1.05, "dilution_overhang": 1.0, "cash_floor": 1.0}, "ARQT": {"crowding": 0.9, "financing": 1.0, "dilution": 0.93, "insider": 1.0, "mgmt": 1.05, "acquisition": 1.05, "moat": 1.0, "failures": 1.0, "concentration": 0.85, "big_pharma_validation": 1.05, "catalyst_density": 1.1, "tech_uniqueness": 1.0, "dilution_overhang": 1.0, "cash_floor": 1.0}, "ARTV": {"crowding": 0.95, "financing": 0.95, "dilution": 1.0, "insider": 1.0, "mgmt": 1.0, "acquisition": 1.0, "moat": 0.95, "failures": 1.0, "concentration": 0.85, "big_pharma_validation": 1.05, "catalyst_density": 0.95, "tech_uniqueness": 1.0, "dilution_overhang": 1.0, "cash_floor": 1.0}, "ARVN": {"crowding": 0.9, "financing": 1.0, "dilution": 1.0, "insider": 1.1, "mgmt": 0.9, "acquisition": 1.0, "moat": 1.0, "failures": 1.0, "concentration": 1.0, "big_pharma_validation": 1.1, "catalyst_density": 1.1, "tech_uniqueness": 1.05, "dilution_overhang": 1.0, "cash_floor": 1.03}, "AUPH": {"crowding": 0.9, "financing": 1.0, "dilution": 1.0, "insider": 1.1, "mgmt": 1.05, "acquisition": 1.05, "moat": 1.0, "failures": 1.0, "concentration": 0.85, "big_pharma_validation": 1.0, "catalyst_density": 0.95, "tech_uniqueness": 1.0, "dilution_overhang": 1.0, "cash_floor": 1.0}, "AVBP": {"crowding": 0.9, "financing": 1.0, "dilution": 1.0, "insider": 1.0, "mgmt": 1.0, "acquisition": 1.05, "moat": 1.0, "failures": 1.0, "concentration": 0.95, "big_pharma_validation": 1.0, "catalyst_density": 1.0, "tech_uniqueness": 1.0, "dilution_overhang": 1.0, "cash_floor": 1.0}, "AVTX": {"crowding": 0.8, "financing": 1.0, "dilution": 0.97, "insider": 1.0, "mgmt": 1.0, "acquisition": 1.0, "moat": 0.95, "failures": 1.0, "concentration": 0.85, "big_pharma_validation": 1.0, "catalyst_density": 1.0, "tech_uniqueness": 1.0, "dilution_overhang": 0.88, "cash_floor": 1.0}, "AVXL": {"crowding": 0.9, "financing": 1.0, "dilution": 1.0, "insider": 1.0, "mgmt": 0.9, "acquisition": 1.0, "moat": 0.95, "failures": 1.0, "concentration": 0.95, "big_pharma_validation": 1.0, "catalyst_density": 1.0, "tech_uniqueness": 1.0, "dilution_overhang": 0.97, "cash_floor": 1.0}, "BBOT": {"crowding": 0.95, "financing": 1.0, "dilution": 1.0, "insider": 1.0, "mgmt": 0.9, "acquisition": 1.05, "moat": 1.0, "failures": 1.0, "concentration": 1.0, "big_pharma_validation": 1.0, "catalyst_density": 1.1, "tech_uniqueness": 1.05, "dilution_overhang": 1.0, "cash_floor": 1.0}, "BCAX": {"crowding": 0.9, "financing": 1.0, "dilution": 0.97, "insider": 1.1, "mgmt": 1.05, "acquisition": 1.05, "moat": 1.0, "failures": 1.0, "concentration": 0.85, "big_pharma_validation": 1.05, "catalyst_density": 1.05, "tech_uniqueness": 1.05, "dilution_overhang": 1.0, "cash_floor": 1.0}, "BCYC": {"crowding": 0.9, "financing": 1.0, "dilution": 1.0, "insider": 1.0, "mgmt": 0.9, "acquisition": 1.0, "moat": 0.95, "failures": 0.9, "concentration": 0.85, "big_pharma_validation": 1.05, "catalyst_density": 1.0, "tech_uniqueness": 1.0, "dilution_overhang": 0.88, "cash_floor": 1.15}, "BEAM": {"crowding": 0.95, "financing": 1.0, "dilution": 0.97, "insider": 1.0, "mgmt": 1.05, "acquisition": 1.05, "moat": 1.05, "failures": 1.0, "concentration": 0.95, "big_pharma_validation": 1.1, "catalyst_density": 1.1, "tech_uniqueness": 1.05, "dilution_overhang": 1.0, "cash_floor": 1.0}, "BHVN": {"crowding": 0.8, "financing": 1.0, "dilution": 0.93, "insider": 1.1, "mgmt": 0.9, "acquisition": 1.05, "moat": 1.0, "failures": 0.9, "concentration": 1.0, "big_pharma_validation": 1.0, "catalyst_density": 1.1, "tech_uniqueness": 1.05, "dilution_overhang": 1.0, "cash_floor": 1.0}, "BNTC": {"crowding": 1.0, "financing": 1.0, "dilution": 0.93, "insider": 1.1, "mgmt": 1.05, "acquisition": 1.05, "moat": 1.0, "failures": 1.0, "concentration": 0.85, "big_pharma_validation": 1.0, "catalyst_density": 1.0, "tech_uniqueness": 1.05, "dilution_overhang": 0.97, "cash_floor": 1.0}, "CAMP": {"crowding": 1.0, "financing": 1.0, "dilution": 0.93, "insider": 1.0, "mgmt": 1.0, "acquisition": 1.0, "moat": 1.0, "failures": 0.9, "concentration": 1.0, "big_pharma_validation": 1.05, "catalyst_density": 1.0, "tech_uniqueness": 1.05, "dilution_overhang": 0.88, "cash_floor": 1.0}, "CBIO": {"crowding": 0.9, "financing": 1.0, "dilution": 0.97, "insider": 1.1, "mgmt": 1.0, "acquisition": 1.0, "moat": 0.95, "failures": 0.9, "concentration": 0.85, "big_pharma_validation": 1.0, "catalyst_density": 1.1, "tech_uniqueness": 0.95, "dilution_overhang": 1.0, "cash_floor": 1.0}, "CCCC": {"crowding": 0.9, "financing": 1.0, "dilution": 0.97, "insider": 1.0, "mgmt": 1.0, "acquisition": 1.05, "moat": 1.0, "failures": 0.9, "concentration": 0.95, "big_pharma_validation": 1.1, "catalyst_density": 1.0, "tech_uniqueness": 1.0, "dilution_overhang": 0.93, "cash_floor": 1.03}, "CGEM": {"crowding": 0.9, "financing": 1.0, "dilution": 1.0, "insider": 1.0, "mgmt": 1.0, "acquisition": 1.05, "moat": 0.95, "failures": 1.0, "concentration": 0.95, "big_pharma_validation": 1.0, "catalyst_density": 1.1, "tech_uniqueness": 1.0, "dilution_overhang": 1.0, "cash_floor": 1.0}, "CLDX": {"crowding": 0.9, "financing": 1.0, "dilution": 0.97, "insider": 1.0, "mgmt": 1.05, "acquisition": 1.05, "moat": 1.05, "failures": 1.0, "concentration": 0.85, "big_pharma_validation": 1.0, "catalyst_density": 1.05, "tech_uniqueness": 1.05, "dilution_overhang": 1.0, "cash_floor": 1.0}, "CMPX": {"crowding": 0.95, "financing": 1.0, "dilution": 0.97, "insider": 1.0, "mgmt": 0.9, "acquisition": 1.0, "moat": 0.95, "failures": 0.9, "concentration": 0.95, "big_pharma_validation": 1.0, "catalyst_density": 1.05, "tech_uniqueness": 1.0, "dilution_overhang": 0.88, "cash_floor": 1.03}, "CNTX": {"crowding": 0.95, "financing": 1.0, "dilution": 1.0, "insider": 1.0, "mgmt": 1.0, "acquisition": 1.0, "moat": 0.95, "failures": 1.0, "concentration": 0.95, "big_pharma_validation": 1.0, "catalyst_density": 1.05, "tech_uniqueness": 1.0, "dilution_overhang": 0.97, "cash_floor": 1.0}, "CPRX": {"crowding": 0.95, "financing": 1.0, "dilution": 1.0, "insider": 1.0, "mgmt": 1.05, "acquisition": 1.0, "moat": 1.0, "failures": 1.0, "concentration": 0.95, "big_pharma_validation": 1.0, "catalyst_density": 0.95, "tech_uniqueness": 1.0, "dilution_overhang": 1.0, "cash_floor": 1.0}, "CRBP": {"crowding": 0.95, "financing": 1.0, "dilution": 0.93, "insider": 1.0, "mgmt": 1.0, "acquisition": 1.05, "moat": 1.0, "failures": 0.9, "concentration": 0.95, "big_pharma_validation": 1.0, "catalyst_density": 1.1, "tech_uniqueness": 1.0, "dilution_overhang": 1.0, "cash_floor": 1.03}, "CRMD": {"crowding": 0.9, "financing": 1.0, "dilution": 1.0, "insider": 1.0, "mgmt": 1.0, "acquisition": 1.05, "moat": 1.0, "failures": 1.0, "concentration": 1.0, "big_pharma_validation": 1.0, "catalyst_density": 1.0, "tech_uniqueness": 1.0, "dilution_overhang": 1.0, "cash_floor": 1.0}, "CRVS": {"crowding": 0.8, "financing": 1.0, "dilution": 0.93, "insider": 1.0, "mgmt": 1.05, "acquisition": 1.05, "moat": 1.0, "failures": 1.0, "concentration": 0.85, "big_pharma_validation": 1.0, "catalyst_density": 1.05, "tech_uniqueness": 1.05, "dilution_overhang": 1.0, "cash_floor": 1.0}, "CTMX": {"crowding": 0.9, "financing": 1.0, "dilution": 0.97, "insider": 1.0, "mgmt": 1.0, "acquisition": 1.05, "moat": 1.0, "failures": 1.0, "concentration": 0.95, "big_pharma_validation": 1.1, "catalyst_density": 1.1, "tech_uniqueness": 1.05, "dilution_overhang": 0.97, "cash_floor": 1.0}, "CTNM": {"crowding": 0.9, "financing": 1.0, "dilution": 0.97, "insider": 1.0, "mgmt": 1.0, "acquisition": 1.0, "moat": 0.95, "failures": 0.9, "concentration": 0.95, "big_pharma_validation": 1.05, "catalyst_density": 1.0, "tech_uniqueness": 1.0, "dilution_overhang": 0.93, "cash_floor": 1.0}, "DAWN": {"crowding": 0.95, "financing": 1.0, "dilution": 1.0, "insider": 1.0, "mgmt": 1.05, "acquisition": 1.1, "moat": 1.0, "failures": 1.0, "concentration": 0.85, "big_pharma_validation": 1.0, "catalyst_density": 0.95, "tech_uniqueness": 1.0, "dilution_overhang": 1.0, "cash_floor": 1.0}, "DBVT": {"crowding": 0.9, "financing": 1.0, "dilution": 0.97, "insider": 1.0, "mgmt": 1.0, "acquisition": 1.05, "moat": 1.0, "failures": 1.0, "concentration": 0.95, "big_pharma_validation": 1.0, "catalyst_density": 1.1, "tech_uniqueness": 1.05, "dilution_overhang": 1.0, "cash_floor": 1.0}, "DMRA": {"crowding": 0.95, "financing": 1.0, "dilution": 0.93, "insider": 1.0, "mgmt": 1.0, "acquisition": 1.0, "moat": 0.95, "failures": 1.0, "concentration": 0.95, "big_pharma_validation": 1.0, "catalyst_density": 0.95, "tech_uniqueness": 1.0, "dilution_overhang": 1.0, "cash_floor": 1.0}, "DNA": {"crowding": 1.0, "financing": 1.0, "dilution": 1.0, "insider": 1.0, "mgmt": 0.9, "acquisition": 1.0, "moat": 0.95, "failures": 1.0, "concentration": 1.0, "big_pharma_validation": 1.05, "catalyst_density": 0.95, "tech_uniqueness": 1.0, "dilution_overhang": 0.93, "cash_floor": 1.03}, "DNLI": {"crowding": 0.95, "financing": 1.0, "dilution": 0.93, "insider": 1.1, "mgmt": 1.05, "acquisition": 1.05, "moat": 1.05, "failures": 1.0, "concentration": 0.95, "big_pharma_validation": 1.1, "catalyst_density": 1.05, "tech_uniqueness": 1.05, "dilution_overhang": 1.0, "cash_floor": 1.0}, "DRUG": {"crowding": 0.9, "financing": 1.0, "dilution": 0.97, "insider": 1.0, "mgmt": 1.0, "acquisition": 1.1, "moat": 1.0, "failures": 1.0, "concentration": 1.0, "big_pharma_validation": 1.0, "catalyst_density": 1.05, "tech_uniqueness": 1.0, "dilution_overhang": 1.0, "cash_floor": 1.0}, "DSGN": {"crowding": 0.95, "financing": 1.0, "dilution": 1.0, "insider": 1.0, "mgmt": 0.9, "acquisition": 1.05, "moat": 1.0, "failures": 0.9, "concentration": 0.95, "big_pharma_validation": 1.0, "catalyst_density": 1.1, "tech_uniqueness": 1.05, "dilution_overhang": 0.97, "cash_floor": 1.0}, "DYN": {"crowding": 0.9, "financing": 1.0, "dilution": 0.97, "insider": 1.1, "mgmt": 1.05, "acquisition": 1.1, "moat": 1.05, "failures": 1.0, "concentration": 0.95, "big_pharma_validation": 1.0, "catalyst_density": 1.1, "tech_uniqueness": 1.05, "dilution_overhang": 1.0, "cash_floor": 1.0}, "ELDN": {"crowding": 0.9, "financing": 1.0, "dilution": 0.97, "insider": 1.0, "mgmt": 1.0, "acquisition": 1.05, "moat": 1.0, "failures": 0.9, "concentration": 0.85, "big_pharma_validation": 1.05, "catalyst_density": 1.05, "tech_uniqueness": 1.0, "dilution_overhang": 0.93, "cash_floor": 1.0}, "ELVN": {"crowding": 0.9, "financing": 1.0, "dilution": 1.0, "insider": 1.0, "mgmt": 1.0, "acquisition": 1.05, "moat": 1.0, "failures": 1.0, "concentration": 0.95, "big_pharma_validation": 1.0, "catalyst_density": 1.0, "tech_uniqueness": 1.0, "dilution_overhang": 1.0, "cash_floor": 1.0}, "ENTA": {"crowding": 0.9, "financing": 1.0, "dilution": 0.97, "insider": 1.0, "mgmt": 1.0, "acquisition": 1.0, "moat": 1.0, "failures": 0.9, "concentration": 1.0, "big_pharma_validation": 1.05, "catalyst_density": 1.1, "tech_uniqueness": 1.0, "dilution_overhang": 0.97, "cash_floor": 1.03}, "EQ": {"crowding": 0.9, "financing": 1.0, "dilution": 0.93, "insider": 1.0, "mgmt": 0.9, "acquisition": 1.0, "moat": 0.95, "failures": 1.0, "concentration": 0.85, "big_pharma_validation": 1.0, "catalyst_density": 1.0, "tech_uniqueness": 1.0, "dilution_overhang": 1.0, "cash_floor": 1.0}, "EWTX": {"crowding": 0.9, "financing": 1.0, "dilution": 1.0, "insider": 1.1, "mgmt": 1.05, "acquisition": 1.05, "moat": 1.05, "failures": 0.9, "concentration": 0.85, "big_pharma_validation": 1.0, "catalyst_density": 1.05, "tech_uniqueness": 1.05, "dilution_overhang": 1.0, "cash_floor": 1.0}, "EYPT": {"crowding": 0.8, "financing": 0.95, "dilution": 1.0, "insider": 1.1, "mgmt": 1.05, "acquisition": 1.05, "moat": 1.0, "failures": 1.0, "concentration": 0.85, "big_pharma_validation": 1.0, "catalyst_density": 1.05, "tech_uniqueness": 1.05, "dilution_overhang": 1.0, "cash_floor": 1.0}, "FATE": {"crowding": 0.95, "financing": 1.0, "dilution": 0.97, "insider": 1.0, "mgmt": 0.9, "acquisition": 1.0, "moat": 1.0, "failures": 1.0, "concentration": 0.95, "big_pharma_validation": 1.0, "catalyst_density": 1.1, "tech_uniqueness": 1.05, "dilution_overhang": 0.88, "cash_floor": 1.08}, "FBRX": {"crowding": 0.95, "financing": 1.0, "dilution": 0.97, "insider": 1.1, "mgmt": 1.0, "acquisition": 1.0, "moat": 0.95, "failures": 1.0, "concentration": 0.85, "big_pharma_validation": 1.0, "catalyst_density": 1.05, "tech_uniqueness": 1.0, "dilution_overhang": 0.97, "cash_floor": 1.0}, "FHTX": {"crowding": 0.95, "financing": 1.0, "dilution": 0.97, "insider": 1.0, "mgmt": 1.0, "acquisition": 1.05, "moat": 1.0, "failures": 1.0, "concentration": 0.95, "big_pharma_validation": 1.05, "catalyst_density": 1.0, "tech_uniqueness": 1.05, "dilution_overhang": 0.93, "cash_floor": 1.03}, "FTRE": {"crowding": 0.9, "financing": 1.0, "dilution": 1.0, "insider": 1.0, "mgmt": 0.9, "acquisition": 1.05, "moat": 0.95, "failures": 1.0, "concentration": 0.85, "big_pharma_validation": 1.0, "catalyst_density": 0.95, "tech_uniqueness": 0.95, "dilution_overhang": 1.0, "cash_floor": 1.0}, "FULC": {"crowding": 0.9, "financing": 1.0, "dilution": 0.97, "insider": 1.0, "mgmt": 0.9, "acquisition": 1.0, "moat": 0.95, "failures": 1.0, "concentration": 0.85, "big_pharma_validation": 1.0, "catalyst_density": 1.0, "tech_uniqueness": 1.0, "dilution_overhang": 0.97, "cash_floor": 1.0}, "GERN": {"crowding": 0.9, "financing": 1.0, "dilution": 0.97, "insider": 1.0, "mgmt": 1.0, "acquisition": 1.05, "moat": 1.0, "failures": 1.0, "concentration": 0.85, "big_pharma_validation": 1.0, "catalyst_density": 1.0, "tech_uniqueness": 1.05, "dilution_overhang": 1.0, "cash_floor": 1.0}, "GHRS": {"crowding": 0.9, "financing": 1.0, "dilution": 1.0, "insider": 1.0, "mgmt": 1.0, "acquisition": 1.05, "moat": 1.0, "failures": 1.0, "concentration": 0.85, "big_pharma_validation": 1.0, "catalyst_density": 1.0, "tech_uniqueness": 1.05, "dilution_overhang": 1.0, "cash_floor": 1.0}, "GLPG": {"crowding": 0.9, "financing": 1.0, "dilution": 1.0, "insider": 1.0, "mgmt": 0.9, "acquisition": 1.0, "moat": 0.95, "failures": 0.9, "concentration": 0.85, "big_pharma_validation": 1.05, "catalyst_density": 1.0, "tech_uniqueness": 0.95, "dilution_overhang": 1.0, "cash_floor": 1.15}, "GLUE": {"crowding": 0.95, "financing": 1.0, "dilution": 0.97, "insider": 1.0, "mgmt": 1.05, "acquisition": 1.05, "moat": 1.05, "failures": 1.0, "concentration": 0.95, "big_pharma_validation": 1.1, "catalyst_density": 1.1, "tech_uniqueness": 1.05, "dilution_overhang": 1.0, "cash_floor": 1.0}, "GPCR": {"crowding": 0.9, "financing": 1.0, "dilution": 0.97, "insider": 1.0, "mgmt": 1.05, "acquisition": 1.05, "moat": 1.05, "failures": 1.0, "concentration": 0.85, "big_pharma_validation": 1.05, "catalyst_density": 1.1, "tech_uniqueness": 1.05, "dilution_overhang": 1.0, "cash_floor": 1.0}, "GRAL": {"crowding": 0.95, "financing": 1.0, "dilution": 1.0, "insider": 1.0, "mgmt": 0.9, "acquisition": 1.0, "moat": 1.0, "failures": 0.9, "concentration": 0.95, "big_pharma_validation": 1.0, "catalyst_density": 1.05, "tech_uniqueness": 1.05, "dilution_overhang": 1.0, "cash_floor": 1.0}, "HELP": {"crowding": 0.95, "financing": 1.0, "dilution": 0.97, "insider": 1.0, "mgmt": 0.9, "acquisition": 1.0, "moat": 1.0, "failures": 1.0, "concentration": 0.85, "big_pharma_validation": 1.0, "catalyst_density": 1.0, "tech_uniqueness": 1.0, "dilution_overhang": 0.88, "cash_floor": 1.03}, "HRMY": {"crowding": 0.9, "financing": 1.0, "dilution": 1.0, "insider": 1.0, "mgmt": 1.0, "acquisition": 1.0, "moat": 1.0, "failures": 0.9, "concentration": 0.95, "big_pharma_validation": 1.0, "catalyst_density": 1.1, "tech_uniqueness": 1.0, "dilution_overhang": 1.0, "cash_floor": 1.0}, "IBIO": {"crowding": 1.0, "financing": 1.0, "dilution": 0.93, "insider": 1.1, "mgmt": 1.0, "acquisition": 1.0, "moat": 0.95, "failures": 1.0, "concentration": 0.95, "big_pharma_validation": 1.0, "catalyst_density": 1.05, "tech_uniqueness": 1.0, "dilution_overhang": 0.88, "cash_floor": 1.0}, "IDYA": {"crowding": 0.9, "financing": 1.0, "dilution": 1.0, "insider": 1.1, "mgmt": 1.05, "acquisition": 1.05, "moat": 1.05, "failures": 1.0, "concentration": 1.0, "big_pharma_validation": 1.1, "catalyst_density": 1.1, "tech_uniqueness": 1.05, "dilution_overhang": 1.0, "cash_floor": 1.0}, "IKT": {"crowding": 0.9, "financing": 1.0, "dilution": 0.93, "insider": 1.1, "mgmt": 0.9, "acquisition": 1.0, "moat": 0.95, "failures": 1.0, "concentration": 0.85, "big_pharma_validation": 1.0, "catalyst_density": 1.0, "tech_uniqueness": 1.0, "dilution_overhang": 0.97, "cash_floor": 1.0}, "IMA": {"crowding": 0.9, "financing": 1.0, "dilution": 0.97, "insider": 1.1, "mgmt": 0.9, "acquisition": 1.0, "moat": 0.95, "failures": 0.9, "concentration": 0.85, "big_pharma_validation": 1.0, "catalyst_density": 1.0, "tech_uniqueness": 1.0, "dilution_overhang": 1.0, "cash_floor": 1.08}, "IMCR": {"crowding": 0.9, "financing": 1.0, "dilution": 0.97, "insider": 1.0, "mgmt": 1.0, "acquisition": 1.05, "moat": 1.05, "failures": 1.0, "concentration": 0.85, "big_pharma_validation": 1.05, "catalyst_density": 1.05, "tech_uniqueness": 1.05, "dilution_overhang": 1.0, "cash_floor": 1.03}, "IMMX": {"crowding": 0.9, "financing": 1.0, "dilution": 0.93, "insider": 1.1, "mgmt": 1.0, "acquisition": 1.05, "moat": 1.0, "failures": 1.0, "concentration": 0.85, "big_pharma_validation": 1.0, "catalyst_density": 1.0, "tech_uniqueness": 1.0, "dilution_overhang": 1.0, "cash_floor": 1.0}, "IMNM": {"crowding": 0.95, "financing": 1.0, "dilution": 0.97, "insider": 1.1, "mgmt": 1.05, "acquisition": 1.1, "moat": 1.0, "failures": 1.0, "concentration": 0.85, "big_pharma_validation": 1.0, "catalyst_density": 1.05, "tech_uniqueness": 1.0, "dilution_overhang": 1.0, "cash_floor": 1.0}, "IMTX": {"crowding": 0.9, "financing": 1.0, "dilution": 0.97, "insider": 1.0, "mgmt": 1.05, "acquisition": 1.05, "moat": 1.05, "failures": 1.0, "concentration": 0.85, "big_pharma_validation": 1.05, "catalyst_density": 1.1, "tech_uniqueness": 1.05, "dilution_overhang": 1.0, "cash_floor": 1.0}, "IMUX": {"crowding": 0.9, "financing": 1.0, "dilution": 0.97, "insider": 1.0, "mgmt": 1.0, "acquisition": 1.05, "moat": 1.0, "failures": 1.0, "concentration": 0.85, "big_pharma_validation": 1.0, "catalyst_density": 1.0, "tech_uniqueness": 1.0, "dilution_overhang": 0.93, "cash_floor": 1.0}, "INBX": {"crowding": 1.0, "financing": 0.85, "dilution": 0.97, "insider": 1.0, "mgmt": 1.05, "acquisition": 1.05, "moat": 1.0, "failures": 1.0, "concentration": 0.85, "big_pharma_validation": 1.05, "catalyst_density": 1.05, "tech_uniqueness": 1.05, "dilution_overhang": 1.0, "cash_floor": 1.0}, "INO": {"crowding": 0.95, "financing": 0.85, "dilution": 0.93, "insider": 1.0, "mgmt": 0.9, "acquisition": 1.0, "moat": 0.95, "failures": 1.0, "concentration": 0.85, "big_pharma_validation": 1.0, "catalyst_density": 1.05, "tech_uniqueness": 1.0, "dilution_overhang": 1.0, "cash_floor": 1.0}, "IOVA": {"crowding": 0.95, "financing": 1.0, "dilution": 1.0, "insider": 1.0, "mgmt": 0.9, "acquisition": 1.05, "moat": 1.0, "failures": 0.9, "concentration": 0.95, "big_pharma_validation": 1.05, "catalyst_density": 1.05, "tech_uniqueness": 1.05, "dilution_overhang": 1.0, "cash_floor": 1.0}, "IRD": {"crowding": 0.9, "financing": 1.0, "dilution": 0.93, "insider": 1.1, "mgmt": 1.0, "acquisition": 1.0, "moat": 0.95, "failures": 1.0, "concentration": 0.95, "big_pharma_validation": 1.0, "catalyst_density": 1.1, "tech_uniqueness": 1.0, "dilution_overhang": 1.0, "cash_floor": 1.0}, "IRON": {"crowding": 0.9, "financing": 1.0, "dilution": 1.0, "insider": 1.0, "mgmt": 1.0, "acquisition": 1.05, "moat": 1.05, "failures": 0.9, "concentration": 0.95, "big_pharma_validation": 1.1, "catalyst_density": 1.1, "tech_uniqueness": 1.05, "dilution_overhang": 1.0, "cash_floor": 1.0}, "IVA": {"crowding": 0.8, "financing": 0.85, "dilution": 0.97, "insider": 1.1, "mgmt": 0.9, "acquisition": 1.1, "moat": 1.0, "failures": 1.0, "concentration": 0.85, "big_pharma_validation": 1.05, "catalyst_density": 1.0, "tech_uniqueness": 1.0, "dilution_overhang": 1.0, "cash_floor": 1.0}, "IVVD": {"crowding": 0.95, "financing": 1.0, "dilution": 0.97, "insider": 1.1, "mgmt": 1.0, "acquisition": 1.0, "moat": 1.0, "failures": 1.0, "concentration": 0.95, "big_pharma_validation": 1.0, "catalyst_density": 1.05, "tech_uniqueness": 1.0, "dilution_overhang": 0.88, "cash_floor": 1.0}, "JANX": {"crowding": 0.95, "financing": 1.0, "dilution": 0.93, "insider": 1.0, "mgmt": 0.9, "acquisition": 1.05, "moat": 1.0, "failures": 1.0, "concentration": 0.95, "big_pharma_validation": 1.05, "catalyst_density": 1.1, "tech_uniqueness": 1.05, "dilution_overhang": 1.0, "cash_floor": 1.03}, "JBIO": {"crowding": 0.8, "financing": 1.0, "dilution": 0.97, "insider": 1.0, "mgmt": 1.0, "acquisition": 1.0, "moat": 0.95, "failures": 1.0, "concentration": 0.85, "big_pharma_validation": 1.0, "catalyst_density": 1.05, "tech_uniqueness": 1.0, "dilution_overhang": 1.0, "cash_floor": 1.0}, "KALV": {"crowding": 0.8, "financing": 1.0, "dilution": 1.0, "insider": 1.0, "mgmt": 1.05, "acquisition": 1.05, "moat": 1.0, "failures": 1.0, "concentration": 0.95, "big_pharma_validation": 1.0, "catalyst_density": 1.0, "tech_uniqueness": 1.05, "dilution_overhang": 1.0, "cash_floor": 1.0}, "KLRS": {"crowding": 0.9, "financing": 1.0, "dilution": 0.93, "insider": 1.1, "mgmt": 0.9, "acquisition": 1.0, "moat": 0.95, "failures": 0.9, "concentration": 0.85, "big_pharma_validation": 1.0, "catalyst_density": 1.0, "tech_uniqueness": 1.0, "dilution_overhang": 0.88, "cash_floor": 1.03}, "KOD": {"crowding": 0.95, "financing": 1.0, "dilution": 0.97, "insider": 1.1, "mgmt": 0.9, "acquisition": 1.0, "moat": 1.0, "failures": 1.0, "concentration": 1.0, "big_pharma_validation": 1.0, "catalyst_density": 1.05, "tech_uniqueness": 1.0, "dilution_overhang": 1.0, "cash_floor": 1.0}, "KPTI": {"crowding": 0.8, "financing": 0.85, "dilution": 0.93, "insider": 1.0, "mgmt": 0.9, "acquisition": 1.0, "moat": 0.95, "failures": 1.0, "concentration": 0.95, "big_pharma_validation": 1.0, "catalyst_density": 1.1, "tech_uniqueness": 1.0, "dilution_overhang": 1.0, "cash_floor": 1.0}, "KURA": {"crowding": 0.95, "financing": 1.0, "dilution": 1.0, "insider": 1.0, "mgmt": 1.05, "acquisition": 1.05, "moat": 1.0, "failures": 1.0, "concentration": 1.0, "big_pharma_validation": 1.0, "catalyst_density": 1.0, "tech_uniqueness": 1.0, "dilution_overhang": 1.0, "cash_floor": 1.03}, "LEGN": {"crowding": 0.9, "financing": 1.0, "dilution": 1.0, "insider": 1.0, "mgmt": 1.05, "acquisition": 1.05, "moat": 1.0, "failures": 1.0, "concentration": 0.85, "big_pharma_validation": 1.1, "catalyst_density": 1.05, "tech_uniqueness": 1.0, "dilution_overhang": 1.0, "cash_floor": 1.0}, "LENZ": {"crowding": 0.9, "financing": 0.95, "dilution": 1.0, "insider": 1.1, "mgmt": 1.0, "acquisition": 1.05, "moat": 1.0, "failures": 1.0, "concentration": 0.85, "big_pharma_validation": 1.0, "catalyst_density": 0.95, "tech_uniqueness": 1.0, "dilution_overhang": 1.0, "cash_floor": 1.03}, "LONA": {"crowding": 0.9, "financing": 1.0, "dilution": 0.93, "insider": 1.1, "mgmt": 0.9, "acquisition": 1.0, "moat": 0.95, "failures": 1.0, "concentration": 0.85, "big_pharma_validation": 1.05, "catalyst_density": 1.0, "tech_uniqueness": 1.0, "dilution_overhang": 0.93, "cash_floor": 1.0}, "LRMR": {"crowding": 0.95, "financing": 1.0, "dilution": 0.97, "insider": 1.1, "mgmt": 0.9, "acquisition": 1.05, "moat": 1.0, "failures": 1.0, "concentration": 0.85, "big_pharma_validation": 1.0, "catalyst_density": 1.05, "tech_uniqueness": 1.05, "dilution_overhang": 0.97, "cash_floor": 1.0}, "LXEO": {"crowding": 0.95, "financing": 1.0, "dilution": 1.0, "insider": 1.0, "mgmt": 1.0, "acquisition": 1.05, "moat": 1.0, "failures": 1.0, "concentration": 0.95, "big_pharma_validation": 1.0, "catalyst_density": 1.05, "tech_uniqueness": 1.05, "dilution_overhang": 1.0, "cash_floor": 1.0}, "LYEL": {"crowding": 0.95, "financing": 1.0, "dilution": 0.97, "insider": 1.1, "mgmt": 1.05, "acquisition": 1.05, "moat": 1.0, "failures": 1.0, "concentration": 1.0, "big_pharma_validation": 1.0, "catalyst_density": 1.0, "tech_uniqueness": 1.0, "dilution_overhang": 0.97, "cash_floor": 1.0}, "MAZE": {"crowding": 0.95, "financing": 1.0, "dilution": 0.97, "insider": 1.0, "mgmt": 1.05, "acquisition": 1.05, "moat": 1.0, "failures": 1.0, "concentration": 0.85, "big_pharma_validation": 1.0, "catalyst_density": 1.05, "tech_uniqueness": 1.0, "dilution_overhang": 1.0, "cash_floor": 1.0}, "MBX": {"crowding": 0.9, "financing": 1.0, "dilution": 0.97, "insider": 1.1, "mgmt": 1.05, "acquisition": 1.05, "moat": 1.0, "failures": 1.0, "concentration": 0.95, "big_pharma_validation": 1.0, "catalyst_density": 1.1, "tech_uniqueness": 1.0, "dilution_overhang": 1.0, "cash_floor": 1.0}, "MDXG": {"crowding": 0.9, "financing": 1.0, "dilution": 1.0, "insider": 1.0, "mgmt": 1.0, "acquisition": 1.05, "moat": 1.0, "failures": 1.0, "concentration": 0.95, "big_pharma_validation": 1.0, "catalyst_density": 0.95, "tech_uniqueness": 1.0, "dilution_overhang": 1.0, "cash_floor": 1.0}, "MIST": {"crowding": 0.9, "financing": 1.0, "dilution": 0.97, "insider": 1.0, "mgmt": 0.9, "acquisition": 1.05, "moat": 1.0, "failures": 1.0, "concentration": 0.95, "big_pharma_validation": 1.0, "catalyst_density": 0.95, "tech_uniqueness": 1.05, "dilution_overhang": 0.93, "cash_floor": 1.0}, "MLTX": {"crowding": 0.9, "financing": 1.0, "dilution": 0.93, "insider": 1.0, "mgmt": 0.9, "acquisition": 1.0, "moat": 0.95, "failures": 0.9, "concentration": 0.95, "big_pharma_validation": 1.0, "catalyst_density": 1.0, "tech_uniqueness": 1.0, "dilution_overhang": 1.0, "cash_floor": 1.0}, "MLYS": {"crowding": 0.9, "financing": 0.95, "dilution": 1.0, "insider": 1.1, "mgmt": 1.05, "acquisition": 1.05, "moat": 1.0, "failures": 0.9, "concentration": 0.85, "big_pharma_validation": 1.0, "catalyst_density": 1.0, "tech_uniqueness": 1.0, "dilution_overhang": 1.0, "cash_floor": 1.0}, "MNKD": {"crowding": 0.9, "financing": 1.0, "dilution": 0.97, "insider": 1.1, "mgmt": 0.9, "acquisition": 1.0, "moat": 1.0, "failures": 0.9, "concentration": 1.0, "big_pharma_validation": 1.0, "catalyst_density": 1.0, "tech_uniqueness": 1.0, "dilution_overhang": 1.0, "cash_floor": 1.0}, "MNPR": {"crowding": 0.95, "financing": 1.0, "dilution": 1.0, "insider": 1.1, "mgmt": 1.0, "acquisition": 1.0, "moat": 1.0, "failures": 1.0, "concentration": 0.85, "big_pharma_validation": 1.05, "catalyst_density": 1.0, "tech_uniqueness": 1.0, "dilution_overhang": 0.97, "cash_floor": 1.0}, "NAGE": {"crowding": 0.9, "financing": 1.0, "dilution": 0.97, "insider": 1.1, "mgmt": 1.0, "acquisition": 1.05, "moat": 1.0, "failures": 1.0, "concentration": 0.85, "big_pharma_validation": 1.0, "catalyst_density": 1.0, "tech_uniqueness": 1.0, "dilution_overhang": 1.0, "cash_floor": 1.0}, "NBP": {"crowding": 0.9, "financing": 1.0, "dilution": 1.0, "insider": 1.0, "mgmt": 1.0, "acquisition": 1.0, "moat": 1.0, "failures": 1.0, "concentration": 0.95, "big_pharma_validation": 1.0, "catalyst_density": 1.1, "tech_uniqueness": 1.05, "dilution_overhang": 0.97, "cash_floor": 1.0}, "NERV": {"crowding": 0.9, "financing": 1.0, "dilution": 0.93, "insider": 1.0, "mgmt": 0.9, "acquisition": 1.0, "moat": 0.95, "failures": 1.0, "concentration": 0.85, "big_pharma_validation": 1.05, "catalyst_density": 0.95, "tech_uniqueness": 1.0, "dilution_overhang": 0.97, "cash_floor": 1.0}, "NGNE": {"crowding": 0.9, "financing": 1.0, "dilution": 1.0, "insider": 1.0, "mgmt": 1.0, "acquisition": 1.05, "moat": 1.0, "failures": 1.0, "concentration": 0.85, "big_pharma_validation": 1.0, "catalyst_density": 1.05, "tech_uniqueness": 1.05, "dilution_overhang": 0.97, "cash_floor": 1.03}, "NKTR": {"crowding": 0.8, "financing": 1.0, "dilution": 0.93, "insider": 1.0, "mgmt": 1.0, "acquisition": 1.0, "moat": 1.0, "failures": 1.0, "concentration": 0.85, "big_pharma_validation": 1.0, "catalyst_density": 1.1, "tech_uniqueness": 1.05, "dilution_overhang": 1.0, "cash_floor": 1.0}, "NKTX": {"crowding": 0.95, "financing": 1.0, "dilution": 0.93, "insider": 1.0, "mgmt": 0.9, "acquisition": 1.0, "moat": 0.95, "failures": 1.0, "concentration": 1.0, "big_pharma_validation": 1.0, "catalyst_density": 1.0, "tech_uniqueness": 1.0, "dilution_overhang": 0.97, "cash_floor": 1.08}, "NRIX": {"crowding": 0.9, "financing": 0.95, "dilution": 0.97, "insider": 1.0, "mgmt": 1.0, "acquisition": 1.05, "moat": 1.0, "failures": 1.0, "concentration": 0.95, "big_pharma_validation": 1.1, "catalyst_density": 1.05, "tech_uniqueness": 1.05, "dilution_overhang": 1.0, "cash_floor": 1.0}, "NTLA": {"crowding": 0.95, "financing": 0.95, "dilution": 0.97, "insider": 1.1, "mgmt": 1.05, "acquisition": 1.05, "moat": 1.05, "failures": 0.9, "concentration": 0.85, "big_pharma_validation": 1.05, "catalyst_density": 1.05, "tech_uniqueness": 1.05, "dilution_overhang": 1.0, "cash_floor": 1.0}, "NUVB": {"crowding": 0.9, "financing": 1.0, "dilution": 0.93, "insider": 1.0, "mgmt": 1.05, "acquisition": 1.05, "moat": 1.0, "failures": 1.0, "concentration": 0.95, "big_pharma_validation": 1.0, "catalyst_density": 1.1, "tech_uniqueness": 1.0, "dilution_overhang": 1.0, "cash_floor": 1.0}, "NVAX": {"crowding": 0.95, "financing": 1.0, "dilution": 1.0, "insider": 1.0, "mgmt": 1.0, "acquisition": 1.0, "moat": 1.0, "failures": 1.0, "concentration": 0.95, "big_pharma_validation": 1.1, "catalyst_density": 1.05, "tech_uniqueness": 1.0, "dilution_overhang": 1.0, "cash_floor": 1.0}, "OBIO": {"crowding": 0.9, "financing": 1.0, "dilution": 0.93, "insider": 1.0, "mgmt": 1.0, "acquisition": 1.05, "moat": 1.0, "failures": 1.0, "concentration": 1.0, "big_pharma_validation": 1.0, "catalyst_density": 1.05, "tech_uniqueness": 1.05, "dilution_overhang": 1.0, "cash_floor": 1.0}, "OLMA": {"crowding": 0.9, "financing": 1.0, "dilution": 0.97, "insider": 1.0, "mgmt": 1.0, "acquisition": 1.05, "moat": 1.0, "failures": 1.0, "concentration": 0.95, "big_pharma_validation": 1.1, "catalyst_density": 1.05, "tech_uniqueness": 1.0, "dilution_overhang": 0.97, "cash_floor": 1.0}, "ORIC": {"crowding": 0.9, "financing": 1.0, "dilution": 0.97, "insider": 1.0, "mgmt": 1.0, "acquisition": 1.0, "moat": 0.95, "failures": 1.0, "concentration": 0.95, "big_pharma_validation": 1.05, "catalyst_density": 0.95, "tech_uniqueness": 1.0, "dilution_overhang": 1.0, "cash_floor": 1.0}, "OVID": {"crowding": 0.9, "financing": 1.0, "dilution": 0.93, "insider": 1.1, "mgmt": 1.0, "acquisition": 1.0, "moat": 1.0, "failures": 1.0, "concentration": 1.0, "big_pharma_validation": 1.1, "catalyst_density": 1.1, "tech_uniqueness": 1.05, "dilution_overhang": 1.0, "cash_floor": 1.0}, "PALI": {"crowding": 0.9, "financing": 1.0, "dilution": 1.0, "insider": 1.1, "mgmt": 0.9, "acquisition": 1.0, "moat": 0.95, "failures": 1.0, "concentration": 0.95, "big_pharma_validation": 1.0, "catalyst_density": 0.95, "tech_uniqueness": 1.0, "dilution_overhang": 1.0, "cash_floor": 1.0}, "PCRX": {"crowding": 0.9, "financing": 1.0, "dilution": 1.0, "insider": 1.0, "mgmt": 1.0, "acquisition": 1.05, "moat": 1.0, "failures": 1.0, "concentration": 0.95, "big_pharma_validation": 1.05, "catalyst_density": 1.1, "tech_uniqueness": 1.0, "dilution_overhang": 1.0, "cash_floor": 1.0}, "PEPG": {"crowding": 0.95, "financing": 1.0, "dilution": 1.0, "insider": 1.0, "mgmt": 0.9, "acquisition": 1.0, "moat": 0.95, "failures": 0.9, "concentration": 0.85, "big_pharma_validation": 1.0, "catalyst_density": 1.0, "tech_uniqueness": 1.0, "dilution_overhang": 0.88, "cash_floor": 1.03}, "PGEN": {"crowding": 0.95, "financing": 0.95, "dilution": 1.0, "insider": 1.0, "mgmt": 1.0, "acquisition": 1.05, "moat": 1.0, "failures": 1.0, "concentration": 0.85, "big_pharma_validation": 1.0, "catalyst_density": 1.1, "tech_uniqueness": 1.05, "dilution_overhang": 1.0, "cash_floor": 1.0}, "PHAT": {"crowding": 0.9, "financing": 1.0, "dilution": 0.97, "insider": 1.0, "mgmt": 1.0, "acquisition": 1.05, "moat": 1.0, "failures": 1.0, "concentration": 0.85, "big_pharma_validation": 1.05, "catalyst_density": 0.95, "tech_uniqueness": 1.0, "dilution_overhang": 1.0, "cash_floor": 1.0}, "PHVS": {"crowding": 0.8, "financing": 0.95, "dilution": 1.0, "insider": 1.0, "mgmt": 1.05, "acquisition": 1.05, "moat": 1.0, "failures": 1.0, "concentration": 0.95, "big_pharma_validation": 1.0, "catalyst_density": 1.0, "tech_uniqueness": 1.0, "dilution_overhang": 1.0, "cash_floor": 1.0}, "PLRX": {"crowding": 0.95, "financing": 0.95, "dilution": 0.97, "insider": 1.0, "mgmt": 0.9, "acquisition": 1.0, "moat": 0.95, "failures": 0.9, "concentration": 0.85, "big_pharma_validation": 1.0, "catalyst_density": 1.05, "tech_uniqueness": 1.0, "dilution_overhang": 0.88, "cash_floor": 1.03}, "PMVP": {"crowding": 0.95, "financing": 1.0, "dilution": 1.0, "insider": 1.0, "mgmt": 1.0, "acquisition": 1.05, "moat": 1.0, "failures": 1.0, "concentration": 0.95, "big_pharma_validation": 1.0, "catalyst_density": 1.1, "tech_uniqueness": 1.05, "dilution_overhang": 0.88, "cash_floor": 1.08}, "PRGO": {"crowding": 1.0, "financing": 1.0, "dilution": 0.97, "insider": 1.1, "mgmt": 0.9, "acquisition": 1.0, "moat": 1.0, "failures": 0.9, "concentration": 0.85, "big_pharma_validation": 1.0, "catalyst_density": 0.95, "tech_uniqueness": 1.0, "dilution_overhang": 1.0, "cash_floor": 1.0}, "PRLD": {"crowding": 0.9, "financing": 1.0, "dilution": 0.93, "insider": 1.1, "mgmt": 1.0, "acquisition": 1.05, "moat": 1.0, "failures": 0.9, "concentration": 0.95, "big_pharma_validation": 1.0, "catalyst_density": 1.05, "tech_uniqueness": 1.05, "dilution_overhang": 1.0, "cash_floor": 1.0}, "PRME": {"crowding": 1.0, "financing": 0.95, "dilution": 1.0, "insider": 1.0, "mgmt": 1.0, "acquisition": 1.05, "moat": 1.05, "failures": 1.0, "concentration": 0.95, "big_pharma_validation": 1.05, "catalyst_density": 1.1, "tech_uniqueness": 1.05, "dilution_overhang": 1.0, "cash_floor": 1.0}, "PRQR": {"crowding": 1.0, "financing": 0.95, "dilution": 0.97, "insider": 1.0, "mgmt": 0.9, "acquisition": 1.05, "moat": 1.0, "failures": 1.0, "concentration": 1.0, "big_pharma_validation": 1.05, "catalyst_density": 1.1, "tech_uniqueness": 1.05, "dilution_overhang": 0.88, "cash_floor": 1.03}, "PVLA": {"crowding": 0.95, "financing": 1.0, "dilution": 0.97, "insider": 1.1, "mgmt": 1.05, "acquisition": 1.05, "moat": 1.0, "failures": 1.0, "concentration": 0.85, "big_pharma_validation": 1.0, "catalyst_density": 1.0, "tech_uniqueness": 1.0, "dilution_overhang": 1.0, "cash_floor": 1.0}, "QTTB": {"crowding": 0.9, "financing": 0.95, "dilution": 0.93, "insider": 1.0, "mgmt": 0.9, "acquisition": 1.0, "moat": 0.95, "failures": 1.0, "concentration": 0.85, "big_pharma_validation": 1.05, "catalyst_density": 1.0, "tech_uniqueness": 1.0, "dilution_overhang": 0.93, "cash_floor": 1.0}, "RAPP": {"crowding": 0.9, "financing": 1.0, "dilution": 0.93, "insider": 1.0, "mgmt": 1.0, "acquisition": 1.05, "moat": 1.0, "failures": 1.0, "concentration": 0.85, "big_pharma_validation": 1.0, "catalyst_density": 1.05, "tech_uniqueness": 1.05, "dilution_overhang": 1.0, "cash_floor": 1.0}, "RARE": {"crowding": 0.9, "financing": 0.85, "dilution": 0.97, "insider": 1.0, "mgmt": 1.0, "acquisition": 1.0, "moat": 1.0, "failures": 0.9, "concentration": 1.0, "big_pharma_validation": 1.05, "catalyst_density": 1.1, "tech_uniqueness": 1.0, "dilution_overhang": 1.0, "cash_floor": 1.0}, "RCKT": {"crowding": 0.95, "financing": 0.95, "dilution": 0.97, "insider": 1.1, "mgmt": 0.9, "acquisition": 1.05, "moat": 1.0, "failures": 0.9, "concentration": 1.0, "big_pharma_validation": 1.0, "catalyst_density": 1.05, "tech_uniqueness": 1.05, "dilution_overhang": 1.0, "cash_floor": 1.0}, "RCUS": {"crowding": 0.9, "financing": 1.0, "dilution": 1.0, "insider": 1.0, "mgmt": 0.9, "acquisition": 1.05, "moat": 1.0, "failures": 0.9, "concentration": 0.95, "big_pharma_validation": 1.1, "catalyst_density": 1.1, "tech_uniqueness": 1.0, "dilution_overhang": 1.0, "cash_floor": 1.0}, "REPL": {"crowding": 0.9, "financing": 0.7, "dilution": 0.93, "insider": 1.0, "mgmt": 0.9, "acquisition": 1.0, "moat": 0.95, "failures": 0.9, "concentration": 0.85, "big_pharma_validation": 1.1, "catalyst_density": 0.95, "tech_uniqueness": 1.0, "dilution_overhang": 0.93, "cash_floor": 1.0}, "RLAY": {"crowding": 0.9, "financing": 1.0, "dilution": 1.0, "insider": 1.0, "mgmt": 1.0, "acquisition": 1.05, "moat": 1.0, "failures": 1.0, "concentration": 0.95, "big_pharma_validation": 1.1, "catalyst_density": 1.05, "tech_uniqueness": 1.05, "dilution_overhang": 1.0, "cash_floor": 1.0}, "RLMD": {"crowding": 0.8, "financing": 1.0, "dilution": 0.93, "insider": 1.1, "mgmt": 1.0, "acquisition": 1.05, "moat": 1.0, "failures": 1.0, "concentration": 0.95, "big_pharma_validation": 1.0, "catalyst_density": 1.05, "tech_uniqueness": 1.0, "dilution_overhang": 1.0, "cash_floor": 1.0}, "RXRX": {"crowding": 0.95, "financing": 1.0, "dilution": 0.97, "insider": 1.0, "mgmt": 0.9, "acquisition": 1.0, "moat": 1.0, "failures": 0.9, "concentration": 0.95, "big_pharma_validation": 1.1, "catalyst_density": 1.1, "tech_uniqueness": 1.0, "dilution_overhang": 1.0, "cash_floor": 1.0}, "RZLT": {"crowding": 0.95, "financing": 0.95, "dilution": 1.0, "insider": 1.1, "mgmt": 0.9, "acquisition": 1.0, "moat": 1.0, "failures": 0.9, "concentration": 0.95, "big_pharma_validation": 1.0, "catalyst_density": 1.05, "tech_uniqueness": 1.05, "dilution_overhang": 0.93, "cash_floor": 1.0}, "SABS": {"crowding": 0.9, "financing": 1.0, "dilution": 0.93, "insider": 1.0, "mgmt": 0.9, "acquisition": 1.0, "moat": 0.95, "failures": 1.0, "concentration": 0.85, "big_pharma_validation": 1.05, "catalyst_density": 0.95, "tech_uniqueness": 1.0, "dilution_overhang": 1.0, "cash_floor": 1.0}, "SANA": {"crowding": 0.95, "financing": 0.85, "dilution": 0.97, "insider": 1.0, "mgmt": 0.9, "acquisition": 1.0, "moat": 1.0, "failures": 1.0, "concentration": 0.85, "big_pharma_validation": 1.0, "catalyst_density": 1.1, "tech_uniqueness": 1.05, "dilution_overhang": 1.0, "cash_floor": 1.0}, "SEPN": {"crowding": 0.8, "financing": 1.0, "dilution": 1.0, "insider": 1.0, "mgmt": 1.0, "acquisition": 1.0, "moat": 1.0, "failures": 1.0, "concentration": 1.0, "big_pharma_validation": 1.05, "catalyst_density": 1.0, "tech_uniqueness": 1.05, "dilution_overhang": 1.0, "cash_floor": 1.0}, "SION": {"crowding": 0.95, "financing": 1.0, "dilution": 1.0, "insider": 1.0, "mgmt": 1.05, "acquisition": 1.05, "moat": 1.0, "failures": 1.0, "concentration": 0.95, "big_pharma_validation": 1.05, "catalyst_density": 1.05, "tech_uniqueness": 1.05, "dilution_overhang": 1.0, "cash_floor": 1.0}, "SLDB": {"crowding": 0.9, "financing": 1.0, "dilution": 0.97, "insider": 1.1, "mgmt": 1.0, "acquisition": 1.05, "moat": 1.0, "failures": 1.0, "concentration": 0.95, "big_pharma_validation": 1.0, "catalyst_density": 1.0, "tech_uniqueness": 1.05, "dilution_overhang": 0.97, "cash_floor": 1.0}, "SLN": {"crowding": 0.8, "financing": 0.95, "dilution": 1.0, "insider": 1.0, "mgmt": 1.0, "acquisition": 1.05, "moat": 1.0, "failures": 1.0, "concentration": 0.85, "big_pharma_validation": 1.05, "catalyst_density": 1.1, "tech_uniqueness": 1.0, "dilution_overhang": 1.0, "cash_floor": 1.0}, "SLNO": {"crowding": 0.95, "financing": 1.0, "dilution": 0.97, "insider": 1.0, "mgmt": 1.0, "acquisition": 1.1, "moat": 1.0, "failures": 1.0, "concentration": 0.85, "big_pharma_validation": 1.0, "catalyst_density": 0.95, "tech_uniqueness": 1.0, "dilution_overhang": 1.0, "cash_floor": 1.0}, "SNDX": {"crowding": 0.95, "financing": 0.95, "dilution": 1.0, "insider": 1.0, "mgmt": 1.05, "acquisition": 1.05, "moat": 1.0, "failures": 1.0, "concentration": 1.0, "big_pharma_validation": 1.0, "catalyst_density": 1.05, "tech_uniqueness": 1.0, "dilution_overhang": 1.0, "cash_floor": 1.0}, "SPRY": {"crowding": 0.9, "financing": 0.95, "dilution": 1.0, "insider": 1.0, "mgmt": 1.0, "acquisition": 1.05, "moat": 1.0, "failures": 1.0, "concentration": 0.85, "big_pharma_validation": 1.0, "catalyst_density": 1.05, "tech_uniqueness": 1.05, "dilution_overhang": 1.0, "cash_floor": 1.0}, "SRPT": {"crowding": 0.9, "financing": 1.0, "dilution": 0.97, "insider": 1.0, "mgmt": 0.9, "acquisition": 1.0, "moat": 1.0, "failures": 0.9, "concentration": 1.0, "big_pharma_validation": 1.05, "catalyst_density": 1.05, "tech_uniqueness": 1.0, "dilution_overhang": 1.0, "cash_floor": 1.0}, "SRZN": {"crowding": 0.95, "financing": 1.0, "dilution": 0.93, "insider": 1.1, "mgmt": 0.9, "acquisition": 1.0, "moat": 0.95, "failures": 1.0, "concentration": 0.95, "big_pharma_validation": 1.05, "catalyst_density": 1.0, "tech_uniqueness": 1.0, "dilution_overhang": 1.0, "cash_floor": 1.0}, "STOK": {"crowding": 0.95, "financing": 1.0, "dilution": 1.0, "insider": 1.0, "mgmt": 1.0, "acquisition": 1.05, "moat": 1.05, "failures": 1.0, "concentration": 0.95, "big_pharma_validation": 1.05, "catalyst_density": 1.05, "tech_uniqueness": 1.05, "dilution_overhang": 1.0, "cash_floor": 1.0}, "STTK": {"crowding": 0.9, "financing": 0.95, "dilution": 0.93, "insider": 1.0, "mgmt": 1.0, "acquisition": 1.05, "moat": 1.0, "failures": 1.0, "concentration": 0.95, "big_pharma_validation": 1.0, "catalyst_density": 1.05, "tech_uniqueness": 1.05, "dilution_overhang": 1.0, "cash_floor": 1.0}, "SVRA": {"crowding": 0.95, "financing": 1.0, "dilution": 0.97, "insider": 1.0, "mgmt": 1.0, "acquisition": 1.05, "moat": 1.0, "failures": 1.0, "concentration": 0.85, "big_pharma_validation": 1.0, "catalyst_density": 1.0, "tech_uniqueness": 1.0, "dilution_overhang": 1.0, "cash_floor": 1.0}, "TARA": {"crowding": 0.8, "financing": 1.0, "dilution": 0.97, "insider": 1.0, "mgmt": 1.0, "acquisition": 1.05, "moat": 1.0, "failures": 1.0, "concentration": 1.0, "big_pharma_validation": 1.0, "catalyst_density": 1.05, "tech_uniqueness": 1.0, "dilution_overhang": 0.93, "cash_floor": 1.03}, "TARS": {"crowding": 0.95, "financing": 1.0, "dilution": 1.0, "insider": 1.0, "mgmt": 1.05, "acquisition": 1.05, "moat": 1.0, "failures": 1.0, "concentration": 0.85, "big_pharma_validation": 1.0, "catalyst_density": 1.1, "tech_uniqueness": 1.05, "dilution_overhang": 1.0, "cash_floor": 1.0}, "TBPH": {"crowding": 0.95, "financing": 1.0, "dilution": 1.0, "insider": 1.0, "mgmt": 0.9, "acquisition": 1.1, "moat": 1.0, "failures": 0.9, "concentration": 0.95, "big_pharma_validation": 1.05, "catalyst_density": 0.95, "tech_uniqueness": 1.0, "dilution_overhang": 1.0, "cash_floor": 1.0}, "TCRX": {"crowding": 0.95, "financing": 1.0, "dilution": 1.0, "insider": 1.0, "mgmt": 0.9, "acquisition": 1.0, "moat": 0.95, "failures": 1.0, "concentration": 0.85, "big_pharma_validation": 1.05, "catalyst_density": 1.0, "tech_uniqueness": 1.0, "dilution_overhang": 1.0, "cash_floor": 1.03}, "TECX": {"crowding": 0.95, "financing": 1.0, "dilution": 1.0, "insider": 1.1, "mgmt": 1.05, "acquisition": 1.05, "moat": 1.0, "failures": 1.0, "concentration": 0.85, "big_pharma_validation": 1.0, "catalyst_density": 1.05, "tech_uniqueness": 1.0, "dilution_overhang": 0.93, "cash_floor": 1.0}, "TENX": {"crowding": 0.9, "financing": 1.0, "dilution": 0.97, "insider": 1.1, "mgmt": 1.0, "acquisition": 1.05, "moat": 1.0, "failures": 1.0, "concentration": 0.85, "big_pharma_validation": 1.0, "catalyst_density": 1.05, "tech_uniqueness": 1.0, "dilution_overhang": 0.97, "cash_floor": 1.0}, "TRDA": {"crowding": 0.9, "financing": 1.0, "dilution": 0.97, "insider": 1.1, "mgmt": 1.0, "acquisition": 1.05, "moat": 1.0, "failures": 1.0, "concentration": 1.0, "big_pharma_validation": 1.05, "catalyst_density": 1.1, "tech_uniqueness": 1.05, "dilution_overhang": 0.93, "cash_floor": 1.03}, "TRVI": {"crowding": 0.95, "financing": 1.0, "dilution": 0.97, "insider": 1.0, "mgmt": 1.0, "acquisition": 1.05, "moat": 1.0, "failures": 1.0, "concentration": 0.85, "big_pharma_validation": 1.0, "catalyst_density": 1.05, "tech_uniqueness": 1.0, "dilution_overhang": 1.0, "cash_floor": 1.0}, "TSHA": {"crowding": 0.95, "financing": 1.0, "dilution": 0.97, "insider": 1.0, "mgmt": 1.05, "acquisition": 1.05, "moat": 1.0, "failures": 1.0, "concentration": 0.85, "big_pharma_validation": 1.0, "catalyst_density": 1.05, "tech_uniqueness": 1.05, "dilution_overhang": 1.0, "cash_floor": 1.0}, "TYRA": {"crowding": 0.9, "financing": 1.0, "dilution": 1.0, "insider": 1.0, "mgmt": 1.0, "acquisition": 1.05, "moat": 1.0, "failures": 1.0, "concentration": 1.0, "big_pharma_validation": 1.0, "catalyst_density": 1.05, "tech_uniqueness": 1.05, "dilution_overhang": 1.0, "cash_floor": 1.0}, "UPB": {"crowding": 0.9, "financing": 1.0, "dilution": 0.97, "insider": 1.0, "mgmt": 1.0, "acquisition": 1.05, "moat": 0.95, "failures": 0.9, "concentration": 0.95, "big_pharma_validation": 1.0, "catalyst_density": 1.0, "tech_uniqueness": 1.0, "dilution_overhang": 0.97, "cash_floor": 1.03}, "URGN": {"crowding": 0.9, "financing": 0.85, "dilution": 0.97, "insider": 1.0, "mgmt": 1.0, "acquisition": 1.05, "moat": 1.0, "failures": 1.0, "concentration": 0.95, "big_pharma_validation": 1.0, "catalyst_density": 1.05, "tech_uniqueness": 1.0, "dilution_overhang": 1.0, "cash_floor": 1.0}, "VCEL": {"crowding": 0.95, "financing": 1.0, "dilution": 1.0, "insider": 1.0, "mgmt": 1.05, "acquisition": 1.05, "moat": 1.05, "failures": 1.0, "concentration": 0.95, "big_pharma_validation": 1.0, "catalyst_density": 0.95, "tech_uniqueness": 1.05, "dilution_overhang": 1.0, "cash_floor": 1.0}, "VERA": {"crowding": 0.9, "financing": 1.0, "dilution": 0.97, "insider": 1.1, "mgmt": 1.05, "acquisition": 1.05, "moat": 1.0, "failures": 1.0, "concentration": 0.85, "big_pharma_validation": 1.0, "catalyst_density": 1.0, "tech_uniqueness": 1.0, "dilution_overhang": 1.0, "cash_floor": 1.0}, "VIR": {"crowding": 0.95, "financing": 0.95, "dilution": 0.93, "insider": 1.0, "mgmt": 1.05, "acquisition": 1.0, "moat": 1.0, "failures": 1.0, "concentration": 0.95, "big_pharma_validation": 1.05, "catalyst_density": 1.1, "tech_uniqueness": 1.05, "dilution_overhang": 1.0, "cash_floor": 1.0}, "VOR": {"crowding": 0.9, "financing": 1.0, "dilution": 0.93, "insider": 1.0, "mgmt": 1.0, "acquisition": 1.05, "moat": 1.0, "failures": 0.9, "concentration": 0.95, "big_pharma_validation": 1.0, "catalyst_density": 1.0, "tech_uniqueness": 1.0, "dilution_overhang": 1.0, "cash_floor": 1.03}, "VRDN": {"crowding": 0.8, "financing": 1.0, "dilution": 1.0, "insider": 1.0, "mgmt": 1.0, "acquisition": 1.05, "moat": 1.0, "failures": 0.9, "concentration": 0.95, "big_pharma_validation": 1.0, "catalyst_density": 1.05, "tech_uniqueness": 1.0, "dilution_overhang": 1.0, "cash_floor": 1.03}, "VSTM": {"crowding": 0.95, "financing": 0.95, "dilution": 0.97, "insider": 1.0, "mgmt": 1.0, "acquisition": 1.05, "moat": 1.0, "failures": 0.9, "concentration": 0.95, "big_pharma_validation": 1.0, "catalyst_density": 1.0, "tech_uniqueness": 1.0, "dilution_overhang": 0.93, "cash_floor": 1.0}, "VTVT": {"crowding": 0.95, "financing": 1.0, "dilution": 0.97, "insider": 1.0, "mgmt": 0.9, "acquisition": 1.0, "moat": 1.0, "failures": 1.0, "concentration": 0.85, "big_pharma_validation": 1.0, "catalyst_density": 1.05, "tech_uniqueness": 1.05, "dilution_overhang": 1.0, "cash_floor": 1.0}, "VYGR": {"crowding": 1.0, "financing": 1.0, "dilution": 0.97, "insider": 1.0, "mgmt": 1.0, "acquisition": 1.05, "moat": 1.0, "failures": 1.0, "concentration": 1.0, "big_pharma_validation": 1.1, "catalyst_density": 1.1, "tech_uniqueness": 1.05, "dilution_overhang": 0.88, "cash_floor": 1.03}, "WHWK": {"crowding": 0.95, "financing": 1.0, "dilution": 1.0, "insider": 1.0, "mgmt": 1.0, "acquisition": 1.0, "moat": 0.95, "failures": 1.0, "concentration": 1.0, "big_pharma_validation": 1.0, "catalyst_density": 1.05, "tech_uniqueness": 1.0, "dilution_overhang": 1.0, "cash_floor": 1.03}, "WVE": {"crowding": 0.95, "financing": 1.0, "dilution": 0.97, "insider": 1.1, "mgmt": 1.0, "acquisition": 1.05, "moat": 1.0, "failures": 0.9, "concentration": 0.95, "big_pharma_validation": 1.05, "catalyst_density": 1.05, "tech_uniqueness": 1.05, "dilution_overhang": 1.0, "cash_floor": 1.0}, "XERS": {"crowding": 0.9, "financing": 1.0, "dilution": 1.0, "insider": 1.0, "mgmt": 1.05, "acquisition": 1.05, "moat": 1.0, "failures": 1.0, "concentration": 0.95, "big_pharma_validation": 1.05, "catalyst_density": 0.95, "tech_uniqueness": 1.0, "dilution_overhang": 1.0, "cash_floor": 1.0}, "XFOR": {"crowding": 0.95, "financing": 1.0, "dilution": 1.0, "insider": 1.0, "mgmt": 1.0, "acquisition": 1.05, "moat": 1.0, "failures": 1.0, "concentration": 0.95, "big_pharma_validation": 1.0, "catalyst_density": 1.1, "tech_uniqueness": 1.05, "dilution_overhang": 1.0, "cash_floor": 1.0}, "XNCR": {"crowding": 0.9, "financing": 1.0, "dilution": 1.0, "insider": 1.0, "mgmt": 1.0, "acquisition": 1.05, "moat": 1.05, "failures": 1.0, "concentration": 1.0, "big_pharma_validation": 1.1, "catalyst_density": 1.1, "tech_uniqueness": 1.05, "dilution_overhang": 1.0, "cash_floor": 1.03}, "XOMA": {"crowding": 1.0, "financing": 1.0, "dilution": 1.0, "insider": 1.1, "mgmt": 1.0, "acquisition": 1.1, "moat": 1.0, "failures": 1.0, "concentration": 0.85, "big_pharma_validation": 1.0, "catalyst_density": 1.0, "tech_uniqueness": 1.0, "dilution_overhang": 1.0, "cash_floor": 1.0}, "ZBIO": {"crowding": 0.9, "financing": 1.0, "dilution": 0.93, "insider": 1.1, "mgmt": 1.05, "acquisition": 1.05, "moat": 1.0, "failures": 1.0, "concentration": 0.95, "big_pharma_validation": 1.05, "catalyst_density": 1.1, "tech_uniqueness": 1.0, "dilution_overhang": 0.97, "cash_floor": 1.0}, "ZLAB": {"crowding": 0.9, "financing": 1.0, "dilution": 1.0, "insider": 1.1, "mgmt": 1.05, "acquisition": 1.0, "moat": 1.0, "failures": 0.9, "concentration": 0.95, "big_pharma_validation": 1.1, "catalyst_density": 1.05, "tech_uniqueness": 1.0, "dilution_overhang": 1.0, "cash_floor": 1.0}, "ZNTL": {"crowding": 0.95, "financing": 1.0, "dilution": 1.0, "insider": 1.1, "mgmt": 1.0, "acquisition": 1.0, "moat": 0.95, "failures": 1.0, "concentration": 0.85, "big_pharma_validation": 1.05, "catalyst_density": 1.05, "tech_uniqueness": 1.0, "dilution_overhang": 1.0, "cash_floor": 1.03}, "ZURA": {"crowding": 0.9, "financing": 1.0, "dilution": 0.97, "insider": 1.1, "mgmt": 0.9, "acquisition": 1.05, "moat": 0.95, "failures": 1.0, "concentration": 0.95, "big_pharma_validation": 1.1, "catalyst_density": 1.05, "tech_uniqueness": 1.0, "dilution_overhang": 0.97, "cash_floor": 1.0}, "ZYME": {"crowding": 0.9, "financing": 1.0, "dilution": 0.97, "insider": 1.0, "mgmt": 1.05, "acquisition": 1.05, "moat": 1.05, "failures": 0.9, "concentration": 0.95, "big_pharma_validation": 1.05, "catalyst_density": 1.1, "tech_uniqueness": 1.05, "dilution_overhang": 1.0, "cash_floor": 1.0}}};
const CURRENT_WEIGHTS = Object.assign({}, MODIFIER_DATA.weights || {});

// ─────────────────────────────────────────────────────────────────
// Expand-arrow click handler: toggle the matching detail row.
// ─────────────────────────────────────────────────────────────────
document.querySelectorAll('.expand-toggle').forEach(btn => {
  btn.addEventListener('click', e => {
    e.stopPropagation();
    const target = document.getElementById(btn.dataset.target);
    if (!target) return;
    const isOpen = target.style.display !== 'none';
    target.style.display = isOpen ? 'none' : 'table-row';
    btn.innerHTML = isOpen ? '&#9654;' : '&#9660;';
    btn.classList.toggle('open', !isOpen);
  });
});

// ─────────────────────────────────────────────────────────────────
// D48 — selection checkboxes (master + per-row + count badge + save)
// ─────────────────────────────────────────────────────────────────
const master = document.getElementById('select-all');
const rowCheckboxes = () => Array.from(
  document.querySelectorAll('input[name="ticker_select"]')
);
const countBadge = document.getElementById('selected-count');
const toast = document.getElementById('toolbar-toast');

function showToast(msg, isError) {
  toast.textContent = msg;
  toast.classList.toggle('error', !!isError);
  toast.classList.add('show');
  setTimeout(() => toast.classList.remove('show'), 4000);
}

function syncMasterAndCount() {
  const cbs = rowCheckboxes();
  const total = cbs.length;
  const checked = cbs.filter(c => c.checked).length;
  countBadge.textContent = checked + ' of ' + total;
  countBadge.classList.toggle('partial', checked > 0 && checked < total);
  countBadge.classList.toggle('empty',   checked === 0);
  if (master) {
    if (checked === 0)            { master.checked = false; master.indeterminate = false; }
    else if (checked === total)   { master.checked = true;  master.indeterminate = false; }
    else                          { master.checked = false; master.indeterminate = true;  }
  }
}

// ─────────────────────────────────────────────────────────────────
// D49 — silent auto-save via the local HTTP server (sidecar JSON).
// On every checkbox toggle, PUT /selection/<quarter> with the current
// state. The server (scripts/6_serve_report.py) writes the sidecar
// JSON atomically. No file-picker dialog, no permission prompt —
// browser security is satisfied because we're talking to a same-origin
// HTTP endpoint, not the local filesystem.
//
// When the page is opened directly via file:// (no server), auto-save
// is disabled and a banner tells the user how to enable it.
// ─────────────────────────────────────────────────────────────────
const SERVER_MODE = (
  window.location.protocol === 'http:' ||
  window.location.protocol === 'https:'
);
const SELECTION_URL = '/selection/' + encodeURIComponent(QUARTER);
let pendingSave = null;          // debounce timer
let firstSaveSeen = false;       // one-shot warning gate for file://
const SAVE_DEBOUNCE_MS = 300;

function selectionPayload() {
  const cbs = rowCheckboxes();
  return {
    schema_version: 2,
    quarter: QUARTER,
    all_tickers:      cbs.map(c => c.value),
    selected_tickers: cbs.filter(c => c.checked).map(c => c.value),
    // D50 — slider state. Server preserves verbatim into the sidecar JSON;
    // the renderer reads it back on next load.
    modifier_weights: typeof CURRENT_WEIGHTS === 'undefined' ? {} : CURRENT_WEIGHTS,
  };
}

async function autoSave() {
  pendingSave = null;
  const checked = rowCheckboxes().filter(c => c.checked).length;
  if (!SERVER_MODE) {
    if (!firstSaveSeen) {
      firstSaveSeen = true;
      showToast(
        'Auto-save disabled — open via the local server '
        + '(python scripts/6_serve_report.py).',
        true,
      );
    }
    return;
  }
  try {
    const res = await fetch(SELECTION_URL, {
      method:  'PUT',
      headers: {'Content-Type': 'application/json'},
      body:    JSON.stringify(selectionPayload()),
    });
    if (!res.ok) throw new Error('HTTP ' + res.status);
    showToast('Auto-saved (' + checked + ' selected).');
  } catch (err) {
    console.error(err);
    showToast('Auto-save failed: ' + err.message
              + ' — is the local server running?', true);
  }
}

function scheduleAutoSave() {
  if (pendingSave) clearTimeout(pendingSave);
  pendingSave = setTimeout(autoSave, SAVE_DEBOUNCE_MS);
}

function onSelectionChange() {
  syncMasterAndCount();
  scheduleAutoSave();
}

if (master) {
  master.addEventListener('change', () => {
    rowCheckboxes().forEach(c => { c.checked = master.checked; });
    onSelectionChange();
  });
}
rowCheckboxes().forEach(c => {
  c.addEventListener('change', onSelectionChange);
});
syncMasterAndCount();

document.getElementById('copy-cli').addEventListener('click', async () => {
  try {
    await navigator.clipboard.writeText(CLI_COMMAND);
    showToast('Copied: ' + CLI_COMMAND);
  } catch (err) {
    showToast('Copy failed — command: ' + CLI_COMMAND, true);
  }
});
document.getElementById('copy-serve').addEventListener('click', async () => {
  try {
    await navigator.clipboard.writeText(SERVE_COMMAND);
    showToast('Copied: ' + SERVE_COMMAND);
  } catch (err) {
    showToast('Copy failed — command: ' + SERVE_COMMAND, true);
  }
});

// Show the file:// warning banner when the page is NOT served by the HTTP
// server. The banner explains how to start the server for auto-save.
if (!SERVER_MODE) {
  const banner = document.getElementById('serve-banner');
  if (banner) banner.style.display = '';
}

// ─────────────────────────────────────────────────────────────────
// On page load, when SERVER_MODE, fetch the live sidecar JSON and
// reconcile both checkbox state and slider weights with whatever the
// server has right now. The embedded MODIFIER_DATA / `checked` attrs
// are snapshots from render time and may be stale relative to a sidecar
// the user (or another tab) has updated since. Without this, the FIRST
// user interaction would PUT the stale HTML state, clobbering the live
// sidecar — bug observed 2026-04-25.
// ─────────────────────────────────────────────────────────────────
async function syncFromSidecar() {
  if (!SERVER_MODE) return;
  let data;
  try {
    const res = await fetch(SELECTION_URL, {cache: 'no-store'});
    if (!res.ok) return;
    data = await res.json();
  } catch (err) {
    console.warn('syncFromSidecar fetch failed:', err);
    return;
  }
  let weightsChanged = false;
  if (data && data.modifier_weights && typeof MODIFIER_DATA !== 'undefined') {
    for (const c of MODIFIER_DATA.components) {
      const v = data.modifier_weights[c];
      if (typeof v !== 'number') continue;
      if (CURRENT_WEIGHTS[c] === v) continue;
      CURRENT_WEIGHTS[c] = v;
      const rowEl = document.querySelector(
        '.mw-row[data-component="' + c + '"]'
      );
      if (rowEl) {
        const slider = rowEl.querySelector('.mw-slider');
        const valueEl = rowEl.querySelector('.mw-value');
        if (slider) slider.value = String(v);
        if (valueEl) valueEl.textContent = v.toFixed(2);
        markRowDirty(rowEl, v);
      }
      weightsChanged = true;
    }
  }
  let selectionChanged = false;
  if (data && Array.isArray(data.selected_tickers)) {
    const wanted = new Set(data.selected_tickers);
    rowCheckboxes().forEach(cb => {
      const want = wanted.has(cb.value);
      if (cb.checked !== want) {
        cb.checked = want;
        selectionChanged = true;
      }
    });
    if (selectionChanged) syncMasterAndCount();
  }
  if (weightsChanged) recomputeAndRerank();
}
syncFromSidecar();

// ─────────────────────────────────────────────────────────────────
// D47 / D50 — modifier weight sliders + live per-row recompute + re-rank.
// Mirrors module_6b.modifiers.apply_weights bit-identically.
// ─────────────────────────────────────────────────────────────────
function effectiveFactor(model, weight) {
  return 1.0 + weight * (model - 1.0);
}
function modifierFor(ticker) {
  const tf = (MODIFIER_DATA.ticker_factors || {})[ticker];
  if (!tf) return 1.0;
  let p = 1.0;
  for (const c of MODIFIER_DATA.components) {
    const m = (c in tf) ? tf[c] : 1.0;
    const w = (c in CURRENT_WEIGHTS) ? CURRENT_WEIGHTS[c] : 1.0;
    p *= effectiveFactor(m, w);
  }
  const b = MODIFIER_DATA.bounds || {min: 0.5, max: 1.5};
  return Math.max(b.min, Math.min(b.max, p));
}
function modifierClass(m) {
  if (m < 0.85) return 'mod_strong_neg';
  if (m < 0.95) return 'mod_neg';
  if (m > 1.20) return 'mod_pos';
  if (m > 1.05) return 'mod_mild_pos';
  return 'mod_neutral';
}
function recomputeAndRerank(opts) {
  opts = opts || {};
  const tbody = document.querySelector('#rank tbody');
  if (!tbody) return;
  const mains = Array.from(tbody.querySelectorAll('.main-row'));
  for (const row of mains) {
    const t = row.dataset.ticker;
    const base = parseFloat(row.dataset.baseScore || '0');
    const m = modifierFor(t);
    const adj = m * base;
    row.dataset.adjusted = adj.toFixed(6);
    const modCell = row.querySelector('.modifier-cell');
    const adjCell = row.querySelector('.adjusted-cell');
    if (modCell) {
      modCell.textContent = m.toFixed(3);
      modCell.className = 'modifier-cell ' + modifierClass(m);
    }
    if (adjCell) {
      adjCell.textContent = isFinite(adj) ? adj.toFixed(2) : '';
    }
  }
  // Re-sort by adjusted desc (the canonical ranking key now), update the
  // rank cell, and re-place rows in the DOM keeping main+detail pairs.
  mains.sort((a, b) => parseFloat(b.dataset.adjusted) - parseFloat(a.dataset.adjusted));
  mains.forEach((row, idx) => {
    const rankCell = row.querySelector('.rank-cell');
    if (rankCell) rankCell.textContent = (idx + 1);
    tbody.appendChild(row);
    const detail = tbody.querySelector(
      '.detail-row[data-for="' + row.dataset.ticker.replace(/"/g, '\\"') + '"]'
    );
    if (detail) tbody.appendChild(detail);
  });
}

// Hamburger drawer open/close (only present when modifier panel was rendered).
const mwToggle  = document.getElementById('mw-toggle');
const mwDrawer  = document.getElementById('mw-drawer');
const mwOverlay = document.getElementById('mw-overlay');
const mwClose   = document.getElementById('mw-close');
function openDrawer() {
  if (!mwDrawer) return;
  mwDrawer.style.display = '';  mwDrawer.setAttribute('aria-hidden', 'false');
  mwOverlay.style.display = ''; mwOverlay.setAttribute('aria-hidden', 'false');
}
function closeDrawer() {
  if (!mwDrawer) return;
  mwDrawer.style.display = 'none';  mwDrawer.setAttribute('aria-hidden', 'true');
  mwOverlay.style.display = 'none'; mwOverlay.setAttribute('aria-hidden', 'true');
}
if (mwToggle)  mwToggle.addEventListener('click', openDrawer);
if (mwClose)   mwClose.addEventListener('click', closeDrawer);
if (mwOverlay) mwOverlay.addEventListener('click', closeDrawer);
window.addEventListener('keydown', e => {
  if (e.key === 'Escape' && mwDrawer && mwDrawer.style.display !== 'none') closeDrawer();
});

// Slider wiring: input → recompute → debounce-save.
function markRowDirty(rowEl, weight) {
  const dft = (MODIFIER_DATA.weights_ui && MODIFIER_DATA.weights_ui.default) || 1.0;
  const dirty = Math.abs(weight - dft) > 1e-9;
  rowEl.classList.toggle('dirty', dirty);
}
function setSliderWeight(component, weight, opts) {
  opts = opts || {};
  CURRENT_WEIGHTS[component] = weight;
  const rowEl = document.querySelector(
    '.mw-row[data-component="' + component + '"]'
  );
  if (rowEl) {
    const slider = rowEl.querySelector('.mw-slider');
    const valueEl = rowEl.querySelector('.mw-value');
    if (slider) slider.value = String(weight);
    if (valueEl) valueEl.textContent = weight.toFixed(2);
    markRowDirty(rowEl, weight);
  }
  if (!opts.silent) {
    recomputeAndRerank();
    scheduleAutoSave();   // weights ride along on the same PUT as selection
  }
}
document.querySelectorAll('.mw-row').forEach(rowEl => {
  const component = rowEl.dataset.component;
  const slider = rowEl.querySelector('.mw-slider');
  const valueEl = rowEl.querySelector('.mw-value');
  const resetBtn = rowEl.querySelector('.mw-reset');
  if (slider) {
    slider.addEventListener('input', () => {
      const w = parseFloat(slider.value);
      if (valueEl) valueEl.textContent = w.toFixed(2);
      markRowDirty(rowEl, w);
      CURRENT_WEIGHTS[component] = w;
      recomputeAndRerank();
    });
    slider.addEventListener('change', scheduleAutoSave);   // commit on release
  }
  if (resetBtn) {
    resetBtn.addEventListener('click', () => {
      const dft = (MODIFIER_DATA.weights_ui && MODIFIER_DATA.weights_ui.default) || 1.0;
      setSliderWeight(component, dft);
    });
  }
  // Initialise dirty class from the seeded weight value.
  markRowDirty(rowEl, parseFloat(slider ? slider.value : '1.0'));
});
const resetAllBtn = document.getElementById('mw-reset-all');
if (resetAllBtn) {
  resetAllBtn.addEventListener('click', () => {
    const dft = (MODIFIER_DATA.weights_ui && MODIFIER_DATA.weights_ui.default) || 1.0;
    for (const c of MODIFIER_DATA.components) {
      setSliderWeight(c, dft, {silent: true});
    }
    recomputeAndRerank();
    scheduleAutoSave();
  });
}
const disableAllBtn = document.getElementById('mw-disable-all');
if (disableAllBtn) {
  disableAllBtn.addEventListener('click', () => {
    // weight=0 → effective_factor = 1 + 0 × (model − 1) = 1.0 for every
    // component → modifier ≡ 1.0 → ranking reverts to raw score.
    const minW = (MODIFIER_DATA.weights_ui && MODIFIER_DATA.weights_ui.min) ?? 0.0;
    for (const c of MODIFIER_DATA.components) {
      setSliderWeight(c, minW, {silent: true});
    }
    recomputeAndRerank();
    scheduleAutoSave();
  });
}

// ─────────────────────────────────────────────────────────────────
// Sort: only iterate over .main-row; for each placed main row, also
// re-place its associated .detail-row right after it so they stay paired.
// ─────────────────────────────────────────────────────────────────
document.querySelectorAll('#rank thead th:not(.no-sort)').forEach(th => {
  th.addEventListener('click', () => {
    const tbody = th.closest('table').tBodies[0];
    const idx = +th.dataset.col;
    const asc = th.dataset.dir !== 'asc';
    th.dataset.dir = asc ? 'asc' : 'desc';
    const mains = Array.from(tbody.querySelectorAll('.main-row'));
    mains.sort((a, b) => {
      const av = a.cells[idx].innerText.replace(/[^-0-9.]/g, '');
      const bv = b.cells[idx].innerText.replace(/[^-0-9.]/g, '');
      const an = parseFloat(av), bn = parseFloat(bv);
      if (!isNaN(an) && !isNaN(bn)) return asc ? an - bn : bn - an;
      return asc ? a.cells[idx].innerText.localeCompare(b.cells[idx].innerText)
                 : b.cells[idx].innerText.localeCompare(a.cells[idx].innerText);
    });
    mains.forEach(main => {
      tbody.appendChild(main);
      const detail = tbody.querySelector(
        '.detail-row[data-for="' + main.dataset.ticker.replace(/"/g, '\\"') + '"]'
      );
      if (detail) tbody.appendChild(detail);
    });
  });
});

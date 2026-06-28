"""platform_discoverer — the "Acrivon-pattern" listed-biotech screener.

Importable package for 6_Biotech_platform_discoverer (the dir name starts with a digit, so the
package is renamed, matching ``hype_parser`` <-> ``5_Hype_parser``). Run scripts with
``PYTHONPATH=src`` per repo convention.

The cardinal rule (spec 0.2): a company is only ever DELETED for ``mktcap_out_of_band`` or
``not_live``; every other narrowing is a flag + audit row. Enforced in code by ``store.Store``.
"""

SCHEMA_VERSION = 1

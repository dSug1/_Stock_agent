"""Stage-3 Claude scoring: evidence bundle + rubric/schema (`rubric`), cost estimate (`cost`), and the
identity hashes that gate re-scoring (`identity`). The network dispatch lives in
`clients/anthropic_client.py`; this package is pure and offline-testable."""

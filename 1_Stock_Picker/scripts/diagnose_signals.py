from database.db import get_connection

conn = get_connection()

run_date = conn.execute(
    'SELECT MAX(run_date) FROM twos_scores'
).fetchone()[0]

print('=== Tier 1A/1B new_position signal distribution by market value ===')
# market_value is stored in raw USD (not thousands).
_M = 1_000_000
buckets = [
    (0,        1 * _M,    '$0-$1M      '),
    (1 * _M,   5 * _M,    '$1M-$5M     '),
    (5 * _M,   10 * _M,   '$5M-$10M    '),
    (10 * _M,  25 * _M,   '$10M-$25M   '),
    (25 * _M,  50 * _M,   '$25M-$50M   '),
    (50 * _M,  10**15,    '$50M+       '),
]

# Find all Tier 1A/1B new positions in most recent filings
new_positions = conn.execute('''
    SELECT ih.ticker, ih.market_value, i.name, i.tier
    FROM institution_holdings ih
    JOIN institutions i ON ih.institution_id = i.id
    WHERE i.tier IN ('1A', '1B')
    AND ih.ticker IS NOT NULL
    AND ih.filing_date = (
        SELECT MAX(ih2.filing_date)
        FROM institution_holdings ih2
        WHERE ih2.institution_id = ih.institution_id
        AND ih2.filing_date <= ?
    )
    AND NOT EXISTS (
        SELECT 1 FROM institution_holdings ih3
        WHERE ih3.institution_id = ih.institution_id
        AND ih3.ticker = ih.ticker
        AND ih3.filing_date < ih.filing_date
    )
''', (run_date,)).fetchall()

print(f'Total Tier 1A/1B new positions: {len(new_positions)}')
print()

for lo, hi, label in buckets:
    count = sum(
        1 for r in new_positions
        if lo <= (r['market_value'] or 0) < hi
    )
    tickers = set(
        r['ticker'] for r in new_positions
        if lo <= (r['market_value'] or 0) < hi
    )
    print(f'  {label}  positions:{count:4}  '
          f'unique tickers:{len(tickers):4}')

print()
print('=== At different minimum market_value thresholds ===')
print('=== how many tickers would fire the signal ===')

for threshold_m in [0, 1, 2, 5, 10, 25]:
    threshold = threshold_m * _M
    qualifying = set(
        r['ticker'] for r in new_positions
        if (r['market_value'] or 0) >= threshold
    )
    print(f'  market_value >= ${threshold_m}M  '
          f'-> {len(qualifying):4} tickers fire signal')

conn.close()
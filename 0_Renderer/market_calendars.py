"""
market_calendars.py
Exchange trading schedules and holiday calendars for major global markets.

Holiday accuracy by region:
  Full algorithmic  : US, GB, EU (Euronext), DE (XETRA), CH (SIX), CA, AU
  Approximate*      : JP, HK, CN, IN, KR, SG, TW, BR, MX
  (* lunar / religious / government-announced holidays are omitted for these
     regions; install `exchange_calendars` for full production accuracy.)
"""

import datetime
import functools

# ── Exchange schedules ────────────────────────────────────────────────────────
# Keys: yfinance info["exchange"] codes.
# open / close : (hour, minute) in the exchange's LOCAL timezone.
# break_start  : (hour, minute) start of midday trading halt, or omitted.
# break_end    : (hour, minute) end   of midday trading halt, or omitted.
# holidays     : region key consumed by get_holidays().

EXCHANGE_SCHEDULES: dict[str, dict] = {
    # ── United States ─────────────────────────────────────────────────────────
    "NYQ": dict(tz="America/New_York",    open=(9,30),  close=(16, 0), name="NYSE",               holidays="US"),
    "NMS": dict(tz="America/New_York",    open=(9,30),  close=(16, 0), name="NASDAQ",             holidays="US"),
    "NGM": dict(tz="America/New_York",    open=(9,30),  close=(16, 0), name="NASDAQ GM",          holidays="US"),
    "NCM": dict(tz="America/New_York",    open=(9,30),  close=(16, 0), name="NASDAQ CM",          holidays="US"),
    "ASE": dict(tz="America/New_York",    open=(9,30),  close=(16, 0), name="NYSE American",      holidays="US"),
    "PCX": dict(tz="America/New_York",    open=(9,30),  close=(16, 0), name="NYSE Arca",          holidays="US"),
    "BTS": dict(tz="America/New_York",    open=(9,30),  close=(16, 0), name="BATS US",            holidays="US"),
    "OBB": dict(tz="America/New_York",    open=(9,30),  close=(16, 0), name="OTC Bulletin Board", holidays="US"),
    "PNK": dict(tz="America/New_York",    open=(9,30),  close=(16, 0), name="OTC Pink Sheets",    holidays="US"),
    # ── Canada ────────────────────────────────────────────────────────────────
    "TOR": dict(tz="America/Toronto",     open=(9,30),  close=(16, 0), name="TSX",                holidays="CA"),
    "TSX": dict(tz="America/Toronto",     open=(9,30),  close=(16, 0), name="TSX",                holidays="CA"),
    "CVE": dict(tz="America/Toronto",     open=(9,30),  close=(16, 0), name="TSX Venture",        holidays="CA"),
    # ── United Kingdom ────────────────────────────────────────────────────────
    "LSE": dict(tz="Europe/London",       open=(8, 0),  close=(16,30), name="London SE",          holidays="GB"),
    "IOB": dict(tz="Europe/London",       open=(8, 0),  close=(16,30), name="London IOB",         holidays="GB"),
    # ── Germany ───────────────────────────────────────────────────────────────
    "FRA": dict(tz="Europe/Berlin",       open=(9, 0),  close=(17,30), name="Frankfurt / XETRA",  holidays="DE"),
    "GER": dict(tz="Europe/Berlin",       open=(9, 0),  close=(17,30), name="XETRA",              holidays="DE"),
    "EBS": dict(tz="Europe/Berlin",       open=(9, 0),  close=(17,30), name="Stuttgart",          holidays="DE"),
    # ── France ────────────────────────────────────────────────────────────────
    "PAR": dict(tz="Europe/Paris",        open=(9, 0),  close=(17,30), name="Euronext Paris",     holidays="EU"),
    # ── Netherlands ───────────────────────────────────────────────────────────
    "AMS": dict(tz="Europe/Amsterdam",    open=(9, 0),  close=(17,30), name="Euronext Amsterdam", holidays="EU"),
    # ── Belgium ───────────────────────────────────────────────────────────────
    "BRU": dict(tz="Europe/Brussels",     open=(9, 0),  close=(17,30), name="Euronext Brussels",  holidays="EU"),
    # ── Portugal ──────────────────────────────────────────────────────────────
    "LIS": dict(tz="Europe/Lisbon",       open=(8, 0),  close=(16,30), name="Euronext Lisbon",    holidays="EU"),
    # ── Italy ─────────────────────────────────────────────────────────────────
    "MIL": dict(tz="Europe/Rome",         open=(9, 0),  close=(17,30), name="Euronext Milan",     holidays="EU"),
    "BIT": dict(tz="Europe/Rome",         open=(9, 0),  close=(17,30), name="Borsa Italiana",     holidays="EU"),
    # ── Spain ─────────────────────────────────────────────────────────────────
    "MCE": dict(tz="Europe/Madrid",       open=(9, 0),  close=(17,30), name="BME Madrid",         holidays="EU"),
    "MAD": dict(tz="Europe/Madrid",       open=(9, 0),  close=(17,30), name="Bolsa Madrid",       holidays="EU"),
    # ── Switzerland ───────────────────────────────────────────────────────────
    "ZRH": dict(tz="Europe/Zurich",       open=(9, 0),  close=(17,30), name="SIX Swiss",          holidays="CH"),
    "VTX": dict(tz="Europe/Zurich",       open=(9, 0),  close=(17,30), name="SIX Swiss",          holidays="CH"),
    # ── Sweden ────────────────────────────────────────────────────────────────
    "STO": dict(tz="Europe/Stockholm",    open=(9, 0),  close=(17,30), name="Nasdaq Stockholm",   holidays="EU"),
    # ── Norway ────────────────────────────────────────────────────────────────
    "OSL": dict(tz="Europe/Oslo",         open=(9, 0),  close=(16,20), name="Oslo Børs",          holidays="EU"),
    # ── Denmark ───────────────────────────────────────────────────────────────
    "CPH": dict(tz="Europe/Copenhagen",   open=(9, 0),  close=(17, 0), name="Nasdaq Copenhagen",  holidays="EU"),
    # ── Finland ───────────────────────────────────────────────────────────────
    "HEL": dict(tz="Europe/Helsinki",     open=(10,0),  close=(18,30), name="Nasdaq Helsinki",    holidays="EU"),
    # ── Austria ───────────────────────────────────────────────────────────────
    "VIE": dict(tz="Europe/Vienna",       open=(9, 0),  close=(17,30), name="Vienna SE",          holidays="EU"),
    # ── Australia ─────────────────────────────────────────────────────────────
    "ASX": dict(tz="Australia/Sydney",    open=(10,0),  close=(16, 0), name="ASX",                holidays="AU"),
    # ── Japan (lunch break 11:30–12:30 JST) ──────────────────────────────────
    "TYO": dict(tz="Asia/Tokyo",          open=(9, 0),  close=(15,30), name="Tokyo SE",           holidays="JP",
                break_start=(11,30), break_end=(12,30)),
    "OSA": dict(tz="Asia/Tokyo",          open=(9, 0),  close=(15,30), name="Osaka SE",           holidays="JP",
                break_start=(11,30), break_end=(12,30)),
    # ── Hong Kong (lunch break 12:00–13:00 HKT) ──────────────────────────────
    "HKG": dict(tz="Asia/Hong_Kong",      open=(9,30),  close=(16, 0), name="HKEX",               holidays="HK",
                break_start=(12,0),  break_end=(13,0)),
    # ── China (lunch break 11:30–13:00 CST) ──────────────────────────────────
    "SHH": dict(tz="Asia/Shanghai",       open=(9,30),  close=(15, 0), name="Shanghai SE",        holidays="CN",
                break_start=(11,30), break_end=(13,0)),
    "SHZ": dict(tz="Asia/Shanghai",       open=(9,30),  close=(15, 0), name="Shenzhen SE",        holidays="CN",
                break_start=(11,30), break_end=(13,0)),
    # ── India ─────────────────────────────────────────────────────────────────
    "NSI": dict(tz="Asia/Kolkata",        open=(9,15),  close=(15,30), name="NSE India",          holidays="IN"),
    "BSE": dict(tz="Asia/Kolkata",        open=(9,15),  close=(15,30), name="BSE India",          holidays="IN"),
    # ── South Korea ───────────────────────────────────────────────────────────
    "KSC": dict(tz="Asia/Seoul",          open=(9, 0),  close=(15,30), name="KRX KOSPI",          holidays="KR"),
    "KOE": dict(tz="Asia/Seoul",          open=(9, 0),  close=(15,30), name="KOSDAQ",             holidays="KR"),
    # ── Singapore (lunch break 12:00–13:00 SGT) ──────────────────────────────
    "SGX": dict(tz="Asia/Singapore",      open=(9, 0),  close=(17, 0), name="SGX",                holidays="SG",
                break_start=(12,0),  break_end=(13,0)),
    # ── Taiwan ────────────────────────────────────────────────────────────────
    "TAI": dict(tz="Asia/Taipei",         open=(9, 0),  close=(13,30), name="TWSE",               holidays="TW"),
    "TWO": dict(tz="Asia/Taipei",         open=(9, 0),  close=(13,30), name="TPEx",               holidays="TW"),
    # ── Brazil ────────────────────────────────────────────────────────────────
    "SAO": dict(tz="America/Sao_Paulo",   open=(10,0),  close=(17,55), name="B3 / Bovespa",       holidays="BR"),
    # ── Mexico ────────────────────────────────────────────────────────────────
    "MEX": dict(tz="America/Mexico_City", open=(8,30),  close=(15, 0), name="BMV Mexico",         holidays="MX"),
}

DEFAULT_EXCHANGE = "NYQ"

# Regions where lunar / religious holidays are NOT computed (approximate only)
APPROXIMATE_REGIONS: frozenset[str] = frozenset({"JP", "HK", "CN", "IN", "KR", "SG", "TW", "BR", "MX"})


# ── Holiday helpers ───────────────────────────────────────────────────────────

def _d(y, m, d): return datetime.date(y, m, d)


@functools.lru_cache(maxsize=64)
def _easter(year: int) -> datetime.date:
    """Easter Sunday — Anonymous Gregorian algorithm."""
    a = year % 19
    b, c = divmod(year, 100)
    d, e = divmod(b, 4)
    f    = (b + 8) // 25
    g    = (b - f + 1) // 3
    h    = (19*a + b - d - g + 15) % 30
    i, k = divmod(c, 4)
    l    = (32 + 2*e + 2*i - h - k) % 7
    m    = (a + 11*h + 22*l) // 451
    mo, dy = divmod(h + l - 7*m + 114, 31)
    return datetime.date(year, mo, dy + 1)


def _nth_weekday(year: int, month: int, n: int, weekday: int) -> datetime.date:
    """
    n-th occurrence of weekday (0=Mon … 6=Sun) in month/year.
    n=1 → first, n=-1 → last.
    """
    if n > 0:
        first = _d(year, month, 1)
        delta = (weekday - first.weekday()) % 7
        return first + datetime.timedelta(days=delta + (n - 1) * 7)
    else:
        # last occurrence
        nxt = month % 12 + 1
        yr2 = year + (1 if month == 12 else 0)
        last = _d(yr2, nxt, 1) - datetime.timedelta(days=1)
        delta = (last.weekday() - weekday) % 7
        return last - datetime.timedelta(days=delta + (-n - 1) * 7)


def _adj(d: datetime.date) -> datetime.date:
    """Saturday → previous Friday, Sunday → next Monday (for fixed-date holidays)."""
    if d.weekday() == 5: return d - datetime.timedelta(days=1)
    if d.weekday() == 6: return d + datetime.timedelta(days=1)
    return d


# ── Holiday sets by region ────────────────────────────────────────────────────

@functools.lru_cache(maxsize=32)
def _us_holidays(year: int) -> frozenset:
    """NYSE / NASDAQ holidays."""
    e = _easter(year)
    h = {
        _adj(_d(year,  1,  1)),              # New Year's Day
        _nth_weekday(year, 1, 3, 0),         # MLK Day (3rd Mon Jan)
        _nth_weekday(year, 2, 3, 0),         # Presidents' Day (3rd Mon Feb)
        e - datetime.timedelta(days=2),      # Good Friday
        _nth_weekday(year, 5, -1, 0),        # Memorial Day (last Mon May)
        _adj(_d(year,  7,  4)),              # Independence Day
        _nth_weekday(year, 9,  1, 0),        # Labor Day (1st Mon Sep)
        _nth_weekday(year, 11, 4, 3),        # Thanksgiving (4th Thu Nov)
        _adj(_d(year, 12, 25)),              # Christmas
    }
    if year >= 2022:
        h.add(_adj(_d(year, 6, 19)))         # Juneteenth (since 2022)
    # If Jan 1 next year falls on Sunday, NYSE observes Dec 31 this year
    if _d(year + 1, 1, 1).weekday() == 6:
        h.add(_d(year, 12, 31))
    return frozenset(h)


@functools.lru_cache(maxsize=32)
def _gb_holidays(year: int) -> frozenset:
    """LSE / London SE."""
    e = _easter(year)
    h = {
        _adj(_d(year,  1,  1)),              # New Year's Day
        e - datetime.timedelta(days=2),      # Good Friday
        e + datetime.timedelta(days=1),      # Easter Monday
        _nth_weekday(year, 5, 1, 0),         # Early May Bank Holiday (1st Mon May)
        _nth_weekday(year, 5, -1, 0),        # Spring Bank Holiday (last Mon May)
        _nth_weekday(year, 8, -1, 0),        # Summer Bank Holiday (last Mon Aug)
        _adj(_d(year, 12, 25)),              # Christmas
        _adj(_d(year, 12, 26)),              # Boxing Day
    }
    return frozenset(h)


@functools.lru_cache(maxsize=32)
def _eu_holidays(year: int) -> frozenset:
    """Euronext shared holidays (Paris, Amsterdam, Brussels, Lisbon, Milan, Nordic)."""
    e = _easter(year)
    return frozenset({
        _d(year, 1, 1),                      # New Year's Day
        e - datetime.timedelta(days=2),      # Good Friday
        e + datetime.timedelta(days=1),      # Easter Monday
        _d(year, 5, 1),                      # Labour Day
        _adj(_d(year, 12, 25)),              # Christmas
        _adj(_d(year, 12, 26)),              # Boxing Day / St Stephen's
    })


@functools.lru_cache(maxsize=32)
def _de_holidays(year: int) -> frozenset:
    """Deutsche Börse / XETRA (EU core + German Unity Day)."""
    return frozenset(_eu_holidays(year) | {_d(year, 10, 3)})  # German Unity Day


@functools.lru_cache(maxsize=32)
def _ch_holidays(year: int) -> frozenset:
    """SIX Swiss Exchange."""
    e = _easter(year)
    return frozenset(_eu_holidays(year) | {
        _d(year, 1, 2),                      # Berchtoldstag
        e + datetime.timedelta(days=39),     # Ascension Thursday
        _d(year, 8, 1),                      # Swiss National Day
    })


@functools.lru_cache(maxsize=32)
def _ca_holidays(year: int) -> frozenset:
    """TSX (Ontario base)."""
    e = _easter(year)
    # Victoria Day: last Monday on or before May 25
    vic = _d(year, 5, 25)
    while vic.weekday() != 0:
        vic -= datetime.timedelta(days=1)
    return frozenset({
        _adj(_d(year,  1,  1)),              # New Year's Day
        _nth_weekday(year, 2, 3, 0),         # Family Day (3rd Mon Feb, ON)
        e - datetime.timedelta(days=2),      # Good Friday
        vic,                                 # Victoria Day
        _adj(_d(year,  7,  1)),              # Canada Day
        _nth_weekday(year, 8,  1, 0),        # Civic Holiday (1st Mon Aug)
        _nth_weekday(year, 9,  1, 0),        # Labour Day (1st Mon Sep)
        _nth_weekday(year, 10, 2, 0),        # Thanksgiving (2nd Mon Oct)
        _adj(_d(year, 12, 25)),              # Christmas
        _adj(_d(year, 12, 26)),              # Boxing Day
    })


@functools.lru_cache(maxsize=32)
def _au_holidays(year: int) -> frozenset:
    """ASX (NSW/national holidays)."""
    e = _easter(year)
    return frozenset({
        _adj(_d(year,  1,  1)),              # New Year's Day
        _adj(_d(year,  1, 26)),              # Australia Day
        e - datetime.timedelta(days=2),      # Good Friday
        e - datetime.timedelta(days=1),      # Easter Saturday
        e + datetime.timedelta(days=1),      # Easter Monday
        _adj(_d(year,  4, 25)),              # ANZAC Day
        _nth_weekday(year, 6, 2, 0),         # King's Birthday (2nd Mon Jun, NSW)
        _nth_weekday(year, 8, 1, 0),         # Bank Holiday (1st Mon Aug, NSW)
        _nth_weekday(year, 10, 1, 0),        # Labour Day (1st Mon Oct, ACT/NSW)
        _adj(_d(year, 12, 25)),              # Christmas
        _adj(_d(year, 12, 26)),              # Boxing Day
    })


# ── Approximate regions (lunar / govt-announced holidays omitted) ─────────────

@functools.lru_cache(maxsize=32)
def _jp_holidays(year: int) -> frozenset:
    """TSE — approximate. Lunar / equinox / substitute rules simplified."""
    h = set()
    for mo, dy in [(1,1),(2,11),(2,23),(4,29),(5,3),(5,4),(5,5),(8,11),(11,3),(11,23)]:
        h.add(_adj(_d(year, mo, dy)))
    # Exchange closed Jan 2–3 and Dec 31 by custom
    h.update({_d(year,1,2), _d(year,1,3), _d(year,12,31)})
    # Moveable holidays
    h.add(_nth_weekday(year, 1, 2, 0))       # Coming of Age Day
    h.add(_nth_weekday(year, 7, 3, 0))       # Marine Day
    h.add(_nth_weekday(year, 9, 3, 0))       # Respect for the Aged Day
    h.add(_nth_weekday(year, 10, 2, 0))      # Sports Day
    # Approximate equinoxes (exact date varies ±1 day by year)
    h.update({_d(year,3,20), _d(year,9,23)})
    return frozenset(h)


@functools.lru_cache(maxsize=32)
def _hk_holidays(year: int) -> frozenset:
    """HKEX — approximate (Lunar New Year & other lunar holidays omitted)."""
    e = _easter(year)
    return frozenset({
        _d(year, 1, 1),
        e - datetime.timedelta(days=2),      # Good Friday
        e - datetime.timedelta(days=1),      # Holy Saturday (HKEX closed)
        e + datetime.timedelta(days=1),      # Easter Monday
        _d(year, 5, 1),                      # Labour Day
        _adj(_d(year, 7, 1)),                # Establishment Day
        _adj(_d(year, 10, 1)),               # National Day
        _adj(_d(year, 12, 25)),              # Christmas
        _adj(_d(year, 12, 26)),              # Boxing Day
    })


@functools.lru_cache(maxsize=32)
def _cn_holidays(year: int) -> frozenset:
    """SSE / SZSE — fixed holidays only (Golden Week Oct 1–7; lunar weeks omitted)."""
    h = {_d(year, 1, 1), _d(year, 5, 1)}
    for day in range(1, 8):                  # National Day Golden Week
        h.add(_d(year, 10, day))
    return frozenset(h)


@functools.lru_cache(maxsize=32)
def _in_holidays(year: int) -> frozenset:
    """NSE / BSE — secular public holidays only (religious/lunar holidays omitted)."""
    return frozenset({
        _d(year, 1, 26),                     # Republic Day
        _d(year, 8, 15),                     # Independence Day
        _d(year, 10,  2),                    # Gandhi Jayanti
        _adj(_d(year, 12, 25)),              # Christmas
    })


@functools.lru_cache(maxsize=32)
def _kr_holidays(year: int) -> frozenset:
    """KRX — fixed holidays only (Seollal / Chuseok omitted)."""
    return frozenset({
        _d(year,  1,  1),                    # New Year's Day
        _d(year,  3,  1),                    # Independence Movement Day
        _d(year,  5,  5),                    # Children's Day
        _d(year,  6,  6),                    # Memorial Day
        _d(year,  8, 15),                    # Liberation Day
        _d(year, 10,  3),                    # National Foundation Day
        _d(year, 10,  9),                    # Hangul Day
        _adj(_d(year, 12, 25)),              # Christmas
        _d(year, 12, 31),                    # Year-end close
    })


@functools.lru_cache(maxsize=32)
def _sg_holidays(year: int) -> frozenset:
    """SGX — fixed / Christian holidays only (Chinese New Year, Hari Raya, Deepavali omitted)."""
    e = _easter(year)
    return frozenset({
        _d(year, 1, 1),
        e - datetime.timedelta(days=2),      # Good Friday
        _d(year, 5, 1),                      # Labour Day
        _adj(_d(year, 8, 9)),                # National Day
        _adj(_d(year, 12, 25)),              # Christmas
    })


@functools.lru_cache(maxsize=32)
def _br_holidays(year: int) -> frozenset:
    """B3 / Bovespa — approximate."""
    e = _easter(year)
    return frozenset({
        _d(year,  1,  1),
        e - datetime.timedelta(days=48),     # Carnival Monday
        e - datetime.timedelta(days=47),     # Carnival Tuesday
        e - datetime.timedelta(days=2),      # Good Friday (Paixão)
        _d(year,  4, 21),                    # Tiradentes
        _d(year,  5,  1),                    # Labour Day
        _d(year,  9,  7),                    # Independence Day
        _d(year, 10, 12),                    # Our Lady of Aparecida
        _d(year, 11,  2),                    # All Souls' Day
        _d(year, 11, 15),                    # Proclamation of the Republic
        _adj(_d(year, 12, 25)),              # Christmas
        _d(year, 12, 31),                    # Year-end close
    })


@functools.lru_cache(maxsize=32)
def _mx_holidays(year: int) -> frozenset:
    """BMV — approximate."""
    return frozenset({
        _d(year,  1,  1),                    # New Year's
        _nth_weekday(year, 2, 1, 0),         # Constitution Day (1st Mon Feb)
        _nth_weekday(year, 3, 3, 0),         # Benito Juárez Day (3rd Mon Mar)
        _d(year,  5,  1),                    # Labour Day
        _adj(_d(year, 9, 16)),               # Independence Day
        _nth_weekday(year, 11, 3, 0),        # Revolution Day (3rd Mon Nov)
        _adj(_d(year, 12, 25)),              # Christmas
    })


@functools.lru_cache(maxsize=32)
def _tw_holidays(year: int) -> frozenset:
    """TWSE — fixed holidays only (Chinese New Year / lunar holidays omitted)."""
    return frozenset({
        _d(year,  1,  1),                    # New Year's / Founding Day
        _d(year,  2, 28),                    # Peace Memorial Day
        _d(year,  4,  4),                    # Children's Day
        _adj(_d(year, 10, 10)),              # National Day
    })


# ── Region dispatcher ─────────────────────────────────────────────────────────

_HOLIDAY_FN = {
    "US": _us_holidays,
    "GB": _gb_holidays,
    "EU": _eu_holidays,
    "DE": _de_holidays,
    "CH": _ch_holidays,
    "CA": _ca_holidays,
    "AU": _au_holidays,
    "JP": _jp_holidays,
    "HK": _hk_holidays,
    "CN": _cn_holidays,
    "IN": _in_holidays,
    "KR": _kr_holidays,
    "SG": _sg_holidays,
    "BR": _br_holidays,
    "MX": _mx_holidays,
    "TW": _tw_holidays,
}


def get_holidays(region: str, year: int) -> frozenset:
    fn = _HOLIDAY_FN.get(region)
    return fn(year) if fn else frozenset()


# ── Public API ────────────────────────────────────────────────────────────────

def get_schedule(exchange_code: str) -> dict:
    """Return the schedule dict for a yfinance exchange code, defaulting to NYSE."""
    return EXCHANGE_SCHEDULES.get(
        exchange_code,
        EXCHANGE_SCHEDULES[DEFAULT_EXCHANGE]
    )


def schedule_for_json(exchange_code: str) -> dict:
    """
    Return a JSON-serialisable version of the schedule for the browser.
    Replaces tuple (h,m) fields with [h,m] lists; drops the holidays key.
    """
    s = get_schedule(exchange_code)
    out = {
        "code":  exchange_code,
        "name":  s["name"],
        "tz":    s["tz"],
        "open":  list(s["open"]),
        "close": list(s["close"]),
        "break_start": list(s["break_start"]) if "break_start" in s else None,
        "break_end":   list(s["break_end"])   if "break_end"   in s else None,
        "approximate": s.get("holidays", "US") in APPROXIMATE_REGIONS,
    }
    return out


def is_exchange_open(exchange_code: str, now_utc: "datetime.datetime") -> bool:
    """
    Return True if the exchange is open for regular trading at the given UTC moment.
    now_utc must be a timezone-naive UTC datetime.
    """
    import pytz
    s    = get_schedule(exchange_code)
    tz   = pytz.timezone(s["tz"])
    now  = now_utc.replace(tzinfo=pytz.utc).astimezone(tz)

    # Weekend
    if now.weekday() >= 5:
        return False

    # Holiday
    region = s.get("holidays", "US")
    if now.date() in get_holidays(region, now.year):
        return False

    # Trading hours
    total   = now.hour * 60 + now.minute
    open_m  = s["open"][0]  * 60 + s["open"][1]
    close_m = s["close"][0] * 60 + s["close"][1]
    if total < open_m or total >= close_m:
        return False

    # Lunch break
    bs = s.get("break_start")
    be = s.get("break_end")
    if bs and be and (bs[0] * 60 + bs[1]) <= total < (be[0] * 60 + be[1]):
        return False

    return True

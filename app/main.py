from fastapi import FastAPI, Form, HTTPException, Query
from fastapi.responses import HTMLResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import (
    create_engine, Column, Integer, String, DateTime, Text, Boolean,
    Float, UniqueConstraint, select, desc, func
)
from sqlalchemy.orm import declarative_base, sessionmaker
from datetime import datetime, timedelta, timezone, date
from zoneinfo import ZoneInfo
from collections import Counter, defaultdict
from contextlib import asynccontextmanager
from apscheduler.schedulers.background import BackgroundScheduler
from bs4 import BeautifulSoup
import httpx, json, math, os, re, hashlib, threading, time

APP_NAME = "OJO-99 Omega V5 Red Propia"
DR_TZ = ZoneInfo("America/Santo_Domingo")

DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./data/ojo99.db")
if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql+psycopg://", 1)
elif DATABASE_URL.startswith("postgresql://"):
    DATABASE_URL = DATABASE_URL.replace("postgresql://", "postgresql+psycopg://", 1)

connect_args = {"check_same_thread": False} if DATABASE_URL.startswith("sqlite") else {}
engine = create_engine(DATABASE_URL, pool_pre_ping=True, connect_args=connect_args)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)
Base = declarative_base()

NETWORK_COLLECTOR_ENABLED = os.getenv("NETWORK_COLLECTOR_ENABLED", "true").lower() == "true"
CURRENT_SYNC_MINUTES = max(5, int(os.getenv("CURRENT_SYNC_MINUTES", "15")))
HISTORY_SYNC_MINUTES = max(30, int(os.getenv("HISTORY_SYNC_MINUTES", "45")))
SAFE_MIN_SECONDS_BETWEEN_REQUESTS = max(5, int(os.getenv("SAFE_MIN_SECONDS_BETWEEN_REQUESTS", "20")))
SAFE_BACKOFF_BASE_MINUTES = max(5, int(os.getenv("SAFE_BACKOFF_BASE_MINUTES", "30")))
SAFE_BACKOFF_MAX_MINUTES = max(SAFE_BACKOFF_BASE_MINUTES, int(os.getenv("SAFE_BACKOFF_MAX_MINUTES", "360")))

SOURCE_PRIMARY_URL = os.getenv("SOURCE_PRIMARY_URL", "https://loterianacional.com.do/resultados/").strip()
SOURCE_SECONDARY_URL = os.getenv("SOURCE_SECONDARY_URL", "").strip()

LOTTERY_SCHEDULES = [
    {"name":"Anguilla Mañana","display":"Anguila 10:00 AM","time":"10:00","numbers":3,"group":"Anguila"},
    {"name":"Anguilla Medio Día","display":"Anguila 1:00 PM","time":"13:00","numbers":3,"group":"Anguila"},
    {"name":"Anguilla Tarde","display":"Anguila 6:00 PM","time":"18:00","numbers":3,"group":"Anguila"},
    {"name":"Anguilla Noche","display":"Anguila 9:00 PM","time":"21:00","numbers":3,"group":"Anguila"},

    {"name":"La Primera Día","display":"La Primera 12:00 PM","time":"12:00","numbers":3,"group":"La Primera"},
    {"name":"La Primera Noche","display":"La Primera Noche","time":"20:00","numbers":3,"group":"La Primera"},

    {"name":"La Suerte","display":"La Suerte 12:30 PM","time":"12:30","numbers":3,"group":"La Suerte"},
    {"name":"La Suerte Tarde","display":"La Suerte 6:00 PM","time":"18:00","numbers":3,"group":"La Suerte"},

    {"name":"Quiniela Real","display":"Real 1:00 PM","time":"13:00","numbers":3,"group":"Real"},
    {"name":"Loto Pool (Real)","display":"Loto Pool Real","time":"13:00","numbers":4,"group":"Real"},

    {"name":"Quiniela Lotedom","display":"LoteDom","time":"13:55","numbers":3,"group":"LoteDom"},

    {"name":"Florida Día","display":"Florida Día 1:30 PM","time":"13:30","numbers":3,"group":"Florida"},
    {"name":"Florida Noche","display":"Florida Noche 9:45 PM","time":"21:45","numbers":3,"group":"Florida"},

    {"name":"Nueva York Día","display":"New York Día 2:30 PM","time":"14:30","numbers":3,"group":"New York"},
    {"name":"Nueva York Noche","display":"New York Noche 10:30 PM","time":"22:30","numbers":3,"group":"New York"},

    {"name":"Gana Más","display":"Gana Más 2:30 PM","time":"14:30","numbers":3,"group":"Nacional"},
    {"name":"Juega + Pega +","display":"Juega + Pega +","time":"14:30","numbers":5,"group":"Nacional"},
    {"name":"Lotería Nacional","display":"Nacional Noche","time":"21:00","numbers":3,"group":"Nacional"},
    {"name":"Pega 3 Más","display":"Pega 3 Más","time":"20:55","numbers":3,"group":"Leidsa"},

    {"name":"Quiniela Loteka","display":"Loteka 7:55 PM","time":"19:55","numbers":3,"group":"Loteka"},
    {"name":"Quiniela Leidsa","display":"Quiniela Leidsa 8:55 PM","time":"20:55","numbers":3,"group":"Leidsa"},
    {"name":"Loto Pool (Leidsa)","display":"Loto Pool Leidsa","time":"20:55","numbers":5,"group":"Leidsa"},
    {"name":"Super Kino TV","display":"Super Kino TV 8:55 PM","time":"20:55","numbers":20,"group":"Kino TV"},

    {"name":"King Lottery","display":"King Lottery","time":"12:30","numbers":3,"group":"King Lottery"},
]

KNOWN_GAMES = [(x["name"], x["numbers"]) for x in LOTTERY_SCHEDULES]
GAME_MAP = {name.lower():(name, count) for name, count in KNOWN_GAMES}
SCHEDULE_MAP = {x["name"]:x for x in LOTTERY_SCHEDULES}

DATE_RE = re.compile(r"^\d{1,2}[/-]\d{1,2}[/-]\d{4}$")
TIME_RE = re.compile(r"^\d{1,2}:\d{2}\s*(?:AM|PM)$", re.I)
NUM_RE = re.compile(r"^\d{1,2}$")

collector_lock = threading.Lock()
_last_request_monotonic = 0.0


class SourceRegistry(Base):
    __tablename__ = "source_registry_v5"
    id = Column(Integer, primary_key=True)
    key = Column(String(80), unique=True, nullable=False, index=True)
    url = Column(Text, nullable=False)
    enabled = Column(Boolean, default=True)
    trust = Column(Float, default=0.5)
    state = Column(String(30), default="HEALTHY")
    consecutive_failures = Column(Integer, default=0)
    pause_until = Column(DateTime(timezone=True))
    last_http_status = Column(Integer)
    last_error = Column(Text)
    last_success_at = Column(DateTime(timezone=True))
    updated_at = Column(DateTime(timezone=True), default=lambda:datetime.now(timezone.utc))


class RawObservation(Base):
    __tablename__ = "raw_observations_v5"
    id = Column(Integer, primary_key=True)
    source_key = Column(String(80), nullable=False, index=True)
    lottery = Column(String(160), nullable=False, index=True)
    draw_time = Column(DateTime(timezone=True), nullable=False, index=True)
    numbers_json = Column(Text, nullable=False)
    fingerprint = Column(String(64), unique=True, nullable=False, index=True)
    observed_at = Column(DateTime(timezone=True), default=lambda:datetime.now(timezone.utc))
    source_url = Column(Text)


class CanonicalDraw(Base):
    __tablename__ = "canonical_draws_v5"
    id = Column(Integer, primary_key=True)
    lottery = Column(String(160), nullable=False, index=True)
    draw_time = Column(DateTime(timezone=True), nullable=False, index=True)
    numbers_json = Column(Text, nullable=False)
    confidence = Column(Float, default=0.0)
    verification_state = Column(String(30), default="PROVISIONAL")  # VERIFIED/PROVISIONAL/CONFLICT
    sources_json = Column(Text, nullable=False, default="[]")
    updated_at = Column(DateTime(timezone=True), default=lambda:datetime.now(timezone.utc))
    __table_args__ = (
        UniqueConstraint("lottery", "draw_time", name="uq_canonical_v5_lottery_time"),
    )


class DateCache(Base):
    __tablename__ = "date_cache_v5"
    id = Column(Integer, primary_key=True)
    source_key = Column(String(80), nullable=False, index=True)
    date_label = Column(String(10), nullable=False, index=True)
    status = Column(String(30), nullable=False)
    rows_found = Column(Integer, default=0)
    checked_at = Column(DateTime(timezone=True), default=lambda:datetime.now(timezone.utc))
    message = Column(Text)
    __table_args__ = (
        UniqueConstraint("source_key", "date_label", name="uq_date_cache_v5"),
    )


class HistoricalCursor(Base):
    __tablename__ = "historical_cursor_v5"
    id = Column(Integer, primary_key=True)
    key = Column(String(80), unique=True, nullable=False)
    cursor_date = Column(String(10), nullable=False)
    target_date = Column(String(10), nullable=False)
    finished = Column(Boolean, default=False)


class Prediction(Base):
    __tablename__ = "predictions_v5"
    id = Column(Integer, primary_key=True)
    lottery = Column(String(160), nullable=False, index=True)
    draw_time = Column(DateTime(timezone=True), nullable=False, index=True)
    created_at = Column(DateTime(timezone=True), default=lambda:datetime.now(timezone.utc))
    top_numbers_json = Column(Text, nullable=False, default="[]")
    top_pairs_json = Column(Text, nullable=False, default="[]")
    top_triples_json = Column(Text, nullable=False, default="[]")
    master_json = Column(Text)
    evaluated = Column(Boolean, default=False)
    result_json = Column(Text)
    hit_top5_numbers = Column(Boolean, default=False)
    hit_2_of_3 = Column(Boolean, default=False)
    hit_top5_pairs = Column(Boolean, default=False)
    hit_top5_triples = Column(Boolean, default=False)
    hit_master = Column(Boolean, default=False)
    __table_args__ = (
        UniqueConstraint("lottery", "draw_time", name="uq_prediction_v5_lottery_time"),
    )


Base.metadata.create_all(engine)


def seed_sources():
    with SessionLocal() as db:
        existing = {x.key for x in db.scalars(select(SourceRegistry)).all()}
        if SOURCE_PRIMARY_URL and "primary" not in existing:
            db.add(SourceRegistry(key="primary", url=SOURCE_PRIMARY_URL, enabled=True, trust=0.65))
        if SOURCE_SECONDARY_URL and "secondary" not in existing:
            db.add(SourceRegistry(key="secondary", url=SOURCE_SECONDARY_URL, enabled=True, trust=0.65))
        db.commit()


def valid_nums(nums):
    return bool(nums) and all(isinstance(n, int) and 0 <= n <= 99 for n in nums)


def nums_of(d):
    return [int(x) for x in json.loads(d.numbers_json)]


def reverse_num(n):
    return int(f"{n:02d}"[::-1])


def dr_local_to_utc(date_s, time_s):
    dd, mm, yyyy = [int(x) for x in re.split(r"[/-]", date_s)]
    t = datetime.strptime(time_s.upper().replace("  ", " "), "%I:%M %p")
    local = datetime(yyyy, mm, dd, t.hour, t.minute, tzinfo=DR_TZ)
    return local.astimezone(timezone.utc)


def respectful_wait():
    global _last_request_monotonic
    now = time.monotonic()
    elapsed = now - _last_request_monotonic
    if elapsed < SAFE_MIN_SECONDS_BETWEEN_REQUESTS:
        time.sleep(SAFE_MIN_SECONDS_BETWEEN_REQUESTS - elapsed)
    _last_request_monotonic = time.monotonic()


def extract_public_results(html):
    soup = BeautifulSoup(html, "html.parser")
    lines = [
        re.sub(r"\s+", " ", x).strip()
        for x in soup.get_text("\n", strip=True).splitlines()
        if x.strip()
    ]
    out = []
    for i, line in enumerate(lines):
        key = line.lower()
        if key not in GAME_MAP:
            continue
        game, expected = GAME_MAP[key]
        window = lines[i+1:min(i+55, len(lines))]
        date_s = next((x for x in window if DATE_RE.match(x)), None)
        time_s = next((x for x in window if TIME_RE.match(x)), None)
        if not date_s or not time_s:
            continue
        start = max(window.index(date_s), window.index(time_s)) + 1
        nums = []
        for x in window[start:]:
            if x.lower() in GAME_MAP:
                break
            if NUM_RE.match(x):
                v = int(x)
                if 0 <= v <= 99:
                    nums.append(v)
                if len(nums) == expected:
                    break
        if len(nums) != expected:
            continue
        try:
            dt = dr_local_to_utc(date_s, time_s)
        except Exception:
            continue
        out.append((game, dt, nums))
    seen, clean = set(), []
    for row in out:
        k = (row[0], row[1].isoformat(), tuple(row[2]))
        if k not in seen:
            seen.add(k)
            clean.append(row)
    return clean


def source_paused(src):
    return bool(src.pause_until and src.pause_until > datetime.now(timezone.utc))


def source_success(db, src, status=200):
    src.state = "HEALTHY"
    src.consecutive_failures = 0
    src.pause_until = None
    src.last_http_status = status
    src.last_error = None
    src.last_success_at = datetime.now(timezone.utc)
    src.updated_at = datetime.now(timezone.utc)
    db.commit()


def source_failure(db, src, status=None, message=""):
    src.consecutive_failures = int(src.consecutive_failures or 0) + 1
    src.last_http_status = status
    src.last_error = message[:700]
    src.updated_at = datetime.now(timezone.utc)

    if status in (403, 429):
        minutes = min(
            SAFE_BACKOFF_MAX_MINUTES,
            SAFE_BACKOFF_BASE_MINUTES * (2 ** max(0, src.consecutive_failures - 1))
        )
        src.pause_until = datetime.now(timezone.utc) + timedelta(minutes=minutes)
        src.state = "PAUSED"
    else:
        src.state = "WAITING"
    db.commit()


def fetch_source(src, url):
    if source_paused(src):
        raise RuntimeError("SOURCE_PAUSED")

    respectful_wait()
    headers = {"User-Agent":"OJO99-Omega/5.0 (low-rate verified-results-network)"}

    try:
        with httpx.Client(timeout=12, follow_redirects=True, headers=headers) as client:
            r = client.get(url)
            with SessionLocal() as db:
                current = db.scalar(select(SourceRegistry).where(SourceRegistry.key == src.key))
                if r.status_code in (403, 429):
                    source_failure(db, current, r.status_code, f"HTTP {r.status_code}")
                    raise RuntimeError(f"HTTP_{r.status_code}_PAUSED")
                r.raise_for_status()
                source_success(db, current, r.status_code)
            return extract_public_results(r.text)

    except httpx.HTTPStatusError as e:
        status = e.response.status_code if e.response is not None else None
        with SessionLocal() as db:
            current = db.scalar(select(SourceRegistry).where(SourceRegistry.key == src.key))
            source_failure(db, current, status, str(e))
        raise
    except httpx.RequestError as e:
        with SessionLocal() as db:
            current = db.scalar(select(SourceRegistry).where(SourceRegistry.key == src.key))
            source_failure(db, current, None, str(e))
        raise


def observation_fingerprint(source_key, lottery, dt, nums):
    raw = f"{source_key}|{lottery}|{dt.isoformat()}|{','.join(map(str, nums))}"
    return hashlib.sha256(raw.encode()).hexdigest()


def ingest_observation(db, source_key, lottery, dt, nums, source_url):
    if not valid_nums(nums):
        return False

    expected = SCHEDULE_MAP.get(lottery, {}).get("numbers")
    if expected is not None and len(nums) != expected:
        return False

    fp = observation_fingerprint(source_key, lottery, dt, nums)
    exists = db.scalar(select(RawObservation).where(RawObservation.fingerprint == fp))
    if exists:
        return False

    db.add(RawObservation(
        source_key=source_key,
        lottery=lottery,
        draw_time=dt,
        numbers_json=json.dumps(nums),
        fingerprint=fp,
        source_url=source_url
    ))
    db.flush()
    return True


def rebuild_canonical(db, lottery, dt):
    obs = db.scalars(
        select(RawObservation).where(
            RawObservation.lottery == lottery,
            RawObservation.draw_time == dt
        )
    ).all()

    if not obs:
        return None

    source_rows = {
        x.key:x for x in db.scalars(select(SourceRegistry)).all()
    }

    groups = defaultdict(list)
    for o in obs:
        groups[o.numbers_json].append(o)

    ranked = []
    for numbers_json, rows in groups.items():
        sources = sorted({r.source_key for r in rows})
        trust_sum = sum(float(source_rows.get(s).trust if source_rows.get(s) else 0.5) for s in sources)
        ranked.append((trust_sum, len(sources), numbers_json, sources))

    ranked.sort(reverse=True)
    best_trust, best_count, best_numbers_json, best_sources = ranked[0]

    conflict = len(ranked) > 1
    if conflict:
        state = "CONFLICT"
        confidence = min(0.49, best_trust / max(1.0, best_trust + ranked[1][0]))
    elif best_count >= 2:
        state = "VERIFIED"
        confidence = min(0.99, 0.75 + 0.10 * best_count)
    else:
        state = "PROVISIONAL"
        confidence = min(0.74, best_trust)

    row = db.scalar(
        select(CanonicalDraw).where(
            CanonicalDraw.lottery == lottery,
            CanonicalDraw.draw_time == dt
        )
    )

    if not row:
        row = CanonicalDraw(lottery=lottery, draw_time=dt)
        db.add(row)

    row.numbers_json = best_numbers_json
    row.confidence = confidence
    row.verification_state = state
    row.sources_json = json.dumps(best_sources)
    row.updated_at = datetime.now(timezone.utc)
    db.flush()

    if state != "CONFLICT":
        evaluate_prediction_if_exists(db, lottery, dt, json.loads(best_numbers_json))

    return row


def collect_current():
    seed_sources()
    summary = []
    with collector_lock:
        with SessionLocal() as db:
            sources = db.scalars(
                select(SourceRegistry).where(SourceRegistry.enabled == True)
            ).all()

        for src in sources:
            if source_paused(src):
                summary.append({"source":src.key, "status":"PAUSED", "inserted":0})
                continue
            try:
                rows = fetch_source(src, src.url)
                new_obs = 0
                with SessionLocal() as db:
                    touched = set()
                    for game, dt, nums in rows:
                        if ingest_observation(db, src.key, game, dt, nums, src.url):
                            new_obs += 1
                        touched.add((game, dt))
                    for game, dt in touched:
                        rebuild_canonical(db, game, dt)
                    db.commit()
                summary.append({"source":src.key, "status":"OK", "inserted":new_obs, "rows":len(rows)})
            except Exception as e:
                summary.append({"source":src.key, "status":"ERROR", "inserted":0, "message":str(e)[:200]})

    return {"status":"OK", "sources":summary}


def ensure_cursor(db):
    row = db.scalar(select(HistoricalCursor).where(HistoricalCursor.key == "default"))
    if row:
        return row
    today = datetime.now(DR_TZ).date()
    row = HistoricalCursor(
        key="default",
        cursor_date=(today - timedelta(days=1)).isoformat(),
        target_date=(today - timedelta(days=730)).isoformat(),
        finished=False
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def cached_date(db, source_key, day):
    return db.scalar(
        select(DateCache).where(
            DateCache.source_key == source_key,
            DateCache.date_label == day.isoformat()
        )
    )


def save_date_cache(db, source_key, day, status, rows_found=0, message=""):
    row = cached_date(db, source_key, day)
    if not row:
        row = DateCache(source_key=source_key, date_label=day.isoformat())
        db.add(row)
    row.status = status
    row.rows_found = rows_found
    row.message = message[:700]
    row.checked_at = datetime.now(timezone.utc)
    db.commit()


def collect_history_step():
    seed_sources()

    with SessionLocal() as db:
        cursor = ensure_cursor(db)
        if cursor.finished:
            return {"status":"FINISHED"}
        day = date.fromisoformat(cursor.cursor_date)
        target = date.fromisoformat(cursor.target_date)
        sources = db.scalars(select(SourceRegistry).where(SourceRegistry.enabled == True)).all()

    if day < target:
        with SessionLocal() as db:
            cursor = ensure_cursor(db)
            cursor.finished = True
            db.commit()
        return {"status":"FINISHED"}

    result = {"date":day.isoformat(), "sources":[]}

    with collector_lock:
        for src in sources:
            with SessionLocal() as db:
                c = cached_date(db, src.key, day)
            if c and c.status in ("COMPLETE", "EMPTY"):
                result["sources"].append({"source":src.key, "status":"CACHED"})
                continue
            if source_paused(src):
                result["sources"].append({"source":src.key, "status":"PAUSED"})
                continue

            label = day.strftime("%d-%m-%Y")
            sep = "&" if "?" in src.url else "?"
            url = f"{src.url}{sep}date={label}"

            try:
                rows = fetch_source(src, url)
                with SessionLocal() as db:
                    touched = set()
                    new_obs = 0
                    for game, dt, nums in rows:
                        if ingest_observation(db, src.key, game, dt, nums, url):
                            new_obs += 1
                        touched.add((game, dt))
                    for game, dt in touched:
                        rebuild_canonical(db, game, dt)
                    db.commit()
                    save_date_cache(
                        db, src.key, day,
                        "COMPLETE" if rows else "EMPTY",
                        len(rows),
                        f"new={new_obs}"
                    )
                result["sources"].append({"source":src.key, "status":"OK", "rows":len(rows)})
            except Exception as e:
                with SessionLocal() as db:
                    save_date_cache(db, src.key, day, "ERROR", 0, str(e))
                result["sources"].append({"source":src.key, "status":"ERROR", "message":str(e)[:160]})

    with SessionLocal() as db:
        cursor = ensure_cursor(db)
        cursor.cursor_date = (day - timedelta(days=1)).isoformat()
        cursor.finished = (day - timedelta(days=1)) < date.fromisoformat(cursor.target_date)
        db.commit()

    return result


def canonical_draws(db, lottery, verified_only=False):
    q = select(CanonicalDraw).where(CanonicalDraw.lottery == lottery)
    if verified_only:
        q = q.where(CanonicalDraw.verification_state == "VERIFIED")
    else:
        q = q.where(CanonicalDraw.verification_state != "CONFLICT")
    q = q.order_by(CanonicalDraw.draw_time.asc())
    return db.scalars(q).all()


def wilson_lower(successes, total, z=1.64):
    if total <= 0:
        return 0.0
    p = successes/total
    den = 1 + z*z/total
    centre = p + z*z/(2*total)
    adj = z*math.sqrt((p*(1-p)+z*z/(4*total))/total)
    return max(0.0, (centre-adj)/den)


def build_analysis(draws, limit=5):
    draws = sorted(draws, key=lambda d:d.draw_time)
    N = len(draws)
    if N < 30:
        return {
            "status":"DATOS INSUFICIENTES",
            "draw_count":N,
            "top_numbers":[],
            "top_pairs":[],
            "top_triples":[],
            "transitions":[],
            "master_play":None
        }

    flattened = [n for d in draws for n in nums_of(d)]
    counts = Counter(flattened)
    recent_draws = draws[-min(30, N):]
    recent_flat = [n for d in recent_draws for n in nums_of(d)]
    recent = Counter(recent_flat)

    last_idx = {}
    for i,d in enumerate(draws):
        for n in set(nums_of(d)):
            last_idx[n] = i

    weekday_presence = defaultdict(Counter)
    for d in draws:
        wd = d.draw_time.astimezone(DR_TZ).weekday()
        weekday_presence[wd].update(set(nums_of(d)))
    target_wd = datetime.now(DR_TZ).weekday()

    transition_counts = Counter()
    origin_counts = Counter()
    for i in range(N-1):
        a = set(nums_of(draws[i]))
        b = set(nums_of(draws[i+1]))
        for x in a:
            origin_counts[x] += 1
            for y in b:
                transition_counts[(x,y)] += 1

    last_values = set(nums_of(draws[-1]))
    transition_support = Counter()
    for x in last_values:
        for y in range(100):
            if origin_counts[x]:
                transition_support[y] += transition_counts[(x,y)] / origin_counts[x]

    top_numbers = []
    for n in range(100):
        hist = counts[n]/max(1,len(flattened))
        rec = recent[n]/max(1,len(recent_flat))
        gap = N-1-last_idx[n] if n in last_idx else N
        wd = weekday_presence[target_wd][n]/max(1,sum(weekday_presence[target_wd].values()))
        rev = reverse_num(n)
        rev_rate = counts[rev]/max(1,len(flattened))
        trans = transition_support[n]/max(1,len(last_values))
        score = (
            24*min(hist/0.01,2)/2 +
            24*min(rec/0.01,2)/2 +
            12*min(gap/50,1) +
            12*min(wd/0.01,2)/2 +
            10*min(rev_rate/0.01,2)/2 +
            18*min(trans/0.20,1)
        )
        top_numbers.append({
            "number":f"{n:02d}",
            "score":round(min(100,score),1)
        })
    top_numbers = sorted(top_numbers, key=lambda x:x["score"], reverse=True)[:limit]

    pair_counts = Counter()
    presence = Counter()
    triple_counts = Counter()
    for d in draws:
        vals = sorted(set(nums_of(d)))
        presence.update(vals)
        for i in range(len(vals)):
            for j in range(i+1,len(vals)):
                pair_counts[(vals[i],vals[j])] += 1
        for i in range(len(vals)):
            for j in range(i+1,len(vals)):
                for k in range(j+1,len(vals)):
                    triple_counts[(vals[i],vals[j],vals[k])] += 1

    pairs = []
    for (a,b),c in pair_counts.items():
        pa, pb = presence[a]/N, presence[b]/N
        expected = N*pa*pb
        lift = c/expected if expected else 0
        score = 44*min(lift/2,1)+28*min((c/N)/0.15,1)+20*min(wilson_lower(c,N)/0.08,1)+(8 if reverse_num(a)==b else 0)
        if c >= 4:
            pairs.append({
                "pair":[f"{a:02d}",f"{b:02d}"],
                "score":round(min(100,score),1),
                "together_count":c,
                "lift":round(lift,2)
            })
    pairs = sorted(pairs, key=lambda x:x["score"], reverse=True)[:limit]

    triples = []
    for t,c in triple_counts.items():
        if c >= 3:
            triples.append({
                "triple":[f"{x:02d}" for x in t],
                "score":round(min(100,25+9*c),1),
                "observed_count":c
            })
    triples = sorted(triples, key=lambda x:x["score"], reverse=True)[:limit]

    transitions = []
    for (x,y),c in transition_counts.items():
        if origin_counts[x] >= 10 and c >= 3:
            transitions.append({
                "from":f"{x:02d}",
                "to":f"{y:02d}",
                "rate":round(c/origin_counts[x],3)
            })
    transitions = sorted(transitions, key=lambda x:x["rate"], reverse=True)[:10]

    master = None
    if pairs:
        p = pairs[0]
        if N >= 250 and p["score"] >= 92 and p["together_count"] >= 10 and p["lift"] >= 1.4:
            master = {
                "status":"JUGADA MAESTRA",
                "pair":p["pair"],
                "score":p["score"],
                "reason":"Afinidad fuerte + muestra amplia + umbral estricto.",
                "disclaimer":"OJO Score es evidencia interna; no garantiza premio."
            }

    return {
        "status":"OK",
        "draw_count":N,
        "top_numbers":top_numbers,
        "top_pairs":pairs,
        "top_triples":triples,
        "transitions":transitions,
        "master_play":master
    }


def next_draw_time_utc(lottery):
    cfg = SCHEDULE_MAP.get(lottery)
    if not cfg:
        return None
    hh,mm = map(int,cfg["time"].split(":"))
    now = datetime.now(DR_TZ)
    dt = now.replace(hour=hh,minute=mm,second=0,microsecond=0)
    if dt <= now:
        dt += timedelta(days=1)
    return dt.astimezone(timezone.utc)


def freeze_prediction(lottery):
    with SessionLocal() as db:
        draws = canonical_draws(db, lottery, verified_only=False)
        a = build_analysis(draws)
        if a["draw_count"] < 30:
            return {"ok":False,"reason":"DATOS_INSUFICIENTES"}
        target = next_draw_time_utc(lottery)
        exists = db.scalar(select(Prediction).where(Prediction.lottery==lottery, Prediction.draw_time==target))
        if exists:
            return {"ok":False,"reason":"YA_CONGELADA"}
        nums = [int(x["number"]) for x in a["top_numbers"]]
        pairs = [[int(v) for v in x["pair"]] for x in a["top_pairs"]]
        triples = [[int(v) for v in x["triple"]] for x in a["top_triples"]]
        master = [int(v) for v in a["master_play"]["pair"]] if a.get("master_play") else None
        db.add(Prediction(
            lottery=lottery,
            draw_time=target,
            top_numbers_json=json.dumps(nums),
            top_pairs_json=json.dumps(pairs),
            top_triples_json=json.dumps(triples),
            master_json=json.dumps(master) if master else None
        ))
        db.commit()
        return {"ok":True,"draw_time":target,"analysis":a}


def evaluate_prediction_if_exists(db, lottery, draw_time, result):
    p = db.scalar(select(Prediction).where(Prediction.lottery==lottery, Prediction.draw_time==draw_time))
    if not p or p.evaluated:
        return False
    target = set(map(int,result))
    nums = list(map(int,json.loads(p.top_numbers_json or "[]")))
    pairs = json.loads(p.top_pairs_json or "[]")
    triples = json.loads(p.top_triples_json or "[]")
    master = json.loads(p.master_json) if p.master_json else None
    captured = len(target.intersection(nums))
    p.hit_top5_numbers = captured >= 1
    p.hit_2_of_3 = captured >= 2
    p.hit_top5_pairs = any(set(map(int,x)).issubset(target) for x in pairs)
    p.hit_top5_triples = any(set(map(int,x)).issubset(target) for x in triples)
    p.hit_master = bool(master and set(map(int,master)).issubset(target))
    p.result_json = json.dumps(list(map(int,result)))
    p.evaluated = True
    return True


def performance(lottery):
    with SessionLocal() as db:
        rows = db.scalars(select(Prediction).where(Prediction.lottery==lottery, Prediction.evaluated==True)).all()
    total = len(rows)
    def pct(n,d): return round(100*n/d,2) if d else None
    master_rows = [r for r in rows if r.master_json]
    return {
        "evaluated":total,
        "sample_status":"MUESTRA INSUFICIENTE" if total<30 else ("EN DESARROLLO" if total<100 else "UTILIZABLE"),
        "top5_numbers":pct(sum(r.hit_top5_numbers for r in rows),total),
        "two_of_three":pct(sum(r.hit_2_of_3 for r in rows),total),
        "top5_pairs":pct(sum(r.hit_top5_pairs for r in rows),total),
        "top5_triples":pct(sum(r.hit_top5_triples for r in rows),total),
        "master_hits":sum(r.hit_master for r in master_rows),
        "master_alerts":len(master_rows),
        "master_pct":pct(sum(r.hit_master for r in master_rows),len(master_rows))
    }


def walk_forward(lottery, max_tests=300):
    with SessionLocal() as db:
        draws = canonical_draws(db, lottery, verified_only=False)
    if len(draws) < 80:
        return {"status":"MUESTRA INSUFICIENTE","tests":0}

    start = max(60,len(draws)-max_tests)
    tests=h1=h2=hp=ht=0
    for idx in range(start,len(draws)):
        a = build_analysis(draws[:idx])
        if not a["top_numbers"]:
            continue
        tests += 1
        target = set(nums_of(draws[idx]))
        nums = [int(x["number"]) for x in a["top_numbers"]]
        pairs = [[int(v) for v in x["pair"]] for x in a["top_pairs"]]
        triples = [[int(v) for v in x["triple"]] for x in a["top_triples"]]
        captured = len(target.intersection(nums))
        h1 += captured >= 1
        h2 += captured >= 2
        hp += any(set(x).issubset(target) for x in pairs)
        ht += any(set(x).issubset(target) for x in triples)
    def pct(n): return round(100*n/tests,2) if tests else None
    return {
        "status":"OK" if tests>=30 else "MUESTRA INSUFICIENTE",
        "tests":tests,
        "top5_number_hit_pct":pct(h1),
        "two_of_three_pct":pct(h2),
        "top5_pair_hit_pct":pct(hp),
        "top5_triple_hit_pct":pct(ht)
    }


scheduler = BackgroundScheduler(timezone="UTC")


@asynccontextmanager
async def lifespan(app):
    seed_sources()
    if NETWORK_COLLECTOR_ENABLED and not scheduler.running:
        scheduler.add_job(
            collect_current, "interval",
            minutes=CURRENT_SYNC_MINUTES,
            id="network-current", replace_existing=True, max_instances=1, coalesce=True
        )
        scheduler.add_job(
            collect_history_step, "interval",
            minutes=HISTORY_SYNC_MINUTES,
            id="network-history", replace_existing=True, max_instances=1, coalesce=True
        )
        scheduler.start()
        collect_current()
    yield
    if scheduler.running:
        scheduler.shutdown(wait=False)


app = FastAPI(title=APP_NAME, lifespan=lifespan)
app.mount("/static", StaticFiles(directory="app/static"), name="static")


@app.get("/", response_class=HTMLResponse)
def home():
    return open("app/static/index.html", encoding="utf-8").read()


@app.get("/manifest.webmanifest")
def manifest():
    return FileResponse("app/static/manifest.webmanifest", media_type="application/manifest+json")


@app.get("/sw.js")
def sw():
    return FileResponse("app/static/sw.js", media_type="application/javascript")


@app.get("/health")
def health():
    return {"ok":True,"app":APP_NAME,"network":"OWN_API","timezone":"America/Santo_Domingo"}


# -------- NUESTRA RED / API PROPIA --------

@app.get("/api/network/status")
def network_status():
    with SessionLocal() as db:
        sources = db.scalars(select(SourceRegistry).order_by(SourceRegistry.key)).all()
        raw_count = db.scalar(select(func.count(RawObservation.id))) or 0
        canonical_count = db.scalar(select(func.count(CanonicalDraw.id))) or 0
        verified_count = db.scalar(
            select(func.count(CanonicalDraw.id)).where(CanonicalDraw.verification_state=="VERIFIED")
        ) or 0
        conflict_count = db.scalar(
            select(func.count(CanonicalDraw.id)).where(CanonicalDraw.verification_state=="CONFLICT")
        ) or 0

    return {
        "raw_observations":int(raw_count),
        "canonical_results":int(canonical_count),
        "verified_results":int(verified_count),
        "conflicts":int(conflict_count),
        "sources":[
            {
                "key":s.key,
                "enabled":s.enabled,
                "state":s.state,
                "trust":s.trust,
                "last_success_at":s.last_success_at,
                "pause_until":s.pause_until,
                "last_http_status":s.last_http_status,
                "last_error":s.last_error
            }
            for s in sources
        ]
    }


@app.get("/api/results")
def own_results(
    lottery:str|None=None,
    limit:int=Query(100, ge=1, le=1000),
    verified_only:bool=False
):
    with SessionLocal() as db:
        q = select(CanonicalDraw).order_by(desc(CanonicalDraw.draw_time))
        if lottery:
            q = q.where(CanonicalDraw.lottery==lottery)
        if verified_only:
            q = q.where(CanonicalDraw.verification_state=="VERIFIED")
        rows = db.scalars(q.limit(limit)).all()
    return {
        "results":[
            {
                "lottery":r.lottery,
                "draw_time":r.draw_time,
                "numbers":nums_of(r),
                "verification":r.verification_state,
                "confidence":r.confidence,
                "sources":json.loads(r.sources_json or "[]")
            }
            for r in rows
        ]
    }


@app.get("/api/history")
def own_history(
    lottery:str,
    limit:int=Query(500, ge=1, le=5000)
):
    with SessionLocal() as db:
        rows = db.scalars(
            select(CanonicalDraw).where(
                CanonicalDraw.lottery==lottery,
                CanonicalDraw.verification_state!="CONFLICT"
            ).order_by(desc(CanonicalDraw.draw_time)).limit(limit)
        ).all()
    return {
        "lottery":lottery,
        "count":len(rows),
        "results":[
            {
                "draw_time":r.draw_time,
                "numbers":nums_of(r),
                "verification":r.verification_state,
                "confidence":r.confidence
            }
            for r in rows
        ]
    }


@app.get("/api/lotteries")
def lotteries():
    with SessionLocal() as db:
        counts = dict(db.execute(
            select(CanonicalDraw.lottery, func.count(CanonicalDraw.id))
            .where(CanonicalDraw.verification_state!="CONFLICT")
            .group_by(CanonicalDraw.lottery)
        ).all())
    return {
        "lotteries":[
            {**x, "draw_count":int(counts.get(x["name"],0))}
            for x in LOTTERY_SCHEDULES
        ]
    }


@app.get("/api/analyze")
def analyze(lottery:str):
    if lottery not in SCHEDULE_MAP:
        raise HTTPException(404,"Lotería no registrada.")
    with SessionLocal() as db:
        draws = canonical_draws(db, lottery, verified_only=False)
    out = build_analysis(draws)
    out["lottery"] = lottery
    out["schedule"] = SCHEDULE_MAP[lottery]
    return out


@app.get("/api/performance")
def api_performance(lottery:str):
    return performance(lottery)


@app.get("/api/backtest")
def api_backtest(lottery:str, max_tests:int=300):
    return walk_forward(lottery, max(30,min(max_tests,1000)))


@app.post("/api/freeze-next")
def api_freeze_next(lottery:str=Form(...)):
    if lottery not in SCHEDULE_MAP:
        raise HTTPException(404,"Lotería no registrada.")
    return freeze_prediction(lottery)


@app.post("/api/network/sync-now")
def network_sync_now():
    return collect_current()


@app.post("/api/network/history-step")
def network_history_step():
    return collect_history_step()


@app.get("/api/network/history-status")
def history_status():
    with SessionLocal() as db:
        cur = ensure_cursor(db)
        cached = db.scalar(select(func.count(DateCache.id))) or 0
        total = db.scalar(
            select(func.count(CanonicalDraw.id)).where(CanonicalDraw.verification_state!="CONFLICT")
        ) or 0
    return {
        "cursor_date":cur.cursor_date,
        "target_date":cur.target_date,
        "finished":cur.finished,
        "cached_requests":int(cached),
        "canonical_results":int(total)
    }


@app.post("/api/draw/manual")
def manual_draw(
    lottery:str=Form(...),
    draw_time:str=Form(...),
    numbers:str=Form(...)
):
    if lottery not in SCHEDULE_MAP:
        raise HTTPException(400,"Lotería no registrada.")

    try:
        nums = [int(x) for x in re.split(r"[,\s-]+",numbers.strip()) if x]
        dt = datetime.fromisoformat(draw_time.replace("Z","+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
    except Exception:
        raise HTTPException(400,"Formato inválido.")

    if len(nums) != SCHEDULE_MAP[lottery]["numbers"] or not valid_nums(nums):
        raise HTTPException(400,"Cantidad o números inválidos.")

    # Manual entries stay provisional unless corroborated.
    with SessionLocal() as db:
        ingest_observation(db,"manual",lottery,dt,nums,"manual")
        rebuild_canonical(db,lottery,dt)
        db.commit()
    return {"ok":True}

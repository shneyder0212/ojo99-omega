from fastapi import FastAPI, Form, HTTPException
from fastapi.responses import HTMLResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import (
    create_engine, Column, Integer, String, DateTime, Text, Boolean,
    UniqueConstraint, select, desc, func
)
from sqlalchemy.orm import declarative_base, sessionmaker
from datetime import datetime, timedelta, timezone, date
from zoneinfo import ZoneInfo
from collections import Counter, defaultdict
from contextlib import asynccontextmanager
from apscheduler.schedulers.background import BackgroundScheduler
from bs4 import BeautifulSoup
import httpx, json, math, os, re, hashlib

APP_NAME = "OJO-99 Omega V4"
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

RESULTS_SOURCE_URL = os.getenv(
    "RESULTS_SOURCE_URL",
    "https://loterianacional.com.do/resultados/"
)
AUTO_SYNC_ENABLED = os.getenv("AUTO_SYNC_ENABLED", "true").lower() == "true"
AUTO_SYNC_MINUTES = max(5, int(os.getenv("AUTO_SYNC_MINUTES", "15")))
HISTORICAL_BACKFILL_ENABLED = os.getenv("HISTORICAL_BACKFILL_ENABLED", "true").lower() == "true"
HISTORICAL_DAYS_TARGET = max(30, int(os.getenv("HISTORICAL_DAYS_TARGET", "730")))
HISTORICAL_DAYS_PER_STEP = max(1, min(7, int(os.getenv("HISTORICAL_DAYS_PER_STEP", "3"))))

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


class Draw(Base):
    __tablename__ = "draws_v4"
    id = Column(Integer, primary_key=True)
    lottery = Column(String(160), nullable=False, index=True)
    draw_time = Column(DateTime(timezone=True), nullable=False, index=True)
    numbers_json = Column(Text, nullable=False)
    source = Column(String(120), nullable=False, default="manual")
    source_url = Column(Text)
    source_hash = Column(String(64), index=True)
    created_at = Column(DateTime(timezone=True), default=lambda:datetime.now(timezone.utc))
    __table_args__ = (
        UniqueConstraint("lottery", "draw_time", name="uq_draws_v4_lottery_time"),
    )


class SyncLog(Base):
    __tablename__ = "sync_log_v4"
    id = Column(Integer, primary_key=True)
    at = Column(DateTime(timezone=True), default=lambda:datetime.now(timezone.utc), index=True)
    source = Column(String(120))
    status = Column(String(30))
    inserted = Column(Integer, default=0)
    message = Column(Text)


class HistoricalState(Base):
    __tablename__ = "historical_state_v4"
    id = Column(Integer, primary_key=True)
    key = Column(String(80), unique=True, nullable=False)
    cursor_date = Column(String(10), nullable=False)
    target_date = Column(String(10), nullable=False)
    finished = Column(Boolean, default=False)
    updated_at = Column(DateTime(timezone=True), default=lambda:datetime.now(timezone.utc))


class Prediction(Base):
    __tablename__ = "predictions_v4"
    id = Column(Integer, primary_key=True)
    lottery = Column(String(160), nullable=False, index=True)
    draw_time = Column(DateTime(timezone=True), nullable=False, index=True)
    created_at = Column(DateTime(timezone=True), default=lambda:datetime.now(timezone.utc))
    top_numbers_json = Column(Text, nullable=False, default="[]")
    top_pairs_json = Column(Text, nullable=False, default="[]")
    top_triples_json = Column(Text, nullable=False, default="[]")
    master_json = Column(Text)
    evaluated = Column(Boolean, default=False, index=True)
    result_json = Column(Text)
    hit_top5_numbers = Column(Boolean, default=False)
    hit_2_of_3 = Column(Boolean, default=False)
    hit_top5_pairs = Column(Boolean, default=False)
    hit_top5_triples = Column(Boolean, default=False)
    hit_master = Column(Boolean, default=False)
    __table_args__ = (
        UniqueConstraint("lottery", "draw_time", name="uq_prediction_v4_lottery_time"),
    )


Base.metadata.create_all(engine)


def nums_of(d):
    return [int(x) for x in json.loads(d.numbers_json)]


def valid_nums(nums):
    return bool(nums) and all(isinstance(n, int) and 0 <= n <= 99 for n in nums)


def reverse_num(n):
    return int(f"{n:02d}"[::-1])


def dr_local_to_utc(date_s, time_s):
    dd, mm, yyyy = [int(x) for x in re.split(r"[/-]", date_s)]
    t = datetime.strptime(time_s.upper().replace("  ", " "), "%I:%M %p")
    local = datetime(yyyy, mm, dd, t.hour, t.minute, tzinfo=DR_TZ)
    return local.astimezone(timezone.utc)


def save_draw(db, lottery, dt, nums, source, source_url=None):
    if not valid_nums(nums):
        return False
    h = hashlib.sha256(
        (lottery + "|" + dt.isoformat() + "|" + ",".join(map(str, nums))).encode()
    ).hexdigest()
    exists = db.scalar(
        select(Draw).where(Draw.lottery == lottery, Draw.draw_time == dt)
    )
    if exists:
        return False
    db.add(Draw(
        lottery=lottery,
        draw_time=dt,
        numbers_json=json.dumps(nums),
        source=source,
        source_url=source_url,
        source_hash=h
    ))
    return True


def extract_public_results(html):
    """
    Parser conservador.
    Solo acepta bloques con:
    nombre conocido + fecha + hora + cantidad exacta esperada de números.
    Si algo es ambiguo se descarta.
    """
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


def fetch_results_url(url):
    headers = {
        "User-Agent":"OJO99-Omega/4.0 (respectful-results-reader; contact-site-owner)"
    }
    with httpx.Client(timeout=15, follow_redirects=True, headers=headers) as client:
        r = client.get(url)
        r.raise_for_status()
        return extract_public_results(r.text)


def sync_public_source():
    inserted = 0
    status = "OK"
    msg = ""
    try:
        rows = fetch_results_url(RESULTS_SOURCE_URL)
        if not rows:
            raise RuntimeError("La fuente respondió sin bloques válidos; no se guardó nada.")
        with SessionLocal() as db:
            for game, dt, nums in rows:
                if save_draw(db, game, dt, nums, "public-current", RESULTS_SOURCE_URL):
                    inserted += 1
                    evaluate_prediction_if_exists(db, game, dt, nums)
            db.add(SyncLog(
                source="public-current",
                status="OK",
                inserted=inserted,
                message=f"Leídos {len(rows)} bloques válidos."
            ))
            db.commit()
        msg = f"Sincronización correcta: {inserted} nuevos."
    except Exception as e:
        status = "ERROR"
        msg = str(e)[:700]
        with SessionLocal() as db:
            db.add(SyncLog(
                source="public-current", status="ERROR", inserted=0, message=msg
            ))
            db.commit()
    return {"status":status, "inserted":inserted, "message":msg}


def ensure_history_state(db):
    row = db.scalar(select(HistoricalState).where(HistoricalState.key == "default"))
    if row:
        return row
    today_dr = datetime.now(DR_TZ).date()
    cursor = today_dr - timedelta(days=1)
    target = today_dr - timedelta(days=HISTORICAL_DAYS_TARGET)
    row = HistoricalState(
        key="default",
        cursor_date=cursor.isoformat(),
        target_date=target.isoformat(),
        finished=False
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def historical_backfill_step():
    """
    Retrocede pocos días por ejecución para evitar abuso y timeouts.
    Usa ?date=DD-MM-YYYY. Si la fuente no soporta una fecha, la salta sin inventar.
    """
    if not HISTORICAL_BACKFILL_ENABLED:
        return {"status":"DISABLED", "inserted":0}

    inserted = 0
    checked = 0
    messages = []
    with SessionLocal() as db:
        state = ensure_history_state(db)
        if state.finished:
            return {"status":"FINISHED", "inserted":0, "cursor":state.cursor_date}

        cursor = date.fromisoformat(state.cursor_date)
        target = date.fromisoformat(state.target_date)

    for _ in range(HISTORICAL_DAYS_PER_STEP):
        if cursor < target:
            break
        checked += 1
        date_label = cursor.strftime("%d-%m-%Y")
        sep = "&" if "?" in RESULTS_SOURCE_URL else "?"
        url = f"{RESULTS_SOURCE_URL}{sep}date={date_label}"
        try:
            rows = fetch_results_url(url)
            with SessionLocal() as db:
                for game, dt, nums in rows:
                    if save_draw(db, game, dt, nums, "public-history", url):
                        inserted += 1
                db.commit()
            messages.append(f"{date_label}:{len(rows)}")
        except Exception as e:
            messages.append(f"{date_label}:error")
        cursor -= timedelta(days=1)

    with SessionLocal() as db:
        state = ensure_history_state(db)
        state.cursor_date = cursor.isoformat()
        state.finished = cursor < date.fromisoformat(state.target_date)
        state.updated_at = datetime.now(timezone.utc)
        db.add(SyncLog(
            source="historical-backfill",
            status="OK",
            inserted=inserted,
            message="; ".join(messages)[:700]
        ))
        db.commit()

    return {
        "status":"OK",
        "inserted":inserted,
        "days_checked":checked,
        "next_cursor":cursor.isoformat(),
    }


def wilson_lower(successes, total, z=1.64):
    if total <= 0:
        return 0.0
    p = successes / total
    den = 1 + z*z/total
    centre = p + z*z/(2*total)
    adj = z * math.sqrt((p*(1-p) + z*z/(4*total))/total)
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
    for i, d in enumerate(draws):
        for n in set(nums_of(d)):
            last_idx[n] = i

    weekday_presence = defaultdict(Counter)
    for d in draws:
        wd = d.draw_time.astimezone(DR_TZ).weekday()
        weekday_presence[wd].update(set(nums_of(d)))
    target_wd = datetime.now(DR_TZ).weekday()

    # transición desde el último sorteo de esta lotería
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

    avg_size = max(1.0, sum(len(nums_of(d)) for d in draws)/N)
    per_slot_base = 1/100

    top_numbers = []
    for n in range(100):
        hist_rate = counts[n] / max(1, len(flattened))
        recent_rate = recent[n] / max(1, len(recent_flat))
        gap = N-1-last_idx[n] if n in last_idx else N
        wd_rate = weekday_presence[target_wd][n] / max(1, sum(weekday_presence[target_wd].values()))
        rev = reverse_num(n)
        rev_rate = counts[rev] / max(1, len(flattened))
        trans = transition_support[n] / max(1, len(last_values))

        score = (
            24 * min(hist_rate/per_slot_base, 2)/2 +
            24 * min(recent_rate/per_slot_base, 2)/2 +
            12 * min(gap/50, 1) +
            12 * min(wd_rate/max(per_slot_base, 0.0001), 2)/2 +
            10 * min(rev_rate/per_slot_base, 2)/2 +
            18 * min(trans/0.20, 1)
        )
        top_numbers.append({
            "number":f"{n:02d}",
            "score":round(min(100, score),1),
            "evidence":{
                "historical_count":counts[n],
                "recent_count":recent[n],
                "draws_since_seen":gap,
                "reverse":f"{rev:02d}",
                "reverse_count":counts[rev],
                "transition_score":round(trans,3)
            }
        })
    top_numbers = sorted(top_numbers, key=lambda x:x["score"], reverse=True)[:limit]

    pair_counts = Counter()
    presence = Counter()
    triple_counts = Counter()
    for d in draws:
        vals = sorted(set(nums_of(d)))
        presence.update(vals)
        for i in range(len(vals)):
            for j in range(i+1, len(vals)):
                pair_counts[(vals[i], vals[j])] += 1
        # Kino tiene muchas combinaciones; limitar triples a históricos repetidos sigue siendo pesado pero manejable.
        for i in range(len(vals)):
            for j in range(i+1, len(vals)):
                for k in range(j+1, len(vals)):
                    triple_counts[(vals[i], vals[j], vals[k])] += 1

    pairs = []
    for (a,b), c in pair_counts.items():
        pa = presence[a]/N
        pb = presence[b]/N
        expected = N*pa*pb
        lift = c/expected if expected > 0 else 0
        support = c/N
        confidence = wilson_lower(c, N)
        reverse_bonus = 8 if reverse_num(a) == b else 0
        score = (
            44*min(lift/2,1) +
            28*min(support/0.15,1) +
            20*min(confidence/0.08,1) +
            reverse_bonus
        )
        if c >= 4:
            pairs.append({
                "pair":[f"{a:02d}",f"{b:02d}"],
                "score":round(min(100,score),1),
                "together_count":c,
                "lift":round(lift,2),
                "reverse_pair":reverse_num(a)==b
            })
    pairs = sorted(pairs, key=lambda x:x["score"], reverse=True)[:limit]

    triples = []
    for t, c in triple_counts.items():
        if c >= 3:
            score = min(100, 25 + 9*c)
            triples.append({
                "triple":[f"{x:02d}" for x in t],
                "score":round(score,1),
                "observed_count":c
            })
    triples = sorted(triples, key=lambda x:x["score"], reverse=True)[:limit]

    transitions = []
    for (x,y), c in transition_counts.items():
        if origin_counts[x] >= 10 and c >= 3:
            rate = c/origin_counts[x]
            transitions.append({
                "from":f"{x:02d}",
                "to":f"{y:02d}",
                "count":c,
                "rate":round(rate,3)
            })
    transitions = sorted(
        transitions, key=lambda x:(x["rate"],x["count"]), reverse=True
    )[:10]

    master = None
    if pairs:
        p = pairs[0]
        # Muy estricta y rara.
        if (
            N >= 250 and
            p["score"] >= 92 and
            p["together_count"] >= 10 and
            p["lift"] >= 1.40
        ):
            master = {
                "status":"JUGADA MAESTRA",
                "pair":p["pair"],
                "score":p["score"],
                "reason":"Afinidad fuerte + muestra amplia + lift alto + umbral estricto.",
                "disclaimer":"OJO Score es evidencia interna, no porcentaje garantizado."
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


def get_draws(db, lottery, before=None):
    q = select(Draw).where(Draw.lottery == lottery)
    if before is not None:
        q = q.where(Draw.draw_time < before)
    q = q.order_by(Draw.draw_time.asc())
    return db.scalars(q).all()


def parse_recommendations(analysis):
    nums = [int(x["number"]) for x in analysis["top_numbers"]]
    pairs = [[int(a), int(b)] for a,b in [x["pair"] for x in analysis["top_pairs"]]]
    triples = [[int(v) for v in x["triple"]] for x in analysis["top_triples"]]
    master = None
    if analysis.get("master_play"):
        master = [int(v) for v in analysis["master_play"]["pair"]]
    return nums, pairs, triples, master


def next_draw_time_utc(lottery):
    cfg = SCHEDULE_MAP.get(lottery)
    if not cfg:
        return None
    hh, mm = [int(x) for x in cfg["time"].split(":")]
    now = datetime.now(DR_TZ)
    candidate = now.replace(hour=hh, minute=mm, second=0, microsecond=0)
    if candidate <= now:
        candidate += timedelta(days=1)
    return candidate.astimezone(timezone.utc)


def freeze_next_prediction(lottery):
    with SessionLocal() as db:
        draws = get_draws(db, lottery)
        analysis = build_analysis(draws)
        if analysis["draw_count"] < 30:
            return {"ok":False, "reason":"DATOS_INSUFICIENTES"}
        draw_time = next_draw_time_utc(lottery)
        if not draw_time:
            return {"ok":False, "reason":"SIN_HORARIO"}
        existing = db.scalar(select(Prediction).where(
            Prediction.lottery == lottery,
            Prediction.draw_time == draw_time
        ))
        if existing:
            return {"ok":False, "reason":"YA_CONGELADA", "id":existing.id}
        nums, pairs, triples, master = parse_recommendations(analysis)
        row = Prediction(
            lottery=lottery,
            draw_time=draw_time,
            top_numbers_json=json.dumps(nums),
            top_pairs_json=json.dumps(pairs),
            top_triples_json=json.dumps(triples),
            master_json=json.dumps(master) if master else None
        )
        db.add(row)
        db.commit()
        db.refresh(row)
        return {"ok":True, "id":row.id, "draw_time":draw_time, "analysis":analysis}


def evaluate_prediction_if_exists(db, lottery, draw_time, result):
    p = db.scalar(select(Prediction).where(
        Prediction.lottery == lottery,
        Prediction.draw_time == draw_time
    ))
    if not p or p.evaluated:
        return False
    result_set = set(int(x) for x in result)
    nums = [int(x) for x in json.loads(p.top_numbers_json or "[]")]
    pairs = json.loads(p.top_pairs_json or "[]")
    triples = json.loads(p.top_triples_json or "[]")
    master = json.loads(p.master_json) if p.master_json else None

    captured = len(result_set.intersection(nums))
    p.hit_top5_numbers = captured >= 1
    p.hit_2_of_3 = captured >= 2
    p.hit_top5_pairs = any(set(map(int, pair)).issubset(result_set) for pair in pairs)
    p.hit_top5_triples = any(set(map(int, t)).issubset(result_set) for t in triples)
    p.hit_master = bool(master and set(map(int, master)).issubset(result_set))
    p.result_json = json.dumps([int(x) for x in result])
    p.evaluated = True
    return True


def performance_summary(lottery):
    with SessionLocal() as db:
        rows = db.scalars(select(Prediction).where(
            Prediction.lottery == lottery,
            Prediction.evaluated == True
        )).all()
    total = len(rows)
    def pct(n, d):
        return round(100*n/d, 2) if d else None
    master_rows = [r for r in rows if r.master_json]
    return {
        "lottery":lottery,
        "sample_status":"MUESTRA INSUFICIENTE" if total < 30 else ("EN DESARROLLO" if total < 100 else "UTILIZABLE"),
        "evaluated":total,
        "top5_numbers":{"hits":sum(r.hit_top5_numbers for r in rows), "pct":pct(sum(r.hit_top5_numbers for r in rows), total)},
        "two_of_three":{"hits":sum(r.hit_2_of_3 for r in rows), "pct":pct(sum(r.hit_2_of_3 for r in rows), total)},
        "top5_pairs":{"hits":sum(r.hit_top5_pairs for r in rows), "pct":pct(sum(r.hit_top5_pairs for r in rows), total)},
        "top5_triples":{"hits":sum(r.hit_top5_triples for r in rows), "pct":pct(sum(r.hit_top5_triples for r in rows), total)},
        "master":{"alerts":len(master_rows), "hits":sum(r.hit_master for r in master_rows), "pct":pct(sum(r.hit_master for r in master_rows), len(master_rows))}
    }


def walk_forward_backtest(lottery, max_tests=300):
    """
    Entrena solo con sorteos anteriores a cada objetivo.
    Nunca usa el resultado futuro para construir la predicción.
    """
    with SessionLocal() as db:
        draws = get_draws(db, lottery)
    if len(draws) < 80:
        return {
            "lottery":lottery,
            "status":"MUESTRA INSUFICIENTE",
            "tests":0
        }

    start = max(60, len(draws)-max_tests)
    tests = 0
    hit1 = hit2 = hitpair = hittriple = 0

    for idx in range(start, len(draws)):
        train = draws[:idx]
        target = set(nums_of(draws[idx]))
        a = build_analysis(train)
        if not a["top_numbers"]:
            continue
        tests += 1
        nums, pairs, triples, _ = parse_recommendations(a)
        captured = len(target.intersection(nums))
        if captured >= 1:
            hit1 += 1
        if captured >= 2:
            hit2 += 1
        if any(set(p).issubset(target) for p in pairs):
            hitpair += 1
        if any(set(t).issubset(target) for t in triples):
            hittriple += 1

    def pct(n):
        return round(100*n/tests, 2) if tests else None

    return {
        "lottery":lottery,
        "status":"OK" if tests >= 30 else "MUESTRA INSUFICIENTE",
        "tests":tests,
        "top5_number_hit_pct":pct(hit1),
        "two_of_three_pct":pct(hit2),
        "top5_pair_hit_pct":pct(hitpair),
        "top5_triple_hit_pct":pct(hittriple),
        "note":"Backtest walk-forward: cada prueba usa únicamente el pasado disponible."
    }


scheduler = BackgroundScheduler(timezone="UTC")


@asynccontextmanager
async def lifespan(app):
    if AUTO_SYNC_ENABLED and not scheduler.running:
        scheduler.add_job(
            sync_public_source,
            "interval",
            minutes=AUTO_SYNC_MINUTES,
            id="current-sync",
            replace_existing=True,
            max_instances=1,
            coalesce=True
        )
        if HISTORICAL_BACKFILL_ENABLED:
            scheduler.add_job(
                historical_backfill_step,
                "interval",
                minutes=max(30, AUTO_SYNC_MINUTES*2),
                id="history-backfill",
                replace_existing=True,
                max_instances=1,
                coalesce=True
            )
        scheduler.start()
        sync_public_source()
        if HISTORICAL_BACKFILL_ENABLED:
            historical_backfill_step()
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
    return {
        "ok":True,
        "app":APP_NAME,
        "timezone":"America/Santo_Domingo",
        "auto_sync":AUTO_SYNC_ENABLED,
        "historical_backfill":HISTORICAL_BACKFILL_ENABLED
    }


@app.get("/api/schedule")
def schedule():
    return {"timezone":"America/Santo_Domingo", "lotteries":LOTTERY_SCHEDULES}


@app.get("/api/lotteries")
def lotteries():
    with SessionLocal() as db:
        counts = dict(db.execute(
            select(Draw.lottery, func.count(Draw.id)).group_by(Draw.lottery)
        ).all())
    return {
        "lotteries":[
            {
                **item,
                "draw_count":int(counts.get(item["name"], 0)),
                "has_data":int(counts.get(item["name"], 0)) > 0
            }
            for item in LOTTERY_SCHEDULES
        ]
    }


@app.get("/api/analyze")
def api_analyze(lottery:str):
    if lottery not in SCHEDULE_MAP:
        raise HTTPException(404, "Lotería no registrada.")
    with SessionLocal() as db:
        draws = get_draws(db, lottery)
    out = build_analysis(draws)
    out["lottery"] = lottery
    out["schedule"] = SCHEDULE_MAP[lottery]
    return out


@app.get("/api/performance")
def api_performance(lottery:str):
    return performance_summary(lottery)


@app.get("/api/backtest")
def api_backtest(lottery:str, max_tests:int=300):
    return walk_forward_backtest(lottery, max(30, min(max_tests, 1000)))


@app.post("/api/freeze-next")
def api_freeze_next(lottery:str=Form(...)):
    if lottery not in SCHEDULE_MAP:
        raise HTTPException(404, "Lotería no registrada.")
    return freeze_next_prediction(lottery)


@app.post("/api/sync-now")
def api_sync_now():
    return sync_public_source()


@app.post("/api/history-step")
def api_history_step():
    return historical_backfill_step()


@app.get("/api/history-status")
def api_history_status():
    with SessionLocal() as db:
        state = ensure_history_state(db)
        total = db.scalar(select(func.count(Draw.id))) or 0
    return {
        "cursor_date":state.cursor_date,
        "target_date":state.target_date,
        "finished":state.finished,
        "total_draws":int(total),
        "days_per_step":HISTORICAL_DAYS_PER_STEP
    }


@app.get("/api/sync-status")
def api_sync_status():
    with SessionLocal() as db:
        log = db.scalar(select(SyncLog).order_by(desc(SyncLog.at)).limit(1))
    if not log:
        return {"status":"NEVER"}
    return {
        "status":log.status,
        "at":log.at,
        "source":log.source,
        "inserted":log.inserted,
        "message":log.message
    }


@app.post("/api/draw")
def add_draw(
    lottery:str=Form(...),
    draw_time:str=Form(...),
    numbers:str=Form(...)
):
    if lottery not in SCHEDULE_MAP:
        raise HTTPException(400, "Lotería no registrada.")
    try:
        nums = [int(x) for x in re.split(r"[,\s-]+", numbers.strip()) if x]
        dt = datetime.fromisoformat(draw_time.replace("Z","+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
    except Exception:
        raise HTTPException(400, "Formato inválido.")
    if not valid_nums(nums):
        raise HTTPException(400, "Los números deben estar entre 00 y 99.")
    expected = SCHEDULE_MAP[lottery]["numbers"]
    if len(nums) != expected:
        raise HTTPException(400, f"{lottery} espera {expected} números.")
    with SessionLocal() as db:
        if not save_draw(db, lottery, dt, nums, "manual"):
            raise HTTPException(409, "Ese sorteo ya existe.")
        evaluate_prediction_if_exists(db, lottery, dt, nums)
        db.commit()
    return {"ok":True}

from fastapi import FastAPI, Form, HTTPException, Query, UploadFile, File
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
import httpx, json, math, os, re, hashlib, threading, time, csv, io

APP_NAME = "OJO-99 Omega V8 Arena Total"
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

COLLECTOR_ENABLED = os.getenv("COLLECTOR_ENABLED", "true").lower() == "true"
CURRENT_SYNC_MINUTES = max(10, int(os.getenv("CURRENT_SYNC_MINUTES", "20")))
HISTORY_SYNC_MINUTES = max(30, int(os.getenv("HISTORY_SYNC_MINUTES", "60")))
SAFE_MIN_SECONDS_BETWEEN_REQUESTS = max(10, int(os.getenv("SAFE_MIN_SECONDS_BETWEEN_REQUESTS", "30")))
SAFE_BACKOFF_BASE_MINUTES = max(15, int(os.getenv("SAFE_BACKOFF_BASE_MINUTES", "60")))
SAFE_BACKOFF_MAX_MINUTES = max(SAFE_BACKOFF_BASE_MINUTES, int(os.getenv("SAFE_BACKOFF_MAX_MINUTES", "720")))
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

# V8.2 — rutas históricas específicas.
# La portada /resultados/ sirve para resultados recientes, mientras que el histórico
# fiable por fecha se consulta en la página individual de cada juego.
HISTORICAL_SLUGS = {
    "Super Kino TV": "super-kino-tv",
    "Lotería Nacional": "loteria-nacional",
    "La Primera Día": "primera",
    "Quiniela Real": "real",
    "Quiniela Lotedom": "lotedom",
    "Florida Día": "florida-dia",
    "Florida Noche": "florida-noche",
    "Nueva York Día": "nueva-york-dia",
    "Nueva York Noche": "nueva-york-noche",
    "Gana Más": "gana-mas",
    "Juega + Pega +": "juega-pega",
    "Quiniela Loteka": "loteka",
    "Quiniela Leidsa": "leidsa",
    "Loto Pool (Leidsa)": "loto-pool-leidsa",
    "Loto Pool (Real)": "loto-pool-real",
    "Pega 3 Más": "pega-3-mas",
    "King Lottery": "king-lottery",
    "Anguilla Mañana": "anguilla-manana",
    "Anguilla Medio Día": "anguilla-medio-dia",
    "Anguilla Tarde": "anguilla-tarde",
    "Anguilla Noche": "anguilla-noche",
    "La Suerte": "la-suerte",
    "La Suerte Tarde": "la-suerte-tarde",
    "La Primera Noche": "primera-noche",
}

# Alias del texto público -> nombre canónico interno.
GAME_ALIASES = {
    "la primera": "La Primera Día",
    "primera": "La Primera Día",
    "lotería nacional": "Lotería Nacional",
    "loteria nacional": "Lotería Nacional",
    "super kino tv": "Super Kino TV",
    "kino tv": "Super Kino TV",
    "la suerte noche": "La Suerte Tarde",
    "king lottery noche": "King Lottery",
}

DATE_RE = re.compile(r"^\d{1,2}[/-]\d{1,2}[/-]\d{4}$")
TIME_RE = re.compile(r"^\d{1,2}:\d{2}\s*(?:AM|PM)$", re.I)
NUM_RE = re.compile(r"^\d{1,2}$")
collector_lock = threading.Lock()
_last_request_monotonic = 0.0


class SourceRegistry(Base):
    __tablename__ = "source_registry_v6"
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
    __tablename__ = "raw_observations_v6"
    id = Column(Integer, primary_key=True)
    source_key = Column(String(80), nullable=False, index=True)
    lottery = Column(String(160), nullable=False, index=True)
    draw_time = Column(DateTime(timezone=True), nullable=False, index=True)
    numbers_json = Column(Text, nullable=False)
    fingerprint = Column(String(64), unique=True, nullable=False, index=True)
    observed_at = Column(DateTime(timezone=True), default=lambda:datetime.now(timezone.utc))
    source_url = Column(Text)


class CanonicalDraw(Base):
    __tablename__ = "canonical_draws_v6"
    id = Column(Integer, primary_key=True)
    lottery = Column(String(160), nullable=False, index=True)
    draw_time = Column(DateTime(timezone=True), nullable=False, index=True)
    numbers_json = Column(Text, nullable=False)
    confidence = Column(Float, default=0.0)
    verification_state = Column(String(30), default="PROVISIONAL")
    sources_json = Column(Text, nullable=False, default="[]")
    updated_at = Column(DateTime(timezone=True), default=lambda:datetime.now(timezone.utc))
    __table_args__ = (UniqueConstraint("lottery","draw_time",name="uq_canonical_v6"),)


class DateCache(Base):
    __tablename__ = "date_cache_v6"
    id = Column(Integer, primary_key=True)
    source_key = Column(String(80), nullable=False, index=True)
    date_label = Column(String(10), nullable=False, index=True)
    status = Column(String(30), nullable=False)
    rows_found = Column(Integer, default=0)
    checked_at = Column(DateTime(timezone=True), default=lambda:datetime.now(timezone.utc))
    message = Column(Text)
    __table_args__ = (UniqueConstraint("source_key","date_label",name="uq_date_cache_v6"),)


class HistoricalCursor(Base):
    __tablename__ = "historical_cursor_v6"
    id = Column(Integer, primary_key=True)
    key = Column(String(80), unique=True, nullable=False)
    cursor_date = Column(String(10), nullable=False)
    target_date = Column(String(10), nullable=False)
    finished = Column(Boolean, default=False)


class ModelValidation(Base):
    __tablename__ = "model_validation_v6"
    id = Column(Integer, primary_key=True)
    lottery = Column(String(160), nullable=False, index=True)
    engine = Column(String(80), nullable=False)
    tests = Column(Integer, default=0)
    hits = Column(Integer, default=0)
    hit_rate = Column(Float, default=0.0)
    baseline_rate = Column(Float, default=0.0)
    lift = Column(Float, default=0.0)
    weight = Column(Float, default=0.0)
    updated_at = Column(DateTime(timezone=True), default=lambda:datetime.now(timezone.utc))
    __table_args__ = (UniqueConstraint("lottery","engine",name="uq_model_validation_v6"),)


class Prediction(Base):
    __tablename__ = "predictions_v6"
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
    __table_args__ = (UniqueConstraint("lottery","draw_time",name="uq_prediction_v6"),)


Base.metadata.create_all(engine)


def seed_sources():
    with SessionLocal() as db:
        existing={x.key for x in db.scalars(select(SourceRegistry)).all()}
        if SOURCE_PRIMARY_URL and "primary" not in existing:
            db.add(SourceRegistry(key="primary",url=SOURCE_PRIMARY_URL,enabled=True,trust=0.65))
        if SOURCE_SECONDARY_URL and "secondary" not in existing:
            db.add(SourceRegistry(key="secondary",url=SOURCE_SECONDARY_URL,enabled=True,trust=0.65))
        if "manual" not in existing:
            db.add(SourceRegistry(key="manual",url="manual://local",enabled=False,trust=0.55))
        if "csv" not in existing:
            db.add(SourceRegistry(key="csv",url="csv://local",enabled=False,trust=0.70))
        db.commit()


def valid_nums(nums): return bool(nums) and all(isinstance(n,int) and 0<=n<=99 for n in nums)
def nums_of(d): return [int(x) for x in json.loads(d.numbers_json)]
def reverse_num(n): return int(f"{n:02d}"[::-1])


def dr_local_to_utc(date_s,time_s):
    dd,mm,yyyy=[int(x) for x in re.split(r"[/-]",date_s)]
    t=datetime.strptime(time_s.upper().replace("  "," "),"%I:%M %p")
    return datetime(yyyy,mm,dd,t.hour,t.minute,tzinfo=DR_TZ).astimezone(timezone.utc)


def respectful_wait():
    global _last_request_monotonic
    now=time.monotonic()
    elapsed=now-_last_request_monotonic
    if elapsed<SAFE_MIN_SECONDS_BETWEEN_REQUESTS:
        time.sleep(SAFE_MIN_SECONDS_BETWEEN_REQUESTS-elapsed)
    _last_request_monotonic=time.monotonic()



def extract_public_results(html, expected_game=None):
    """
    Parser V8.2:
    - soporta aliases;
    - puede fijarse a una sola lotería cuando estamos en su página histórica;
    - conserva el parser general para la portada actual.
    """
    soup=BeautifulSoup(html,"html.parser")
    lines=[re.sub(r"\s+"," ",x).strip() for x in soup.get_text("\n",strip=True).splitlines() if x.strip()]
    out=[]

    def resolve_game(line):
        key=line.lower().strip()
        if key in GAME_MAP:
            return GAME_MAP[key]
        alias=GAME_ALIASES.get(key)
        if alias and alias in SCHEDULE_MAP:
            return alias, SCHEDULE_MAP[alias]["numbers"]
        return None

    if expected_game:
        expected=SCHEDULE_MAP[expected_game]["numbers"]
        # En páginas individuales el encabezado puede variar, por lo que buscamos
        # fecha/hora/números en todo el documento y usamos el juego esperado.
        for i,line in enumerate(lines):
            if not DATE_RE.match(line):
                continue
            date_s=line
            window=lines[i:min(i+90,len(lines))]
            time_s=next((x for x in window if TIME_RE.match(x)),None)
            if not time_s:
                continue
            start_idx=max(window.index(date_s),window.index(time_s))+1
            nums=[]
            for x in window[start_idx:]:
                if DATE_RE.match(x):
                    break
                if NUM_RE.match(x):
                    v=int(x)
                    if 0<=v<=99:
                        nums.append(v)
                    if len(nums)==expected:
                        break
            if len(nums)!=expected:
                continue
            try:
                dt=dr_local_to_utc(date_s,time_s)
            except Exception:
                continue
            out.append((expected_game,dt,nums))
    else:
        for i,line in enumerate(lines):
            resolved=resolve_game(line)
            if not resolved:
                continue
            game,expected=resolved
            window=lines[i+1:min(i+70,len(lines))]
            date_s=next((x for x in window if DATE_RE.match(x)),None)
            time_s=next((x for x in window if TIME_RE.match(x)),None)
            if not date_s or not time_s:
                continue
            start_idx=max(window.index(date_s),window.index(time_s))+1
            nums=[]
            for x in window[start_idx:]:
                if resolve_game(x):
                    break
                if NUM_RE.match(x):
                    v=int(x)
                    if 0<=v<=99:
                        nums.append(v)
                    if len(nums)==expected:
                        break
            if len(nums)!=expected:
                continue
            try:
                dt=dr_local_to_utc(date_s,time_s)
            except Exception:
                continue
            out.append((game,dt,nums))

    seen=set(); clean=[]
    for row in out:
        k=(row[0],row[1].isoformat(),tuple(row[2]))
        if k not in seen:
            seen.add(k); clean.append(row)
    return clean

def source_paused(src): return bool(src.pause_until and src.pause_until>datetime.now(timezone.utc))


def source_success(db,src,status=200):
    src.state="HEALTHY"; src.consecutive_failures=0; src.pause_until=None
    src.last_http_status=status; src.last_error=None; src.last_success_at=datetime.now(timezone.utc)
    src.updated_at=datetime.now(timezone.utc); db.commit()


def source_failure(db,src,status=None,message=""):
    src.consecutive_failures=int(src.consecutive_failures or 0)+1
    src.last_http_status=status; src.last_error=message[:700]; src.updated_at=datetime.now(timezone.utc)
    if status in (403,429):
        mins=min(SAFE_BACKOFF_MAX_MINUTES,SAFE_BACKOFF_BASE_MINUTES*(2**max(0,src.consecutive_failures-1)))
        src.pause_until=datetime.now(timezone.utc)+timedelta(minutes=mins); src.state="PAUSED"
    else:
        src.state="WAITING"
    db.commit()


def fetch_source(src,url,expected_game=None):
    if source_paused(src): raise RuntimeError("SOURCE_PAUSED")
    respectful_wait()
    headers={"User-Agent":"OJO99-Omega/6.0 (low-rate cached verified-results-client)"}
    try:
        with httpx.Client(timeout=12,follow_redirects=True,headers=headers) as client:
            r=client.get(url)
            with SessionLocal() as db:
                cur=db.scalar(select(SourceRegistry).where(SourceRegistry.key==src.key))
                if r.status_code in (403,429):
                    source_failure(db,cur,r.status_code,f"HTTP {r.status_code}")
                    raise RuntimeError(f"HTTP_{r.status_code}_PAUSED")
                r.raise_for_status(); source_success(db,cur,r.status_code)
            return extract_public_results(r.text, expected_game=expected_game)
    except httpx.HTTPStatusError as e:
        status=e.response.status_code if e.response is not None else None
        with SessionLocal() as db:
            cur=db.scalar(select(SourceRegistry).where(SourceRegistry.key==src.key))
            source_failure(db,cur,status,str(e))
        raise
    except httpx.RequestError as e:
        with SessionLocal() as db:
            cur=db.scalar(select(SourceRegistry).where(SourceRegistry.key==src.key))
            source_failure(db,cur,None,str(e))
        raise


def fp(source_key,lottery,dt,nums):
    return hashlib.sha256(f"{source_key}|{lottery}|{dt.isoformat()}|{','.join(map(str,nums))}".encode()).hexdigest()


def ingest_observation(db,source_key,lottery,dt,nums,source_url):
    if lottery not in SCHEDULE_MAP or not valid_nums(nums): return False
    if len(nums)!=SCHEDULE_MAP[lottery]["numbers"]: return False
    f=fp(source_key,lottery,dt,nums)
    if db.scalar(select(RawObservation).where(RawObservation.fingerprint==f)): return False
    db.add(RawObservation(source_key=source_key,lottery=lottery,draw_time=dt,numbers_json=json.dumps(nums),fingerprint=f,source_url=source_url))
    db.flush(); return True


def rebuild_canonical(db,lottery,dt):
    obs=db.scalars(select(RawObservation).where(RawObservation.lottery==lottery,RawObservation.draw_time==dt)).all()
    if not obs: return None
    srcs={x.key:x for x in db.scalars(select(SourceRegistry)).all()}
    groups=defaultdict(list)
    for o in obs: groups[o.numbers_json].append(o)
    ranked=[]
    for nj,rows in groups.items():
        sources=sorted({r.source_key for r in rows})
        trust=sum(float(srcs.get(s).trust if srcs.get(s) else 0.5) for s in sources)
        ranked.append((trust,len(sources),nj,sources))
    ranked.sort(reverse=True)
    best_trust,best_count,best_json,best_sources=ranked[0]
    if len(ranked)>1:
        state="CONFLICT"; confidence=min(0.49,best_trust/max(1.0,best_trust+ranked[1][0]))
    elif best_count>=2:
        state="VERIFIED"; confidence=min(0.99,0.75+0.08*best_count)
    else:
        state="PROVISIONAL"; confidence=min(0.74,best_trust)
    row=db.scalar(select(CanonicalDraw).where(CanonicalDraw.lottery==lottery,CanonicalDraw.draw_time==dt))
    if not row:
        row=CanonicalDraw(lottery=lottery,draw_time=dt); db.add(row)
    row.numbers_json=best_json; row.confidence=confidence; row.verification_state=state
    row.sources_json=json.dumps(best_sources); row.updated_at=datetime.now(timezone.utc); db.flush()
    if state!="CONFLICT": evaluate_prediction_if_exists(db,lottery,dt,json.loads(best_json))
    return row


def collect_current():
    seed_sources()
    with collector_lock:
        with SessionLocal() as db:
            sources=db.scalars(select(SourceRegistry).where(SourceRegistry.enabled==True)).all()
        summary=[]
        for src in sources:
            if source_paused(src):
                summary.append({"source":src.key,"status":"PAUSED"}); continue
            try:
                rows=fetch_source(src,src.url); touched=set(); new=0
                with SessionLocal() as db:
                    for game,dt,nums in rows:
                        if ingest_observation(db,src.key,game,dt,nums,src.url): new+=1
                        touched.add((game,dt))
                    for game,dt in touched: rebuild_canonical(db,game,dt)
                    db.commit()
                summary.append({"source":src.key,"status":"OK","new":new,"rows":len(rows)})
            except Exception as e:
                summary.append({"source":src.key,"status":"ERROR","message":str(e)[:160]})
        return {"status":"OK","sources":summary}


def ensure_cursor(db):
    row=db.scalar(select(HistoricalCursor).where(HistoricalCursor.key=="default"))
    if row: return row
    today=datetime.now(DR_TZ).date()
    row=HistoricalCursor(key="default",cursor_date=(today-timedelta(days=1)).isoformat(),target_date=(today-timedelta(days=730)).isoformat(),finished=False)
    db.add(row); db.commit(); db.refresh(row); return row


def get_cache(db,source_key,day):
    return db.scalar(select(DateCache).where(DateCache.source_key==source_key,DateCache.date_label==day.isoformat()))


def set_cache(db,source_key,day,status,rows_found=0,message=""):
    row=get_cache(db,source_key,day)
    if not row:
        row=DateCache(source_key=source_key,date_label=day.isoformat()); db.add(row)
    row.status=status; row.rows_found=rows_found; row.message=message[:700]; row.checked_at=datetime.now(timezone.utc); db.commit()




def collect_history_step():
    """
    V8.2 HISTORICAL SOURCE FIX
    Procesa un día histórico por ronda y consulta cada lotería por su ruta individual.
    El cursor solo avanza después de terminar la fecha.
    """
    seed_sources()
    processed=[]
    total_new=0

    with SessionLocal() as db:
        cur=ensure_cursor(db)
        if cur.finished:
            return {"status":"FINISHED","cursor_date":cur.cursor_date,"new_observations":0}
        day=date.fromisoformat(cur.cursor_date)
        target=date.fromisoformat(cur.target_date)
        sources=db.scalars(select(SourceRegistry).where(SourceRegistry.enabled==True)).all()

    if day < target:
        with SessionLocal() as db:
            cur=ensure_cursor(db); cur.finished=True; db.commit()
        return {"status":"FINISHED","cursor_date":day.isoformat(),"new_observations":0}

    label=day.strftime("%d-%m-%Y")

    with collector_lock:
        for src in sources:
            if source_paused(src):
                processed.append({"source":src.key,"status":"PAUSED"})
                continue

            # Solo la fuente web pública usa rutas por juego.
            if src.key != "primary":
                continue

            for game,slug in HISTORICAL_SLUGS.items():
                cache_key=f"{src.key}:{game}"
                with SessionLocal() as db:
                    c=get_cache(db,cache_key,day)
                if c and c.status in ("COMPLETE","EMPTY"):
                    continue

                base=src.url.rstrip("/")
                # SOURCE_PRIMARY_URL termina normalmente en /resultados/
                if base.endswith("/resultados"):
                    root=base
                else:
                    root=base.split("/resultados")[0].rstrip("/") + "/resultados"

                url=f"{root}/{slug}/?date={label}"

                try:
                    rows=fetch_source(src,url,expected_game=game)
                    rows=[
                        (g,dt,nums) for g,dt,nums in rows
                        if g==game and dt.astimezone(DR_TZ).date()==day
                    ]

                    touched=set(); new=0
                    with SessionLocal() as db:
                        for g,dt,nums in rows:
                            if ingest_observation(db,src.key,g,dt,nums,url):
                                new+=1; total_new+=1
                            touched.add((g,dt))
                        for g,dt in touched:
                            rebuild_canonical(db,g,dt)
                        db.commit()
                        set_cache(
                            db,cache_key,day,
                            "COMPLETE" if rows else "EMPTY",
                            len(rows),
                            f"game={game}; date={label}; rows={len(rows)}; new={new}"
                        )

                    processed.append({
                        "source":src.key,
                        "game":game,
                        "status":"OK",
                        "rows":len(rows),
                        "new":new
                    })

                except Exception as e:
                    with SessionLocal() as db:
                        set_cache(db,cache_key,day,"ERROR",0,str(e))
                    processed.append({
                        "source":src.key,
                        "game":game,
                        "status":"ERROR",
                        "message":str(e)[:160]
                    })
                    # Si la fuente entra en pausa, paramos esta ronda para respetar backoff.
                    with SessionLocal() as db:
                        fresh=db.scalar(select(SourceRegistry).where(SourceRegistry.key==src.key))
                        if fresh and source_paused(fresh):
                            break

    # Avanzar la fecha solo al terminar esta ronda.
    with SessionLocal() as db:
        cur=ensure_cursor(db)
        if date.fromisoformat(cur.cursor_date)==day:
            nxt=day-timedelta(days=1)
            cur.cursor_date=nxt.isoformat()
            cur.finished=nxt < date.fromisoformat(cur.target_date)
            db.commit()
        cursor=cur.cursor_date

    return {
        "status":"OK",
        "date":day.isoformat(),
        "processed":processed,
        "new_observations":total_new,
        "cursor_date":cursor
    }

def canonical_draws(db,lottery,before=None):
    q=select(CanonicalDraw).where(CanonicalDraw.lottery==lottery,CanonicalDraw.verification_state!="CONFLICT")
    if before is not None: q=q.where(CanonicalDraw.draw_time<before)
    return db.scalars(q.order_by(CanonicalDraw.draw_time.asc())).all()



def engine_scores(draws):
    """
    Motores independientes OJO-99.
    Todos producen puntuaciones 00-99 basadas únicamente en datos reales.
    """
    N = len(draws)
    flat = [n for d in draws for n in nums_of(d)]
    counts = Counter(flat)

    recent_draws = draws[-min(30, N):]
    recent_flat = [n for d in recent_draws for n in nums_of(d)]
    recent = Counter(recent_flat)

    last_idx = {}
    for i, d in enumerate(draws):
        for n in set(nums_of(d)):
            last_idx[n] = i

    # Calendario
    weekday = defaultdict(Counter)
    monthday = defaultdict(Counter)
    month = defaultdict(Counter)
    for d in draws:
        local = d.draw_time.astimezone(DR_TZ)
        vals = set(nums_of(d))
        weekday[local.weekday()].update(vals)
        monthday[local.day].update(vals)
        month[local.month].update(vals)

    now_local = datetime.now(DR_TZ)
    wd = now_local.weekday()
    md = now_local.day
    mo = now_local.month

    # Posiciones
    position_counts = defaultdict(Counter)
    for d in draws:
        vals = nums_of(d)
        for pos, n in enumerate(vals):
            position_counts[pos][n] += 1

    # Familias: decena, terminación, suma de dígitos
    tens_counts = Counter()
    units_counts = Counter()
    digit_sum_counts = Counter()
    for n in flat:
        tens_counts[n // 10] += 1
        units_counts[n % 10] += 1
        digit_sum_counts[(n // 10) + (n % 10)] += 1

    # Transición inmediata y a dos sorteos
    transition1 = Counter()
    transition2 = Counter()
    origin1 = Counter()
    origin2 = Counter()
    for i in range(N - 1):
        a = set(nums_of(draws[i]))
        b = set(nums_of(draws[i + 1]))
        for x in a:
            origin1[x] += 1
            for y in b:
                transition1[(x, y)] += 1

    for i in range(N - 2):
        a = set(nums_of(draws[i]))
        c = set(nums_of(draws[i + 2]))
        for x in a:
            origin2[x] += 1
            for y in c:
                transition2[(x, y)] += 1

    last_values = set(nums_of(draws[-1])) if draws else set()

    trans1_support = Counter()
    trans2_support = Counter()
    for x in last_values:
        for y in range(100):
            if origin1[x]:
                trans1_support[y] += transition1[(x, y)] / origin1[x]
            if origin2[x]:
                trans2_support[y] += transition2[(x, y)] / origin2[x]

    # Compañeros: qué números han coexistido con los últimos números
    pair_counts = Counter()
    presence = Counter()
    for d in draws:
        vals = sorted(set(nums_of(d)))
        presence.update(vals)
        for i in range(len(vals)):
            for j in range(i + 1, len(vals)):
                pair_counts[(vals[i], vals[j])] += 1

    companion_support = Counter()
    for x in last_values:
        for y in range(100):
            if x == y:
                continue
            a, b = sorted((x, y))
            together = pair_counts[(a, b)]
            denom = max(1, presence[x])
            companion_support[y] += together / denom

    # Posición: mejor comportamiento entre posiciones disponibles
    pos_score = {}
    for n in range(100):
        vals = []
        for pos, c in position_counts.items():
            total_pos = sum(c.values())
            vals.append(c[n] / max(1, total_pos))
        pos_score[n] = max(vals) if vals else 0.0

    engines = {}
    engines["frequency"] = {n: counts[n] / max(1, len(flat)) for n in range(100)}
    engines["recent"] = {n: recent[n] / max(1, len(recent_flat)) for n in range(100)}
    engines["gap"] = {n: min((N - 1 - last_idx[n] if n in last_idx else N) / 50, 1) for n in range(100)}

    engines["weekday"] = {
        n: weekday[wd][n] / max(1, sum(weekday[wd].values()))
        for n in range(100)
    }
    engines["day_of_month"] = {
        n: monthday[md][n] / max(1, sum(monthday[md].values()))
        for n in range(100)
    }
    engines["month"] = {
        n: month[mo][n] / max(1, sum(month[mo].values()))
        for n in range(100)
    }

    engines["reverse"] = {
        n: counts[reverse_num(n)] / max(1, len(flat))
        for n in range(100)
    }

    engines["transition_1"] = {
        n: trans1_support[n] / max(1, len(last_values))
        for n in range(100)
    }
    engines["transition_2"] = {
        n: trans2_support[n] / max(1, len(last_values))
        for n in range(100)
    }

    engines["companions"] = {
        n: companion_support[n] / max(1, len(last_values))
        for n in range(100)
    }

    engines["position"] = pos_score

    engines["tens_family"] = {
        n: tens_counts[n // 10] / max(1, len(flat))
        for n in range(100)
    }
    engines["ending_family"] = {
        n: units_counts[n % 10] / max(1, len(flat))
        for n in range(100)
    }
    engines["digit_sum_family"] = {
        n: digit_sum_counts[(n // 10) + (n % 10)] / max(1, len(flat))
        for n in range(100)
    }

    return engines

def normalize_score_map(m):
    vals=list(m.values())
    if not vals: return {k:0 for k in m}
    lo=min(vals); hi=max(vals)
    if hi<=lo: return {k:0 for k in m}
    return {k:(v-lo)/(hi-lo) for k,v in m.items()}



def validate_engines(lottery, draws, max_tests=180):
    """
    Valida cada motor caminando hacia adelante sin mirar el futuro.
    Un motor solo gana peso si supera su baseline con muestra suficiente.
    """
    if len(draws) < 90:
        return {}

    start = max(60, len(draws) - max_tests)

    initial_engines = engine_scores(draws[:start])
    stats = {e: [0, 0] for e in initial_engines.keys()}

    for idx in range(start, len(draws)):
        train = draws[:idx]
        target = set(nums_of(draws[idx]))
        es = engine_scores(train)

        for e, m in es.items():
            if e not in stats:
                stats[e] = [0, 0]
            top = [
                n for n, _ in sorted(
                    m.items(),
                    key=lambda kv: kv[1],
                    reverse=True
                )[:5]
            ]
            stats[e][1] += 1
            if target.intersection(top):
                stats[e][0] += 1

    k = max(1, SCHEDULE_MAP.get(lottery, {}).get("numbers", 3))
    nohit = 1.0
    for i in range(min(k, 100)):
        nohit *= max(0, (95 - i) / (100 - i))
    baseline = 1 - nohit

    results = {}
    with SessionLocal() as db:
        for e, (hits, tests) in stats.items():
            rate = hits / tests if tests else 0
            lift = rate / baseline if baseline else 0

            # Penalización conservadora: no peso con pocas pruebas.
            weight = (
                max(0.0, min(1.0, (lift - 1.0) / 0.50))
                if tests >= 50
                else 0.0
            )

            results[e] = {
                "tests": tests,
                "hits": hits,
                "hit_rate": rate,
                "baseline": baseline,
                "lift": lift,
                "weight": weight
            }

            row = db.scalar(
                select(ModelValidation).where(
                    ModelValidation.lottery == lottery,
                    ModelValidation.engine == e
                )
            )
            if not row:
                row = ModelValidation(lottery=lottery, engine=e)
                db.add(row)

            row.tests = tests
            row.hits = hits
            row.hit_rate = rate
            row.baseline_rate = baseline
            row.lift = lift
            row.weight = weight
            row.updated_at = datetime.now(timezone.utc)

        db.commit()

    return results

def wilson_lower(successes,total,z=1.64):
    if total<=0: return 0.0
    p=successes/total; den=1+z*z/total
    centre=p+z*z/(2*total); adj=z*math.sqrt((p*(1-p)+z*z/(4*total))/total)
    return max(0.0,(centre-adj)/den)


def build_analysis(lottery,draws,limit=5):
    N=len(draws)
    if N<30:
        return {"status":"DATOS INSUFICIENTES","draw_count":N,"top_numbers":[],"top_pairs":[],"top_triples":[],"transitions":[],"master_play":None,"engine_validation":{}}

    validations=validate_engines(lottery,draws)
    es=engine_scores(draws)
    norm={e:normalize_score_map(m) for e,m in es.items()}
    # Until validation exists, use conservative equal low weights.
    weights={}
    for e in es:
        weights[e]=validations.get(e,{}).get("weight",0.0)
    if sum(weights.values())<=0:
        weights={e:0.20 for e in es}

    totalw=sum(weights.values())
    ensemble=[]
    for n in range(100):
        score=sum(weights[e]*norm[e][n] for e in es)/max(totalw,1e-9)
        ensemble.append({"number":f"{n:02d}","score":round(score*100,1)})
    top_numbers=sorted(ensemble,key=lambda x:x["score"],reverse=True)[:limit]

    pair_counts=Counter(); presence=Counter(); triple_counts=Counter()
    for d in draws:
        vals=sorted(set(nums_of(d))); presence.update(vals)
        for i in range(len(vals)):
            for j in range(i+1,len(vals)): pair_counts[(vals[i],vals[j])]+=1
        for i in range(len(vals)):
            for j in range(i+1,len(vals)):
                for k in range(j+1,len(vals)): triple_counts[(vals[i],vals[j],vals[k])]+=1

    pairs=[]
    for (a,b),c in pair_counts.items():
        pa,pb=presence[a]/N,presence[b]/N
        expected=N*pa*pb; lift=c/expected if expected else 0
        support=c/N; lower=wilson_lower(c,N)
        score=45*min(lift/2,1)+30*min(support/0.15,1)+25*min(lower/0.08,1)
        if c>=4:
            pairs.append({"pair":[f"{a:02d}",f"{b:02d}"],"score":round(min(100,score),1),"together_count":c,"lift":round(lift,2)})
    pairs=sorted(pairs,key=lambda x:x["score"],reverse=True)[:limit]

    triples=[{"triple":[f"{x:02d}" for x in t],"score":round(min(100,25+9*c),1),"observed_count":c}
             for t,c in triple_counts.items() if c>=3]
    triples=sorted(triples,key=lambda x:x["score"],reverse=True)[:limit]

    # cross-draw transitions
    transition_counts=Counter(); origin_counts=Counter()
    for i in range(N-1):
        a=set(nums_of(draws[i])); b=set(nums_of(draws[i+1]))
        for x in a:
            origin_counts[x]+=1
            for y in b: transition_counts[(x,y)]+=1
    transitions=[{"from":f"{x:02d}","to":f"{y:02d}","rate":round(c/origin_counts[x],3)}
                 for (x,y),c in transition_counts.items() if origin_counts[x]>=10 and c>=3]
    transitions=sorted(transitions,key=lambda x:x["rate"],reverse=True)[:10]

    master=None
    if N>=250 and pairs:
        p=pairs[0]
        # Master only if pair is strong AND at least one validated engine is truly above baseline.
        validated_advantage=any(v.get("weight",0)>0.35 and v.get("tests",0)>=80 for v in validations.values())
        if validated_advantage and p["score"]>=92 and p["together_count"]>=10 and p["lift"]>=1.4:
            master={"status":"JUGADA MAESTRA","pair":p["pair"],"score":p["score"],
                    "reason":"Palé fuerte + muestra amplia + motores con ventaja walk-forward validada.",
                    "disclaimer":"No garantiza premio; es una señal estadística interna."}

    return {"status":"OK","draw_count":N,"top_numbers":top_numbers,"top_pairs":pairs,"top_triples":triples,
            "transitions":transitions,"master_play":master,"engine_validation":validations}


def next_draw_time_utc(lottery):
    cfg=SCHEDULE_MAP.get(lottery)
    if not cfg: return None
    hh,mm=map(int,cfg["time"].split(":"))
    now=datetime.now(DR_TZ); dt=now.replace(hour=hh,minute=mm,second=0,microsecond=0)
    if dt<=now: dt+=timedelta(days=1)
    return dt.astimezone(timezone.utc)


def freeze_prediction(lottery):
    with SessionLocal() as db:
        draws=canonical_draws(db,lottery)
        a=robust_build_analysis(lottery,draws)
        if a["draw_count"]<30: return {"ok":False,"reason":"DATOS_INSUFICIENTES"}
        target=next_draw_time_utc(lottery)
        if db.scalar(select(Prediction).where(Prediction.lottery==lottery,Prediction.draw_time==target)):
            return {"ok":False,"reason":"YA_CONGELADA"}
        nums=[int(x["number"]) for x in a["top_numbers"]]
        pairs=[[int(v) for v in x["pair"]] for x in a["top_pairs"]]
        triples=[[int(v) for v in x["triple"]] for x in a["top_triples"]]
        master=[int(v) for v in a["master_play"]["pair"]] if a.get("master_play") else None
        db.add(Prediction(lottery=lottery,draw_time=target,top_numbers_json=json.dumps(nums),top_pairs_json=json.dumps(pairs),
                          top_triples_json=json.dumps(triples),master_json=json.dumps(master) if master else None))
        db.commit()
        return {"ok":True,"draw_time":target,"analysis":a}


def evaluate_prediction_if_exists(db,lottery,draw_time,result):
    p=db.scalar(select(Prediction).where(Prediction.lottery==lottery,Prediction.draw_time==draw_time))
    if not p or p.evaluated: return False
    target=set(map(int,result)); nums=list(map(int,json.loads(p.top_numbers_json or "[]")))
    pairs=json.loads(p.top_pairs_json or "[]"); triples=json.loads(p.top_triples_json or "[]")
    master=json.loads(p.master_json) if p.master_json else None
    captured=len(target.intersection(nums))
    p.hit_top5_numbers=captured>=1; p.hit_2_of_3=captured>=2
    p.hit_top5_pairs=any(set(map(int,x)).issubset(target) for x in pairs)
    p.hit_top5_triples=any(set(map(int,x)).issubset(target) for x in triples)
    p.hit_master=bool(master and set(map(int,master)).issubset(target))
    p.result_json=json.dumps(list(map(int,result))); p.evaluated=True; return True


def performance(lottery):
    with SessionLocal() as db:
        rows=db.scalars(select(Prediction).where(Prediction.lottery==lottery,Prediction.evaluated==True)).all()
    total=len(rows); pct=lambda n,d: round(100*n/d,2) if d else None
    mr=[r for r in rows if r.master_json]
    return {"evaluated":total,"sample_status":"MUESTRA INSUFICIENTE" if total<30 else ("EN DESARROLLO" if total<100 else "UTILIZABLE"),
            "top5_numbers":pct(sum(r.hit_top5_numbers for r in rows),total),
            "two_of_three":pct(sum(r.hit_2_of_3 for r in rows),total),
            "top5_pairs":pct(sum(r.hit_top5_pairs for r in rows),total),
            "top5_triples":pct(sum(r.hit_top5_triples for r in rows),total),
            "master_hits":sum(r.hit_master for r in mr),"master_alerts":len(mr),"master_pct":pct(sum(r.hit_master for r in mr),len(mr))}


def walk_forward(lottery,max_tests=300):
    with SessionLocal() as db: draws=canonical_draws(db,lottery)
    if len(draws)<80: return {"status":"MUESTRA INSUFICIENTE","tests":0}
    start=max(60,len(draws)-max_tests); tests=h1=h2=hp=ht=0
    for idx in range(start,len(draws)):
        a=build_analysis(lottery,draws[:idx])
        if not a["top_numbers"]: continue
        tests+=1; target=set(nums_of(draws[idx]))
        nums=[int(x["number"]) for x in a["top_numbers"]]
        pairs=[[int(v) for v in x["pair"]] for x in a["top_pairs"]]
        triples=[[int(v) for v in x["triple"]] for x in a["top_triples"]]
        captured=len(target.intersection(nums))
        h1+=captured>=1; h2+=captured>=2
        hp+=any(set(x).issubset(target) for x in pairs)
        ht+=any(set(x).issubset(target) for x in triples)
    pct=lambda n:round(100*n/tests,2) if tests else None
    return {"status":"OK" if tests>=30 else "MUESTRA INSUFICIENTE","tests":tests,
            "top5_number_hit_pct":pct(h1),"two_of_three_pct":pct(h2),
            "top5_pair_hit_pct":pct(hp),"top5_triple_hit_pct":pct(ht)}


def radar_total_report(draws):
    """
    Informe explicable de las señales que el usuario pidió.
    No modifica por sí solo una predicción: sirve para auditar qué está viendo OJO-99.
    """
    if not draws:
        return {
            "reverse_pairs": [],
            "companions": [],
            "attractors": [],
            "lag2_attractors": [],
            "calendar": {},
            "families": {}
        }

    N = len(draws)
    flat = [n for d in draws for n in nums_of(d)]
    counts = Counter(flat)
    last_values = sorted(set(nums_of(draws[-1])))

    # Inversos presentes en histórico
    reverse_pairs = []
    seen = set()
    for n in range(100):
        r = reverse_num(n)
        key = tuple(sorted((n, r)))
        if key in seen or n == r:
            continue
        seen.add(key)
        support = counts[n] + counts[r]
        if support > 0:
            reverse_pairs.append({
                "pair": [f"{n:02d}", f"{r:02d}"],
                "support": support
            })
    reverse_pairs.sort(key=lambda x: x["support"], reverse=True)

    # Compañeros con lift
    pair_counts = Counter()
    presence = Counter()
    for d in draws:
        vals = sorted(set(nums_of(d)))
        presence.update(vals)
        for i in range(len(vals)):
            for j in range(i + 1, len(vals)):
                pair_counts[(vals[i], vals[j])] += 1

    companions = []
    for (a, b), c in pair_counts.items():
        pa = presence[a] / max(1, N)
        pb = presence[b] / max(1, N)
        expected = N * pa * pb
        lift = c / expected if expected else 0
        if c >= 4:
            companions.append({
                "pair": [f"{a:02d}", f"{b:02d}"],
                "together": c,
                "lift": round(lift, 2)
            })
    companions.sort(key=lambda x: (x["lift"], x["together"]), reverse=True)

    # Jaladores inmediatos y a dos sorteos
    trans1 = Counter()
    trans2 = Counter()
    origins1 = Counter()
    origins2 = Counter()

    for i in range(N - 1):
        a = set(nums_of(draws[i]))
        b = set(nums_of(draws[i + 1]))
        for x in a:
            origins1[x] += 1
            for y in b:
                trans1[(x, y)] += 1

    for i in range(N - 2):
        a = set(nums_of(draws[i]))
        cset = set(nums_of(draws[i + 2]))
        for x in a:
            origins2[x] += 1
            for y in cset:
                trans2[(x, y)] += 1

    attractors = []
    lag2 = []
    for x in last_values:
        if origins1[x]:
            for y in range(100):
                c = trans1[(x, y)]
                if c >= 3:
                    attractors.append({
                        "from": f"{x:02d}",
                        "to": f"{y:02d}",
                        "rate": round(c / origins1[x], 3),
                        "count": c
                    })
        if origins2[x]:
            for y in range(100):
                c = trans2[(x, y)]
                if c >= 3:
                    lag2.append({
                        "from": f"{x:02d}",
                        "to": f"{y:02d}",
                        "rate": round(c / origins2[x], 3),
                        "count": c
                    })

    attractors.sort(key=lambda x: (x["rate"], x["count"]), reverse=True)
    lag2.sort(key=lambda x: (x["rate"], x["count"]), reverse=True)

    # Calendario
    now = datetime.now(DR_TZ)
    wd = now.weekday()
    md = now.day
    mo = now.month
    weekday_c = Counter()
    monthday_c = Counter()
    month_c = Counter()

    for d in draws:
        local = d.draw_time.astimezone(DR_TZ)
        vals = set(nums_of(d))
        if local.weekday() == wd:
            weekday_c.update(vals)
        if local.day == md:
            monthday_c.update(vals)
        if local.month == mo:
            month_c.update(vals)

    def top_counter(c, n=10):
        return [
            {"number": f"{num:02d}", "count": count}
            for num, count in c.most_common(n)
        ]

    # Familias
    tens = Counter(n // 10 for n in flat)
    endings = Counter(n % 10 for n in flat)
    sums = Counter((n // 10) + (n % 10) for n in flat)

    return {
        "last_numbers": [f"{n:02d}" for n in last_values],
        "reverse_pairs": reverse_pairs[:10],
        "companions": companions[:10],
        "attractors": attractors[:10],
        "lag2_attractors": lag2[:10],
        "calendar": {
            "weekday": top_counter(weekday_c),
            "day_of_month": top_counter(monthday_c),
            "month": top_counter(month_c)
        },
        "families": {
            "tens": [{"family": str(k), "count": v} for k, v in tens.most_common(10)],
            "endings": [{"family": str(k), "count": v} for k, v in endings.most_common(10)],
            "digit_sums": [{"family": str(k), "count": v} for k, v in sums.most_common(10)]
        }
    }


def data_quality_score(draws):
    """
    Puntúa calidad interna del histórico.
    No usa resultados futuros ni inventa datos.
    """
    if not draws:
        return 0.0
    total = len(draws)
    valid = 0
    ordered = 0
    prev = None
    seen = set()

    for d in draws:
        nums = nums_of(d)
        if valid_nums(nums):
            valid += 1
        if prev is None or d.draw_time > prev:
            ordered += 1
        prev = d.draw_time
        seen.add((d.draw_time.isoformat(), tuple(nums)))

    uniqueness = len(seen) / total
    return round(
        100 * (
            0.45 * (valid / total) +
            0.25 * (ordered / total) +
            0.30 * uniqueness
        ),
        1
    )


def regime_weights(draws):
    """
    Detecta si la distribución reciente se aleja de la histórica.
    Si cambia mucho, aumenta peso de ventanas recientes y reduce histórico largo.
    """
    if len(draws) < 80:
        return {"long":0.45, "mid":0.30, "short":0.25, "drift":0.0}

    long_flat = [n for d in draws for n in nums_of(d)]
    short_draws = draws[-30:]
    short_flat = [n for d in short_draws for n in nums_of(d)]

    cl = Counter(long_flat)
    cs = Counter(short_flat)

    total_l = max(1, sum(cl.values()))
    total_s = max(1, sum(cs.values()))

    # Distancia total variacional aproximada sobre 00..99.
    drift = 0.5 * sum(
        abs(cl[n]/total_l - cs[n]/total_s)
        for n in range(100)
    )
    drift = max(0.0, min(1.0, drift))

    return {
        "long": round(0.50 - 0.25*drift, 3),
        "mid": round(0.30, 3),
        "short": round(0.20 + 0.25*drift, 3),
        "drift": round(drift, 3),
    }


def calibrated_number_rank(lottery, draws):
    """
    Ensemble multi-ventana con pesos de motores validados.
    - largo: todo el histórico
    - medio: últimos 120
    - corto: últimos 30
    """
    if len(draws) < 30:
        return [], {}, {}

    rw = regime_weights(draws)
    windows = {
        "long": draws,
        "mid": draws[-min(120, len(draws)):],
        "short": draws[-min(30, len(draws)):],
    }

    # Validaciones walk-forward sobre el histórico completo.
    validations = validate_engines(lottery, draws)

    combined = {n:0.0 for n in range(100)}
    detail = {}

    for wname, wdraws in windows.items():
        engines = engine_scores(wdraws)
        norm = {e:normalize_score_map(m) for e,m in engines.items()}

        raw_weights = {}
        for e in engines:
            vw = validations.get(e,{}).get("weight",0.0)
            raw_weights[e] = vw

        # Si todavía no hay motores validados, usar pesos muy conservadores.
        if sum(raw_weights.values()) <= 0:
            raw_weights = {e:0.15 for e in engines}

        sw = max(1e-9, sum(raw_weights.values()))
        for n in range(100):
            local = sum(raw_weights[e] * norm[e][n] for e in engines) / sw
            combined[n] += rw[wname] * local

    ranked = sorted(
        [{"number":f"{n:02d}", "score":round(combined[n]*100,1)} for n in range(100)],
        key=lambda x:x["score"],
        reverse=True
    )

    detail["regime"] = rw
    detail["validation"] = validations
    detail["quality"] = data_quality_score(draws)

    return ranked, validations, detail


def hard_validation_gate(lottery, draws, analysis):
    """
    Puerta dura para alertas máximas.
    100/100 = score interno máximo, NO probabilidad garantizada.
    """
    v = analysis.get("v7", {})
    validations = analysis.get("engine_validation", {}) or {}
    quality = float(v.get("quality") or data_quality_score(draws))
    validated = [
        x for x in validations.values()
        if x.get("tests", 0) >= 100
        and x.get("weight", 0) >= 0.35
        and x.get("lift", 0) > 1.05
    ]
    return {
        "quality": quality,
        "validated_engines": len(validated),
        "sample": len(draws),
        "pass_number": quality >= 95 and len(draws) >= 250 and len(validated) >= 2,
        "pass_pair": quality >= 96 and len(draws) >= 350 and len(validated) >= 3,
        "pass_triple": quality >= 97 and len(draws) >= 500 and len(validated) >= 3,
    }


def max_alerts(lottery, draws, analysis):
    """
    Crea tres niveles:
    - NÚMERO 100/100
    - PALÉ MÁXIMO 100/100
    - TRIPLETA MÁXIMA 100/100

    Son scores internos; nunca se presentan como certeza matemática.
    """
    gate = hard_validation_gate(lottery, draws, analysis)

    alerts = {
        "number_alert": None,
        "pair_alert": None,
        "triple_alert": None,
        "gate": gate
    }

    topn = analysis.get("top_numbers") or []
    topp = analysis.get("top_pairs") or []
    topt = analysis.get("top_triples") or []

    if gate["pass_number"] and topn:
        n = topn[0]
        # Requiere score extremo y margen sobre el segundo.
        margin = n["score"] - (topn[1]["score"] if len(topn) > 1 else 0)
        if n["score"] >= 97 and margin >= 2:
            internal = min(
                100.0,
                70
                + 0.20 * gate["quality"]
                + 2.0 * gate["validated_engines"]
                + min(8, margin)
            )
            if internal >= 99:
                alerts["number_alert"] = {
                    "status": "ALERTA NÚMERO 100/100",
                    "number": n["number"],
                    "internal_score": 100,
                    "quality": gate["quality"],
                    "validated_engines": gate["validated_engines"],
                    "warning": "100/100 es score interno máximo; no garantiza que salga."
                }

    if gate["pass_pair"] and topp:
        p = topp[0]
        # Palé máximo exige afinidad, lift y repetición.
        if (
            p["score"] >= 95
            and p.get("lift", 0) >= 1.50
            and p.get("together_count", 0) >= 12
        ):
            alerts["pair_alert"] = {
                "status": "ALERTA PALÉ MÁXIMO 100/100",
                "pair": p["pair"],
                "internal_score": 100,
                "pair_score": p["score"],
                "lift": p.get("lift"),
                "together_count": p.get("together_count"),
                "warning": "Señal interna máxima; no equivale a palé garantizado."
            }

    if gate["pass_triple"] and topt:
        t = topt[0]
        if (
            t["score"] >= 97
            and t.get("observed_count", 0) >= 8
        ):
            alerts["triple_alert"] = {
                "status": "ALERTA TRIPLETA MÁXIMA 100/100",
                "triple": t["triple"],
                "internal_score": 100,
                "triple_score": t["score"],
                "observed_count": t.get("observed_count"),
                "warning": "Señal interna máxima; no garantiza una tripleta."
            }

    return alerts


def kino_special_radar(draws, analysis):
    """
    Radar dedicado a Super Kino TV.
    Kino se analiza aparte porque cada sorteo contiene muchos más números
    que una quiniela de 3 números.
    """
    if not draws:
        return {
            "status": "SIN DATOS",
            "top_numbers": [],
            "hot_affinity_pairs": [],
            "stable_numbers": [],
            "alerts": []
        }

    N = len(draws)
    flat = [n for d in draws for n in nums_of(d)]
    counts = Counter(flat)

    recent_draws = draws[-min(30, N):]
    recent_counts = Counter(n for d in recent_draws for n in nums_of(d))

    # Estabilidad por ventanas: largo, 120, 30
    long_rank = {n: counts[n] / max(1, len(flat)) for n in range(100)}
    mid_draws = draws[-min(120, N):]
    mid_flat = [n for d in mid_draws for n in nums_of(d)]
    mid_counts = Counter(mid_flat)
    mid_rank = {n: mid_counts[n] / max(1, len(mid_flat)) for n in range(100)}
    short_rank = {n: recent_counts[n] / max(1, sum(recent_counts.values())) for n in range(100)}

    nl = normalize_score_map(long_rank)
    nm = normalize_score_map(mid_rank)
    ns = normalize_score_map(short_rank)

    stable = []
    for n in range(100):
        stability = 0.40 * nl[n] + 0.35 * nm[n] + 0.25 * ns[n]
        stable.append({
            "number": f"{n:02d}",
            "stability_score": round(stability * 100, 1),
            "historical_count": counts[n],
            "recent_count": recent_counts[n]
        })
    stable.sort(key=lambda x: x["stability_score"], reverse=True)

    # Afinidades específicas de Kino
    pair_counts = Counter()
    presence = Counter()
    for d in draws:
        vals = sorted(set(nums_of(d)))
        presence.update(vals)
        for i in range(len(vals)):
            for j in range(i + 1, len(vals)):
                pair_counts[(vals[i], vals[j])] += 1

    pairs = []
    for (a, b), c in pair_counts.items():
        pa = presence[a] / max(1, N)
        pb = presence[b] / max(1, N)
        expected = N * pa * pb
        lift = c / expected if expected else 0
        if c >= max(6, int(0.05 * N)):
            pairs.append({
                "pair": [f"{a:02d}", f"{b:02d}"],
                "together_count": c,
                "lift": round(lift, 2)
            })
    pairs.sort(key=lambda x: (x["lift"], x["together_count"]), reverse=True)

    # Alertas Kino: más estrictas porque muchos números salen cada sorteo.
    gate = hard_validation_gate("Super Kino TV", draws, analysis)
    alerts = []

    if gate["pass_number"] and stable:
        best = stable[0]
        second = stable[1] if len(stable) > 1 else {"stability_score": 0}
        if best["stability_score"] >= 96 and best["stability_score"] - second["stability_score"] >= 1.5:
            alerts.append({
                "type": "KINO_NUMERO_MAXIMO",
                "number": best["number"],
                "internal_score": 100,
                "warning": "Score interno Kino; no garantiza aparición."
            })

    if gate["pass_pair"] and pairs:
        p = pairs[0]
        if p["lift"] >= 1.20 and p["together_count"] >= 15:
            alerts.append({
                "type": "KINO_PAREJA_MAXIMA",
                "pair": p["pair"],
                "internal_score": 100,
                "lift": p["lift"],
                "warning": "Afinidad Kino extrema; no es garantía."
            })

    return {
        "status": "OK" if N >= 30 else "DATOS INSUFICIENTES",
        "draw_count": N,
        "top_numbers": stable[:10],
        "hot_affinity_pairs": pairs[:10],
        "stable_numbers": stable[:20],
        "alerts": alerts
    }


def decade_pressure(draws):
    windows = {"7":draws[-7:], "14":draws[-14:], "30":draws[-30:], "all":draws}
    out = {}
    for name, ds in windows.items():
        c = Counter()
        total = 0
        for d in ds:
            for n in nums_of(d):
                c[n//10] += 1
                total += 1
        out[name] = [
            {"decade":f"{k*10:02d}-{k*10+9:02d}", "count":c[k], "share":round(c[k]/max(1,total),3)}
            for k in range(10)
        ]
    return out


def number_dna(draws, number):
    if not draws:
        return {}
    n = int(number)
    appearances, companions, next_nums = [], Counter(), Counter()
    weekdays, positions = Counter(), Counter()
    for i,d in enumerate(draws):
        vals = nums_of(d)
        if n in vals:
            appearances.append(i)
            local = d.draw_time.astimezone(DR_TZ)
            weekdays[local.weekday()] += 1
            for pos,v in enumerate(vals):
                if v == n:
                    positions[pos+1] += 1
            for v in set(vals):
                if v != n:
                    companions[v] += 1
            if i+1 < len(draws):
                next_nums.update(set(nums_of(draws[i+1])))
    last_gap = len(draws)-1-appearances[-1] if appearances else len(draws)
    return {
        "number":f"{n:02d}",
        "appearances":len(appearances),
        "draws_since_seen":last_gap,
        "reverse":f"{reverse_num(n):02d}",
        "best_companions":[{"number":f"{x:02d}","count":c} for x,c in companions.most_common(5)],
        "best_next":[{"number":f"{x:02d}","count":c} for x,c in next_nums.most_common(5)],
        "weekdays":[{"weekday":w,"count":c} for w,c in weekdays.most_common()],
        "positions":[{"position":p,"count":c} for p,c in positions.most_common()]
    }


def active_regime(draws):
    if len(draws) < 30:
        return {"status":"MUESTRA INSUFICIENTE"}
    recent = draws[-14:]
    prev = draws[-28:-14] if len(draws)>=28 else draws[:-14]

    def metrics(ds):
        repeated=reverse_hits=pair_reuse=0
        pairs=Counter()
        for i,d in enumerate(ds):
            vals=set(nums_of(d))
            if i>0:
                before=set(nums_of(ds[i-1]))
                repeated += len(vals & before)
                reverse_hits += sum(1 for x in before if reverse_num(x) in vals)
            sv=sorted(vals)
            for a in range(len(sv)):
                for b in range(a+1,len(sv)):
                    pairs[(sv[a],sv[b])] += 1
        pair_reuse=sum(1 for c in pairs.values() if c>=2)
        return {"repeat":repeated,"reverse":reverse_hits,"pairing":pair_reuse}

    r=metrics(recent)
    p=metrics(prev) if prev else {"repeat":0,"reverse":0,"pairing":0}
    labels={}
    for k in r:
        ratio=r[k]/max(1,p[k])
        labels[k]="MUY FUERTE" if ratio>=1.75 else "FUERTE" if ratio>=1.25 else "NORMAL" if ratio>=0.75 else "DEBIL"
    return {"status":"OK","recent":r,"previous":p,"signals":labels}


def special_date_engine(draws):
    now=datetime.now(DR_TZ)
    c=Counter()
    exact=0
    for d in draws:
        local=d.draw_time.astimezone(DR_TZ)
        if local.weekday()==now.weekday() and local.day==now.day:
            c.update(set(nums_of(d))); exact+=1
        elif local.day==now.day:
            for n in set(nums_of(d)): c[n]+=0.5
        elif local.month==now.month and abs(local.day-now.day)<=1:
            for n in set(nums_of(d)): c[n]+=0.25
    return {
        "date":now.date().isoformat(),
        "weekday":now.weekday(),
        "day":now.day,
        "month":now.month,
        "exact_matches":exact,
        "top":[{"number":f"{n:02d}","score":round(v,2)} for n,v in c.most_common(10)]
    }


def fire_labels(analysis):
    def mark(items):
        out=[]
        for i,x in enumerate(items[:5]):
            y=dict(x)
            y["rank"]=i+1
            y["fire"]="MAS FUEGO" if i==0 else ""
            out.append(y)
        return out
    return {
        "numbers":mark(analysis.get("top_numbers") or []),
        "pairs":mark(analysis.get("top_pairs") or []),
        "triples":mark(analysis.get("top_triples") or [])
    }


def kino_double_play(draws, kino_radar):
    stable=kino_radar.get("stable_numbers") or []
    if len(stable)<10:
        return {"A":[],"B":[],"status":"DATOS INSUFICIENTES"}
    A=[x["number"] for x in stable[:10]]
    B=[]
    for x in stable[10:]:
        if x["number"] not in A:
            B.append(x["number"])
        if len(B)>=10:
            break
    return {
        "status":"OK" if len(B)>=5 else "COBERTURA LIMITADA",
        "A":A[:10],
        "B":B[:10],
        "strategy_A":"Concentración de estabilidad y consenso",
        "strategy_B":"Cobertura alternativa sin números aleatorios"
    }


def council_of_generals(analysis):
    top=analysis.get("top_numbers") or []
    validations=analysis.get("engine_validation") or {}
    if not top:
        return {"status":"SIN SEÑAL","votes":[]}
    families={
        "HISTORICO":["frequency","gap"],
        "MOMENTO":["recent","transition_1","transition_2"],
        "RELACIONES":["reverse","companions","position"],
        "CALENDARIO":["weekday","day_of_month","month"],
        "FAMILIAS":["tens_family","ending_family","digit_sum_family"]
    }
    votes=[]
    for fam,engs in families.items():
        usable=[validations[e] for e in engs if e in validations]
        score=sum(v.get("weight",0) for v in usable)/max(1,len(usable))
        votes.append({"general":fam,"strength":round(score,3),"active":score>0.20})
    return {
        "status":"OK",
        "candidate":top[0]["number"],
        "votes":votes,
        "active_generals":sum(1 for v in votes if v["active"])
    }

def robust_build_analysis(lottery, draws, limit=5):
    """
    Capa superior V7:
    - calidad de datos
    - régimen temporal
    - ensemble multi-ventana
    - motores validados
    - puertas de confianza
    """
    base = build_analysis(lottery, draws, limit=limit)

    if base["draw_count"] < 30:
        base["v7"] = {
            "quality": data_quality_score(draws),
            "gate":"DATOS_INSUFICIENTES"
        }
        base["radar_total"] = radar_total_report(draws)
        base["max_alerts"] = {
            "number_alert": None,
            "pair_alert": None,
            "triple_alert": None,
            "gate": {
                "quality": data_quality_score(draws),
                "validated_engines": 0,
                "sample": len(draws),
                "pass_number": False,
                "pass_pair": False,
                "pass_triple": False
            }
        }
        base["kino_radar"] = kino_special_radar(draws, base) if lottery == "Super Kino TV" else None
        base["kino_double_play"] = kino_double_play(draws, base["kino_radar"]) if base["kino_radar"] else None
        base["v8"] = {
            "regime_weekly": active_regime(draws),
            "decade_pressure": decade_pressure(draws),
            "special_date": special_date_engine(draws),
            "council":{"status":"SIN SEÑAL","votes":[]},
            "fire":{"numbers":[],"pairs":[],"triples":[]},
            "dna_top5":[]
        }
        return base

    ranked, validations, detail = calibrated_number_rank(lottery, draws)
    base["top_numbers"] = ranked[:limit]
    base["engine_validation"] = validations
    base["v7"] = detail
    base["radar_total"] = radar_total_report(draws)

    quality = detail["quality"]
    validated = [
        v for v in validations.values()
        if v.get("tests",0) >= 80 and v.get("weight",0) > 0.20
    ]

    # Puerta fuerte: evita mostrar una falsa "máxima señal".
    if quality < 92:
        gate = "CALIDAD_INSUFICIENTE"
        base["master_play"] = None
    elif len(draws) < 250:
        gate = "MUESTRA_INSUFICIENTE"
        base["master_play"] = None
    elif len(validated) < 2:
        gate = "MOTORES_NO_CONFIRMADOS"
        base["master_play"] = None
    else:
        gate = "OPERATIVO"

    base["v7"]["gate"] = gate
    base["v7"]["validated_engines"] = len(validated)
    base["v8"] = {
        "regime_weekly": active_regime(draws),
        "decade_pressure": decade_pressure(draws),
        "special_date": special_date_engine(draws),
        "council": council_of_generals(base),
        "fire": fire_labels(base),
        "dna_top5": [number_dna(draws, int(x["number"])) for x in (base.get("top_numbers") or [])[:5]]
    }

    base["max_alerts"] = max_alerts(lottery, draws, base)
    if lottery == "Super Kino TV":
        base["kino_radar"] = kino_special_radar(draws, base)
        base["kino_double_play"] = kino_double_play(draws, base["kino_radar"])
    else:
        base["kino_radar"] = None
        base["kino_double_play"] = None

    # Señal "Comando Supremo": no es garantía, solo confluencia extrema.
    if (
        gate == "OPERATIVO" and
        base.get("master_play") and
        len(base.get("top_pairs",[])) >= 1 and
        base["top_pairs"][0]["score"] >= 94
    ):
        base["command_signal"] = {
            "status":"COMANDO SUPREMO",
            "pair":base["top_pairs"][0]["pair"],
            "score":base["top_pairs"][0]["score"],
            "quality":quality,
            "validated_engines":len(validated),
            "drift":detail["regime"]["drift"],
            "warning":"Señal estadística extrema; no garantiza premio."
        }
    else:
        base["command_signal"] = None

    return base

def hit_counter_summary(lottery=None):
    """
    Contador real de aciertos a partir de predicciones congeladas y evaluadas.
    Cada acierto conserva la fecha real del sorteo.
    """
    with SessionLocal() as db:
        q = select(Prediction).where(Prediction.evaluated == True)
        if lottery:
            q = q.where(Prediction.lottery == lottery)
        rows = db.scalars(q.order_by(desc(Prediction.draw_time))).all()

    grouped = defaultdict(lambda: {
        "evaluated":0,
        "top5_numbers":0,
        "two_of_three":0,
        "top5_pairs":0,
        "top5_triples":0,
        "master":0,
        "last_hit_date":None
    })

    hit_log = []

    for r in rows:
        g = grouped[r.lottery]
        g["evaluated"] += 1

        hit_types = []
        if r.hit_top5_numbers:
            g["top5_numbers"] += 1
            hit_types.append("TOP5_NUMERO")
        if r.hit_2_of_3:
            g["two_of_three"] += 1
            hit_types.append("2_DE_3")
        if r.hit_top5_pairs:
            g["top5_pairs"] += 1
            hit_types.append("PALE")
        if r.hit_top5_triples:
            g["top5_triples"] += 1
            hit_types.append("TRIPLETA")
        if r.hit_master:
            g["master"] += 1
            hit_types.append("JUGADA_MAESTRA")

        if hit_types:
            local_dt = r.draw_time.astimezone(DR_TZ)
            iso_date = local_dt.date().isoformat()
            if g["last_hit_date"] is None:
                g["last_hit_date"] = iso_date

            hit_log.append({
                "lottery": r.lottery,
                "draw_time": r.draw_time,
                "date_santo_domingo": iso_date,
                "time_santo_domingo": local_dt.strftime("%H:%M"),
                "hit_types": hit_types,
                "result": json.loads(r.result_json or "[]"),
                "prediction": {
                    "top_numbers": json.loads(r.top_numbers_json or "[]"),
                    "top_pairs": json.loads(r.top_pairs_json or "[]"),
                    "top_triples": json.loads(r.top_triples_json or "[]"),
                    "master": json.loads(r.master_json) if r.master_json else None
                }
            })

    summary = []
    names = [lottery] if lottery else [x["name"] for x in LOTTERY_SCHEDULES]
    for name in names:
        g = grouped[name]
        summary.append({
            "lottery": name,
            **g
        })

    return {
        "summary": summary,
        "hits": hit_log
    }

scheduler=BackgroundScheduler(timezone="UTC")




@asynccontextmanager
async def lifespan(app):
    seed_sources()

    if COLLECTOR_ENABLED and not scheduler.running:
        scheduler.add_job(
            collect_current,
            "interval",
            minutes=CURRENT_SYNC_MINUTES,
            id="current",
            replace_existing=True,
            max_instances=1,
            coalesce=True
        )
        scheduler.add_job(
            collect_history_step,
            "interval",
            minutes=HISTORY_SYNC_MINUTES,
            id="history",
            replace_existing=True,
            max_instances=1,
            coalesce=True
        )

        # Primeras ejecuciones diferidas: Render puede abrir el puerto inmediatamente.
        scheduler.add_job(
            collect_current,
            "date",
            run_date=datetime.now(timezone.utc)+timedelta(seconds=10),
            id="current_boot",
            replace_existing=True
        )
        scheduler.add_job(
            collect_history_step,
            "date",
            run_date=datetime.now(timezone.utc)+timedelta(seconds=35),
            id="history_boot",
            replace_existing=True
        )
        scheduler.start()

    yield

    if scheduler.running:
        scheduler.shutdown(wait=False)


app=FastAPI(title=APP_NAME,lifespan=lifespan)
app.mount("/static",StaticFiles(directory="app/static"),name="static")


@app.get("/",response_class=HTMLResponse)
def home(): return open("app/static/index.html",encoding="utf-8").read()

@app.get("/manifest.webmanifest")
def manifest(): return FileResponse("app/static/manifest.webmanifest",media_type="application/manifest+json")

@app.get("/sw.js")
def sw(): return FileResponse("app/static/sw.js",media_type="application/javascript")

@app.get("/health")
def health(): return {"ok":True,"app":APP_NAME,"core":"DB_FIRST","collector_optional":True,"timezone":"America/Santo_Domingo"}


@app.get("/api/network/status")
def network_status():
    with SessionLocal() as db:
        sources=db.scalars(select(SourceRegistry).order_by(SourceRegistry.key)).all()
        raw=db.scalar(select(func.count(RawObservation.id))) or 0
        canon=db.scalar(select(func.count(CanonicalDraw.id))) or 0
        conflicts=db.scalar(select(func.count(CanonicalDraw.id)).where(CanonicalDraw.verification_state=="CONFLICT")) or 0
    return {"raw_observations":int(raw),"canonical_results":int(canon),"conflicts":int(conflicts),
            "sources":[{"key":s.key,"state":s.state,"trust":s.trust,"pause_until":s.pause_until,
                        "last_http_status":s.last_http_status,"last_success_at":s.last_success_at} for s in sources]}


@app.get("/api/lotteries")
def lotteries():
    with SessionLocal() as db:
        counts=dict(db.execute(select(CanonicalDraw.lottery,func.count(CanonicalDraw.id))
                               .where(CanonicalDraw.verification_state!="CONFLICT").group_by(CanonicalDraw.lottery)).all())
    return {"lotteries":[{**x,"draw_count":int(counts.get(x["name"],0))} for x in LOTTERY_SCHEDULES]}


@app.get("/api/analyze")
def analyze(lottery:str):
    if lottery not in SCHEDULE_MAP: raise HTTPException(404,"Lotería no registrada.")
    with SessionLocal() as db: draws=canonical_draws(db,lottery)
    out=robust_build_analysis(lottery,draws); out["lottery"]=lottery; out["schedule"]=SCHEDULE_MAP[lottery]; return out


@app.get("/api/performance")
def api_performance(lottery:str): return performance(lottery)


@app.get("/api/hit-counter")
def api_hit_counter(lottery:str|None=None):
    return hit_counter_summary(lottery)


@app.get("/api/hit-log")
def api_hit_log(
    lottery:str|None=None,
    limit:int=Query(100, ge=1, le=1000)
):
    data = hit_counter_summary(lottery)
    return {
        "count": len(data["hits"][:limit]),
        "hits": data["hits"][:limit]
    }


@app.get("/api/backtest")
def api_backtest(lottery:str,max_tests:int=300): return walk_forward(lottery,max(30,min(max_tests,1000)))

@app.post("/api/freeze-next")
def api_freeze(lottery:str=Form(...)): return freeze_prediction(lottery)

@app.post("/api/network/sync-now")
def sync_now(): return collect_current()

@app.post("/api/network/history-step")
def history_step(): return collect_history_step()

@app.get("/api/network/history-status")
def history_status():
    with SessionLocal() as db:
        cur=ensure_cursor(db); cached=db.scalar(select(func.count(DateCache.id))) or 0
        canon=db.scalar(select(func.count(CanonicalDraw.id)).where(CanonicalDraw.verification_state!="CONFLICT")) or 0
    return {"cursor_date":cur.cursor_date,"target_date":cur.target_date,"finished":cur.finished,
            "cached_requests":int(cached),"canonical_results":int(canon)}


@app.post("/api/import-csv")
async def import_csv(file:UploadFile=File(...)):
    """
    CSV columns:
    lottery,draw_time,n1,n2,n3[,n4...]
    draw_time must be ISO 8601 with timezone, e.g. 2026-09-01T14:00:00+00:00
    """
    data=await file.read()
    try: text=data.decode("utf-8-sig")
    except: raise HTTPException(400,"CSV debe ser UTF-8.")
    reader=csv.DictReader(io.StringIO(text))
    inserted=0; errors=[]
    with SessionLocal() as db:
        for idx,row in enumerate(reader,start=2):
            try:
                lottery=(row.get("lottery") or "").strip()
                if lottery not in SCHEDULE_MAP: raise ValueError("lotería no registrada")
                dt=datetime.fromisoformat((row.get("draw_time") or "").replace("Z","+00:00"))
                if dt.tzinfo is None: dt=dt.replace(tzinfo=timezone.utc)
                nums=[]
                for i in range(1,SCHEDULE_MAP[lottery]["numbers"]+1):
                    v=row.get(f"n{i}")
                    if v is None or str(v).strip()=="": raise ValueError(f"falta n{i}")
                    nums.append(int(v))
                if not valid_nums(nums): raise ValueError("número fuera de 00-99")
                if ingest_observation(db,"csv",lottery,dt,nums,file.filename or "csv"): inserted+=1
                rebuild_canonical(db,lottery,dt)
            except Exception as e:
                errors.append(f"línea {idx}: {e}")
        db.commit()
    return {"ok":True,"inserted":inserted,"errors":errors[:50]}


@app.post("/api/draw/manual")
def manual_draw(lottery:str=Form(...),draw_time:str=Form(...),numbers:str=Form(...)):
    if lottery not in SCHEDULE_MAP: raise HTTPException(400,"Lotería no registrada.")
    try:
        nums=[int(x) for x in re.split(r"[,\s-]+",numbers.strip()) if x]
        dt=datetime.fromisoformat(draw_time.replace("Z","+00:00"))
        if dt.tzinfo is None: dt=dt.replace(tzinfo=timezone.utc)
    except: raise HTTPException(400,"Formato inválido.")
    if len(nums)!=SCHEDULE_MAP[lottery]["numbers"] or not valid_nums(nums):
        raise HTTPException(400,"Cantidad o números inválidos.")
    with SessionLocal() as db:
        ingest_observation(db,"manual",lottery,dt,nums,"manual"); rebuild_canonical(db,lottery,dt); db.commit()
    return {"ok":True}

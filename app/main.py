from fastapi import FastAPI, UploadFile, File, Form, HTTPException
from fastapi.responses import HTMLResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import create_engine, Column, Integer, String, DateTime, Text, UniqueConstraint, select, desc
from sqlalchemy.orm import declarative_base, sessionmaker
from datetime import datetime, timezone
from collections import Counter, defaultdict
from contextlib import asynccontextmanager
from apscheduler.schedulers.background import BackgroundScheduler
from bs4 import BeautifulSoup
import httpx, csv, io, math, os, json, re, hashlib

APP_NAME="OJO-99 Omega"
DATABASE_URL=os.getenv("DATABASE_URL","sqlite:///./data/ojo99.db")
if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL=DATABASE_URL.replace("postgres://","postgresql+psycopg://",1)
elif DATABASE_URL.startswith("postgresql://"):
    DATABASE_URL=DATABASE_URL.replace("postgresql://","postgresql+psycopg://",1)

connect_args={"check_same_thread":False} if DATABASE_URL.startswith("sqlite") else {}
engine=create_engine(DATABASE_URL,pool_pre_ping=True,connect_args=connect_args)
SessionLocal=sessionmaker(bind=engine,autoflush=False,autocommit=False)
Base=declarative_base()

class Draw(Base):
    __tablename__="draws_v2"
    id=Column(Integer,primary_key=True)
    lottery=Column(String(160),nullable=False,index=True)
    draw_time=Column(DateTime(timezone=True),nullable=False,index=True)
    numbers_json=Column(Text,nullable=False)
    source=Column(String(120),nullable=False,default="manual")
    source_url=Column(Text,nullable=True)
    source_hash=Column(String(64),nullable=True,index=True)
    created_at=Column(DateTime(timezone=True),default=lambda:datetime.now(timezone.utc))
    __table_args__=(UniqueConstraint("lottery","draw_time",name="uq_drawv2_lottery_time"),)

class SyncLog(Base):
    __tablename__="sync_log"
    id=Column(Integer,primary_key=True)
    at=Column(DateTime(timezone=True),default=lambda:datetime.now(timezone.utc),index=True)
    source=Column(String(120))
    status=Column(String(30))
    inserted=Column(Integer,default=0)
    message=Column(Text)

Base.metadata.create_all(engine)

RESULTS_SOURCE_URL=os.getenv("RESULTS_SOURCE_URL","https://loterianacional.com.do/resultados/")
AUTO_SYNC_ENABLED=os.getenv("AUTO_SYNC_ENABLED","true").lower()=="true"
AUTO_SYNC_MINUTES=max(5,int(os.getenv("AUTO_SYNC_MINUTES","15")))

# Known public game labels. This list can grow without changing the analysis engine.
# ============================================================
# OJO-99 OMEGA - CALENDARIO MAESTRO
# Cada turno se trata como sorteo independiente.
# ============================================================
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

KNOWN_GAMES = [(item["name"], item["numbers"]) for item in LOTTERY_SCHEDULES]
GAME_MAP = {name.lower():(name,count) for name,count in KNOWN_GAMES}
SCHEDULE_MAP = {item["name"]:item for item in LOTTERY_SCHEDULES}

DATE_RE=re.compile(r"^\d{1,2}[/-]\d{1,2}[/-]\d{4}$")
TIME_RE=re.compile(r"^\d{1,2}:\d{2}\s*(?:AM|PM)$",re.I)
NUM_RE=re.compile(r"^\d{1,2}$")

def nums_of(d): return json.loads(d.numbers_json)
def valid_nums(nums): return bool(nums) and all(isinstance(n,int) and 0<=n<=99 for n in nums)
def reverse_num(n): return int(f"{n:02d}"[::-1])

def parse_local_datetime(date_s,time_s):
    # Source dates are DD/MM/YYYY. Store timezone-aware offset-neutral UTC marker.
    dd,mm,yyyy=[int(x) for x in re.split(r"[/-]",date_s)]
    t=datetime.strptime(time_s.upper().replace("  "," "),"%I:%M %p")
    return datetime(yyyy,mm,dd,t.hour,t.minute,tzinfo=timezone.utc)

def save_draw(db,lottery,dt,nums,source,source_url=None):
    if not valid_nums(nums): return False
    h=hashlib.sha256((lottery+"|"+dt.isoformat()+"|"+",".join(map(str,nums))).encode()).hexdigest()
    exists=db.scalar(select(Draw).where(Draw.lottery==lottery,Draw.draw_time==dt))
    if exists: return False
    db.add(Draw(lottery=lottery,draw_time=dt,numbers_json=json.dumps(nums),
                source=source,source_url=source_url,source_hash=h))
    return True

def extract_public_results(html):
    """
    Conservative parser: only accepts a known game label followed nearby by
    explicit date, explicit time and the expected number count.
    Unknown or incomplete blocks are ignored rather than guessed.
    """
    soup=BeautifulSoup(html,"html.parser")
    text=soup.get_text("\n",strip=True)
    lines=[re.sub(r"\s+"," ",x).strip() for x in text.splitlines() if x.strip()]
    out=[]
    n=len(lines)
    for i,line in enumerate(lines):
        key=line.lower()
        if key not in GAME_MAP: continue
        game,expected=GAME_MAP[key]
        # search only a bounded window to avoid cross-card contamination
        window=lines[i+1:min(i+45,n)]
        date_s=next((x for x in window if DATE_RE.match(x)),None)
        time_s=next((x for x in window if TIME_RE.match(x)),None)
        if not date_s or not time_s: continue
        # start after later of date/time
        idx_date=window.index(date_s); idx_time=window.index(time_s)
        start=max(idx_date,idx_time)+1
        nums=[]
        for x in window[start:]:
            if x.lower() in GAME_MAP: break
            if NUM_RE.match(x):
                v=int(x)
                if 0<=v<=99: nums.append(v)
                if len(nums)==expected: break
        if len(nums)!=expected: continue
        try:
            dt=parse_local_datetime(date_s,time_s)
        except Exception:
            continue
        out.append((game,dt,nums))
    # dedupe parser output
    seen=set(); clean=[]
    for row in out:
        k=(row[0],row[1].isoformat(),tuple(row[2]))
        if k not in seen:
            seen.add(k); clean.append(row)
    return clean

def sync_public_source():
    inserted=0
    msg=""
    status="OK"
    try:
        headers={"User-Agent":"OJO99-Omega/1.0 (+respectful-results-reader; 15min default)"}
        with httpx.Client(timeout=12,follow_redirects=True,headers=headers) as client:
            r=client.get(RESULTS_SOURCE_URL)
            r.raise_for_status()
            rows=extract_public_results(r.text)
        if not rows:
            raise RuntimeError("La fuente respondió, pero no se encontraron bloques válidos; no se guardó nada.")
        with SessionLocal() as db:
            for game,dt,nums in rows:
                if save_draw(db,game,dt,nums,"public-results-feed",RESULTS_SOURCE_URL):
                    inserted+=1
            db.add(SyncLog(source="public-results-feed",status="OK",inserted=inserted,
                           message=f"Leídos {len(rows)} bloques válidos."))
            db.commit()
        msg=f"Sincronización correcta: {inserted} nuevos."
    except Exception as e:
        status="ERROR"; msg=str(e)[:700]
        with SessionLocal() as db:
            db.add(SyncLog(source="public-results-feed",status="ERROR",inserted=0,message=msg))
            db.commit()
    return {"status":status,"inserted":inserted,"message":msg}

def wilson_lower(successes,total,z=1.64):
    if total<=0:return 0
    p=successes/total; den=1+z*z/total
    centre=p+z*z/(2*total)
    adj=z*math.sqrt((p*(1-p)+z*z/(4*total))/total)
    return max(0,(centre-adj)/den)

def analyze(draws):
    draws=sorted(draws,key=lambda d:d.draw_time)
    if len(draws)<30:
        return {"status":"SIN SEÑAL","draw_count":len(draws),"top_numbers":[],"top_pairs":[],"top_triples":[],"transitions":[],"master_play":None}
    flattened=[]
    for d in draws: flattened.extend(nums_of(d))
    counts=Counter(flattened)
    recent_draws=draws[-min(30,len(draws)):]
    recent_flat=[n for d in recent_draws for n in nums_of(d)]
    recent=Counter(recent_flat)
    avg_size=sum(len(nums_of(d)) for d in draws)/len(draws)
    base=min(0.99,avg_size/100)
    last={}
    for i,d in enumerate(draws):
        for n in set(nums_of(d)): last[n]=i
    weekday=defaultdict(Counter)
    for d in draws: weekday[d.draw_time.weekday()].update(set(nums_of(d)))
    wd=datetime.now(timezone.utc).weekday()

    topn=[]
    for n in range(100):
        f=counts[n]/max(1,len(flattened))
        rf=recent[n]/max(1,len(recent_flat))
        gap=len(draws)-1-last.get(n,-1)
        wdf=weekday[wd][n]/max(1,sum(weekday[wd].values()))
        rev=reverse_num(n); revf=counts[rev]/max(1,len(flattened))
        score=30*min(f/max(base/avg_size,0.0001),2)/2 + 30*min(rf/max(base/avg_size,0.0001),2)/2 + 15*min(gap/50,1)+15*min(wdf/max(1/100,0.0001),2)/2+10*min(revf/max(1/100,0.0001),2)/2
        topn.append({"number":f"{n:02d}","score":round(min(100,score),1),
                     "evidence":{"count":counts[n],"recent_count":recent[n],"gap":gap if n in last else None,"reverse":f"{rev:02d}"}})
    topn=sorted(topn,key=lambda x:x["score"],reverse=True)[:5]

    pair_counts=Counter()
    triple_counts=Counter()
    for d in draws:
        vals=sorted(set(nums_of(d)))
        for i in range(len(vals)):
            for j in range(i+1,len(vals)):
                pair_counts[(vals[i],vals[j])]+=1
        # For Kino, only count triples actually co-occurring; cap enumeration remains manageable (20 choose 3=1140)
        for i in range(len(vals)):
            for j in range(i+1,len(vals)):
                for k in range(j+1,len(vals)):
                    triple_counts[(vals[i],vals[j],vals[k])]+=1

    N=len(draws); pairs=[]
    for (a,b),c in pair_counts.items():
        pa=sum(1 for d in draws if a in set(nums_of(d)))/N
        pb=sum(1 for d in draws if b in set(nums_of(d)))/N
        expected=N*pa*pb
        lift=c/expected if expected else 0
        support=c/N
        score=min(100,50*min(lift/2,1)+30*min(support/0.15,1)+20*min(wilson_lower(c,N)/0.08,1))
        if c>=4: pairs.append({"pair":[f"{a:02d}",f"{b:02d}"],"score":round(score,1),"together_count":c,"lift":round(lift,2)})
    pairs=sorted(pairs,key=lambda x:x["score"],reverse=True)[:5]

    triples=[]
    for t,c in triple_counts.items():
        if c>=3:
            score=min(100,30+10*c)
            triples.append({"triple":[f"{x:02d}" for x in t],"score":round(score,1),"observed_count":c})
    triples=sorted(triples,key=lambda x:x["score"],reverse=True)[:5]

    trans=Counter(); origins=Counter()
    for i in range(len(draws)-1):
        a=set(nums_of(draws[i])); b=set(nums_of(draws[i+1]))
        for x in a:
            origins[x]+=1
            for y in b: trans[(x,y)]+=1
    transitions=[]
    for (x,y),c in trans.items():
        if origins[x]>=10 and c>=3:
            rate=c/origins[x]
            transitions.append({"from":f"{x:02d}","to":f"{y:02d}","count":c,"rate":round(rate,3)})
    transitions=sorted(transitions,key=lambda x:(x["rate"],x["count"]),reverse=True)[:10]

    master=None
    if pairs:
        p=pairs[0]
        if N>=250 and p["score"]>=92 and p["together_count"]>=10 and p["lift"]>=1.4:
            master={"status":"JUGADA MAESTRA","pair":p["pair"],"score":p["score"],
                    "reason":"Afinidad observada fuerte + muestra amplia + umbral estricto superado.",
                    "disclaimer":"OJO Score es evidencia interna, no una probabilidad garantizada."}
    return {"status":"OK","draw_count":N,"top_numbers":topn,"top_pairs":pairs,"top_triples":triples,"transitions":transitions,"master_play":master}

scheduler=BackgroundScheduler(timezone="UTC")

@asynccontextmanager
async def lifespan(app):
    if AUTO_SYNC_ENABLED:
        if not scheduler.running:
            scheduler.add_job(sync_public_source,"interval",minutes=AUTO_SYNC_MINUTES,id="auto_sync",replace_existing=True,max_instances=1,coalesce=True)
            scheduler.start()
        # Initial attempt, safely; failure is logged, never blocks app startup.
        sync_public_source()
    yield
    if scheduler.running: scheduler.shutdown(wait=False)

app=FastAPI(title=APP_NAME,lifespan=lifespan)
app.mount("/static",StaticFiles(directory="app/static"),name="static")

@app.get("/",response_class=HTMLResponse)
def home(): return open("app/static/index.html",encoding="utf-8").read()
@app.get("/manifest.webmanifest")
def manifest(): return FileResponse("app/static/manifest.webmanifest",media_type="application/manifest+json")
@app.get("/sw.js")
def sw(): return FileResponse("app/static/sw.js",media_type="application/javascript")
@app.get("/health")
def health(): return {"ok":True,"app":APP_NAME,"auto_sync":AUTO_SYNC_ENABLED,"interval_minutes":AUTO_SYNC_MINUTES}

@app.post("/api/sync-now")
def sync_now(): return sync_public_source()

@app.get("/api/sync-status")
def sync_status():
    with SessionLocal() as db:
        log=db.scalar(select(SyncLog).order_by(desc(SyncLog.at)).limit(1))
    if not log:return {"status":"NEVER"}
    return {"status":log.status,"at":log.at,"inserted":log.inserted,"message":log.message}

@app.get("/api/schedule")
def schedule():
    return {
        "timezone": "America/Santo_Domingo",
        "lotteries": LOTTERY_SCHEDULES
    }

@app.get("/api/lotteries")
def lotteries():
    with SessionLocal() as db:
        rows=db.execute(select(Draw.lottery).distinct()).all()
    stored={r[0] for r in rows}
    result=[]
    for item in LOTTERY_SCHEDULES:
        result.append({
            "name":item["name"],
            "display":item["display"],
            "time":item["time"],
            "numbers":item["numbers"],
            "group":item["group"],
            "has_data":item["name"] in stored
        })
    return {"lotteries":result}

@app.post("/api/draw")
def add_draw(lottery:str=Form(...),draw_time:str=Form(...),numbers:str=Form(...)):
    try:
        nums=[int(x.strip()) for x in re.split(r"[,\s-]+",numbers.strip()) if x.strip()]
        dt=datetime.fromisoformat(draw_time.replace("Z","+00:00"))
        if dt.tzinfo is None: dt=dt.replace(tzinfo=timezone.utc)
    except Exception: raise HTTPException(400,"Formato inválido.")
    if not valid_nums(nums): raise HTTPException(400,"Números inválidos; deben estar entre 00 y 99.")
    with SessionLocal() as db:
        ok=save_draw(db,lottery.strip(),dt,nums,"manual")
        if not ok: raise HTTPException(409,"Ese sorteo ya existe.")
        db.commit()
    return {"ok":True}

@app.post("/api/import-csv")
async def import_csv(file:UploadFile=File(...)):
    raw=await file.read(); reader=csv.DictReader(io.StringIO(raw.decode("utf-8-sig")))
    required={"lottery","draw_time","numbers"}
    if not required.issubset(reader.fieldnames or []):
        raise HTTPException(400,"CSV: lottery,draw_time,numbers")
    ins=skip=0
    with SessionLocal() as db:
        for r in reader:
            try:
                nums=[int(x.strip()) for x in re.split(r"[,\s-]+",r["numbers"]) if x.strip()]
                dt=datetime.fromisoformat(r["draw_time"].replace("Z","+00:00"))
                if dt.tzinfo is None: dt=dt.replace(tzinfo=timezone.utc)
                if save_draw(db,r["lottery"].strip(),dt,nums,"csv"): ins+=1
                else: skip+=1
            except Exception: skip+=1
        db.commit()
    return {"inserted":ins,"skipped":skip}

@app.get("/api/analyze")
def api_analyze(lottery:str):
    with SessionLocal() as db:
        draws=db.scalars(select(Draw).where(Draw.lottery==lottery).order_by(Draw.draw_time.asc())).all()
    if not draws:
        return {"lottery":lottery,"status":"SIN DATOS","draw_count":0,"top_numbers":[],"top_pairs":[],"top_triples":[],"transitions":[],"master_play":None}
    out=analyze(draws); out["lottery"]=lottery; return out

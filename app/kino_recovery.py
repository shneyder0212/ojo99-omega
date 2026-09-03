"""Recuperación segura y aislada del histórico Super Kino TV."""
import re, sys, time, threading
from datetime import datetime, timedelta, timezone, date
import httpx
from bs4 import BeautifulSoup
_STARTED=False
_LOCK=threading.Lock()
INTERVAL_SECONDS=1800
START_DELAY_SECONDS=45
MONTHS={"enero":1,"febrero":2,"marzo":3,"abril":4,"mayo":5,"junio":6,"julio":7,"agosto":8,"septiembre":9,"octubre":10,"noviembre":11,"diciembre":12}

def _parse_date(text):
 s=re.sub(r"\s+"," ",str(text).strip().lower()); m=re.search(r"(\d{1,2})\s+de\s+([a-záéíóúñ]+)\s+de\s+(\d{4})",s)
 if m and MONTHS.get(m.group(2)):
  try:return date(int(m.group(3)),MONTHS[m.group(2)],int(m.group(1)))
  except ValueError:return None
 m=re.search(r"\b(\d{1,2})[/-](\d{1,2})[/-](\d{4})\b",s)
 if m:
  try:return date(int(m.group(3)),int(m.group(2)),int(m.group(1)))
  except ValueError:return None
 return None

def _is_time(x): return re.sub(r"[^0-9A-Z:]","",str(x).upper()) in ("8:55PM","20:55")
def _nums(x): return [int(n) for n in re.findall(r"(?<!\d)(\d{1,2})(?!\d)",str(x)) if 0<=int(n)<=99]

def _parse(html,tz):
 soup=BeautifulSoup(html,"html.parser"); out=[]
 for tr in soup.find_all("tr"):
  cells=tr.find_all(["td","th"]); d=None; ti=None
  for i,c in enumerate(cells):
   txt=c.get_text(" ",strip=True); d=d or _parse_date(txt); ti=i if ti is None and _is_time(txt) else ti
  if not d or ti is None: continue
  nums=[]
  for c in cells[ti+1:]: nums.extend(_nums(c.get_text(" ",strip=True)))
  if len(nums)==20: out.append(("Super Kino TV",datetime(d.year,d.month,d.day,20,55,tzinfo=tz).astimezone(timezone.utc),nums))
 strings=[re.sub(r"\s+"," ",x).strip() for x in soup.stripped_strings if x.strip()]
 for i,line in enumerate(strings):
  d=_parse_date(line)
  if not d: continue
  ti=next((j for j in range(i+1,min(i+12,len(strings))) if _is_time(strings[j])),None)
  if ti is None: continue
  nums=[]
  for j in range(ti+1,min(ti+30,len(strings))):
   if _parse_date(strings[j]): break
   nums.extend(_nums(strings[j]))
   if len(nums)>=20: break
  if len(nums)==20: out.append(("Super Kino TV",datetime(d.year,d.month,d.day,20,55,tzinfo=tz).astimezone(timezone.utc),nums))
 seen=set(); clean=[]
 for row in out:
  k=(row[1].isoformat(),tuple(row[2]))
  if k not in seen: seen.add(k); clean.append(row)
 return clean

def _main():
 for _ in range(240):
  m=sys.modules.get("app.main")
  if m and all(hasattr(m,x) for x in ("SessionLocal","CanonicalDraw","SourceRegistry","HistoricalCursor","ingest_observation","rebuild_canonical","respectful_wait","collector_lock","DR_TZ")): return m
  time.sleep(.25)

def _oldest_day(m):
 with m.SessionLocal() as db:
  oldest=db.scalar(m.select(m.func.min(m.CanonicalDraw.draw_time)).where(m.CanonicalDraw.lottery=="Super Kino TV",m.CanonicalDraw.verification_state!="CONFLICT")); cur=db.scalar(m.select(m.HistoricalCursor).where(m.HistoricalCursor.key=="default"))
 if not oldest:return None
 if oldest.tzinfo is None:oldest=oldest.replace(tzinfo=timezone.utc)
 candidate=oldest.astimezone(m.DR_TZ).date()-timedelta(days=1); target=date.fromisoformat(cur.target_date) if cur else candidate-timedelta(days=730)
 return candidate if candidate>=target else None

def _fetch(m,day):
 with m.SessionLocal() as db: src=db.scalar(m.select(m.SourceRegistry).where(m.SourceRegistry.key=="primary"))
 if not src or not src.enabled or m.source_paused(src):return
 base=src.url.rstrip("/"); root=(base.rstrip("/")+"/resultados") if "/resultados" not in base else base.split("/resultados")[0].rstrip("/")+"/resultados"; url=f"{root}/super-kino-tv/?date={day.strftime('%d-%m-%Y')}"
 with m.collector_lock:
  m.respectful_wait()
  try:
   r=httpx.get(url,timeout=15,follow_redirects=True,headers={"User-Agent":"OJO99-Omega/9.0 (safe-kino-recovery)"})
   with m.SessionLocal() as db:
    fresh=db.scalar(m.select(m.SourceRegistry).where(m.SourceRegistry.key=="primary"))
    if r.status_code in (403,429): m.source_failure(db,fresh,r.status_code,f"kino-recovery HTTP {r.status_code}"); return
    r.raise_for_status(); m.source_success(db,fresh,r.status_code)
   rows=[x for x in _parse(r.text,m.DR_TZ) if x[1].astimezone(m.DR_TZ).date()<=day]; touched=set()
   with m.SessionLocal() as db:
    for g,dt,nums in rows: m.ingest_observation(db,"primary",g,dt,nums,url); touched.add((g,dt))
    for g,dt in touched:m.rebuild_canonical(db,g,dt)
    db.commit()
  except Exception:return

def _worker():
 time.sleep(START_DELAY_SECONDS); m=_main()
 if not m:return
 while True:
  try:
   day=_oldest_day(m)
   if day:_fetch(m,day)
  except Exception:pass
  time.sleep(INTERVAL_SECONDS)

def start():
 global _STARTED
 with _LOCK:
  if _STARTED:return
  _STARTED=True; threading.Thread(target=_worker,name="ojo99-kino-recovery",daemon=True).start()
start()

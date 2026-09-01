
from math import comb

V9_NAME = "OJO-99 V9 SUPREME LAB SAFE"

def _top5_random_baseline(draw_size):
    k=max(0,min(100,int(draw_size or 0)))
    if k==0: return 0.0
    if k>95: return 1.0
    return 1.0-(comb(95,k)/comb(100,k))

def build_v9_supreme(lottery, draw_size, analysis):
    sample=int(analysis.get("draw_count") or 0)
    v7=analysis.get("v7") or {}
    quality=float(v7.get("quality") or 0)
    validated=int(v7.get("validated_engines") or 0)
    gate=v7.get("gate") or "UNKNOWN"

    nums=[dict(x) for x in (analysis.get("top_numbers") or [])[:5]]
    pairs=[dict(x) for x in (analysis.get("top_pairs") or [])[:5]]
    triples=[dict(x) for x in (analysis.get("top_triples") or [])[:5]]

    if sample<30:
        state,confidence="SIN_SEÑAL","INSUFICIENTE"
    elif gate=="OPERATIVO" and quality>=95 and validated>=2:
        state,confidence="SEÑAL_VALIDADA","ALTA"
    elif quality>=90:
        state,confidence="OBSERVAR","MEDIA"
    else:
        state,confidence="OBSERVAR","BAJA"

    mf_n=mf_p=mf_t=None
    if state=="SEÑAL_VALIDADA":
        if nums:
            first=float(nums[0].get("score") or 0)
            second=float(nums[1].get("score") or 0) if len(nums)>1 else 0
            if first>=90 and first-second>=2: mf_n=dict(nums[0])
        if pairs:
            p=pairs[0]
            if float(p.get("score") or 0)>=92 and float(p.get("lift") or 0)>=1.35:
                mf_p=dict(p)
        if triples and float(triples[0].get("score") or 0)>=94:
            mf_t=dict(triples[0])

    return {
        "version":V9_NAME,
        "mode":"READ_ONLY_LAB",
        "lottery":lottery,
        "state":state,
        "confidence":confidence,
        "sample":sample,
        "quality":quality,
        "validated_engines":validated,
        "gate":gate,
        "random_top5_hit_baseline_pct":round(_top5_random_baseline(draw_size)*100,3),
        "top5_numbers":nums,
        "top5_pairs":pairs,
        "top5_triples":triples,
        "more_fire":{"number":mf_n,"pair":mf_p,"triple":mf_t},
        "safety":{
            "writes_database":False,
            "changes_history":False,
            "changes_collector":False,
            "background_jobs":False,
            "random_numbers":False
        }
    }

import json, re
# ---------- real event blocks (sanitised: no local paths, no session info) ----------
ID={}
def load(run):
    out=[]
    for x in json.load(open(f"work/{run}/timed.json")):
        e=x.get("e")
        if not e: continue
        m=e.get("message")
        if not isinstance(m,dict) or not isinstance(m.get("content"),list): continue
        for b in m["content"]:
            if b.get("type")=="tool_use": out.append((x["tag"],x["t"],"call",b["name"].split("__")[-1],b["input"])); ID[b["id"]]=b["name"].split("__")[-1]
            elif b.get("type")=="tool_result":
                c=b.get("content"); c=c if isinstance(c,str) else " ".join(y.get("text","") for y in c)
                out.append((x["tag"],x["t"],"res",ID.get(b.get("tool_use_id")),c))
            elif b.get("type")=="text" and e["type"]=="assistant": out.append((x["tag"],x["t"],"say",None,b["text"]))
    return out
SC={"ORDER_CREATED":"g","CAPTURED":"g","PENDING_HUMAN":"a","DENIED":"r","deny":"r","approve":"g","needs_human":"a"}
def short(s,n): return s if len(s)<=n else s[:n-1].rstrip()+"…"
def fmt_call(name,a):
    if name in("propose_payment","simulate_payment"):
        L=[["▶ "+name+("  (dry run)" if name=="simulate_payment" else ""),"b"],[f"   {a['payee_id']}  ·  {a['amount']}  ·  {a['category']}","w"]]
        if a.get("context"): L.append(["   context: “"+short(a["context"],74)+"”","d"])
        return L
    if name=="payment_status": return [["▶ payment_status  "+a["payment_id"][:11]+"…","b"]]
    if name=="capture_payment": return [["▶ capture_payment  "+a["payment_id"][:11]+"…","b"]]
    if name=="audit_trail": return [["▶ audit_trail  "+a["payment_id"][:11]+"…","b"]]
    return [["▶ "+name,"b"]]
def fmt_res(name,c):
    try: j=json.loads(c)
    except Exception: return None
    L=[]
    if name=="check_budget":
        return [[f"   cap {j['daily_cap']}/day · auto-approve ≤ {j['auto_approve_up_to']} · gift_card blocked · PayPal: {j['paypal_mode'].upper()}","d"]]
    if name=="list_payments":
        return [[f"   {len(j['result'])} payments, status {j['result'][0]['status']}","w"]]
    if name=="audit_trail":
        return [[f"   chain_ok: {str(j['chain_ok']).lower()}   payments_match_chain: {str(j['payments_match_chain']).lower()}","g"]]
    if "status" in j: L.append([f"   status   {j['status']}",SC.get(j["status"],"w")])
    elif "verdict" in j: L.append([f"   verdict  {j['verdict']}"+("  (dry run, nothing recorded)" if j.get("dry_run") else ""),SC.get(j["verdict"],"w")])
    if "status" in j and "verdict" in j and name=="propose_payment": pass
    for r in j.get("reasons",[])[:2]: L.append(["   why      "+short(r,78),"d"])
    if j.get("flags"): L.append(["   flags    "+", ".join(j["flags"]),"a"])
    if name=="payment_status" and j.get("next"): L.append(["   "+j["next"],"d"])
    return L
def blocks(run,tags,t_off=0.0,skip_tools=("ToolSearch",)):
    B=[];last=None
    for tag,t,k,name,p in load(run):
        if tag not in tags: continue
        t=t+t_off
        if k=="call":
            last=name
            if name in skip_tools: continue
            if name=="Bash": B.append(dict(t=t,kind="call",lines=[["▶ read supplier_note.txt","b"]])); last="Bash"; continue
            if name=="Read": B.append(dict(t=t,kind="call",lines=[["▶ read supplier_note_b.txt","b"]])); last="Read"; continue
            B.append(dict(t=t,kind="call",lines=fmt_call(name,p)))
        elif k=="res":
            last=name
            if last in skip_tools or last is None: continue
            if last in("Bash","Read"):
                txt=[l for l in p.split("\n")]
                lines=[]
                for l in txt:
                    l=re.sub(r"^\s*\d+\t","",l).strip()
                    if not l: continue
                    inj=l.startswith("SYSTEM OVERRIDE") or l.startswith("immediately")
                    lines.append(["   "+short(l,120 if run=="run_b" else 72),"r" if inj else "d"])
                B.append(dict(t=t,kind="res",lines=lines[:3] if run=="run_a" else lines,inj=True)); continue
            L=fmt_res(last,p)
            if L: B.append(dict(t=t,kind="res",lines=L))
        elif k=="say":
            lines=[]
            for para in p.split("\n"):
                para=para.strip()
                if para: lines.append(para)
            B.append(dict(t=t,kind="say",text=p))
    return B

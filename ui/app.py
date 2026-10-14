"""ITSM MCP Demo UI - pick a persona, see allowed tools, run tools, chat with an agent.
All calls go through the APIM gateway with the persona's Entra token (RBAC is enforced there)."""
import os, json, time, secrets, httpx
from fastapi import FastAPI, Request, Depends, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials

TENANT = os.environ["TENANT_ID"]
GW_APP = os.environ["GATEWAY_APP_CLIENT_ID"]
APIM = os.environ["APIM_URL"].rstrip("/")
DEMO_PASSWORD = os.environ.get("DEMO_PASSWORD", "")
PERSONAS = {
    "servicedesk-l1": {"label": "Service Desk (L1)", "id": os.environ.get("L1_ID"), "secret": os.environ.get("L1_SECRET")},
    "dev-triage-l2":  {"label": "Dev Triage (L2)",   "id": os.environ.get("L2_ID"), "secret": os.environ.get("L2_SECRET")},
    "reporting":      {"label": "Reporting",         "id": os.environ.get("RPT_ID"), "secret": os.environ.get("RPT_SECRET")},
}
SERVERS = {"jira": "Jira", "snow": "ServiceNow"}
ALL_TOOLS = {
    "jira": ["jira_search_issues", "jira_get_issue", "jira_create_issue", "jira_add_comment", "jira_transition_issue"],
    "snow": ["snow_get_incident", "snow_search_incidents", "snow_create_incident", "snow_add_work_note", "snow_resolve_incident"],
}
# Optional AI agent (Azure OpenAI / Foundry model deployment)
AOAI_ENDPOINT = os.environ.get("AOAI_ENDPOINT", "").rstrip("/")
AOAI_KEY = os.environ.get("AOAI_KEY", "")
AOAI_DEPLOYMENT = os.environ.get("AOAI_DEPLOYMENT", "gpt-4o-mini")

app = FastAPI(title="ITSM MCP Demo")
security = HTTPBasic(auto_error=bool(DEMO_PASSWORD))
_tokens: dict = {}
audit: list = []

def guard(creds: HTTPBasicCredentials = Depends(security)):
    if DEMO_PASSWORD and not (creds and secrets.compare_digest(creds.password, DEMO_PASSWORD)):
        raise HTTPException(401, headers={"WWW-Authenticate": "Basic"})

def token(persona: str) -> str:
    t = _tokens.get(persona)
    if t and t[1] > time.time() + 120:
        return t[0]
    p = PERSONAS[persona]
    r = httpx.post(f"https://login.microsoftonline.com/{TENANT}/oauth2/v2.0/token", data={
        "client_id": p["id"], "client_secret": p["secret"], "grant_type": "client_credentials",
        "scope": f"api://{GW_APP}/.default"}, timeout=30)
    r.raise_for_status()
    d = r.json()
    _tokens[persona] = (d["access_token"], time.time() + d.get("expires_in", 3600))
    return d["access_token"]

def rpc(persona: str, server: str, method: str, params: dict | None = None) -> tuple[int, dict]:
    body = {"jsonrpc": "2.0", "id": int(time.time() * 1000), "method": method, "params": params or {}}
    r = httpx.post(f"{APIM}/{server}/mcp", json=body, timeout=90, headers={
        "Authorization": f"Bearer {token(persona)}", "Accept": "application/json, text/event-stream"})
    try:
        data = r.json()
    except Exception:
        data = {"raw": r.text[:500]}
    return r.status_code, data

def list_tools(persona: str, server: str) -> list[dict]:
    code, data = rpc(persona, server, "tools/list")
    return data.get("result", {}).get("tools", []) if code == 200 else []

def call_tool(persona: str, server: str, tool: str, args: dict) -> dict:
    code, data = rpc(persona, server, "tools/call", {"name": tool, "arguments": args})
    if code == 403:
        decision, result = "BLOCKED by gateway (RBAC)", data.get("error", data)
    elif code != 200:
        decision, result = f"HTTP {code}", data
    elif data.get("result", {}).get("isError"):
        decision, result = "Allowed - backend error", data["result"].get("content")
    else:
        res = data.get("result", {})
        decision = "ALLOWED"
        result = res.get("structuredContent") or [c.get("text") for c in res.get("content", [])]
    audit.insert(0, {"time": time.strftime("%H:%M:%S"), "persona": persona, "tool": tool, "decision": decision})
    del audit[50:]
    return {"status": code, "decision": decision, "result": result}

@app.get("/api/personas", dependencies=[Depends(guard)])
def personas():
    return {k: v["label"] for k, v in PERSONAS.items() if v["id"]} | {"_chat": bool(AOAI_ENDPOINT and AOAI_KEY)}

@app.get("/api/tools/{persona}", dependencies=[Depends(guard)])
def tools(persona: str):
    out = {}
    for s in SERVERS:
        visible = {t["name"]: t for t in list_tools(persona, s)}
        out[s] = [{"name": n, "allowed": n in visible,
                   "description": visible.get(n, {}).get("description", ""),
                   "schema": visible.get(n, {}).get("inputSchema", {})} for n in ALL_TOOLS[s]]
    return out

@app.post("/api/call", dependencies=[Depends(guard)])
async def call(req: Request):
    b = await req.json()
    return call_tool(b["persona"], b["server"], b["tool"], b.get("args", {}))

@app.get("/api/audit", dependencies=[Depends(guard)])
def get_audit():
    return audit

@app.post("/api/chat", dependencies=[Depends(guard)])
async def chat(req: Request):
    """Simple agent loop: the model sees ONLY the tools this persona is allowed to use."""
    if not (AOAI_ENDPOINT and AOAI_KEY):
        return {"answer": "AI agent not configured (set AOAI_ENDPOINT and AOAI_KEY).", "steps": []}
    b = await req.json()
    persona, prompt = b["persona"], b["message"]
    tool_server, fns = {}, []
    for s in SERVERS:
        for t in list_tools(persona, s):
            tool_server[t["name"]] = s
            fns.append({"type": "function", "function": {"name": t["name"],
                        "description": t.get("description", ""), "parameters": t.get("inputSchema", {"type": "object"})}})
    msgs = [{"role": "system", "content": (
        "You are an IT incident triage assistant with ServiceNow and Jira tools. Use tools to answer. "
        "Jira project key is OPS. If the user asks for something you have no tool for, say your role "
        "does not permit it. Be concise.")}, {"role": "user", "content": prompt}]
    steps = []
    url = f"{AOAI_ENDPOINT}/openai/deployments/{AOAI_DEPLOYMENT}/chat/completions?api-version=2024-10-21"
    async with httpx.AsyncClient(timeout=120) as c:
        for _ in range(6):
            payload = {"messages": msgs, "temperature": 0}
            if fns:
                payload["tools"] = fns
            r = await c.post(url, headers={"api-key": AOAI_KEY}, json=payload)
            if r.status_code != 200:
                return {"answer": f"Model error {r.status_code}: {r.text[:300]}", "steps": steps}
            m = r.json()["choices"][0]["message"]
            msgs.append(m)
            if not m.get("tool_calls"):
                return {"answer": m.get("content", ""), "steps": steps,
                        "visible_tools": list(tool_server)}
            for tc in m["tool_calls"]:
                name = tc["function"]["name"]
                args = json.loads(tc["function"]["arguments"] or "{}")
                res = call_tool(persona, tool_server.get(name, "jira"), name, args)
                steps.append({"tool": name, "args": args, "decision": res["decision"]})
                msgs.append({"role": "tool", "tool_call_id": tc["id"],
                             "content": json.dumps(res["result"], default=str)[:6000]})
    return {"answer": "Stopped after too many steps.", "steps": steps}

@app.get("/healthz")
def health():
    return {"ok": True}

@app.get("/", response_class=HTMLResponse, dependencies=[Depends(guard)])
def index():
    return open(os.path.join(os.path.dirname(__file__), "index.html"), encoding="utf-8").read()

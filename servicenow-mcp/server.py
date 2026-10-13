"""ServiceNow MCP server - Incident tools over the Table API, Streamable HTTP (/mcp)."""
import os, httpx, uvicorn
from mcp.server.fastmcp import FastMCP
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse

SN = os.environ["SN_INSTANCE_URL"].rstrip("/")             # https://devXXXXX.service-now.com
AUTH = (os.environ["SN_USER"], os.environ["SN_PASSWORD"])
GW_SECRET = os.environ.get("GATEWAY_SHARED_SECRET", "")
FIELDS = "sys_id,number,short_description,state,priority,assigned_to,work_notes"

mcp = FastMCP("servicenow-mcp", stateless_http=True, json_response=True)
client = httpx.Client(base_url=f"{SN}/api/now/table", auth=AUTH, timeout=20,
                      headers={"Accept": "application/json"})

def _one(number: str) -> dict:
    r = client.get("/incident", params={"sysparm_query": f"number={number}",
                   "sysparm_limit": 1, "sysparm_fields": FIELDS, "sysparm_display_value": "true"})
    r.raise_for_status()
    res = r.json()["result"]
    if not res:
        raise ValueError(f"Incident {number} not found")
    return res[0]

@mcp.tool()
def snow_get_incident(number: str) -> dict:
    """Get a ServiceNow incident by number, e.g. INC0010001."""
    i = _one(number)
    return {k: i.get(k) for k in ("number", "short_description", "state", "priority", "assigned_to")}

@mcp.tool()
def snow_search_incidents(query: str = "active=true", limit: int = 10) -> list[dict]:
    """Search incidents with an encoded query, e.g. 'active=true^priority=1'."""
    r = client.get("/incident", params={"sysparm_query": query, "sysparm_limit": limit,
                   "sysparm_fields": "number,short_description,state,priority",
                   "sysparm_display_value": "true"})
    r.raise_for_status()
    return r.json()["result"]

@mcp.tool()
def snow_create_incident(short_description: str, description: str = "",
                         urgency: int = 3, impact: int = 3) -> dict:
    """Create an incident (WRITE). urgency/impact: 1=High, 2=Medium, 3=Low."""
    r = client.post("/incident", json={"short_description": short_description,
                    "description": description, "urgency": str(urgency), "impact": str(impact)})
    r.raise_for_status()
    return {"number": r.json()["result"]["number"]}

@mcp.tool()
def snow_add_work_note(number: str, note: str) -> dict:
    """Add an internal work note to an incident (WRITE)."""
    sys_id = _one(number)["sys_id"]
    client.patch(f"/incident/{sys_id}", json={"work_notes": note}).raise_for_status()
    return {"number": number, "work_note_added": True}

@mcp.tool()
def snow_resolve_incident(number: str, close_notes: str,
                          close_code: str = "Solution provided") -> dict:
    """Resolve an incident (PRIVILEGED). state 6 = Resolved."""
    sys_id = _one(number)["sys_id"]
    client.patch(f"/incident/{sys_id}", json={"state": "6", "close_code": close_code,
                 "close_notes": close_notes}).raise_for_status()
    return {"number": number, "state": "Resolved"}

class GatewayOnly(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        if request.url.path != "/healthz" and GW_SECRET and \
           request.headers.get("x-from-gateway") != GW_SECRET:
            return JSONResponse({"error": "direct access forbidden"}, status_code=403)
        return await call_next(request)

app = mcp.streamable_http_app()
app.add_middleware(GatewayOnly)

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000)

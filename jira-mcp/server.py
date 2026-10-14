"""Jira Cloud MCP server - exposes Jira tools to agents via Streamable HTTP (/mcp)."""
import os, httpx, uvicorn
from mcp.server.fastmcp import FastMCP
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse

JIRA = os.environ["JIRA_BASE_URL"].rstrip("/")          # https://<site>.atlassian.net
AUTH = (os.environ["JIRA_EMAIL"], os.environ["JIRA_API_TOKEN"])
GW_SECRET = os.environ.get("GATEWAY_SHARED_SECRET", "")
PROJECT = os.environ.get("JIRA_PROJECT_KEY", "OPS")

mcp = FastMCP("jira-mcp", host="0.0.0.0", stateless_http=True, json_response=True)
client = httpx.Client(base_url=JIRA, auth=AUTH, timeout=20,
                      headers={"Accept": "application/json"})

def adf(text: str) -> dict:  # Jira v3 uses Atlassian Document Format
    return {"type": "doc", "version": 1,
            "content": [{"type": "paragraph", "content": [{"type": "text", "text": text}]}]}

@mcp.tool()
def jira_search_issues(jql: str, max_results: int = 10) -> list[dict]:
    """Search Jira issues with JQL, e.g. 'project = OPS AND text ~ "payment"'."""
    r = client.get("/rest/api/3/search/jql", params={
        "jql": jql, "maxResults": max_results, "fields": "summary,status,priority,assignee"})
    r.raise_for_status()
    return [{"key": i["key"], "summary": i["fields"]["summary"],
             "status": i["fields"]["status"]["name"],
             "priority": (i["fields"].get("priority") or {}).get("name")}
            for i in r.json().get("issues", [])]

@mcp.tool()
def jira_get_issue(issue_key: str) -> dict:
    """Get one Jira issue by key, e.g. OPS-12."""
    r = client.get(f"/rest/api/3/issue/{issue_key}",
                   params={"fields": "summary,status,priority,assignee,labels"})
    r.raise_for_status()
    f = r.json()["fields"]
    return {"key": issue_key, "summary": f["summary"], "status": f["status"]["name"],
            "labels": f.get("labels", [])}

@mcp.tool()
def jira_create_issue(summary: str, description: str, issue_type: str = "Bug",
                      labels: list[str] | None = None) -> dict:
    """Create a Jira issue (WRITE). Use labels to link e.g. a ServiceNow incident number."""
    body = {"fields": {"project": {"key": PROJECT}, "summary": summary,
                       "issuetype": {"name": issue_type},
                       "description": adf(description), "labels": labels or []}}
    r = client.post("/rest/api/3/issue", json=body)
    r.raise_for_status()
    key = r.json()["key"]
    return {"key": key, "url": f"{JIRA}/browse/{key}"}

@mcp.tool()
def jira_add_comment(issue_key: str, comment: str) -> dict:
    """Add a comment to a Jira issue (WRITE)."""
    r = client.post(f"/rest/api/3/issue/{issue_key}/comment", json={"body": adf(comment)})
    r.raise_for_status()
    return {"issue": issue_key, "comment_id": r.json()["id"]}

@mcp.tool()
def jira_transition_issue(issue_key: str, to_status: str) -> dict:
    """Move a Jira issue to a new status, e.g. 'In Progress' or 'Done' (PRIVILEGED)."""
    t = client.get(f"/rest/api/3/issue/{issue_key}/transitions").json()["transitions"]
    match = next((x for x in t if x["to"]["name"].lower() == to_status.lower()), None)
    if not match:
        return {"error": f"No transition to '{to_status}'", "available": [x["to"]["name"] for x in t]}
    client.post(f"/rest/api/3/issue/{issue_key}/transitions",
                json={"transition": {"id": match["id"]}}).raise_for_status()
    return {"issue": issue_key, "status": to_status}

class GatewayOnly(BaseHTTPMiddleware):
    """Reject calls that did not come through APIM (PoC substitute for private networking)."""
    async def dispatch(self, request, call_next):
        if request.url.path != "/healthz" and GW_SECRET and \
           request.headers.get("x-from-gateway") != GW_SECRET:
            return JSONResponse({"error": "direct access forbidden"}, status_code=403)
        return await call_next(request)

app = mcp.streamable_http_app()
app.add_middleware(GatewayOnly)

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000)

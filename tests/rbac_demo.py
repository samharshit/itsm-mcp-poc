"""Runs the RBAC matrix: each persona tries allowed and forbidden tools through APIM."""
import os, asyncio, msal
from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client

TENANT = os.environ["TENANT_ID"]
SCOPE = f"api://{os.environ['GATEWAY_APP_CLIENT_ID']}/.default"
GW = os.environ["APIM_URL"]                       # https://apim-mcp-xxxx.azure-api.net
PERSONAS = {
  "servicedesk-l1": (os.environ["L1_ID"], os.environ["L1_SECRET"]),
  "dev-triage-l2":  (os.environ["L2_ID"], os.environ["L2_SECRET"]),
  "reporting":      (os.environ["RPT_ID"], os.environ["RPT_SECRET"]),
}
CASES = [
  ("snow", "snow_get_incident",     {"number": "INC0010001"}),
  ("snow", "snow_add_work_note",    {"number": "INC0010001", "note": "Triage started by agent"}),
  ("jira", "jira_search_issues",    {"jql": 'project = OPS ORDER BY created DESC', "max_results": 3}),
  ("jira", "jira_create_issue",     {"summary": "Payment API 500s", "description": "From INC0010001",
                                     "labels": ["INC0010001"]}),
  ("snow", "snow_resolve_incident", {"number": "INC0010001", "close_notes": "Fixed"}),
]

def token(cid, secret):
    app = msal.ConfidentialClientApplication(
        cid, authority=f"https://login.microsoftonline.com/{TENANT}", client_credential=secret)
    return app.acquire_token_for_client(scopes=[SCOPE])["access_token"]

async def run(api, tool, args, tok):
    try:
        async with streamablehttp_client(f"{GW}/{api}/mcp",
                                          headers={"Authorization": f"Bearer {tok}"}) as (r, w, _):
            async with ClientSession(r, w) as s:
                await s.initialize()
                visible = [t.name for t in (await s.list_tools()).tools]
                res = await s.call_tool(tool, args)
                return ("DENY " if res.isError else "ALLOW") + f"  (sees {len(visible)} tools)"
    except Exception as e:
        return f"DENY   ({type(e).__name__})"

async def main():
    for p, (cid, sec) in PERSONAS.items():
        tok = token(cid, sec)
        print(f"\n== {p} ==")
        for api, tool, args in CASES:
            print(f"  {tool:24} -> {await run(api, tool, args, tok)}")

asyncio.run(main())

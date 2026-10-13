"""Create + run the Foundry Incident Triage agent against the APIM gateway.
The agent's MCP calls carry an Entra token for 'agent-dev-triage-l2' (passed as a run-time header).
pip install azure-ai-projects azure-ai-agents azure-identity msal
SDK names for MCP evolve - if an import fails, check the current azure-ai-agents MCP sample."""
import os, time, msal
from azure.identity import DefaultAzureCredential
from azure.ai.projects import AIProjectClient
from azure.ai.agents.models import (McpTool, RequiredMcpToolCall, SubmitToolApprovalAction,
                                    ToolApproval)

TENANT = os.environ["TENANT_ID"]; GW = os.environ["APIM_URL"]
SCOPE = f"api://{os.environ['GATEWAY_APP_CLIENT_ID']}/.default"
AGENT_ID, AGENT_SECRET = os.environ["AGENT_CLIENT_ID"], os.environ["AGENT_SECRET"]  # which persona?

tok = msal.ConfidentialClientApplication(AGENT_ID,
        authority=f"https://login.microsoftonline.com/{TENANT}",
        client_credential=AGENT_SECRET).acquire_token_for_client(scopes=[SCOPE])["access_token"]

snow = McpTool(server_label="servicenow", server_url=f"{GW}/snow/mcp")
jira = McpTool(server_label="jira", server_url=f"{GW}/jira/mcp")
for t in (snow, jira):
    t.update_headers("Authorization", f"Bearer {tok}")
    t.set_approval_mode("always")          # human approves each tool call (shown in console)

project = AIProjectClient(endpoint=os.environ["PROJECT_ENDPOINT"], credential=DefaultAzureCredential())
agents = project.agents
agent = agents.create_agent(
    model=os.environ.get("MODEL", "gpt-4o-mini"), name="incident-triage-agent",
    instructions=("You are an L2 incident triage assistant. For an incident number: read it from "
                  "ServiceNow, search Jira (project OPS) for related issues, create a Jira Bug labelled "
                  "with the incident number if none exists, then add a ServiceNow work note with the "
                  "Jira key. If a tool is not available to you, say which role is needed."),
    tools=snow.definitions + jira.definitions)

thread = agents.threads.create()
agents.messages.create(thread_id=thread.id, role="user",
                       content=os.environ.get("PROMPT", "Triage INC0010001"))
# Headers are supplied with each approval below, so they apply to whichever MCP server is called.
run = agents.runs.create(thread_id=thread.id, agent_id=agent.id)

while run.status in ("queued", "in_progress", "requires_action"):
    time.sleep(1.5)
    run = agents.runs.get(thread_id=thread.id, run_id=run.id)
    if run.status == "requires_action" and isinstance(run.required_action, SubmitToolApprovalAction):
        approvals = []
        for call in run.required_action.submit_tool_approval.tool_calls:
            if isinstance(call, RequiredMcpToolCall):
                ok = input(f"Approve {call.name} {call.arguments}? [y/n] ").lower() == "y"
                approvals.append(ToolApproval(tool_call_id=call.id, approve=ok,
                                              headers={"Authorization": f"Bearer {tok}"}))
        run = agents.runs.submit_tool_outputs(thread_id=thread.id, run_id=run.id,
                                              tool_approvals=approvals)

print("Run status:", run.status, run.last_error or "")
for m in agents.messages.list(thread_id=thread.id):
    if m.role == "assistant" and m.text_messages:
        print("\nAGENT:", m.text_messages[-1].text.value)
agents.delete_agent(agent.id)

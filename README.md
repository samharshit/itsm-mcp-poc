# ITSM Multi-MCP PoC v2 (free-tier friendly)
Run everything from **Azure Cloud Shell (Bash)**. Follow the step-by-step guide.
- deploy/00-variables.sh   fill in and `source`
- deploy/01-entra-setup.sh gateway app + 6 roles + 3 agent identities
- .github/workflows        builds images to ghcr.io for free (make packages Public)
- deploy/02-azure-deploy.sh  Log Analytics(capped), App Insights, Key Vault, Container Apps, APIM Consumption
- apim/mcp-rbac-policy.xml paste into APIM portal (both APIs)
- tests/rbac_demo.py       proves the RBAC matrix
- run_agent.py             Foundry agent end-to-end
- apim/kql-queries.kql     telemetry
- deploy/99-cleanup.sh     delete everything

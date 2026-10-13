# Fill these in, then:  source deploy/00-variables.sh
export RG=rg-mcp-itsm-poc
export LOC=eastus2                       # pick a region where you have gpt-4o-mini quota
export SFX=$(echo $RANDOM)
export GH_OWNER="<your-github-username-lowercase>"
export JIRA_BASE_URL="https://<your-site>.atlassian.net"
export JIRA_EMAIL="<your-atlassian-email>"
export JIRA_API_TOKEN="<jira-api-token>"
export SN_INSTANCE_URL="https://devXXXXX.service-now.com"
export SN_USER="admin"
export SN_PASSWORD="<pdi-admin-password>"
export TENANT_ID=$(az account show --query tenantId -o tsv)

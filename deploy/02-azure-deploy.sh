#!/usr/bin/env bash
# Free-tier friendly deploy: no ACR, no App Gateway, Container Apps scale-to-zero, APIM Consumption,
# Log Analytics with a daily cap. Run in Azure Cloud Shell (Bash) after: source deploy/00-variables.sh
set -euo pipefail
APIM=apim-mcp-$SFX; KV=kv-mcp-$SFX; LAW=law-mcp-$SFX; AI=appi-mcp-$SFX; ENV=cae-mcp-$SFX
GW_SECRET=$(openssl rand -hex 24)

for p in Microsoft.App Microsoft.OperationalInsights Microsoft.ApiManagement Microsoft.KeyVault \
         Microsoft.Insights Microsoft.CognitiveServices; do az provider register -n $p --wait; done
az extension add -n containerapp --upgrade -y
az extension add -n application-insights --upgrade -y

az group create -n $RG -l $LOC -o none

echo "== Log Analytics (with 0.1 GB/day cap so it can never run up a bill) =="
az monitor log-analytics workspace create -g $RG -n $LAW -l $LOC --retention-time 30 --quota 0.1 -o none
LAW_ID=$(az monitor log-analytics workspace show -g $RG -n $LAW --query id -o tsv)
LAW_CID=$(az monitor log-analytics workspace show -g $RG -n $LAW --query customerId -o tsv)
LAW_KEY=$(az monitor log-analytics workspace get-shared-keys -g $RG -n $LAW --query primarySharedKey -o tsv)
az monitor app-insights component create -g $RG -a $AI -l $LOC --workspace $LAW_ID -o none
AI_CONN=$(az monitor app-insights component show -g $RG -a $AI --query connectionString -o tsv)
AI_KEY=$(az monitor app-insights component show -g $RG -a $AI --query instrumentationKey -o tsv)

echo "== Key Vault (Standard; pennies per 10k operations) =="
az keyvault create -g $RG -n $KV -l $LOC --enable-rbac-authorization true -o none
KV_ID=$(az keyvault show -n $KV --query id -o tsv)
az role assignment create --assignee $(az ad signed-in-user show --query id -o tsv) \
   --role "Key Vault Secrets Officer" --scope $KV_ID -o none
sleep 30   # RBAC propagation
az keyvault secret set --vault-name $KV -n jira-api-token --value "$JIRA_API_TOKEN" -o none
az keyvault secret set --vault-name $KV -n sn-password    --value "$SN_PASSWORD" -o none
az keyvault secret set --vault-name $KV -n gateway-secret --value "$GW_SECRET" -o none

echo "== Container Apps environment (Consumption only, reuses our workspace) =="
az containerapp env create -g $RG -n $ENV -l $LOC \
   --logs-workspace-id $LAW_CID --logs-workspace-key $LAW_KEY -o none

deploy_app () {  # $1 app name  $2 image name  $3 secret name  $4 env var for secret  $5.. plain env vars
  local n=$1 img=$2 sec=$3 envvar=$4; shift 4
  # create with a placeholder public image first (avoids the failed-first-start issue)
  az containerapp create -g $RG -n $n --environment $ENV \
     --image mcr.microsoft.com/k8se/quickstart:latest --target-port 80 --ingress external \
     --min-replicas 0 --max-replicas 1 --cpu 0.25 --memory 0.5Gi \
     --system-assigned -o none
  local pid=$(az containerapp show -g $RG -n $n --query identity.principalId -o tsv)
  az role assignment create --assignee-object-id $pid --assignee-principal-type ServicePrincipal \
     --role "Key Vault Secrets User" --scope $KV_ID -o none
  sleep 30
  az containerapp secret set -g $RG -n $n --secrets \
     "$sec=keyvaultref:https://$KV.vault.azure.net/secrets/$sec,identityref:system" \
     "gw=keyvaultref:https://$KV.vault.azure.net/secrets/gateway-secret,identityref:system" -o none
  az containerapp ingress update -g $RG -n $n --target-port 8000 -o none
  az containerapp update -g $RG -n $n --image ghcr.io/$GH_OWNER/$img:latest \
     --set-env-vars "$envvar=secretref:$sec" "GATEWAY_SHARED_SECRET=secretref:gw" \
                    "APPLICATIONINSIGHTS_CONNECTION_STRING=$AI_CONN" "$@" -o none
}
deploy_app jira-mcp jira-mcp jira-api-token JIRA_API_TOKEN \
   JIRA_BASE_URL=$JIRA_BASE_URL JIRA_EMAIL=$JIRA_EMAIL JIRA_PROJECT_KEY=OPS
deploy_app snow-mcp servicenow-mcp sn-password SN_PASSWORD \
   SN_INSTANCE_URL=$SN_INSTANCE_URL SN_USER=$SN_USER

echo "== API Management - Consumption tier (1M calls/month free). Takes ~5-10 min =="
az apim create -g $RG -n $APIM -l $LOC --sku-name Consumption \
   --publisher-email "$(az ad signed-in-user show --query userPrincipalName -o tsv)" \
   --publisher-name "MCP PoC" -o none
az apim nv create -g $RG --service-name $APIM --named-value-id gateway-shared-secret \
   --display-name gateway-shared-secret --value "$GW_SECRET" --secret true -o none

for pair in "jira jira-mcp" "snow snow-mcp"; do
  set -- $pair
  URL=https://$(az containerapp show -n $2 -g $RG --query properties.configuration.ingress.fqdn -o tsv)
  az apim api create -g $RG --service-name $APIM --api-id $1 --path $1 --display-name "$1 MCP" \
     --service-url $URL --protocols https --subscription-required false -o none
  az apim api operation create -g $RG --service-name $APIM --api-id $1 \
     --url-template /mcp --method POST --display-name "mcp-post" -o none
  az apim api operation create -g $RG --service-name $APIM --api-id $1 \
     --url-template /mcp --method GET --display-name "mcp-get" -o none
done

echo "== APIM -> Application Insights logger =="
az rest --method put \
  --uri "https://management.azure.com/subscriptions/$(az account show --query id -o tsv)/resourceGroups/$RG/providers/Microsoft.ApiManagement/service/$APIM/loggers/appinsights?api-version=2022-08-01" \
  --body "{\"properties\":{\"loggerType\":\"applicationInsights\",\"credentials\":{\"instrumentationKey\":\"$AI_KEY\"}}}" -o none

cat > ../poc-outputs.env <<OUT
export RG=$RG APIM=$APIM KV=$KV AI=$AI
export APIM_URL=https://$APIM.azure-api.net
OUT
echo "DONE. Outputs saved to poc-outputs.env  ->  APIM_URL=https://$APIM.azure-api.net"

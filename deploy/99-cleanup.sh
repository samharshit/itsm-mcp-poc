#!/usr/bin/env bash
# Deletes EVERYTHING created by the PoC.
az group delete -n ${RG:-rg-mcp-itsm-poc} --yes --no-wait
for n in mcp-itsm-gateway agent-servicedesk-l1 agent-dev-triage-l2 agent-reporting; do
  id=$(az ad app list --display-name $n --query "[0].appId" -o tsv); [ -n "$id" ] && az ad app delete --id $id
done
# Key Vault is soft-deleted; purge so the name/secret is gone too:
for kv in $(az keyvault list-deleted --query "[?contains(name,'kv-mcp-')].name" -o tsv); do az keyvault purge -n $kv; done
echo "Cleanup started. Also delete the Foundry project/model if created outside $RG."

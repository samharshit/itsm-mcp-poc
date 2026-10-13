#!/usr/bin/env bash
# Creates the gateway app (with app roles) and 3 agent identities (client apps) with least-privilege roles.
set -euo pipefail
GW_NAME="mcp-itsm-gateway"

roles='[
 {"allowedMemberTypes":["User","Application"],"displayName":"SNOW Read","value":"SNOW.Read","description":"Read incidents","isEnabled":true,"id":"'$(uuidgen)'"},
 {"allowedMemberTypes":["User","Application"],"displayName":"SNOW Write","value":"SNOW.Write","description":"Create incidents / work notes","isEnabled":true,"id":"'$(uuidgen)'"},
 {"allowedMemberTypes":["User","Application"],"displayName":"SNOW Resolve","value":"SNOW.Resolve","description":"Resolve incidents","isEnabled":true,"id":"'$(uuidgen)'"},
 {"allowedMemberTypes":["User","Application"],"displayName":"JIRA Read","value":"JIRA.Read","description":"Read issues","isEnabled":true,"id":"'$(uuidgen)'"},
 {"allowedMemberTypes":["User","Application"],"displayName":"JIRA Write","value":"JIRA.Write","description":"Create issues / comments","isEnabled":true,"id":"'$(uuidgen)'"},
 {"allowedMemberTypes":["User","Application"],"displayName":"JIRA Transition","value":"JIRA.Transition","description":"Change issue status","isEnabled":true,"id":"'$(uuidgen)'"}
]'
echo "$roles" > roles.json

GW_APPID=$(az ad app create --display-name "$GW_NAME" --app-roles @roles.json \
           --query appId -o tsv)
az ad app update --id "$GW_APPID" --identifier-uris "api://$GW_APPID" \
   --set api.requestedAccessTokenVersion=2
az ad sp create --id "$GW_APPID" >/dev/null
GW_SP=$(az ad sp show --id "$GW_APPID" --query id -o tsv)
echo "GATEWAY_APP_CLIENT_ID=$GW_APPID"

role_id () { az ad sp show --id "$GW_APPID" --query "appRoles[?value=='$1'].id" -o tsv; }

make_agent () {   # $1 = name, rest = roles
  local name=$1; shift
  local appid=$(az ad app create --display-name "$name" --query appId -o tsv)
  az ad sp create --id "$appid" >/dev/null
  local sp=$(az ad sp show --id "$appid" --query id -o tsv)
  local secret=$(az ad app credential reset --id "$appid" --years 1 --query password -o tsv)
  for r in "$@"; do
    az rest --method POST \
      --uri "https://graph.microsoft.com/v1.0/servicePrincipals/$sp/appRoleAssignments" \
      --body "{\"principalId\":\"$sp\",\"resourceId\":\"$GW_SP\",\"appRoleId\":\"$(role_id $r)\"}" >/dev/null
  done
  echo "$name  CLIENT_ID=$appid  SECRET=$secret  ROLES=$*"
}

make_agent "agent-servicedesk-l1" SNOW.Read SNOW.Write JIRA.Read
make_agent "agent-dev-triage-l2"  SNOW.Read SNOW.Write JIRA.Read JIRA.Write
make_agent "agent-reporting"      SNOW.Read JIRA.Read
# Human 'Incident Manager' (SNOW.Resolve + JIRA.Transition): assign to a user in
# Entra admin center > Enterprise apps > mcp-itsm-gateway > Users and groups.

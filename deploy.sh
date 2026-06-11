#!/usr/bin/env bash
set -euo pipefail

HA_HOST="192.168.1.72"
HA_SHARE="config"
HA_USER="homeassistant"
HA_PASS="1234"
SRC="$(cd "$(dirname "$0")"; pwd)/custom_components/wavin_ahc9000"
DST_REL="custom_components\\wavin_ahc9000"

# Convert WSL path to Windows UNC path for PowerShell
WIN_SRC=$(wslpath -w "$SRC")

PS_SCRIPT=$(cat <<PSEOF
\$ErrorActionPreference = 'Stop'
\$pass = ConvertTo-SecureString '$HA_PASS' -AsPlainText -Force
\$cred = New-Object System.Management.Automation.PSCredential('$HA_USER', \$pass)
\$share = '\\\\$HA_HOST\\$HA_SHARE'
New-PSDrive -Name HADEPLOY -PSProvider FileSystem -Root \$share -Credential \$cred | Out-Null
\$dst = "HADEPLOY:\\$DST_REL"
if (-not (Test-Path \$dst)) { New-Item -ItemType Directory -Path \$dst | Out-Null }
Copy-Item -Path '$WIN_SRC\\*' -Destination \$dst -Recurse -Force
Remove-PSDrive -Name HADEPLOY
Write-Host 'Done.'
PSEOF
)

echo "Syncing integration files to HA via Samba..."
powershell.exe -ExecutionPolicy Bypass -Command "$PS_SCRIPT"

echo ""
echo "Restart Home Assistant to apply: http://$HA_HOST:8123/config/logs"

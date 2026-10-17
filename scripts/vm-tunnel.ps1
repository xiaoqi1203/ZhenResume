# VM 中间件 SSH 隧道管理（本机 Docker 部署用）
#
# 为什么用隧道而不是把 VM 的 5432/6379 绑到 0.0.0.0：
#   · VM 的 compose 现在只绑 127.0.0.1，端口不对局域网开放；
#   · 改成 0.0.0.0 后，**Docker 的端口发布会绕过 ufw**（需要在 DOCKER-USER 链额外放行规则），
#     配错就等于"整个局域网都能连数据库"。隧道把暴露面限制在"能 SSH 的人"，安全性高得多。
#
# 用法：
#   .\scripts\vm-tunnel.ps1 -VmUser <用户名>                 # 前台保持（Ctrl+C 断开）
#   .\scripts\vm-tunnel.ps1 -VmUser <用户名> -CheckOnly      # 只做检查，不建隧道
#   .\scripts\vm-tunnel.ps1 -VmUser <用户名> -Ports 15432,16379 -LocalPorts 5432,6379

[CmdletBinding()]
param(
    [string]$VmHost = "192.168.10.128",
    [Parameter(Mandatory = $true)][string]$VmUser,
    # VM 侧端口（其 127.0.0.1 上监听的端口）
    [int[]]$Ports = @(5432, 6379),
    # 本机侧端口（默认与 VM 侧相同；被占用时可用 -LocalPorts 改）
    [int[]]$LocalPorts,
    [switch]$CheckOnly
)

$ErrorActionPreference = "Stop"

if (-not $LocalPorts -or $LocalPorts.Count -eq 0) { $LocalPorts = $Ports }
if ($Ports.Count -ne $LocalPorts.Count) {
    Write-Error "-Ports 与 -LocalPorts 数量必须一致（各 $($Ports.Count) / $($LocalPorts.Count)）"
}

$ssh = (Get-Command ssh -ErrorAction SilentlyContinue).Source
if (-not $ssh) { $ssh = "$env:SystemRoot\System32\OpenSSH\ssh.exe" }
if (-not (Test-Path $ssh)) { Write-Error "找不到 ssh.exe" }

function Test-Port([string]$Host_, [int]$Port, [int]$TimeoutMs = 1500) {
    $c = New-Object System.Net.Sockets.TcpClient
    try {
        $iar = $c.BeginConnect($Host_, $Port, $null, $null)
        if ($iar.AsyncWaitHandle.WaitOne($TimeoutMs)) { $c.EndConnect($iar); return $true }
        return $false
    } catch { return $false } finally { $c.Close() }
}

Write-Host "=== 1. SSH 免密连通性 ===" -ForegroundColor Cyan
$sshOut = & $ssh -o BatchMode=yes -o StrictHostKeyChecking=accept-new -o ConnectTimeout=8 "$VmUser@$VmHost" "echo OK" 2>&1
if ($LASTEXITCODE -ne 0) {
    Write-Host "  ✗ SSH 免密失败（exit=$LASTEXITCODE）：" -ForegroundColor Red
    $sshOut | Select-Object -First 3 | ForEach-Object { Write-Host "    $_" }
    Write-Host @"

  先完成免密授权（把本机公钥装到 VM）：
    type `$env:USERPROFILE\.ssh\id_ed25519.pub | ssh $VmUser@$VmHost "mkdir -p ~/.ssh && cat >> ~/.ssh/authorized_keys && chmod 600 ~/.ssh/authorized_keys"
"@ -ForegroundColor Yellow
    exit 1
}
Write-Host "  ✓ 免密可用" -ForegroundColor Green

Write-Host "`n=== 2. 本机目标端口占用检查 ===" -ForegroundColor Cyan
$busy = @()
for ($i = 0; $i -lt $Ports.Count; $i++) {
    $lp = $LocalPorts[$i]
    $inUse = Test-Port "127.0.0.1" $lp
    Write-Host ("  本机 127.0.0.1:{0,-6} {1}" -f $lp, $(if ($inUse) { "已被占用（可能是上一次的隧道）" } else { "空闲" }))
    if ($inUse) { $busy += $lp }
}
if ($busy.Count -gt 0) {
    Write-Host "  ⚠️ 以下端口已占用：$($busy -join ', ')" -ForegroundColor Yellow
    Write-Host "     · 若是上次隧道残留：Get-Process ssh | Stop-Process" -ForegroundColor Yellow
    Write-Host "     · 或改用其他本机端口：-LocalPorts 15432,16379（同时要改 .env.local-deploy 的 POSTGRES_PORT/REDIS_PORT）" -ForegroundColor Yellow
}

Write-Host "`n=== 3. VM 侧中间件进程 ===" -ForegroundColor Cyan
$vmPs = & $ssh -o BatchMode=yes "$VmUser@$VmHost" "docker ps --format '{{.Names}}`t{{.Status}}' 2>/dev/null | head -5" 2>&1
if ($vmPs) { $vmPs | ForEach-Object { Write-Host "  $_" } } else { Write-Host "  （没查到容器，VM 上 docker 可能未运行）" -ForegroundColor Yellow }

if ($CheckOnly) {
    Write-Host "`n(-CheckOnly：不建隧道，检查结束)" -ForegroundColor Cyan
    exit 0
}

$args_ = @("-N")
for ($i = 0; $i -lt $Ports.Count; $i++) {
    $args_ += @("-L", "$($LocalPorts[$i]):127.0.0.1:$($Ports[$i])")
}
$args_ += @("-o", "ServerAliveInterval=30", "-o", "ServerAliveCountMax=3", "-o", "ExitOnForwardFailure=yes")
$args_ += "$VmUser@$VmHost"

Write-Host "`n=== 4. 建立隧道（前台保持，Ctrl+C 断开）===" -ForegroundColor Cyan
Write-Host "  ssh $($args_ -join ' ')" -ForegroundColor DarkGray
$job = Start-Job -ScriptBlock { param($s, $a) & $s @a } -ArgumentList $ssh, $args_
Start-Sleep -Seconds 3

Write-Host "`n=== 5. 隧道连通性验证 ===" -ForegroundColor Cyan
$okAll = $true
for ($i = 0; $i -lt $Ports.Count; $i++) {
    $lp = $LocalPorts[$i]
    $ok = Test-Port "127.0.0.1" $lp
    if (-not $ok) { $okAll = $false }
    Write-Host ("  127.0.0.1:{0,-6} {1}" -f $lp, $(if ($ok) { "✓ 通" } else { "✗ 不通" })) -ForegroundColor $(if ($ok) { "Green" } else { "Red" })
}

if ($okAll) {
    Write-Host @"

✓ 隧道可用。接下来：
  1) 确认 .env.local-deploy 里的 POSTGRES_PORT/REDIS_PORT 与本机映射端口一致
  2) docker compose -f docker-compose.local.yml --env-file .env.local-deploy up -d --build
  3) docker exec -it zhenresume-api python -m scripts.seed_knowledge --force   # 首次建知识库
  4) curl http://localhost:8000/api/v1/health

按 Ctrl+C 断开隧道（断开后应用会连不上中间件）。
"@ -ForegroundColor Green
} else {
    Write-Host "`n✗ 有端口不通。检查：VM 上容器是否在跑（docker ps）、VM 侧端口是否真在 127.0.0.1 监听。" -ForegroundColor Red
}

Wait-Job $job | Out-Null
Receive-Job $job
Remove-Job $job -Force

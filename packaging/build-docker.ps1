param([string]$Docker = "docker")
$ErrorActionPreference = "Stop"
if (Test-Path -LiteralPath $Docker -PathType Leaf) {
    $env:PATH = (Split-Path -Parent (Resolve-Path $Docker)) + [IO.Path]::PathSeparator + $env:PATH
}
Set-Location (Split-Path -Parent $PSScriptRoot)
$Delivery = Join-Path (Get-Location) "dist/docker-linux-amd64"
New-Item -ItemType Directory -Force $Delivery | Out-Null
& $Docker build --platform linux/amd64 -t warehouse-shelve:linux-amd64 .
if ($LASTEXITCODE -ne 0) { throw "Docker build failed" }
& $Docker image inspect warehouse-shelve:linux-amd64 --format '{{.Os}}/{{.Architecture}}'
if ($LASTEXITCODE -ne 0) { throw "Image inspection failed" }
$TestRoot = Join-Path (Get-Location) (".tmp/docker-test-" + [guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Force "$TestRoot/results", "$TestRoot/config" | Out-Null
$Name = "warehouse-test-" + [guid]::NewGuid().ToString('N')
try {
    & $Docker run -d --name $Name --mount "type=bind,source=$TestRoot/results,target=/app/results" --mount "type=bind,source=$TestRoot/config,target=/app/config" warehouse-shelve:linux-amd64
    if ($LASTEXITCODE -ne 0) { throw "Test container failed to start" }
    for ($i = 0; $i -lt 60; $i++) {
        $Health = & $Docker inspect --format '{{.State.Health.Status}}' $Name
        if ($LASTEXITCODE -ne 0) { throw "Health inspection failed" }
        if ($Health -eq 'healthy') { break }
        Start-Sleep -Seconds 2
    }
    if ($Health -ne 'healthy') { throw "Test container did not become healthy" }
    & $Docker cp packaging/smoke-docker.py "${Name}:/tmp/smoke.py"
    if ($LASTEXITCODE -ne 0) { throw "Copying smoke test failed" }
    & $Docker exec -e PYTHONPATH=/app $Name python /tmp/smoke.py --solvers
    if ($LASTEXITCODE -ne 0) { throw "Container smoke test failed" }
} finally {
    & $Docker logs $Name 2>&1 | Out-File "$Delivery/container-test.log" -Encoding utf8
    & $Docker rm -f $Name
}
& $Docker run --rm --mount "type=bind,source=$TestRoot/results,target=/app/results" --mount "type=bind,source=$TestRoot/config,target=/app/config" warehouse-shelve:linux-amd64 python -c "import json; from pathlib import Path; from core.algorithm_settings import read_settings; files=list(Path('/app/results/warehouse').glob('*/result.json')); assert len(files)==2; assert all(json.loads(p.read_text())['status']=='feasible' for p in files); settings=read_settings(); assert all(settings[k]['threads']==2 for k in ('slotting','picking_path')); print('PASS persisted results and settings after container recreation')"
if ($LASTEXITCODE -ne 0) { throw "Persistence verification failed" }
& $Docker save -o "$Delivery/warehouse-shelve-linux-amd64.tar" warehouse-shelve:linux-amd64
if ($LASTEXITCODE -ne 0) { throw "Docker export failed" }
Copy-Item compose.yaml "$Delivery/compose.yaml"
Copy-Item packaging/deploy.sh, packaging/DOCKER.md, packaging/smoke-docker.py $Delivery
$DeployScript = Join-Path $Delivery 'deploy.sh'
[IO.File]::WriteAllText($DeployScript, [IO.File]::ReadAllText($DeployScript).Replace("`r`n", "`n"), (New-Object Text.UTF8Encoding $false))
$Hash = (Get-FileHash "$Delivery/warehouse-shelve-linux-amd64.tar" -Algorithm SHA256).Hash.ToLowerInvariant()
"$Hash  warehouse-shelve-linux-amd64.tar" | Set-Content "$Delivery/SHA256.txt" -Encoding ascii
Write-Host "Delivery: $Delivery"

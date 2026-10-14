# 文件说明：一键打包脚本。项目根目录执行：powershell -ExecutionPolicy Bypass -File packaging\build.ps1
# 用独立的 .venv-build（pip 装的 numpy，OpenBLAS）构建，不要用 conda 环境——conda 的 MKL numpy
# 打包后会因为 mkl_intel_thread.2.dll 收不全而启动失败。
param(
    # 用来创建 .venv-build 的基础解释器，需为 Python 3.9+
    [string]$BasePython = "C:\Users\xuhel\anaconda3\envs\python39\python.exe"
)

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $PSScriptRoot
Set-Location $ProjectRoot

$VenvPython = Join-Path $ProjectRoot ".venv-build\Scripts\python.exe"

Write-Host "[1/4] 准备构建环境 .venv-build ..." -ForegroundColor Cyan
if (-not (Test-Path $VenvPython)) {
    & $BasePython -m venv "$ProjectRoot\.venv-build"
    if ($LASTEXITCODE -ne 0) { throw "创建 .venv-build 失败" }
    & $VenvPython -m pip install -r "$ProjectRoot\requirements.txt"
    if ($LASTEXITCODE -ne 0) { throw "安装项目依赖失败" }
    & $VenvPython -m pip install pyinstaller==6.11.1
    if ($LASTEXITCODE -ne 0) { throw "安装 PyInstaller 失败" }
}

Write-Host "[2/4] 清理上次构建产物..." -ForegroundColor Cyan
Remove-Item -Recurse -Force "$ProjectRoot\build", "$ProjectRoot\dist" -ErrorAction SilentlyContinue

Write-Host "[3/4] 开始打包（约 1-3 分钟）..." -ForegroundColor Cyan
& $VenvPython -m PyInstaller --noconfirm --clean "packaging\warehouse_demo.spec"
if ($LASTEXITCODE -ne 0) { throw "PyInstaller 打包失败" }

Write-Host "[4/4] 附带使用说明..." -ForegroundColor Cyan
$DistDir = Join-Path $ProjectRoot "dist\仓储优化演示"
Copy-Item "$PSScriptRoot\使用说明.txt" $DistDir -Force

$SizeMB = [math]::Round((Get-ChildItem $DistDir -Recurse -File | Measure-Object -Property Length -Sum).Sum / 1MB, 0)
Write-Host ""
Write-Host "打包完成：$DistDir （约 $SizeMB MB）" -ForegroundColor Green
Write-Host "把整个「仓储优化演示」文件夹压缩成 zip 发给同事即可。" -ForegroundColor Green

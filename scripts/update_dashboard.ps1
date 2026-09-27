param(
    [switch]$NoValidate
)

$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $PSScriptRoot
$venvPython = Join-Path $projectRoot ".venv\Scripts\python.exe"
$python = if (Test-Path -LiteralPath $venvPython) { $venvPython } else { "python" }

Push-Location $projectRoot
try {
    & $python -m etl.build
    if ($LASTEXITCODE -ne 0) { throw "O ETL terminou com código $LASTEXITCODE." }

    if (-not $NoValidate) {
        & $python -m etl.validate
        if ($LASTEXITCODE -ne 0) { throw "A validação terminou com código $LASTEXITCODE." }
    }

    $output = Join-Path $projectRoot "web\data\dashboard.json"
    $sizeKb = [math]::Round((Get-Item -LiteralPath $output).Length / 1KB, 1)
    Write-Host "Dashboard pronto: $output ($sizeKb KB)" -ForegroundColor Green
    Write-Host "Agora revise, faça commit e envie ao repositório para a Vercel publicar." -ForegroundColor Cyan
}
finally {
    Pop-Location
}

$ErrorActionPreference = "Stop"

$repoRoot = Split-Path -Parent $PSScriptRoot
Set-Location $repoRoot

$patterns = @(
    'job\.job_status\s*=\s*[^=]',
    'assignment\.assignment_status\s*=\s*[^=]',
    'update\(\s*job_status\s*=',
    'update\(\s*assignment_status\s*='
)

$statusWriteMatches = Get-ChildItem -Path jobs, ui, assignments -Recurse -Filter *.py |
    Where-Object {
        $_.FullName -notmatch '\\test' -and
        $_.FullName -notmatch '\\tests\\' -and
        $_.Name -ne 'services_state_transitions.py'
    } |
    Select-String -Pattern $patterns

if ($statusWriteMatches) {
    Write-Host "Direct status writes detected outside jobs/services_state_transitions.py:" -ForegroundColor Red
    $statusWriteMatches | ForEach-Object {
        $relativePath = $_.Path.Replace($repoRoot + '\\', '')
        Write-Host ("{0}:{1}:{2}" -f $relativePath, $_.LineNumber, $_.Line.Trim()) -ForegroundColor Yellow
    }
    throw "Lifecycle contract guard failed"
}

Write-Host "Lifecycle contract guard passed: no direct runtime status writes found." -ForegroundColor Green

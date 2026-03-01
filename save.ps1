# ===============================
# NODO - PRO SAVE SCRIPT
# Backup SQL + Commit (fecha+rama) + Pull(Rebase) + Push + Log
# ===============================

$ErrorActionPreference = "Stop"

# ---- CONFIG ----
$ProjectPath = "C:\Users\luisf\OneDrive\Desktop\service_nodo"
$SqlInstance = ".\SQLEXPRESS"
$DbName      = "Nodo"
$BackupDir   = "C:\Users\luisf\OneDrive\Desktop\Backups"
$LogDir      = "C:\Users\luisf\OneDrive\Desktop\Backups\logs"
# --------------

Write-Host ""
Write-Host "===== NODO: BACKUP SQL + GIT (PRO) =====" -ForegroundColor Cyan
Write-Host ""

# Ir al proyecto
Set-Location $ProjectPath

# Verificar repo git
if (-not (Test-Path ".git")) { throw "No encuentro .git en: $ProjectPath" }

# Create folders if they do not exist
if (-not (Test-Path $BackupDir)) { New-Item -ItemType Directory -Path $BackupDir | Out-Null }
if (-not (Test-Path $LogDir))    { New-Item -ItemType Directory -Path $LogDir    | Out-Null }

# Detectar rama actual
$branch = (git rev-parse --abbrev-ref HEAD).Trim()
if ([string]::IsNullOrWhiteSpace($branch)) { $branch = "unknown-branch" }

# Timestamp
$tsFile   = Get-Date -Format "yyyy_MM_dd_HHmmss"
$tsCommit = Get-Date -Format "yyyy-MM-dd HH:mm"

# 1) BACKUP SQL
$backupFile = Join-Path $BackupDir ("{0}_{1}.bak" -f $DbName, $tsFile)

Write-Host "1) BACKUP SQL..." -ForegroundColor Yellow
Write-Host "   Instance: $SqlInstance | DB: $DbName"
Write-Host "   -> $backupFile"
Write-Host ""

$backupQuery = @"
BACKUP DATABASE [$DbName]
TO DISK = N'$backupFile'
WITH INIT, CHECKSUM;
"@

# Preferir sqlcmd, fallback Invoke-Sqlcmd
$hasSqlCmd = $false
try { $null = Get-Command sqlcmd -ErrorAction Stop; $hasSqlCmd = $true } catch { $hasSqlCmd = $false }

if ($hasSqlCmd) {
  sqlcmd -S $SqlInstance -d master -b -Q $backupQuery
} else {
  $hasInvoke = $false
  try { $null = Get-Command Invoke-Sqlcmd -ErrorAction Stop; $hasInvoke = $true } catch { $hasInvoke = $false }
  if (-not $hasInvoke) { throw "No encuentro 'sqlcmd' ni 'Invoke-Sqlcmd'. Instala SSMS/SQL tools o módulo SqlServer." }
  Invoke-Sqlcmd -ServerInstance $SqlInstance -Database "master" -Query $backupQuery
}

if (-not (Test-Path $backupFile)) { throw "Backup NO creado. Revisa permisos/carpeta: $BackupDir" }

Write-Host "? BACKUP OK" -ForegroundColor Green
Write-Host ""

# 2) GIT STATUS
Write-Host "2) Git status:" -ForegroundColor Yellow
git status
Write-Host ""

# Si no hay cambios, abortar limpio (pero dejando backup hecho)
$porcelain = (git status --porcelain)
if ([string]::IsNullOrWhiteSpace($porcelain)) {
  Write-Host "No hay cambios para commitear. (Backup ya quedó hecho.)" -ForegroundColor Green
  Write-Host "Backup: $backupFile"
  exit 0
}

# 3) Commit msg con fecha + rama
$msgBase = Read-Host "Escribe el mensaje del commit"
if ([string]::IsNullOrWhiteSpace($msgBase)) { throw "Mensaje vacío. Cancelado." }

$commitMsg = "[$tsCommit][$branch] $msgBase"

Write-Host ""
Write-Host "3) git add + commit:" -ForegroundColor Yellow
Write-Host "   $commitMsg"
Write-Host ""

git add .
git commit -m "$commitMsg"

# Capturar hash
$commitHash = (git rev-parse HEAD).Trim()

# 4) Pull --rebase antes de push
Write-Host ""
Write-Host "4) git pull --rebase..." -ForegroundColor Yellow
git pull --rebase

# 5) Push
Write-Host ""
Write-Host "5) git push..." -ForegroundColor Yellow
git push

# 6) LOG
$logFile = Join-Path $LogDir ("save_log_{0}.txt" -f $tsFile)

@"
DATE:       $tsCommit
BRANCH:     $branch
COMMIT:     $commitHash
MESSAGE:    $commitMsg
BACKUP:     $backupFile
PROJECT:    $ProjectPath
SQL:        $SqlInstance
DB:         $DbName
"@ | Out-File -FilePath $logFile -Encoding UTF8

Write-Host ""
Write-Host "===== TODO LISTO ? =====" -ForegroundColor Green
Write-Host "Backup: $backupFile"
Write-Host "Log:    $logFile"
Write-Host "Commit: $commitHash"
Write-Host ""

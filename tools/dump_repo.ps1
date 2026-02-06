# tools/dump_repo.ps1
# Hardened repo snapshot:
# - Inlines ALL git-tracked text files (with size caps + secret guards)
# - Lists untracked files; only inlines small, safe-text ones
# - Skips binaries + big files (still listed)
# - Excludes junk dirs everywhere in the path
#
# Usage:
#   powershell -ExecutionPolicy Bypass -File .\tools\dump_repo.ps1
#
# Output:
#   tools\repo_snapshot.txt

$ErrorActionPreference = "Stop"

function Write-Section([string]$title) {
  return "`n`n==================== $title ====================`n"
}

function Normalize-RelPath([string]$path) {
  return ($path -replace '/', '\')
}

function Is-ExcludedDirPath([string]$fullPath, [string[]]$excludeDirNames) {
  foreach ($d in $excludeDirNames) {
    $needle = [System.IO.Path]::DirectorySeparatorChar + $d + [System.IO.Path]::DirectorySeparatorChar
    if ($fullPath.IndexOf($needle, [System.StringComparison]::OrdinalIgnoreCase) -ge 0) {
      return $true
    }
  }
  return $false
}

function Matches-SecretPattern([string]$relPath) {
  $p = $relPath.ToLowerInvariant()
  # Conservative: avoid dumping obvious secret files even if they appear.
  if ($p.EndsWith(".env")) { return $true }
  if ($p.Contains("\.env.")) { return $true }
  if ($p.Contains("secret")) { return $true }
  if ($p.Contains("secrets")) { return $true }
  if ($p.Contains("credential")) { return $true }
  if ($p.Contains("credentials")) { return $true }
  if ($p.Contains("apikey")) { return $true }
  if ($p.Contains("api_key")) { return $true }
  if ($p.Contains("privatekey")) { return $true }
  if ($p.Contains("private_key")) { return $true }
  if ($p.Contains("id_rsa")) { return $true }
  return $false
}

function Is-LikelyBinaryByExt([string]$extLower) {
  $binaryExt = @(
    ".png",".jpg",".jpeg",".gif",".webp",".ico",".bmp",".tiff",
    ".zip",".7z",".rar",".tar",".gz",
    ".exe",".dll",".pyd",".so",
    ".mp4",".mov",".avi",".mkv",
    ".wav",".mp3",".flac",
    ".pdf",
    ".ttf",".otf",
    ".bin"
  )
  return ($binaryExt -contains $extLower)
}

function Is-ProbablyTextExt([string]$extLower) {
  # Safe-ish text types that we can inline even if untracked (subject to size cap).
  $textExt = @(
    ".py",".pyi",".ps1",".psm1",".bat",".cmd",
    ".json",".yaml",".yml",".toml",".ini",".cfg",
    ".md",".txt",".rst",
    ".xml",".html",".css",".js",".ts",
    ".gitignore",".gitattributes",".editorconfig"
  )
  return ($textExt -contains $extLower)
}

function Has-NulByte([byte[]]$bytes) {
  if ($bytes.Length -eq 0) { return $false }
  $n = [Math]::Min($bytes.Length, 8000)
  for ($i = 0; $i -lt $n; $i++) {
    if ($bytes[$i] -eq 0) { return $true }
  }
  return $false
}

function Inline-FileContent(
  [System.Text.StringBuilder]$sb,
  [string]$absPath,
  [string]$relPath,
  [int64]$maxInlineBytes
) {
  $fi = Get-Item -LiteralPath $absPath -ErrorAction Stop
  $sb.AppendLine("size_bytes: $($fi.Length)") | Out-Null

  if ($fi.Length -gt $maxInlineBytes) {
    $sb.AppendLine("[SKIPPED: exceeds max inline size ($maxInlineBytes bytes)]") | Out-Null
    return
  }

  $ext = $fi.Extension.ToLowerInvariant()
  if (Is-LikelyBinaryByExt $ext) {
    $sb.AppendLine("[BINARY SKIPPED: extension $ext]") | Out-Null
    return
  }

  try {
    $bytes = [System.IO.File]::ReadAllBytes($absPath)
    if (Has-NulByte $bytes) {
      $sb.AppendLine("[BINARY SKIPPED: NUL-byte detected]") | Out-Null
      return
    }
    # Decode as UTF-8; if file isn't UTF-8, you'll still get something (worst case replacement chars).
    $text = [System.Text.Encoding]::UTF8.GetString($bytes)
    $sb.AppendLine($text) | Out-Null
  } catch {
    $sb.AppendLine("[ERROR reading file] $($_.Exception.Message)") | Out-Null
  }
}

$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
Set-Location $repoRoot

$outPath = Join-Path $repoRoot "tools\repo_snapshot.txt"

# Directory exclusions anywhere in path
$excludeDirNames = @(
  ".git",
  ".venv",
  "__pycache__",
  ".mypy_cache",
  ".pytest_cache",
  ".ruff_cache",
  ".idea",
  ".vscode",
  "dist",
  "build",
  ".tox",
  "node_modules",
  "logs",
  "log",
  "captures",
  "capture_dumps",
  "screenshots",
  "assets"
)

# Inline size caps
$maxInlineTrackedBytes   = 2MB   # tracked files can be bigger; cap to avoid huge snapshots
$maxInlineUntrackedBytes = 256KB # untracked are more likely to be noise; keep tight

$sb = New-Object System.Text.StringBuilder

$sb.AppendLine((Write-Section "REPO")) | Out-Null
$sb.AppendLine("root: $repoRoot") | Out-Null
$sb.AppendLine("timestamp_utc: $([DateTime]::UtcNow.ToString('yyyy-MM-ddTHH:mm:ssZ'))") | Out-Null

$sb.AppendLine((Write-Section "GIT")) | Out-Null
$gitOk = $true
try {
  $sb.AppendLine("commit: " + (git rev-parse HEAD)) | Out-Null
  $sb.AppendLine("branch: " + (git rev-parse --abbrev-ref HEAD)) | Out-Null
  $sb.AppendLine("status --porcelain:") | Out-Null
  $sb.AppendLine((git status --porcelain)) | Out-Null
  $sb.AppendLine("log -10:") | Out-Null
  $sb.AppendLine((git log -10 --oneline)) | Out-Null
} catch {
  $gitOk = $false
  $sb.AppendLine("git not available or repo not initialized.") | Out-Null
}

$sb.AppendLine((Write-Section "TREE (focused)")) | Out-Null
# Keep tree focused and readable
foreach ($p in @("src", "tools", "run.ps1", "README.md")) {
  if (Test-Path (Join-Path $repoRoot $p)) {
    if ((Get-Item (Join-Path $repoRoot $p)).PSIsContainer) {
      $sb.AppendLine("## $p") | Out-Null
      try {
        $sb.AppendLine((cmd /c ("tree /f /a `"$p`""))) | Out-Null
      } catch {
        $sb.AppendLine(("tree failed for {0}: {1}" -f $p, $_.Exception.Message)) | Out-Null
      }
    } else {
      $sb.AppendLine("## $p (file present)") | Out-Null
    }
  } else {
    $sb.AppendLine("## $p (missing)") | Out-Null
  }
}

# Tracked file list
$tracked = @()
$untracked = @()

if ($gitOk) {
  $tracked = (git ls-files) | Where-Object { $_ -and $_.Trim().Length -gt 0 } | ForEach-Object { Normalize-RelPath $_.Trim() }
  $untracked = (git ls-files --others --exclude-standard) | Where-Object { $_ -and $_.Trim().Length -gt 0 } | ForEach-Object { Normalize-RelPath $_.Trim() }
}

$sb.AppendLine((Write-Section "TRACKED FILE CONTENTS (capped + secret-guarded)")) | Out-Null
if (-not $gitOk) {
  $sb.AppendLine("[SKIPPED: git unavailable]") | Out-Null
} else {
  foreach ($rel in ($tracked | Sort-Object)) {
    $abs = Join-Path $repoRoot $rel
    if (-not (Test-Path -LiteralPath $abs)) {
      $sb.AppendLine("----- FILE: $rel -----") | Out-Null
      $sb.AppendLine("[MISSING ON DISK]") | Out-Null
      $sb.AppendLine("----- END FILE: $rel -----`n") | Out-Null
      continue
    }

    $full = (Resolve-Path -LiteralPath $abs).Path
    if (Is-ExcludedDirPath $full $excludeDirNames) { continue }

    $sb.AppendLine("----- FILE: $rel -----") | Out-Null

    if (Matches-SecretPattern $rel) {
      $fi = Get-Item -LiteralPath $abs
      $sb.AppendLine("size_bytes: $($fi.Length)") | Out-Null
      $sb.AppendLine("[SKIPPED: matches secret filename pattern]") | Out-Null
    } else {
      Inline-FileContent -sb $sb -absPath $abs -relPath $rel -maxInlineBytes $maxInlineTrackedBytes
    }

    $sb.AppendLine("----- END FILE: $rel -----") | Out-Null
    $sb.AppendLine("") | Out-Null
  }
}

$sb.AppendLine((Write-Section "UNTRACKED FILES (listed; only safe small text inlined)")) | Out-Null
if (-not $gitOk) {
  $sb.AppendLine("[SKIPPED: git unavailable]") | Out-Null
} else {
  if ($untracked.Count -eq 0) {
    $sb.AppendLine("[none]") | Out-Null
  }

  foreach ($rel in ($untracked | Sort-Object)) {
    $abs = Join-Path $repoRoot $rel
    if (-not (Test-Path -LiteralPath $abs)) { continue }

    $full = (Resolve-Path -LiteralPath $abs).Path
    if (Is-ExcludedDirPath $full $excludeDirNames) { continue }

    $fi = Get-Item -LiteralPath $abs
    $ext = $fi.Extension.ToLowerInvariant()

    $sb.AppendLine("----- FILE: $rel -----") | Out-Null
    $sb.AppendLine("size_bytes: $($fi.Length)") | Out-Null

    if (Matches-SecretPattern $rel) {
      $sb.AppendLine("[SKIPPED: matches secret filename pattern]") | Out-Null
    } elseif (-not (Is-ProbablyTextExt $ext) -and $ext -ne "") {
      $sb.AppendLine("[SKIPPED: untracked and not whitelisted text extension ($ext)]") | Out-Null
    } else {
      Inline-FileContent -sb $sb -absPath $abs -relPath $rel -maxInlineBytes $maxInlineUntrackedBytes
    }

    $sb.AppendLine("----- END FILE: $rel -----") | Out-Null
    $sb.AppendLine("") | Out-Null
  }
}

$sb.ToString() | Set-Content -Path $outPath -Encoding UTF8
Write-Host "Wrote snapshot to: $outPath"

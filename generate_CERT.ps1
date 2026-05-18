# 1. Ask user for input
$projectPath = (Read-Host "Enter the absolute path to your Python project (e.g., C:\Users\546493)").Trim()
$ipAddress = (Read-Host "Enter the IP address of your webserver (e.g., 192.168.1.100)").Trim()

# 2. Setup target folder
$sslFolder = Join-Path $projectPath "ssl"
if (!(Test-Path $sslFolder)) {
    New-Item -ItemType Directory -Force -Path $sslFolder | Out-Null
    Write-Host "`n[+] Created folder: $sslFolder" -ForegroundColor Green
}
$certFilePath = Join-Path $sslFolder "cert.pem"
$keyFilePath = Join-Path $sslFolder "key.pem"

Write-Host "`n[*] Starting SSL generation process for IP: $ipAddress..." -ForegroundColor Cyan

# --- STRATEGY 1: Native PowerShell 7 (.NET Core 3.0+) ---
if ($PSVersionTable.PSVersion.Major -ge 7) {
    Write-Host "[+] Modern PowerShell 7+ detected. Generating natively..." -ForegroundColor Green
    
    $cert = New-SelfSignedCertificate -DnsName $ipAddress -CertStoreLocation "Cert:\CurrentUser\My" -KeyExportPolicy Exportable -KeySpec Signature
    
    $certBytes = $cert.Export([System.Security.Cryptography.X509Certificates.X509ContentType]::Cert)
    $certPem = "-----BEGIN CERTIFICATE-----`n$([Convert]::ToBase64String($certBytes, [Base64FormattingOptions]::InsertLineBreaks))`n-----END CERTIFICATE-----"
    Set-Content -Path $certFilePath -Value $certPem
    
    $key = [System.Security.Cryptography.X509Certificates.RSACertificateExtensions]::GetRSAPrivateKey($cert)
    $keyPem = "-----BEGIN PRIVATE KEY-----`n$([Convert]::ToBase64String($key.ExportPkcs8PrivateKey(), [Base64FormattingOptions]::InsertLineBreaks))`n-----END PRIVATE KEY-----"
    Set-Content -Path $keyFilePath -Value $keyPem
    
    Write-Host "`n[SUCCESS] Files created natively!" -ForegroundColor Green
    Write-Host "-> $certFilePath" -ForegroundColor Yellow
    Write-Host "-> $keyFilePath`n" -ForegroundColor Yellow
    exit
}

# --- STRATEGY 2 & 3: OpenSSL Fallback & Auto-Installer (For PS 5.1) ---
Write-Host "[!] PowerShell 5.1 detected. Switching to OpenSSL fallback mode..." -ForegroundColor Yellow

$openSslExe = $null
$commonPaths = @(
    "openssl.exe", # Checks system PATH first
    "C:\Program Files\Git\usr\bin\openssl.exe",
    "C:\Program Files\OpenSSL-Win64\bin\openssl.exe",
    "C:\Program Files\OpenSSL\bin\openssl.exe"
)

# Search for existing OpenSSL installations
foreach ($path in $commonPaths) {
    $cmd = Get-Command $path -ErrorAction SilentlyContinue
    if ($cmd) {
        $openSslExe = $cmd.Source
        Write-Host "[+] Found OpenSSL at: $openSslExe" -ForegroundColor Green
        break
    }
}

# Auto-Install if completely missing
if (!$openSslExe) {
    Write-Host "[-] OpenSSL not found. Automatically installing via winget... (Please wait)" -ForegroundColor Magenta
    winget install -e --id ShiningLight.OpenSSL --silent --accept-package-agreements --accept-source-agreements | Out-Null
    
    $installedPath = "C:\Program Files\OpenSSL-Win64\bin\openssl.exe"
    if (Test-Path $installedPath) {
        $openSslExe = $installedPath
        Write-Host "[+] OpenSSL installed successfully!" -ForegroundColor Green
    } else {
        Write-Host "`n[ERROR] Auto-installation failed. Please install PowerShell 7 or OpenSSL manually." -ForegroundColor Red
        exit
    }
}

Write-Host "[*] Generating keys..." -ForegroundColor Cyan
# Execute OpenSSL and suppress the command-line noise
& $openSslExe req -x509 -newkey rsa:2048 -nodes -keyout $keyFilePath -out $certFilePath -days 365 -subj "/CN=$ipAddress" 2>$null

# Validate success
if (Test-Path $keyFilePath) {
    Write-Host "`n[SUCCESS] Files created successfully using OpenSSL!" -ForegroundColor Green
    Write-Host "-> $certFilePath" -ForegroundColor Yellow
    Write-Host "-> $keyFilePath`n" -ForegroundColor Yellow
} else {
    Write-Host "`n[ERROR] OpenSSL failed to generate the files." -ForegroundColor Red
}

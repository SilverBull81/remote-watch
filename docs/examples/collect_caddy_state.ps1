# Диагностика процесса Caddy и публичных сертификатов без чтения закрытых ключей.
# Version 1.0.0
# Автор: Sergey Fundobny (silverbull@mail.ru) + GPT-6
# Дата и время последнего изменения: 261006-170706
# Запуск: powershell -NoProfile -File .\collect_caddy_state.ps1 -StoragePath C:\Work\RemoteWatch\caddy-data
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$StoragePath,
    [string]$CaddyBinary = 'C:\Work\RemoteWatch\caddy.exe'
)

$ErrorActionPreference = 'Stop'
$report = [ordered]@{
    utc = [DateTime]::UtcNow.ToString('o')
    version = $null
    boot_utc = $null
    processes = @()
    services = @()
    storage_exists = (Test-Path -LiteralPath $StoragePath -PathType Container)
    storage_acl = @()
    certificates = @()
    issues = @()
}

# Командные строки процессов, среда, конфиги, журналы и файлы .key не читаются.
try { $report.version = ((& $CaddyBinary version 2>$null) -join ' ') }
catch { $report.issues += 'version_unavailable' }
try {
    $report.boot_utc = (Get-CimInstance Win32_OperatingSystem).LastBootUpTime.ToUniversalTime().ToString('o')
    $report.processes = @(Get-CimInstance Win32_Process -Filter "Name = 'caddy.exe'" | ForEach-Object {
        $owner = Invoke-CimMethod -InputObject $_ -MethodName GetOwner
        [ordered]@{ pid = $_.ProcessId; executable = $_.ExecutablePath
            started_utc = $_.CreationDate.ToUniversalTime().ToString('o')
            account = if ($owner.ReturnValue -eq 0) { "$($owner.Domain)\$($owner.User)" } else { 'unavailable' }
        }
    })
    $report.services = @(Get-CimInstance Win32_Service | Where-Object {
        $_.PathName -match '(?i)caddy' -or $_.Name -match '(?i)caddy'
    } | Select-Object Name, State, StartMode, StartName, ProcessId)
} catch { $report.issues += 'process_or_service_query_failed' }

if ($report.storage_exists) {
    try {
        $report.storage_acl = @((Get-Acl -LiteralPath $StoragePath).Access | Select-Object `
            @{Name='account'; Expression={$_.IdentityReference.Value}}, `
            @{Name='rights'; Expression={$_.FileSystemRights.ToString()}}, `
            @{Name='type'; Expression={$_.AccessControlType.ToString()}}, IsInherited)
    } catch { $report.issues += 'storage_acl_unavailable' }

    # В .crt Caddy может находиться несколько PEM-блоков: leaf и intermediate.
    # Разбираем каждый, не пытаясь считать локальный файл доказательством того,
    # какой сертификат сейчас выдаётся по сети. Проверка peer выполняется отдельно.
    try {
        $certFiles = @(Get-ChildItem -LiteralPath $StoragePath -Filter '*.crt' -File -Recurse |
            Select-Object -First 101)
        if ($certFiles.Count -gt 100) { throw 'bounded_inventory' }
        foreach ($certFile in $certFiles) {
            if ($certFile.Length -gt 1MB) { $report.issues += 'certificate_file_too_large'; continue }
            $pem = [IO.File]::ReadAllText($certFile.FullName)
            $blocks = [regex]::Matches($pem, '-----BEGIN CERTIFICATE-----\s*([A-Za-z0-9+/=\s]+)-----END CERTIFICATE-----')
            if ($blocks.Count -eq 0) { $report.issues += 'certificate_pem_unrecognized'; continue }
            $index = 0
            foreach ($block in $blocks) {
                $der = [Convert]::FromBase64String($block.Groups[1].Value)
                $certificate = New-Object Security.Cryptography.X509Certificates.X509Certificate2 -ArgumentList @(,$der)
                $sha = [Security.Cryptography.SHA256]::Create()
                try {
                    $fingerprint = [BitConverter]::ToString($sha.ComputeHash($der)).Replace('-', '')
                    $basic = $certificate.Extensions | Where-Object { $_.Oid.Value -eq '2.5.29.19' }
                    $report.certificates += [ordered]@{
                        file = $certFile.FullName; pem_index = $index
                        sha256 = $fingerprint; serial = $certificate.SerialNumber
                        is_ca = if ($basic) { $basic.CertificateAuthority } else { $false }
                        self_issued = ($certificate.Subject -eq $certificate.Issuer)
                        not_before_utc = $certificate.NotBefore.ToUniversalTime().ToString('o')
                        not_after_utc = $certificate.NotAfter.ToUniversalTime().ToString('o')
                        expired_at_server_time = ($certificate.NotAfter.ToUniversalTime() -lt [DateTime]::UtcNow)
                        last_write_utc = $certFile.LastWriteTimeUtc.ToString('o')
                    }
                } finally { $sha.Dispose(); $certificate.Dispose() }
                $index++
            }
        }
    } catch { $report.issues += 'certificate_inventory_incomplete' }
} else { $report.issues += 'storage_not_found' }

# ACL — сведения для проверки прав, а не доказательство записи от имени службы.
# Запись пробного файла под другим пользователем дала бы ложный положительный ответ.
$report | ConvertTo-Json -Depth 6

# Windows counterpart of macsign: signing identities from the user's certificate store (Cert:\CurrentUser\My,
# smart cards included) and certificate trust, through .NET. Windows PowerShell 5.1 and PowerShell 7 both work.
#   winsign.ps1 list                    JSON array of {sha1, subject, token, certificate (base64 DER)}
#   winsign.ps1 sign SHA1 sha1|sha256 FILE   base64 RSA PKCS#1 v1.5 signature of the file's bytes
#   winsign.ps1 trust                   one JSON {trusted, reason} per base64 DER certificate line on stdin
param([string]$Command, [string]$Sha1, [string]$Alg, [string]$DataFile)
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [Text.Encoding]::UTF8
Add-Type -AssemblyName System.Security
$X509 = 'System.Security.Cryptography.X509Certificates'
$RsaOid = '1.2.840.113549.1.1.1'

function Open-Store {
    $store = New-Object "$X509.X509Store" ('My', 'CurrentUser')
    $store.Open('ReadOnly')
    return $store
}

switch ($Command) {
    'list' {
        $store = Open-Store
        # Keys are not opened here: a smart card would ask for its PIN just to be listed.
        $found = @(foreach ($cert in $store.Certificates) {
            if ($cert.HasPrivateKey -and $cert.NotAfter -gt (Get-Date) -and $cert.PublicKey.Oid.Value -eq $RsaOid) {
                [pscustomobject]@{
                    sha1        = $cert.Thumbprint.ToLower()
                    subject     = $cert.GetNameInfo('SimpleName', $false)
                    token       = $false
                    certificate = [Convert]::ToBase64String($cert.RawData)
                }
            }
        })
        ConvertTo-Json -InputObject $found -Compress -Depth 3
    }
    'sign' {
        $store = Open-Store
        $cert = $store.Certificates | Where-Object { $_.Thumbprint -eq $Sha1.ToUpper() } | Select-Object -First 1
        if (-not $cert) { throw "Certifikát $Sha1 nebyl nalezen" }
        $key = [System.Security.Cryptography.X509Certificates.RSACertificateExtensions]::GetRSAPrivateKey($cert)
        $hash = if ($Alg -eq 'sha1') { [System.Security.Cryptography.HashAlgorithmName]::SHA1 }
                else { [System.Security.Cryptography.HashAlgorithmName]::SHA256 }
        $data = [IO.File]::ReadAllBytes($DataFile)
        $signature = $key.SignData($data, $hash, [System.Security.Cryptography.RSASignaturePadding]::Pkcs1)
        [Convert]::ToBase64String($signature)
    }
    'trust' {
        foreach ($line in ([Console]::In.ReadToEnd() -split "`n")) {
            if (-not $line.Trim()) { continue }
            $cert = New-Object "$X509.X509Certificate2" (, [Convert]::FromBase64String($line.Trim()))
            $chain = New-Object "$X509.X509Chain"
            $chain.ChainPolicy.RevocationMode = 'NoCheck'
            $trusted = $chain.Build($cert)
            $reason = (@($chain.ChainStatus | ForEach-Object { $_.StatusInformation.Trim() }) -join '; ')
            ConvertTo-Json -InputObject @{ trusted = $trusted; reason = $reason } -Compress
        }
    }
    default { throw "Neznámý příkaz: $Command" }
}

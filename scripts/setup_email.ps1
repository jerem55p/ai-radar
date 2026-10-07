# Enregistre le mot de passe d'application Gmail dans GitHub Secrets (une seule fois).
# Saisie masquee ; le mot de passe n'est ni affiche ni ecrit sur le disque.
param([string]$Repo = "jerem55p/ai-radar")

$ErrorActionPreference = "Stop"
$secure = Read-Host "Colle le mot de passe d'application Gmail (16 lettres, la saisie est masquee)" -AsSecureString
$pwd16 = [Net.NetworkCredential]::new("", $secure).Password -replace '\s', ''
if ($pwd16 -notmatch '^[a-z]{16}$') { throw "Un mot de passe d'application Gmail fait 16 lettres minuscules (espaces ignores)." }
$pwd16 | gh secret set SMTP_PASSWORD -R $Repo
Write-Host "SMTP_PASSWORD enregistre dans $Repo. Tu peux lancer le workflow."
$pwd16 = $null

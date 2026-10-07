# Configure les secrets Telegram du dépôt GitHub (à lancer UNE fois, sur ton PC).
# Le token est saisi en masqué, n'est jamais affiché ni écrit sur le disque :
# il part directement vers GitHub Secrets (chiffré) via la CLI `gh`.
param([string]$Repo = "jerem55p/ai-radar")

$ErrorActionPreference = "Stop"
$secure = Read-Host "Colle le token donné par BotFather (la saisie est masquée)" -AsSecureString
$token = [Net.NetworkCredential]::new("", $secure).Password
if ($token -notmatch '^\d+:[\w-]{30,}$') { throw "Ce token n'a pas le bon format (attendu : 123456789:AAH...)." }

try {
    $updates = Invoke-RestMethod -Uri "https://api.telegram.org/bot$token/getUpdates" -TimeoutSec 20
} catch {
    throw "Telegram a refusé le token (vérifie qu'il est complet)."
}
$msg = @($updates.result | Where-Object { $_.message }) | Select-Object -Last 1
if (-not $msg) {
    throw "Aucun message reçu par le bot. Ouvre ton bot dans Telegram, appuie sur Démarrer / envoie 'bonjour', puis relance ce script."
}
$chatId = [string]$msg.message.chat.id
Write-Host "Conversation trouvée (chat_id = $chatId)."

$token  | gh secret set TELEGRAM_BOT_TOKEN -R $Repo
$chatId | gh secret set TELEGRAM_CHAT_ID   -R $Repo
Write-Host "Secrets enregistrés dans $Repo."

# Message de test pour confirmer que tout est branché
$body = @{ chat_id = $chatId; text = "✅ AI Radar est connecté. Le premier top 20 arrive dans quelques minutes." }
try { Invoke-RestMethod -Method Post -Uri "https://api.telegram.org/bot$token/sendMessage" -Body $body -TimeoutSec 20 | Out-Null
      Write-Host "Message de test envoyé sur Telegram." } catch { Write-Host "Secrets OK, mais le message de test a échoué." }
$token = $null

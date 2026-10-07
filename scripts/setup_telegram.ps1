# Configure les secrets Telegram du dépôt GitHub (à lancer UNE fois, sur ton PC).
# Le token est saisi en masqué, n'est jamais affiché ni écrit sur le disque :
# il part directement vers GitHub Secrets (chiffré) via la CLI `gh`.
param([string]$Repo = "jerem55p/ai-radar")

$ErrorActionPreference = "Stop"
$secure = Read-Host "Colle le token donne par BotFather (la saisie est masquee)" -AsSecureString
$token = [Net.NetworkCredential]::new("", $secure).Password.Trim()
if ($token -notmatch '^\d+:[\w-]{30,}$') { throw "Ce token n'a pas le bon format (attendu : 123456789:AAH...)." }

function Call-Telegram($method) {
    try { Invoke-RestMethod -Uri "https://api.telegram.org/bot$token/$method" -TimeoutSec 20 }
    catch { throw "Telegram a refuse la requete '$method' (token incorrect ou revoque ?)." }
}

# 1. De quel bot s'agit-il ?
$me = Call-Telegram "getMe"
$botName = $me.result.username
Write-Host "Token valide : c'est le bot @$botName"

# 2. On retire un eventuel webhook (il empeche getUpdates de voir les messages)
try { Invoke-RestMethod -Uri "https://api.telegram.org/bot$token/deleteWebhook" -TimeoutSec 20 | Out-Null } catch {}

# 3. On attend ton message (90 s)
Write-Host ""
Write-Host ">>> Dans Telegram, ouvre https://t.me/$botName et envoie 'bonjour' MAINTENANT (a CE bot, pas a BotFather)."
$chatId = $null
for ($i = 0; $i -lt 30 -and -not $chatId; $i++) {
    $updates = Call-Telegram "getUpdates"
    $msg = @($updates.result | Where-Object { $_.message }) | Select-Object -Last 1
    if ($msg) { $chatId = [string]$msg.message.chat.id } else { Start-Sleep -Seconds 3 }
}
if (-not $chatId) { throw "Toujours aucun message recu par @$botName apres 90 s. Verifie que tu ecris bien a @$botName." }
Write-Host "Conversation trouvee (chat_id = $chatId)."

# 4. Secrets GitHub
$token  | gh secret set TELEGRAM_BOT_TOKEN -R $Repo
$chatId | gh secret set TELEGRAM_CHAT_ID   -R $Repo
Write-Host "Secrets enregistres dans $Repo."

# 5. Message de test
$body = @{ chat_id = $chatId; text = "AI Radar est connecte. Le premier top 20 arrive dans quelques minutes." }
try { Invoke-RestMethod -Method Post -Uri "https://api.telegram.org/bot$token/sendMessage" -Body $body -TimeoutSec 20 | Out-Null
      Write-Host "Message de test envoye sur Telegram." } catch { Write-Host "Secrets OK, mais le message de test a echoue." }
$token = $null

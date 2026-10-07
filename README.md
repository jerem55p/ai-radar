# 🤖 AI Radar

Chaque matin à 8 h (heure de Paris), AI Radar t'envoie sur Telegram le **top 20 des dépôts GitHub open source d'IA qui montent le plus fort**, classés par catégorie, avec une phrase pour chacun. Il publie aussi une page web complète (détails, chiffres, archives, filtre par catégorie) sur GitHub Pages.

Il ne cherche pas les plus gros projets, mais ceux qui ont **la plus forte dynamique** : étoiles gagnées en 24 h, accélération, nouveauté, activité du code, buzz sur Hacker News.

- **Gratuit** : tout tourne sur GitHub Actions, même quand ton PC est éteint.
- **Sans clé payante** : la clé Anthropic est *optionnelle* (elle sert aux résumés en français).
- **Résilient** : si la page Trending, Hacker News ou l'API Claude tombent, le message part quand même.

> ⚠️ **GitHub Pages gratuit exige un dépôt public.** Tout ce qui est dans le dépôt (dont `data/history.json` et `docs/`) sera visible de tous. Les secrets (tokens, clés) ne sont *jamais* dans le dépôt : ils vivent dans GitHub Secrets.

---

## Ce que tu reçois

1. **Un message Telegram compact** (1 à 3 messages max) : le podium (3 projets, 2 phrases chacun), puis les 17 suivants regroupés par catégorie, chacun en une ligne. Il se termine par le lien vers la page web.
2. **Une page web** : `https://<ton-user>.github.io/ai-radar/` (rang, badges, étoiles 24 h / 7 j, mini-graphique 14 jours, langage, licence, âge, lien Hacker News, liste « À surveiller » 21–30, archives, mode sombre).
3. **Le dimanche**, un récap en plus : top 10 de la semaine, projets qui **se confirment** (≥ 4 jours sur 7 dans le top) et **feux de paille** (un seul gros pic), catégorie la plus active.

Légende : 🧪 facile à essayer · 🔥HN discuté sur Hacker News · 🆕 nouveau dans le top · ↑3 / ↓2 variation de rang vs hier · 🔁 dans le top depuis ≥ 3 jours · ≈ chiffre estimé (voir « Limites »).

---

## Mise en place pas à pas

Compte une vingtaine de minutes. Il te faut un compte GitHub et l'application Telegram.

### 1. Créer le bot Telegram et récupérer le `chat_id`

1. Dans Telegram, ouvre une conversation avec **@BotFather** et envoie `/newbot`.
2. Choisis un nom (ex. « AI Radar ») puis un identifiant qui finit par `bot` (ex. `mon_ai_radar_bot`).
3. BotFather te donne un **token** (forme `123456789:AAH...`). C'est un mot de passe : ne le partage pas, ne le colle jamais dans le code.
4. Ouvre la conversation avec **ton** bot (lien donné par BotFather) et envoie-lui un message, par exemple `bonjour`. *(Sans ça, le bot n'a pas le droit de t'écrire.)*
5. Dans ton navigateur, ouvre (en remplaçant `<TOKEN>`) :
   `https://api.telegram.org/bot<TOKEN>/getUpdates`
   Cherche `"chat":{"id":123456789` : ce nombre est ton **`TELEGRAM_CHAT_ID`**.

### 2. Créer le dépôt public et y pousser le code

1. Sur github.com : **New repository** → nom `ai-radar` → **Public** → ne coche rien d'autre → Create.
2. Ouvre `config.yaml` et remplace `github_user: "<ton-user>"` par **ton nom d'utilisateur GitHub** (il sert à construire l'URL de la page dans le message).
3. Dans un terminal, dans le dossier du projet :

```bash
git init
git add .
git commit -m "AI Radar"
git branch -M main
git remote add origin https://github.com/<ton-user>/ai-radar.git
git push -u origin main
```

### 3. Ajouter les secrets

Sur GitHub : dépôt → **Settings → Secrets and variables → Actions → New repository secret**.

| Secret | Obligatoire ? | Rôle |
|---|---|---|
| `TELEGRAM_BOT_TOKEN` | oui | token donné par BotFather |
| `TELEGRAM_CHAT_ID` | oui | ton identifiant de conversation |
| `ANTHROPIC_API_KEY` | non, **recommandé** | résumés et catégories en français (modèle `claude-haiku-4-5-20251001`, quelques centimes par jour) |
| `GH_PAT` | non | jeton GitHub en lecture seule : quota de 5 000 requêtes/heure au lieu de ~1 000 |

Sans `ANTHROPIC_API_KEY`, le bot fonctionne, mais les descriptions restent celles des dépôts (souvent en anglais) et les catégories sont déduites des topics par des règles : c'est moins précis.

*Créer un `GH_PAT` (seulement si tu vois des avertissements de quota dans les logs)* : GitHub → Settings → Developer settings → Personal access tokens → **Fine-grained tokens** → accès « Public Repositories (read-only) », aucune autre permission.

### 4. Activer GitHub Pages sur `/docs`

Dépôt → **Settings → Pages** → *Source* : **Deploy from a branch** → Branch : `main`, dossier **`/docs`** → Save. L'URL apparaît au bout d'une minute : `https://<ton-user>.github.io/ai-radar/`.

### 5. Premier test manuel

1. Onglet **Actions** → workflow « AI Radar quotidien » → **Run workflow**. (Si GitHub demande d'activer les workflows, accepte.)
2. Compte ~6 minutes. Tu dois recevoir 1 à 3 messages Telegram, et un commit `chore: snapshot AAAA-MM-JJ` apparaît.
3. Si ça échoue, tu reçois « ⚠️ AI Radar a échoué : … » sur Telegram et le détail est dans les logs de l'étape « Générer et envoyer le radar ».

Ensuite, tout est automatique.

### 6. Régler les poids, les catégories et les quotas

Tout est dans **`config.yaml`** (commenté). Les réglages qui comptent :

| Je veux… | Je modifie |
|---|---|
| Privilégier les très gros gains en valeur absolue / les plus fortes croissances relatives | `score.weights` (`velocity_abs`, `velocity_rel`, `acceleration`, `freshness`, `activity`, `buzz`) |
| Plus ou moins de projets par catégorie | `max_per_category` (défaut 5) |
| Plus de diversité entre auteurs | `max_per_owner` (défaut 2) |
| Un classement plus court / plus long | `top_n` (et `podium`) |
| Éviter les mini-dépôts à 30 étoiles | `filters.min_stars_24h` (défaut 40) |
| Ajouter / retirer / renommer une catégorie | `categories` (id, emoji, nom, `topics` et `keywords` pour le classement de secours) |
| Chercher d'autres sujets | `collect.search.topics` |
| Changer les licences acceptées | `filters.licenses` |
| Réintégrer les listes « awesome » | `filters.exclude_lists: false` |
| Recevoir par e-mail | `channel: email` + secrets `SMTP_HOST`, `SMTP_PORT`, `SMTP_USER`, `SMTP_PASSWORD`, `EMAIL_TO` (à ajouter aussi dans `daily.yml`) |

Teste tes réglages sans rien envoyer :

```bash
python -m src.main --dry-run
```

---

## Heure d'été / heure d'hiver

Le `cron` de GitHub est en **UTC** et ne connaît pas le changement d'heure. Paris, c'est UTC+2 en été et UTC+1 en hiver. Pour envoyer à 8 h toute l'année, le workflow se déclenche **deux fois** :

- `0 6 * * *` (06:00 UTC = 8 h en été),
- `0 7 * * *` (07:00 UTC = 8 h en hiver).

Au démarrage, le script regarde l'heure à Paris : il ne travaille que s'il est au moins 8 h **et** s'il n'a pas déjà envoyé aujourd'hui. L'autre déclenchement s'arrête aussitôt (« Déclenchement ignoré »), sans commit. Avantage : même si GitHub retarde le cron (c'est fréquent aux heures chargées), l'envoi a lieu quand même le jour même. Pour changer l'heure d'envoi, modifie `user.send_hour` **et** les deux lignes `cron` (heure souhaitée moins 2 h, et moins 1 h).

## Pourquoi le dépôt reçoit un commit chaque jour

GitHub **désactive les workflows planifiés après 60 jours sans activité** sur le dépôt. Chaque exécution fait donc un commit `chore: snapshot AAAA-MM-JJ` (avec `--allow-empty` : il a lieu même si rien n'a changé), ce qui garde le cron actif. Il sauvegarde aussi `data/history.json` et les pages de `docs/`. Si tu vois un jour un message GitHub « scheduled workflows disabled », clique simplement sur *Enable* dans l'onglet Actions.

## Comment ça marche

1. **Collecte** (≈ 1 900 dépôts) : page Trending (jour + semaine, 7 langages), API Search (32 topics × 2 requêtes), et les dépôts classés dans ton top 60 ces 7 derniers jours.
2. **Filtres** : licence OSI (liste blanche), lié à l'IA (topics, mots-clés, vérification du README pour les cas limites), pas de fork / archivé / miroir, un push dans les 14 jours, pas de liste « awesome » ni de collection de prompts ou de cours. Les dépôts aux étoiles suspectes (> 1 000 étoiles et < 0,5 % de forks) sont rétrogradés (score × 0,5).
3. **Pré-classement** gratuit puis **mesures fines** des 80 meilleurs : étoiles 24 h / 7 j, commits, release, Hacker News, README.
4. **Score** = somme pondérée de six composantes normalisées (vélocité absolue et relative, accélération, fraîcheur, activité, buzz).
5. **Répartition** : max 5 par catégorie, max 2 par auteur, règle de secours pour ne pas laisser une catégorie vide si elle a un bon candidat (≥ 60 % du score du 20e).
6. **Rendu et envoi**, puis sauvegarde de l'état.

Un projet resté plus de 7 jours dans le top, dont les gains baissent 3 jours de suite, sort du message Telegram (il reste sur la page web, marqué 💤).

## Limites à connaître

- **Étoiles récentes : pas de `starred_at`.** L'endpoint `/stargazers` (qui donnait la date de chaque étoile) répond 404 pour les dépôts tiers avec les jetons actuels (constaté en octobre 2026, REST et GraphQL). AI Radar reconstitue donc les étoiles récentes à partir des **événements du dépôt** (`WatchEvent`, 300 max). Pour les très gros mouvements, la fenêtre ne remonte pas à 24 h : le chiffre est alors **extrapolé** (marqué **≈**), avec le risque de surestimer un pic très récent. À partir du 2ᵉ jour, l'**historique** (`history.json`) prend le relais et donne des différences exactes.
- **7 jours inconnus** : si un dépôt ancien n'est pas dans Trending et n'a pas d'historique, son gain sur 7 jours est inconnu (affiché « ≥ ») et son accélération prend une valeur neutre.
- **Premier jour** : le top 20 est correct mais sans flèches ni 🔁 ; les listes « confirmés / feux de paille » du dimanche demandent ≥ 5 jours d'historique.
- **Page Trending** : c'est du scraping HTML, il peut casser si GitHub change sa page. Le bot continue alors sans, avec un classement un peu moins précis.
- **Résumés sans clé Anthropic** : descriptions brutes (souvent en anglais), catégories par règles. Avec la clé : un à trois appels par jour, résumés mis en cache (`data/history.json`).
- Les catégories sont posées par un modèle de langage : elles peuvent se tromper. Corrige les règles de secours dans `config.yaml` si tu constates des erreurs récurrentes.

## Utilisation en local (optionnel)

```bash
python -m venv .venv && .venv\Scripts\activate        # Windows (macOS/Linux : source .venv/bin/activate)
pip install -r requirements.txt
set GH_PAT=ghp_xxx                                    # PowerShell : $env:GH_PAT="ghp_xxx"
python -m src.main --dry-run                          # aperçu console + page dans out/docs/
python -m src.main --dry-run --top 10                 # top 10 seulement
python -m src.main --dry-run --weekly                 # aperçu du récap hebdomadaire
python -m pytest                                      # tests (aucun appel réseau)
```

`--dry-run` n'envoie rien et n'écrit ni `history.json` ni `docs/`.
Sans `GH_PAT`/`GITHUB_TOKEN`, GitHub limite à 60 requêtes par heure : le programme le signale et dégrade ses mesures.
Erreur `CERTIFICATE_VERIFY_FAILED` sous Windows (antivirus/proxy) : `pip install truststore` (utilisé automatiquement).

## Arborescence

```
.github/workflows/daily.yml   planification, concurrence, commit quotidien
src/main.py        orchestration + options (--dry-run, --top N, --weekly)
src/collect.py     Trending (isolé, protégé) + API Search + dépôts suivis
src/metrics.py     étoiles (événements), commits, releases, Hacker News, README
src/filters.py     licence, IA, forks, listes awesome, étoiles suspectes
src/scoring.py     score, quotas par catégorie, badges, anti-monotonie
src/enrich.py      API Claude (JSON strict, cache) + repli par règles
src/render.py      message Telegram (découpage ≤ 4096) + pages HTML (Jinja2)
src/weekly.py      récap du dimanche
src/notify.py      Telegram / e-mail (aucun secret dans les logs)
src/state.py       data/history.json (purge à 60 jours)
templates/ docs/ tests/ data/ config.yaml requirements.txt
```

# AI News

Veille RSS/Atom sur l’IA, l’IT, la cybersécurité et les risques, avec un contexte métier de casino physique au Luxembourg. Le projet produit un briefing français, un MP3 et, en option, un podcast publié par SFTP.

## Flux de traitement

```text
config/sources.yaml
  → Collecte RSS/Atom et normalisation HTML
  → PostgreSQL : articles et historique de collecte
  → Scoring par mots-clés, fraîcheur, longueur et bonus métier
  → Vue unique_articles : une ligne par GUID
  → Briefing : filtres, watchlists, détection CVE/CVSS
  → MiniLM : classification et regroupement des événements
  → Qwen via Ollama : déduplication complémentaire, évaluation et sélection
  → Qwen : titres et résumés anglais
  → MarianMT + glossaire : traduction française
  → output/briefing.md
  → Mail HTML + texte via SMTP (optionnel)
  → Kokoro : output/AAAA-MM-JJ-briefing.mp3
  → SFTP : MP3, illustration et podcast.xml
```

La collecte et le scoring écrivent en base. Le briefing lit la base et écrit le Markdown ; ses évaluations Qwen ne sont pas conservées en base. Les modèles fonctionnent localement après téléchargement initial. Les priorités P1/P2 sont des signaux textuels provisoires, à vérifier dans les sources.

Le regroupement du briefing est autonome. `embeddings.worker` est un outil de diagnostic qui affiche des similarités ; il ne calcule aucun score et ne stocke aucun groupe.

## Installation

Toutes les commandes partent de la racine du dépôt. Docker et Docker Compose avec profils sont nécessaires ; ajouter `sudo` devant `docker` si votre compte l’exige.

### 1. Préparer la configuration

```bash
cd /opt/ai-news
mkdir -p output podcast/state
# Créer ou compléter .env avec votre éditeur.
```

Variables attendues dans `.env` (remplacer les exemples) :

```dotenv
POSTGRES_DB=ai_news
POSTGRES_USER=ai_news
POSTGRES_PASSWORD=mot_de_passe_a_remplacer
POSTGRES_PORT=5432
# Seulement pour la publication SFTP :
PODCAST_SFTP_USER=utilisateur_sftp
PODCAST_SFTP_PASSWORD=mot_de_passe_sftp
```

### 2. Construire et démarrer les services

```bash
docker compose --profile ai config --quiet
docker compose --profile ai build collector embeddings audio podcast
docker compose --profile ai up -d --wait postgres ollama
docker compose exec ollama ollama pull qwen2.5:3b
```

PostgreSQL et Ollama restent démarrés. Les autres services s’exécutent à la demande. Le profil `xtts` est une alternative audio distincte. Les limites mémoire des conteneurs s’additionnent : 6 Go pour `embeddings`, 4 Go pour Ollama, 2 Go pour `audio` ; surveiller la mémoire sur une machine de 8 Go.

### 3. Initialiser le schéma SQL

Compose ne monte pas de scripts d’initialisation PostgreSQL. Sur une base neuve, appliquer les quatre fichiers dans l’ordre :

```bash
for sql in app/sql/001_initial_schema.sql \
           app/sql/002_normalized_content.sql \
           app/sql/003_article_scoring.sql \
           app/sql/004_unique_articles.sql; do
  docker compose exec -T postgres sh -c \
    'psql -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d "$POSTGRES_DB"' < "$sql" || break
done
```

Vérifier que chaque fichier s’est terminé sans erreur avant de poursuivre. Les scripts actuels utilisent `IF NOT EXISTS` ou `CREATE OR REPLACE VIEW`.

## Exécution étape par étape

### 1. Collecter et normaliser

```bash
docker compose run --rm -T collector
```

Le collecteur parcourt les sources activées, insère les nouveaux articles, puis normalise le HTML. Un article existant de même source et même GUID n’est pas mis à jour. Les erreurs de flux sont enregistrées dans `collection_runs` et les autres flux continuent.

**Comportement actuel : une source avec `enabled: false` entraîne la suppression de ses articles en base au début de la collecte.**

Le catalogue est copié dans l’image du collecteur : après modification de `config/sources.yaml`, reconstruire :

```bash
docker compose build collector
```

### 2. Calculer les scores

```bash
docker compose run --rm -T collector python -m app.scorer
```

Le score combine `config/scoring.yaml` et le bonus métier de `config/briefing.yaml`. Il est utilisé pour filtrer les candidats du briefing. `scoring.rescore_all: true` recalcule toute la base ; sinon les scores absents ou anciens de plus de 12 heures sont traités.

Pour reprendre uniquement une normalisation incomplète :

```bash
docker compose run --rm -T collector python -m app.normalize_articles
```

### 3. Vérifier la sélection sans générer le briefing

```bash
docker compose --profile ai run --rm -T \
  -e BRIEFING_DRY_RUN=1 embeddings python -m embeddings.briefing
```

Ce contrôle charge MiniLM et appelle Qwen pour la déduplication et l’évaluation. Il ne fait ni synthèse, ni traduction, ni écriture du Markdown.

### 4. Générer le briefing

```bash
docker compose --profile ai run --rm -T \
  -e BRIEFING_DRY_RUN=0 \
  -e BRIEFING_OUTPUT=/output/briefing.md \
  embeddings python -m embeddings.briefing
cat output/briefing.md
```

Le montage `./output:/output` conserve le résultat après suppression du conteneur. Sans `BRIEFING_OUTPUT`, le chemin par défaut est `/tmp/ai-news-briefing.md`, non persistant.

Le briefing filtre les articles récents selon le score et les règles de veille, puis regroupe les événements. Qwen évalue leur pertinence technique, leur importance et leur pertinence métier. La sélection applique les quotas thématiques et donne priorité aux P1 si l’option est activée. Les synthèses sont produites en anglais, puis traduites en français avec protection des termes techniques. Une annexe peut ajouter des liens vers des alertes P1/P2 évaluées mais non retenues. Le nombre publié peut être inférieur au plafond configuré.

### 5. Générer le MP3

Avec le moteur actuel `kokoro`, ces fichiers locaux sont nécessaires :

```text
audio/models/kokoro/config.json
audio/models/kokoro/kokoro-v1_0.pth
audio/models/kokoro/voices/ff_siwis.pt
```

Ils sont montés dans `/models`. Les modèles présents sont actuellement suivis par Git malgré la règle de `.gitignore`. Le conteneur audio est configuré en mode Hugging Face hors ligne.

```bash
docker compose --profile ai run --rm -T audio
ls -lh "output/$(TZ=Europe/Paris date +%F)-briefing.mp3"
```

`config/audio.yaml` indique l’entrée Markdown, le moteur, la voix, les pauses et le débit. Le module préfixe automatiquement le nom de sortie par la date en Europe/Paris.

Le MP3 est mono, encodé à 22 050 Hz. Les paramètres se règlent dans le YAML : `pause_ms` entre articles, `title_pause_ms` après les titres et rubriques (0 à 10 000 ms), et `bitrate` parmi `24k`, `32k`, `40k`, `48k`. Vérifier à l’écoute les sigles, noms propres, CVE et anglicismes ; les pauses ne garantissent pas une prosodie particulière. Vérifier aussi les licences des modèles et voix utilisés.

Pour utiliser **Piper**, définir `engine: piper` dans `config/audio.yaml` et fournir le modèle ONNX ainsi que son fichier `.onnx.json` dans `audio/models/`. La voix française configurée est `/models/fr_FR-tom-medium.onnx`.

```bash
docker compose --profile ai build audio
docker compose --profile ai run --rm -T audio
```

Pour utiliser **XTTS**, définir `engine: xtts` et utiliser le service dédié, qui installe Coqui TTS. Le modèle nommé `tts_models/multilingual/multi-dataset/xtts_v2` utilise le cache `audio/models/xtts/` ; le premier lancement peut nécessiter un téléchargement. La référence vocale configurée est `audio/voices/homme_fr.wav`, montée dans `/voices/homme_fr.wav`.

```bash
docker compose --profile xtts build xtts
docker compose --profile xtts run --rm -T xtts
```

Repasser `engine: kokoro` pour le script quotidien, qui impose ce moteur. La génération audio produit le MP3 local ; la publication du RSS est réalisée à l’étape suivante.

### 6. Préparer et publier le podcast (optionnel)

Adapter `config/podcast.yaml` : URL publique, métadonnées, destination SFTP et rétention. Fournir `podcast/cover.jpg` et les identifiants dans `.env`. La clé du serveur doit être vérifiée et présente dans le fichier `known_hosts` monté par Compose ; son chemin hôte actuel est `/home/seb/.ssh/known_hosts`.

Utiliser une illustration JPEG carrée, idéalement 3 000 × 3 000 pixels en RGB. Vérifier `sftp.remote_path` depuis la session SFTP. L’adresse `podcast.email` apparaît publiquement dans le RSS : choisir une adresse de contact adaptée.

Pour récupérer la clé du serveur, `ssh-keyscan -p 22 ftp.cluster030.hosting.ovh.net` peut l’afficher ; vérifier son empreinte auprès d’une source de confiance avant de l’ajouter au fichier `known_hosts`.

Une clé SSH peut remplacer le mot de passe : la monter en lecture seule dans le service `podcast`, puis définir son chemin **dans le conteneur** via `.env` :

```dotenv
PODCAST_SFTP_KEY_FILE=/run/secrets/ovh_sftp_key
# Surcharges facultatives de config/podcast.yaml :
PODCAST_SFTP_HOST=ftp.cluster030.hosting.ovh.net
PODCAST_SFTP_PORT=22
```

Exemple de montage à ajouter aux volumes existants :

```yaml
      - /chemin/hote/cle_privee:/run/secrets/ovh_sftp_key:ro
```

`PODCAST_SFTP_PASSWORD` n’est pas nécessaire avec cette clé. Le programme de publication est `podcast/publish.py` ; son image est construite par `podcast/Dockerfile`.

Le montage `./podcast/state:/state` conserve l’historique RSS entre les exécutions. Chaque MP3 publié reçoit un nom unique `AAAA-MM-JJ-<empreinte SHA-256>-briefing.mp3`, utilisé aussi comme identifiant RSS. Plusieurs fichiers différents publiés le même jour ajoutent plusieurs épisodes ; republier le même fichier le même jour conserve une seule entrée et sa date initiale. Le fichier local reste `output/AAAA-MM-JJ-briefing.mp3`.

```bash
# Vérifie les fichiers et leur lecture, sans connexion SFTP.
docker compose --profile ai run --rm -T podcast --dry-run
# Publie le MP3, l’illustration et le RSS sur le serveur configuré.
docker compose --profile ai run --rm -T podcast
```

Après publication, vérifier le RSS à l’adresse configurée, actuellement <https://podcast.chauchard.info/podcast.xml>, ainsi que les liens MP3. La description des épisodes reprend les titres et les sources du Markdown (`**Sources :**`, `Sources:` ou listes de liens après un bloc sources). Si le format du briefing change, vérifier son interprétation avec `--dry-run`.

Le contrôle à blanc nécessite déjà le MP3 daté, le Markdown et l’illustration. La publication transfère d’abord le MP3 et l’illustration, puis remplace atomiquement le RSS avant de supprimer les MP3 distants expirés (7 jours calendaires actuellement). Seuls les MP3 de briefing expirés portant le nom daté historique ou le nouveau nom avec empreinte sont supprimés. Conserver `podcast/state/episodes.json` : les anciens épisodes ne sont pas reconstruits depuis le serveur. Si cet historique a déjà été perdu, cette correction conserve les nouveaux épisodes mais ne restaure pas les précédents.

### 7. Envoyer le briefing par mail (optionnel)

Le service `mailer` transforme `output/briefing.md` en mail HTML avec titres, listes, tableaux et liens sources. Il joint aussi une version texte dans le même message. L’envoi est indépendant de l’audio et du podcast.

Modifier `config/mail.yaml` :

```yaml
input: /output/briefing.md
sender: "AI News <ai-news@example.com>"
recipient: destinataire@example.com
subject: "AI News — Briefing du {date}"
smtp:
  host: smtp.example.com
  port: 587
  security: starttls
  auth: true
  timeout: 30
```

`{date}` est remplacé par la date en Europe/Paris. Choisir `starttls` pour une connexion mise à niveau TLS (généralement port 587), ou `ssl` pour TLS dès la connexion (généralement port 465). La vérification du certificat est activée. `none` est réservé à un relais sans authentification (`auth: false`). L’expéditeur doit être autorisé par votre serveur SMTP.

Renseigner les deux variables ajoutées à `.env` :

```dotenv
SMTP_USERNAME=identifiant_smtp
SMTP_PASSWORD=mot_de_passe_smtp
```

Compose transmet uniquement ces deux variables au service mail. Le module lancé directement en Python attend ces variables dans son environnement ; il ne charge pas `.env` lui-même.

```bash
docker compose --profile mail build mailer
# Génère output/briefing-email.html sans connexion SMTP ni envoi.
docker compose --profile mail run --rm -T mailer --dry-run
# Envoie le briefing au destinataire configuré.
docker compose --profile mail run --rm -T mailer
```

L’aperçu ne nécessite pas d’identifiants SMTP. Chaque exécution sans `--dry-run` envoie un nouveau mail ; il n’y a pas de déduplication des envois. Le script `run-daily.sh` envoie le mail après la génération et la vérification du Markdown, avant les étapes audio et podcast. Un échec d’envoi arrête le pipeline. Construire l’image `mailer` et renseigner la configuration SMTP avant le prochain lancement quotidien.

## Configuration utile

| Fichier | Rôle |
|---|---|
| `config/sources.yaml` | Catalogue RSS/Atom et activation des sources |
| `config/scoring.yaml` | Mots-clés et pondérations du score historique |
| `config/briefing.yaml` | Fenêtre, sélection, Qwen et bonus métier du scorer |
| `config/watchlists.yaml` | Inclusion, exclusion et technologies suivies |
| `config/semantic_scoring.yaml` | Descriptions des domaines pour MiniLM |
| `config/glossary.yaml` | Termes protégés pendant la traduction |
| `config/audio.yaml` | Moteur, voix et paramètres MP3 |
| `config/podcast.yaml` | Métadonnées publiques, SFTP et rétention |
| `config/mail.yaml` | Destinataire, expéditeur, objet et paramètres SMTP |

Paramètres actuels du briefing : fenêtre de 24 heures, score minimum 35, plafond global de 15 événements, cible de 50 candidats Qwen, jusqu’à 8 arbitrages de déduplication. La sélection ordinaire exige une pertinence technique d’au moins 2/5 et une importance d’au moins `min_strategic_importance` (2 actuellement). La pertinence métier sert au classement ; les seuils `min_business_relevance` et `min_security_business_relevance` ne sont pas appliqués par la sélection actuelle.

Les fichiers de configuration du briefing sont montés depuis l’hôte. Ceux du collecteur et du scorer sont copiés à la construction : reconstruire `collector` après modification. Le contexte métier du prompt Qwen reste écrit dans `embeddings/briefing.py` ; modifier le YAML ne le remplace pas.

## Diagnostic et automatisation

```bash
# État et journaux des services persistants
docker compose --profile ai ps
docker compose logs --tail=100 postgres ollama
docker stats --no-stream

# Analyse facultative des similarités (aucune écriture en base)
docker compose --profile ai run --rm -T embeddings python -m embeddings.worker

# Statut des collectes
docker compose exec -T postgres sh -c \
  'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -c "SELECT status, COUNT(*) FROM collection_runs GROUP BY status;"'
```

`run-daily.sh` enchaîne infrastructure, collecte, diagnostic MiniLM, briefing, envoi du mail, audio et publication. **Il omet actuellement `app.scorer`** : ajouter l’étape de scoring après la collecte avant de l’utiliser comme pipeline complet. Son verrou `/run/ai-news.lock` exige les permissions adaptées. Aucune planification cron/systemd n’est livrée.

Autres limites à garder en tête : les articles sans date de publication sont exclus du briefing ; la vue SQL déduplique uniquement par GUID ; les évaluations Qwen sont répétées à chaque exécution. Les résumés et priorités produits par les modèles demandent une vérification des sources avant une décision opérationnelle.

Documentation vérifiée par lecture du code. L’exécution complète PostgreSQL/Ollama/traduction/audio/SFTP n’a pas été relancée pour cette revue.

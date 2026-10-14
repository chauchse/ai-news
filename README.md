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

La collecte et le scoring écrivent en base. Le briefing lit la base et écrit le Markdown ; la traçabilité optionnelle des retours conserve aussi ses évaluations Qwen après activation. Les modèles fonctionnent localement après téléchargement initial. Les priorités P1/P2 sont des signaux textuels provisoires, à vérifier dans les sources.

Le regroupement du briefing est autonome. `embeddings.worker` est un outil de diagnostic qui affiche des similarités ; il ne calcule aucun score et ne stocke aucun groupe.

## Installation

Prérequis : Linux, Docker avec Compose v2, `curl`, accès Internet et espace disque
pour les modèles. Toutes les commandes partent de la racine du dépôt ; ajouter
`sudo` devant `docker` si nécessaire.

```bash
./init-repo.sh
```

Le script prépare les dossiers, crée `.env` s’il est absent, télécharge les modèles
et voix, construit les images, démarre PostgreSQL et Ollama et applique les scripts
SQL. Il conserve le `.env` et les fichiers de modèles non vides déjà présents.
Il ne lance ni collecte, ni envoi de mail, ni publication.

```bash
# Modèles Kokoro uniquement
./init-repo.sh --models-only
# Installation avec les voix Piper
./init-repo.sh --with-piper
# Modèles Kokoro et Piper uniquement
./init-repo.sh --models-only --with-piper
```

PostgreSQL et Ollama sont persistants ; les autres services s’exécutent à la demande.
Les limites mémoire s’additionnent : 6 Go pour `embeddings`, 4 Go pour Ollama et
2 Go pour `audio` dans Compose. Les modèles sont exclus de Git.

## Configuration

Les réglages applicatifs sont dans `config.yaml`, chargés par `project_config.py`.
Les secrets et adresses de déploiement restent dans `.env`.

```bash
docker compose --profile ai config --quiet
docker compose --profile ai build collector embeddings audio podcast
docker compose --profile ai up -d --wait postgres ollama
docker compose exec ollama ollama pull qwen2.5:3b
```

PostgreSQL et Ollama restent démarrés. Les autres services s’exécutent à la demande. Le profil `xtts` est une alternative audio distincte. Les limites mémoire des conteneurs s’additionnent : 6 Go pour `embeddings`, 4 Go pour Ollama, 2 Go pour `audio` ; surveiller la mémoire sur une machine de 8 Go.

### 3. Initialiser le schéma SQL

Compose ne monte pas de scripts d’initialisation PostgreSQL. Sur une base neuve, appliquer les fichiers dans l’ordre :

```bash
for sql in app/sql/001_initial_schema.sql \
           app/sql/002_normalized_content.sql \
           app/sql/003_article_scoring.sql \
           app/sql/004_unique_articles.sql \
           app/sql/005_feedback.sql; do
  docker compose exec -T postgres sh -c \
    'psql -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d "$POSTGRES_DB"' < "$sql" || break
done
```

Le collecteur monte le dépôt en écriture pour mettre à jour les sources ; les autres
services lisent `config.yaml` en lecture seule. Pour une copie de configuration,
utiliser `AI_NEWS_CONFIG` en Python local ou `--config` pour mail, audio et podcast.

## Collecte et scoring

```bash
# Écrit les articles et les résultats de collecte en base
docker compose run --rm -T collector
# Recalcule les scores en base
docker compose run --rm -T collector python -m app.scorer
```

Les sources utilisent `collection.sources[].url` et `enabled`.
Une source désactivée entraîne la suppression de ses articles pendant la collecte.
La désactivation automatique utilise le seuil de `collection.source_health` : les
erreurs de téléchargement ou de lecture comptent, les erreurs SQL ne comptent pas.
Un flux valide sans article n’est pas un échec.

La mise à jour du YAML est verrouillée, atomique et sauvegardée dans
`config.yaml.bak`. Si elle échoue, la purge automatique n’est pas effectuée.
Un article de même source et de même GUID n’est pas réécrit.

`briefing.scoring.rescore_all` active le recalcul total ; sinon seuls les scores
absents ou datant de plus de douze heures sont traités.

## Briefing et éditions quotidiennes

```bash
# Prépare le jour courant : collecte, scoring, Markdown, aperçus HTML et MP3
# Écrit en base et dans output/, sans SMTP ni SFTP
sudo ./run-daily.sh --prepare
# Pipeline quotidien avec envoi des mails et publication SFTP
sudo ./run-daily.sh
```

Les deux Markdown sont préparés avant les envois. Le pipeline traite ensuite le
mail, l’audio et le podcast anglais, puis français. Il impose le moteur Kokoro.

```text
output/daily/AAAA-MM-JJ/
├── generated
├── en/
│   ├── briefing.md
│   ├── briefing-email.html
│   ├── AAAA-MM-JJ-briefing.mp3
│   ├── mail.done
│   ├── audio.done
│   └── podcast.done
└── fr/
    └── mêmes fichiers
```

Les marqueurs permettent la reprise sans répéter les étapes terminées. Les aperçus
ne créent pas de marqueur d’envoi. La date est fixée en Europe/Paris au début du
pipeline. Les dossiers quotidiens ne sont pas purgés automatiquement.

Pour évaluer la sélection seule :

```bash
docker compose --profile ai run --rm -T \
  -e BRIEFING_DRY_RUN=1 embeddings python -m embeddings.briefing
```

Cette commande charge MiniLM et appelle Qwen, sans synthèse, traduction ni écriture
Markdown. `embeddings.worker` est un diagnostic de similarité, sans scoring ni
stockage de groupes.

## Éditions rétrospectives

```bash
# Remplacer la date par une journée passée
sudo ./run-daily.sh --date 2026-10-10
# Publication des podcasts, sans mail
sudo ./run-daily.sh --date 2026-10-10 --publish
# Envoi des mails, sans publication
sudo ./run-daily.sh --date 2026-10-10 --send-mail
```

La préparation utilise les articles et scores disponibles en base, sans collecte
ni recalcul. La sélection couvre la journée civile Europe/Paris selon
`published_at`, y compris les journées de 23 ou 25 heures. Les modèles et la
configuration sont ceux du lancement.

Préparer plusieurs journées dans l’ordre chronologique. Les éditions ultérieures
ne sont pas réécrites. Des Markdown présents sans marqueur `generated` arrêtent
la reprise pour permettre leur vérification. La publication est limitée par la
rétention du podcast ; les liens de feedback suivent leur propre expiration.

## Mail

Renseigner le serveur dans `mail.smtp` et les variables suivantes dans `.env` :

```dotenv
SENDER="AI News <ai-news@example.com>"
RECIPIENT=destinataire@example.com
SMTP_USERNAME=identifiant_smtp
SMTP_PASSWORD=mot_de_passe_smtp
```

Les objets peuvent être définis par langue ; `{date}` utilise Europe/Paris.
SMTP prend en charge STARTTLS et SSL avec vérification du certificat.

```bash
# Aperçu local HTML, sans connexion SMTP
docker compose --profile mail run --rm -T mailer --dry-run
# Envoi réel du Markdown configuré
docker compose --profile mail run --rm -T mailer
```

Chaque envoi manuel sans `--dry-run` envoie un nouveau mail. Les marqueurs de
reprise sont gérés par le pipeline quotidien, pas par cette commande manuelle.

## Audio et podcast

Kokoro utilise les modèles et voix de `audio/models/kokoro/`. Le MP3 est mono,
à 22 050 Hz. Les débits acceptés sont `24k`, `32k`, `40k`, `48k` ; les pauses sont
configurables entre 0 et 10 000 ms. La date préfixe le nom de sortie.

```bash
# Génère le MP3 à partir du Markdown configuré
docker compose --profile ai run --rm -T audio
```

Piper nécessite une voix ONNX et son fichier `.onnx.json`. XTTS utilise le service
`xtts`, son cache de modèle et un fichier WAV de référence. Ces alternatives
s’utilisent hors du pipeline quotidien.

Le podcast publie le MP3, la couverture et un RSS par langue : `/en/podcast.xml`
et `/fr/podcast.xml`. Configurer les métadonnées dans `podcast`, les identifiants
SFTP et `PODCAST_SITE_URL` dans `.env`. Fournir `podcast/cover.jpg` et la clé du
serveur vérifiée dans `podcast/known_hosts`, ou via `PODCAST_KNOWN_HOSTS`.

```bash
# Vérifie les fichiers et prépare le RSS local, sans transfert
docker compose --profile ai run --rm -T podcast --language fr --dry-run
# Publication SFTP réelle
docker compose --profile ai run --rm -T podcast --language fr
```

Le contrôle à blanc nécessite le Markdown, le MP3 daté et la couverture.
La publication transfère les fichiers avant le remplacement atomique du RSS.
Les fichiers MP3 distants expirés sont supprimés selon `podcast.retention_days`.

Le nom distant inclut la date et l’empreinte SHA-256 du MP3. Publier le même fichier
pour la même date conserve une seule entrée RSS et sa date de publication.
Conserver `podcast/state/` pour préserver les épisodes.

## Feedback

Le mail peut afficher six motifs par article : utile, hors sujet, non applicable,
superficiel ou promotionnel, sans nouveauté, déjà couvert. Les liens sont signés
avec `FEEDBACK_SIGNING_SECRET` et pointent vers `FEEDBACK_URL`, définis dans `.env`.
Le secret doit correspondre à celui du récepteur PHP et contenir au moins 32 octets.

Les options de `mail.feedback` sont :

Avant la sélection, le briefing lit les `briefing.md` des sept jours précédents dans `output/daily/AAAA-MM-JJ/en/` ou, à défaut, `fr/`. Il ne compare pas les deux éditions d’une même journée, ni celle du jour en cours. Aucun historique JSON supplémentaire n’est créé. Sans briefing antérieur, aucun contrôle n’est effectué.

Les événements sans résumé exploitable dont toutes les URL sources ont déjà été utilisées sont exclus. Avec du contenu disponible, une URL déjà traitée est soumise à l’arbitrage de nouveauté pour préserver les évolutions substantielles. Pour les nouvelles sources ou reformulations FR/EN, MiniLM repère les événements proches ; Qwen confirme s’il s’agit du même événement sans information nouvelle. Une évolution substantielle est conservée, et un thème commun ne suffit pas à exclure un sujet. La détection sémantique reste probabiliste.

La configuration `briefing.history.days` règle la fenêtre (7 par défaut, 0 pour désactiver) et `similarity_threshold` le seuil de présélection sémantique (0.72). Le modèle MiniLM est partagé avec le regroupement pour éviter un deuxième chargement. Les exclusions sont consignées dans les logs. Les fichiers existants ne sont pas modifiés. Une erreur de lecture ou d’évaluation arrête la génération plutôt que d’ignorer silencieusement le contrôle.

## Retours utilisateur et apprentissage local

Une boucle optionnelle de votes par motif est préparée :

```text
Bouton du mail → confirmation PHP publique dans /feedback/
  → fichiers JSON privés hors racine Web
  → pull local → stockage PostgreSQL → confirmation et purge des retours importés
  → personnalisation et apprentissage locaux → sélection du prochain briefing
```

Aucun modèle ni base distante.
Les sources du composant public sont conservées dans [web/](web/),
avec un exemple de configuration privée sans secrets. Le script PHP et son `.htaccess` sont déployés sur l’hébergement du podcast.
Le `.htaccess` demande aussi aux robots de ne pas indexer les pages. Cela ne
bloque pas les vérifications automatiques de Gmail : le GET reste sans vote,
avec confirmation POST par défaut pour éviter les fausses annotations.
Pour l’essai demandé, le vote dès le clic est activé dans la configuration
PHP privée (`require_confirmation: false`, syntaxe PHP `=> false`). Remettre
`require_confirmation` à `true` rétablit la confirmation, sans renvoyer les mails.
Ce mode expérimental peut enregistrer les visites des vérificateurs Gmail ;
le clic ouvre toujours une page Web.
La personnalisation commence en parallèle ; l'entraînement supervisé CPU dispose
de commandes séparées et n'active aucun modèle automatiquement.

La fonctionnalité est désactivée par défaut dans `config/feedback.yaml`. Appliquer
la migration `app/sql/005_feedback.sql`, préparer le composant public et vérifier
ses permissions avant activation. L'URL vient de `FEEDBACK_URL` dans `.env`, avec
deux secrets distincts pour les liens et l'API. Les briefings existants ne sont
pas régénérés. Le composant public a été déployé le 12 octobre 2026 par SFTP :
`/home/chaucha/podcast/feedback/` pour PHP et
`/home/chaucha/ai-news-feedback-private/pending/` pour les JSON privés.
La confirmation POST, le double envoi, le pull authentifié et l’ack exact ont
été vérifiés en HTTPS avec un retour technique sauvegardé localement puis purgé.
La migration PostgreSQL 005 a ensuite été appliquée et un import manuel réel
a stocké deux retours avant confirmation et purge ; la file publique est vide.
Ces retours de test sont conservés comme non résolus jusqu’au rattachement de
leurs présentations. L’import automatique et la personnalisation restent désactivés.

Des mails de test FR/EN ont été préparés à partir des briefings existants du
12 octobre 2026 dans `output/feedback-email-test/2026-10-12/{fr,en}/` : ouvrir
`email.html` pour l'aperçu ou `email.eml` dans un logiciel de messagerie.
Chaque édition contient 14 événements et 84 liens signés vérifiés. À la demande
de l'utilisateur, les deux mails portant un objet `[TEST FR]` ou `[TEST EN]` ont
été acceptés par le serveur SMTP pour le destinataire configuré dans `.env`.
Cela ne confirme pas leur arrivée en boîte de réception. Les reçus locaux
`smtp-receipt.json` conservent la trace de l'envoi. Aucun podcast n'a été
régénéré et les briefings originaux restent intacts.
Un nouvel envoi FR/EN avec l’objet `[TEST FEEDBACK …]` a été accepté par SMTP
pour tester le mode sans confirmation. La collecte suivante a importé 11 retours
sans erreur, puis confirmé leur purge ; la file publique est vide.
Les liens et identités initiaux sont réutilisés : un même lien déjà importé reste
le même retour, sans double annotation. Les traces sont dans
`output/feedback-email-test/essai-*/`.
Les identifiants de ces copies sont propres au test, communs FR/EN et non
enregistrés en base ; le test complet exige encore leur enregistrement local et l’import PostgreSQL. Ces fichiers contenant des liens signés restent privés.

Audit PHP ciblé : plafond de 12 retours en attente par présentation, refus des
POST cross-site, erreurs PHP masquées et fichiers publics limités au script.
Correctifs déployés et vérifiés en HTTPS. Le mode direct reste sensible aux
robots Gmail et aux liens volés ; l’hébergement n’a pas fait l’objet d’un audit complet.

Première analyse locale : 13 soumissions, 11 présentations notées sur 14 ;
6 votes effectifs « utile », 3 « hors sujet », 2 « déjà traité ».
Rapports dans `output/feedback-analysis/20261012T122813Z/`.
L’apprentissage a été lancé mais n’a créé aucun modèle : les présentations de
test restent non résolues et les caractéristiques d’entraînement manquent.

Voir [docs/feedback.md](docs/feedback.md) pour le protocole, les commandes,
les tests, les limites d'évaluation et le retour arrière.

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
| `config/feedback.yaml` | Activation, limites d’import et personnalisation (URL/secrets dans `.env`) |
| `config/mail.yaml` | Objet par langue et paramètres SMTP (adresses dans `.env`) |

Par défaut, GET affiche une confirmation et POST enregistre le vote. Le mode
`require_confirmation: false` permet le vote direct mais expose aux votes de
scanners mail. Ne pas suivre les liens réels pour un contrôle automatique.

`feedback/importer.py` fournit la récupération authentifiée, le stockage
PostgreSQL puis l’acquittement des identifiants enregistrés. Les votes d’article
et les votes globaux sont stockés séparément ; les répétitions connues ne créent
pas de doublon. La synchronisation n’est pas intégrée à `run-daily.sh` et aucun
entraînement automatique n’est implémenté.

## Planification et journaux

Sur Linux avec systemd, cron et logrotate :

```bash
sudo ./post-install.sh
```

Le script installe le service `ai-news.service`, un cron quotidien à **04:00 selon
le fuseau du serveur** et la rotation des journaux. Il ne lance pas le pipeline
immédiatement. Le service exécute le pipeline complet, avec mail et publication.

```bash
sudo systemctl status ai-news.service
sudo journalctl -u ai-news.service -f
sudo tail -f /var/log/ai-news.log
```

Le verrou `/run/ai-news.lock` empêche deux pipelines simultanés. Le journal
`/var/log/ai-news.log` reçoit les sorties et erreurs ; logrotate conserve sept
archives avec compression différée. Les journaux de PostgreSQL et Ollama restent
accessibles par `docker compose logs`.

## Limites et repères

Les articles sans date sont exclus du briefing. Une date RSS erronée peut fausser
la sélection. La vue `unique_articles` déduplique par GUID ; le briefing déduplique
les événements. Les synthèses et traductions demandent une vérification humaine.

Les scripts du schéma PostgreSQL sont dans `app/sql/`. Les tests sont dans `tests/`.
Les règles de contribution et de validation sont dans [AGENTS.md](AGENTS.md).

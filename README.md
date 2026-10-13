# AI News

Veille RSS/Atom sur l’IA, l’IT, la cybersécurité et les risques, avec un contexte
métier de casino physique au Luxembourg. Le projet produit un briefing en anglais
et en français, des mails HTML et texte, des MP3 et des flux RSS de podcast.
Les modèles fonctionnent localement après téléchargement.

## Fonctionnement

1. Collecte des sources activées et normalisation des articles dans PostgreSQL.
2. Scoring par mots-clés, fraîcheur, longueur du contenu et bonus métier.
3. Filtrage des articles, règles de veille et détection des indices CVE/CVSS.
4. Classification et regroupement des événements avec MiniLM.
5. Arbitrage des doublons, évaluation et synthèse anglaise avec Qwen via Ollama.
6. Traduction française avec MarianMT et protection des termes du glossaire.
7. Génération des Markdown, mails, MP3 et podcasts.

Les éditions EN et FR partagent la même sélection et les mêmes sources.
Les titres et noms des sources restent dans leur langue d’origine.
Les priorités P1/P2 sont des indices textuels, pas des confirmations de gravité
ou d’exploitation. Une sélection peut contenir moins d’événements que le plafond.

Le contrôle inter-journalier compare les briefings précédents avec MiniLM puis
Qwen pour distinguer répétition et évolution substantielle. Il utilise les
Markdown existants, sans fichier d’historique supplémentaire.

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

| Section | Rôle |
|---|---|
| `collection` | Sources RSS/Atom et désactivation automatique |
| `scoring` | Poids communs des domaines, mots-clés et plafonds du score |
| `briefing.briefing` | Langues, sélection, historique et déduplication |
| `briefing.prompts` | Instructions Qwen et gabarits de messages |
| `briefing.llm` | Modèle et température |
| `briefing.scoring` | Bonus et contexte métier |
| `semantic_scoring` | Profils MiniLM et diagnostic |
| `watchlists` | Inclusions, exclusions et technologies suivies |
| `glossary` | Termes protégés et traductions |
| `audio` | Moteur, voix, pauses et débit |
| `podcast` | Métadonnées, éditions et rétention |
| `mail` | SMTP, objets et options de feedback |

`scoring.domain_weights` fournit les mêmes priorités au bonus métier et à MiniLM.
Les poids doivent être des nombres finis positifs ou nuls. Les
`scoring.topics[].max_points` sont des plafonds du score par mots-clés.

`briefing.briefing.strategic_topics` définit les motifs de reconnaissance, leur
ordre de priorité, la limite de résumé analysée et le thème de repli.
Le premier motif correspondant détermine le thème. Cet ordre sert aussi à
répartir les candidats entre thèmes.

Les prompts et leurs champs sont validés au chargement. Conserver les noms des
champs entre accolades ; doubler les accolades pour un JSON littéral (`{{`, `}}`).
MiniLM et MarianMT reçoivent des textes à encoder ou traduire, sans prompt
conversationnel.

Modifier le YAML ne régénère aucune édition et n’envoie aucun mail.
Après modification du code ou des dépendances, reconstruire les images concernées :

```bash
docker compose --profile ai --profile mail build collector embeddings audio podcast mailer
# Alternative XTTS uniquement
docker compose --profile xtts build xtts
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

| Option | Rôle |
|---|---|
| `enabled` | Active les liens quand l’URL est renseignée ; valeur par défaut : `true` |
| `link_days` | Durée depuis la date d’édition ; valeur par défaut : 90 jours |
| `newsletter_enabled` | Active les choix « Équilibrée », « Trop cyber », « Trop IA » ; défaut : `false` |

Le récepteur est `web/public/feedback.php` (PHP >= 8.1). Sa configuration privée,
ses secrets et ses fichiers de votes restent hors de la racine web ; un exemple
est fourni dans `web/private.example/config.php`. En mode GET direct, la durée
`link_seconds` doit correspondre à `mail.feedback.link_days`.

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

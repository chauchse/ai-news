# AI News — README technique V52

> Documentation basée sur les fichiers V52, le catalogue `sources.yaml`, ainsi que le `docker-compose.yml` et le code du collecteur communiqués le 10 octobre 2026. Les Dockerfiles, le schéma SQL complet, `app.normalizer`, `embeddings/worker.py` et `embeddings/glossary.py` ne sont pas fournis. **Le DRY RUN V52 a été exécuté avec succès ; la génération complète V52 reste à valider.**

## 1. Objectif et périmètre

Veille locale RSS/Atom pour un CTO/CISO d'un **casino physique au Luxembourg** : cybersécurité, vulnérabilités exploitées, continuité, risques, fraude, conformité UE/LU, IA appliquée, automatisation et transformation numérique. Les jeux vidéo et casinos en ligne ne sont pas des priorités.

## 2. Architecture fonctionnelle

```text
sources.yaml (catalogue de flux RSS/Atom)
    ↓
Collecteur RSS/Atom (feedparser, urllib) + normalisation (app.normalizer)
    ↓
PostgreSQL → vue unique_articles (score historique)
    ↓
briefing.py : filtres + watchlists + détection CVE/CVSS
    ↓
MiniLM multilingue : classification et regroupement d'événements
    ↓
Présélection diversifiée et conservation des signaux P1
    ↓
Ollama / Qwen2.5:3b : évaluation métier et importance (JSON)
    ↓
Sélection avec quotas souples
    ↓
Ollama / Qwen2.5:3b : titre et résumé anglais (JSON)
    ↓
MarianMT + glossaire : traduction anglais → français
    ↓
Markdown : BRIEFING_OUTPUT
```

Le module de briefing lit PostgreSQL mais n'y écrit **aucun** score, résultat ou résumé. Il ne collecte pas directement les flux RSS. Le code de collecte a été fourni ; aucun ordonnanceur n’est défini dans le Compose communiqué ni dans la fonction `main()` du collecteur.

## 3. Modèles et bibliothèques

| Modèle | Référence | Fonction | Configuration effective |
|---|---|---|---|
| MiniLM multilingue | `sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2` utilisé lors des tests ; en V52 la valeur réelle vient de `embeddings.worker.MODEL_NAME` | Encodage, classification, déduplication | CPU, embeddings normalisés, `batch_size=16`, seuil `worker.THRESHOLD` |
| Qwen | `qwen2.5:3b` | Note métier, importance, catégorie et synthèse | Ollama `/api/chat`, JSON, `temperature=0`, `num_ctx=3072`, `num_predict=160` (tri) ou `320` (synthèse) |
| MarianMT | `Helsinki-NLP/opus-mt-en-fr` | Traduction anglais→français | PyTorch CPU, 4 threads, `num_beams=4`, `max_new_tokens=180` |

Bibliothèques visibles : `psycopg`, `PyYAML`, `sentence-transformers`, `torch`, `transformers`, bibliothèque standard `urllib.request`. Le glossaire repose sur `CyberGlossary` (`embeddings.glossary`) ; son contenu n'a pas été fourni. Les modèles tournent localement après installation/téléchargement initial. Environnement cible de développement : VM Linux, 4 vCPU, 8 Go RAM, sans GPU. Qwen est conservé en mémoire `5m` pour le triage et libéré (`keep_alive=0`) à la fin des appels de synthèse avant MarianMT.

## 4. Fichiers

```text
/opt/ai-news/
├── config/
│   ├── briefing.yaml             # paramètres de génération et sélection
│   ├── semantic_scoring.yaml     # profils descriptifs MiniLM
│   ├── watchlists.yaml           # listes d'inclusion/exclusion
│   ├── glossary.yaml             # protection terminologique (non fourni)
│   └── sources.yaml              # catalogue RSS (chemin réel à confirmer)
└── embeddings/
    ├── briefing.py              # orchestration V52
    ├── watchlist.py             # moteur des règles
    ├── vulnerability_detector.py # CVE/CVSS/exploitation
    ├── worker.py                # MODEL_NAME, THRESHOLD, extract_cves (non fourni)
    └── glossary.py              # CyberGlossary (non fourni)
```

Les chemins `/config/` sont vus depuis le conteneur `embeddings` : le Compose monte `./config:/config:ro`. Le collecteur utilise `/app/config/sources.yaml` ; vérifier que le Dockerfile du collecteur copie bien ce fichier, car il ne dispose d’aucun montage `./config` dans le Compose.

## 4 bis. Infrastructure Docker Compose (configuration fournie)

Le déploiement utilise **Docker Compose**, un réseau Docker Compose implicite (aucun `networks:` personnalisé), trois volumes nommés et quatre services. Les services ne sont pas tous démarrés par défaut : `embeddings` et `ollama` sont associés au profil `ai`.

| Service | Image / build | Rôle | Persistance | Contraintes et démarrage |
|---|---|---|---|---|
| `postgres` (`ai-news-postgres`) | `postgres:17` | Stockage des sources, articles et historiques de collecte | `postgres_data:/var/lib/postgresql/data` | `restart: unless-stopped` ; contrôle `pg_isready` toutes les 10 s, délai 5 s, 5 tentatives ; port hôte `${POSTGRES_PORT}:5432` |
| `collector` | Build depuis `.` (Dockerfile racine par défaut) | Collecte RSS et normalisation | Base PostgreSQL uniquement | Attend PostgreSQL sain ; aucun `restart` ni planification déclarés |
| `embeddings` | Build depuis `embeddings/Dockerfile` | MiniLM, évaluation métier, synthèse, traduction | `embeddings_models:/models`, montage `./config:/config:ro` | Profil `ai`, `mem_limit: 6g`, attend PostgreSQL sain |
| `ollama` (`ai-news-ollama`) | `ollama/ollama:latest` | Serveur local Qwen | `ollama_models:/root/.ollama` | Profil `ai`, `mem_limit: 4g`, `restart: unless-stopped` |

**Attention à la RAM :** les limites de 6 Go (`embeddings`) et 4 Go (`ollama`) représentent des plafonds *par conteneur*, et non une réservation ni une limite globale de 8 Go. Sur la VM de 8 Go, l'exécution simultanée peut provoquer une pression mémoire ou un OOM, en particulier lors de l'évaluation Qwen. La séquence V52 charge MiniLM, puis appelle Qwen et charge MarianMT après les synthèses. Surveiller `docker stats` et les logs du noyau.

### Variables réellement définies dans Compose

| Service | Variable | Valeur fournie / effet |
|---|---|---|
| `postgres` | `POSTGRES_DB`, `POSTGRES_USER`, `POSTGRES_PASSWORD` | Valeurs depuis `.env` ; initialisation PostgreSQL |
| `postgres` | `POSTGRES_PORT` | Port publié sur l'hôte ; nécessaire uniquement si accès externe à Docker |
| `collector` | `DATABASE_URL` | `postgresql://${POSTGRES_USER}:${POSTGRES_PASSWORD}@postgres:5432/${POSTGRES_DB}` |
| `collector` | `SOURCES_FILE` | `/app/config/sources.yaml` |
| `embeddings` | `DATABASE_URL` | Même DSN interne PostgreSQL |
| `embeddings` | `HF_HOME` | `/models` (cache Hugging Face persistant) |
| `embeddings` | `OMP_NUM_THREADS` | `4` |
| `embeddings` | `BRIEFING_CONFIG` | `/config/briefing.yaml` |
| `ollama` | `OLLAMA_NUM_THREADS` | `4` ; effet exact dépend de la version Ollama, à vérifier |
| `ollama` | `OLLAMA_NUM_PARALLEL` | `1` |
| `ollama` | `OLLAMA_MAX_LOADED_MODELS` | `1` |
| `ollama` | `OLLAMA_KEEP_ALIVE` | `0` (mais V52 transmet `keep_alive` par requête) |
| `ollama` | `DATABASE_URL`, `HF_HOME`, `OMP_NUM_THREADS`, `OLLAMA_URL`, `OLLAMA_MODEL` | Variables présentes dans le service, mais **aucune utilisation n'est démontrée** par l'image standard `ollama/ollama` ; `OLLAMA_MODEL` ne télécharge pas automatiquement le modèle |

Le code du briefing lit `OLLAMA_URL` depuis son propre environnement, avec la valeur par défaut `http://ollama:11434`. La variable `OLLAMA_URL` déclarée dans le service `ollama` **n'est pas transmise automatiquement** au service `embeddings`. Le nom de service `ollama` est résolu par le réseau Docker Compose.

### Commandes Compose utiles

```bash
cd /opt/ai-news
# Vérifier les services et le profil IA
sudo docker compose config --services
sudo docker compose --profile ai ps

# Démarrer les services persistants (PostgreSQL et Ollama)
sudo docker compose --profile ai up -d postgres ollama

# Vérifier que Qwen est disponible dans le stockage Ollama
sudo docker compose exec ollama ollama list
# Si absent : téléchargement initial du modèle (connexion Internet nécessaire)
sudo docker compose exec ollama ollama pull qwen2.5:3b

# Collecte manuelle, une exécution puis arrêt
sudo docker compose run --rm collector

# Lire les journaux
sudo docker compose logs --tail=100 postgres ollama
sudo docker stats
```

`docker compose run --rm embeddings ...` et `docker compose run --rm collector` créent des conteneurs ponctuels. Le profil `ai` ne constitue pas un ordonnanceur. Pour le briefing, le conteneur Ollama doit déjà être lancé et le modèle téléchargé. L'exécution du collecteur dépend du contenu du Dockerfile et de son `CMD`/`ENTRYPOINT`, non fournis.

### Points d'exploitation à vérifier

1. **Exposition de PostgreSQL :** `${POSTGRES_PORT}:5432` publie le port sur les interfaces de l'hôte par défaut. Si aucun client externe n'en a besoin, préférer une liaison locale `127.0.0.1:${POSTGRES_PORT}:5432` ou retirer `ports:`.
2. **Version des images :** `postgres:17` est épinglé sur une version majeure ; `ollama/ollama:latest` peut évoluer lors des mises à jour. Épingler une version testée en production.
3. **Persistance des briefings :** aucun volume de sortie n'est déclaré sur `embeddings` ; fournir `-v /opt/ai-news/output:/output` lors d'une exécution complète.
4. **Réseau :** Ollama n'a pas de port hôte publié dans le Compose, ce qui est cohérent avec son utilisation interne.
5. **Secrets :** `.env` contient vraisemblablement des identifiants PostgreSQL ; ne pas le versionner et éviter d'exposer `docker compose config` dans les tickets publics (il peut révéler des valeurs).

## 5. Paramétrage principal : `config/briefing.yaml`

| Clé | Valeur V52 | Effet réel |
|---|---|---|
| `briefing.language` | `fr` | `fr` : traduction ; `en` : texte anglais |
| `briefing.max_events` | `10` | Plafond de publication |
| `briefing.min_relevance_score` | `40` | Seuil sur le score historique ; exceptions whitelist/vulnérabilité |
| `briefing.lookback_hours` | `72` | Fenêtre SQL |
| `briefing.business_triage.max_candidates` | `16` | Taille cible du lot Qwen, **non plafond strict** (P1 et couverture thématique peuvent le dépasser) |
| `briefing.business_triage.min_business_relevance` | `2` | Minimum métier Qwen sur 5 pour article ordinaire |
| `briefing.selection.security.target` | `4` | Quota souple cybersécurité |
| `briefing.selection.artificial_intelligence.target` | `3` | Quota souple IA |
| `briefing.selection.regulation_and_risk.target` | `2` | Quota souple risques/réglementation |
| `briefing.selection.gaming_and_transformation.target` | `1` | Quota souple transformation/casino |
| `briefing.selection.flexible_quotas` | `true` | Redistribue les places inutilisées |
| `briefing.selection.critical_security_override` | `true` | **Clé présente mais non lue en V52** ; les P1 sont toujours prioritaires |
| `llm.model` | `qwen2.5:3b` | Modèle Ollama |
| `llm.temperature` | `0` | Température LLM |
| `scoring.rescore_all` | `true` | **Non exploité directement par `briefing.py` V52** |
| `scoring.domains.*.weight` | 25/25/20/15/10/5 | **Non appliqués au classement V52** |
| `scoring.business_context` | `LU`, `casino`, priorités | Valeurs présentes mais **non injectées dynamiquement** dans le prompt V52 |
| `scoring.online_gaming_priority` | `low` | **Non lu directement** ; la faible priorité est codée dans le prompt |

Le prompt d'évaluation métier est actuellement écrit dans `business_assessments()` ; modifier `scoring.business_context` ne le change pas.

### Variables d'environnement

| Variable | Défaut | Usage |
|---|---|---|
| `DATABASE_URL` | obligatoire | Connexion PostgreSQL ; **secret à protéger** |
| `OLLAMA_URL` | `http://ollama:11434` | Endpoint Ollama |
| `BRIEFING_CONFIG` | `/config/briefing.yaml` | YAML principal |
| `WATCHLIST_FILE` | `/config/watchlists.yaml` | Règles de veille |
| `SEMANTIC_SCORING_FILE` | `/config/semantic_scoring.yaml` | Descriptions MiniLM |
| `GLOSSARY_CONFIG` | `/config/glossary.yaml` | Glossaire de traduction |
| `BRIEFING_FETCH_LIMIT` | `2000` | Limite SQL de lecture |
| `BRIEFING_DRY_RUN` | `0` | `1` : **appelle Qwen** pour évaluer, mais ne résume/traduit/écrit pas |
| `BRIEFING_OUTPUT` | `/tmp/ai-news-briefing.md` | Chemin de sortie Markdown |

### Profils `config/semantic_scoring.yaml`

Le fichier disponible définit sept domaines : `cybersecurity`, `artificial_intelligence`, `risk_management`, `digital_transformation`, `regulation`, `infrastructure`, `gaming_industry`. Chaque domaine a plusieurs `descriptions` en anglais/français. V52 retient la meilleure similarité avec ces descriptions, puis les ramène aux quatre catégories du briefing :

- `cybersecurity`, `infrastructure` → `security`
- `artificial_intelligence` → `artificial_intelligence`
- `risk_management`, `regulation` → `regulation_and_risk`
- `digital_transformation`, `gaming_industry` → `gaming_and_transformation`

Une vulnérabilité candidate est initialement classée en sécurité ; Qwen peut ensuite reclasser l'événement. Les champs `weight`, `lookback_days`, `test_limit`, `similarity_floor`, `similarity_ceiling` et `batch_size` du YAML sémantique ne sont **pas utilisés** dans le chemin de classification du briefing V52.

### Règles `config/watchlists.yaml`

```yaml
whitelist:
  - name: Technologies suivies
    keywords: [Elasticsearch, Elastic Security, VMware, Microsoft 365]
    scope: title_summary
    mode: literal
    operator: any
blacklist:
  - name: Jeux vidéo grand public
    keywords: [Steam Deck, PlayStation, Xbox]
    scope: title_summary
    mode: literal
    operator: any
    absolute: false
```

- `scope`: `title`, `title_summary` (défaut), `all` (inclut le contenu si transmis ; `briefing.py` ne transmet que titre/résumé).
- `mode`: `literal` (sous-chaîne, insensible à la casse), `regex` (expression régulière).
- `operator`: `any` (défaut) ou `all`.
- `absolute: true` sur blacklist : exclusion sans exception, **y compris vulnérabilité critique**.
- Une blacklist ordinaire peut être contournée par une whitelist ou une vulnérabilité candidate. La whitelist garantit l'accès au triage, **pas la publication**.

## 5 bis. Collecteur RSS/Atom : fonctionnement confirmé dans le code

Le collecteur utilise `feedparser`, `urllib.request`, `psycopg`, `PyYAML`, `hashlib`, et la fonction interne `app.normalizer.normalize_text`. Son entrée est `SOURCES_FILE` (par défaut `/app/config/sources.yaml`), qui contient une liste `sources`. Le collecteur est un **programme à exécution unique** : chaque appel parcourt toutes les sources activées, puis normalise les articles encore en attente. Il n'intègre ni boucle de polling ni temporisation périodique.

### Étapes de collecte

1. Lire `sources.yaml` ; ignorer les sources avec `enabled: false` (ou une valeur fausse). `enabled` vaut `true` par défaut si le champ manque.
2. Pour chaque source, enregistrer ou mettre à jour son nom dans `sources` avec `ON CONFLICT (feed_url) DO UPDATE`, puis créer une entrée `collection_runs`.
3. Effectuer une requête HTTP(S) avec `urllib.request.urlopen(..., timeout=15)`, un `User-Agent` de navigateur et des en-têtes `Accept` RSS/Atom/XML. Le téléchargement est limité à **10 Mio** (`10 * 1024 * 1024` octets).
4. Parser le flux avec `feedparser.parse`. Si `feed.bozo` est vrai et qu'aucune entrée n'est disponible, lever une erreur ; si des entrées sont présentes, traiter le flux et enregistrer le statut `partial`.
5. Pour chaque entrée, exiger `link` et `title`. Prendre `entry.id` comme GUID, sinon l'URL ; récupérer `summary`, le premier `content[0].value` disponible, l'auteur et la date de publication.
6. Calculer `content_hash = SHA-256(title + "\n" + summary + "\n" + content)` ; insérer l'article avec `ON CONFLICT (source_id, guid) DO NOTHING`. Le hash est **stocké**, mais **n'est pas utilisé pour dédupliquer lors de l'insertion** dans ce code.
7. Enregistrer `sources.last_fetched_at` et finaliser `collection_runs` avec `status`, `articles_found`, `articles_inserted`, `finished_at`.
8. Après toutes les sources, sélectionner les articles dont `normalized_content` ou `normalized_summary` est `NULL`, appliquer `normalize_text` aux deux champs et enregistrer les mises à jour.

### Gestion des dates et des erreurs

- Date : tenter `published` puis `updated` avec `email.utils.parsedate_to_datetime`; sinon `published_parsed` puis `updated_parsed`; sans date exploitable, `published_at = NULL`.
- Les dates sans fuseau explicite sont interprétées en UTC.
- Une erreur sur un flux entraîne un `rollback`, une entrée `collection_runs` avec `status='failed'` et `error_message` tronqué à 4 000 caractères, puis le passage au flux suivant.
- La normalisation intervient **après** la boucle des flux ; une erreur de normalisation peut interrompre l'exécution globale.
- Les transactions sont validées après chaque source. Les logs indiquent le nombre d'articles trouvés et nouveaux.

### Champs de données observés

| Table / vue | Champs utilisés par le code fourni | Utilisation |
|---|---|---|
| `sources` | `id`, `name`, `feed_url`, `last_fetched_at` | Catalogue des flux et dernier passage |
| `collection_runs` | `id`, `source_id`, `finished_at`, `status`, `articles_found`, `articles_inserted`, `error_message` | Historique de collecte et incidents |
| `articles` | `id`, `source_id`, `guid`, `url`, `title`, `author`, `summary`, `content`, `published_at`, `content_hash`, `normalized_content`, `normalized_summary` | Articles bruts et normalisés |
| `unique_articles` | `id`, `title`, `normalized_summary`, `relevance_score`, `published_at`, URL détectée | Lecture pour le briefing ; définition SQL non fournie |

**Hypothèses de schéma :** le code attend une contrainte d'unicité sur `sources.feed_url` et sur `(articles.source_id, articles.guid)`. Sans le DDL SQL, on ne peut pas confirmer leur création. Les champs `category` du fichier `sources.yaml` ne sont pas insérés en base par ce collecteur. Il n'effectue pas non plus de classification, de calcul de `relevance_score`, de mise à jour des articles déjà présents, ni de suppression des anciens articles.

### Points de vigilance sur les flux

- Une URL de flux obsolète, redirigée ou indisponible peut échouer ; vérifier périodiquement `collection_runs`.
- Les flux ne fournissant pas de date exploitable peuvent produire des articles avec `published_at = NULL`, que la requête de briefing V52 n'inclut pas (`WHERE published_at BETWEEN ...`).
- Un GUID stable empêche la réinsertion, même si l'éditeur modifie le titre ou le contenu d'un article déjà collecté : `ON CONFLICT ... DO NOTHING` ne met pas à jour les articles existants.
- La normalisation de **tous** les articles en attente est exécutée à chaque collecte, sans limite de lot ; elle peut être coûteuse si la file d'attente grossit.
- `urlparse` est importé mais non utilisé dans l'extrait fourni ; aucune validation explicite du schéma d'URL des flux n'est visible. Éviter de charger des sources non fiables sans contrôle, notamment pour prévenir les accès réseau internes non souhaités.
- Le collecteur ne filtre pas les articles selon leur catégorie : les règles de pertinence et de sécurité s'appliquent plus tard dans le pipeline.

### Requêtes de diagnostic SQL

```sql
-- État des dernières collectes par flux
SELECT s.name, s.feed_url, s.last_fetched_at,
       r.status, r.finished_at, r.articles_found,
       r.articles_inserted, r.error_message
FROM sources AS s
LEFT JOIN LATERAL (
    SELECT * FROM collection_runs AS cr
    WHERE cr.source_id = s.id
    ORDER BY cr.id DESC LIMIT 1
) AS r ON TRUE
ORDER BY s.name;

-- Articles restant à normaliser
SELECT COUNT(*) AS pending_normalization
FROM articles
WHERE normalized_content IS NULL OR normalized_summary IS NULL;

-- Distribution des statuts de collecte
SELECT status, COUNT(*) FROM collection_runs GROUP BY status ORDER BY status;
```

## 6. Pipeline détaillé

1. **SQL** : lecture de `unique_articles` (`id`, `title`, `normalized_summary`, `relevance_score`, `published_at` et colonne URL autodétectée), sur les dernières `lookback_hours` heures, tri par date descendante, limite `BRIEFING_FETCH_LIMIT`.
2. **Filtrage** : titre, blacklist absolue/ordinaire, préfixes de newsletters, résumé, score historique et exceptions de whitelist/vulnérabilité.
3. **Détection** : regex CVE, CVSS, exploitation active, vulnérabilités et correctifs. `P1_review` = exploitation mentionnée ; `P2_review` = critique/CVSS ≥9 ; `P3_review` = autre candidat ; `none` = pas de signal. **Ce sont des alertes textuelles provisoires.**
4. **Boost initial** : score max entre l'historique et P1=95, P2=85, P3=65, whitelist=75.
5. **MiniLM** : vecteurs des descriptions et des articles (titre + 650 premiers caractères du résumé), classification et similarité interarticles.
6. **Déduplication** : identifiants CVE ou ZDI-CAN incompatibles empêchent une fusion ; même URL canonique ou titre normalisé peut fusionner ; sinon forte similarité (`>= max(0.93, THRESHOLD)`) ET recouvrement des termes distinctifs du titre. Chaque nouvel élément doit être compatible avec tout le groupe.
7. **Présélection** : événements P1, puis candidats de chaque catégorie, puis compléments. `max_candidates` peut être dépassé.
8. **Qwen métier** : JSON `business_relevance` (0–5), `news_importance` (0–5), `category`, `reason`. Classement secondaire `12 × business_relevance + 8 × news_importance`. En cas d'erreur, notes 0 ; P1 maintenus.
9. **Sélection** : P1 en premier, puis quotas thématiques, puis redistribution si activée ; au plus `max_events` événements. Une sélection de moins de dix événements est possible.
10. **Résumé** : Qwen reçoit jusqu'à trois articles du même groupe ; JSON `title` et `summary` anglais. Consignes de factualité (ne pas inventer de CVE, dates, impacts ou auteurs).
11. **Traduction** : MarianMT anglais→français ; le glossaire protège les termes techniques ; perte d'un marqueur = événement non publié.
12. **Markdown** : horodatage UTC, nombre d'articles et événements, titres, résumés, score **historique/boosté** (pas la note Qwen), liens des sources.

### Limites et vigilance

- Qwen2.5:3b peut halluciner ; toujours recouper les CVE, CVSS, dates, impacts et obligations réglementaires.
- Le texte des flux RSS est non fiable et peut contenir des tentatives de *prompt injection*.
- Le regroupement est volontairement conservateur ; quelques doublons peuvent subsister.
- Aucun cache des évaluations Qwen : elles sont répétées à chaque exécution.
- La politique P1 est prioritaire même si la pertinence métier est faible ; elle ne confirme pas une exploitation réelle.
- Les poids `scoring.domains` ne modifient pas directement les scores V52.

## 7. Catalogue RSS/Atom

Le fichier `sources.yaml` fourni provient d'un export Feedly OPML. **63 flux, tous `enabled: true` dans le fichier de référence**, dont **46 classés `Securité`**. Les URL sont recopiées telles que fournies et **n'ont pas été testées**. L'appartenance au catalogue ne prouve pas que le collecteur actif lit actuellement ce fichier ni que le flux répond.

| Catégorie source | Nombre |
|---|---:|


| Securité | 46 |
| Actu societé IT | 6 |
| Divers | 3 |
| Informatique | 2 |
| Web-2.0 | 2 |
| Linux | 1 |
| Php | 1 |
| Technologies | 1 |
| Cloud | 1 |


### Inventaire intégral des sources

| # | Source | Catégorie | URL | Activé |
|---:|---|---|---|---|


| 1 | Schneier on Security | Securité | `http://www.schneier.com/blog/index.rdf` | oui |


| 2 | Darknet – Hacking Tools, Hacker News & Cyber Security | Securité | `http://feeds.feedburner.com/darknethackers` | oui |


| 3 | Deriving Cyber Threat Intelligence and Driving Threat Hunting | Securité | `http://malwarenailed.blogspot.com/feeds/posts/default?alt=rss` | oui |


| 4 | Microsoft Admin Center Status | Securité | `https://status.office365.com/api/feed/mac` | oui |


| 5 | Sécurité informatique : Toute l’actualité sur Le Monde.fr. | Securité | `http://www.lemonde.fr/rss/tag/securite-informatique.xml` | oui |


| 6 | MONARC | Securité | `https://www.monarc.lu/feeds/all.atom.xml` | oui |


| 7 | Dr. Fu's Security Blog | Securité | `http://fumalwareanalysis.blogspot.com/feeds/posts/default` | oui |


| 8 | ZDI: Published Advisories | Securité | `https://www.zerodayinitiative.com/rss/published/` | oui |


| 9 | /dev/random | Securité | `http://feeds.feedburner.com/dev/rand` | oui |


| 10 | ZATAZ.COM | Securité | `https://www.zataz.com/rss/zataz-news.rss` | oui |


| 11 | UnderNews | Securité | `http://feeds.feedburner.com/undernews/oCmA` | oui |


| 12 | Azure Status | Securité | `https://azure.status.microsoft/status/feed/` | oui |


| 13 | Varonis Blog | Securité | `http://feeds.feedburner.com/metadata-era-varonis` | oui |


| 14 | Trend Micro Research, News, Perspectives | Securité | `http://feeds.trendmicro.com/Anti-MalwareBlog/` | oui |


| 15 | ClearSky Cyber Security | Securité | `http://www.clearskysec.com/feed/` | oui |


| 16 | SCRT Team Blog | Securité | `http://blog.scrt.ch/feed/` | oui |


| 17 | Krebs on Security | Securité | `http://krebsonsecurity.com/feed/` | oui |


| 18 | BleepingComputer | Securité | `http://www.bleepingcomputer.com/feed/` | oui |


| 19 | darkreading | Securité | `https://www.darkreading.com/rss_simple.asp` | oui |


| 20 | Blog RSS Feed | Securité | `http://feeds.feedburner.com/tripwire-state-of-security` | oui |


| 21 | Clusif | Securité | `https://clusif.fr/feed/` | oui |


| 22 | ZDI: Upcoming Advisories | Securité | `https://www.zerodayinitiative.com/rss/upcoming/` | oui |


| 23 | Securelist | Securité | `https://securelist.com/feed/` | oui |


| 24 | Actualités securite | Securité | `http://www.lemondeinformatique.fr/flux-rss/thematique/securite/rss.xml` | oui |


| 25 | Sophos Blogs | Securité | `http://blogs.sophos.com/feed/` | oui |


| 26 | Control Manager RSS | Securité | `http://downloadcenter.trendmicro.com/pull/feed/product/7?region=NABU` | oui |


| 27 | MISP Open Source Threat Intelligence Platform & Open Standards For Threat Intelligence Sharing | Securité | `http://www.misp-project.org/feed.xml` | oui |


| 28 | Troy Hunt's Blog | Securité | `http://feeds.feedburner.com/TroyHunt` | oui |


| 29 | CIRCL updates | Securité | `https://www.circl.lu/rss.xml` | oui |


| 30 | Microsoft Security Blog | Securité | `https://www.microsoft.com/security/blog/feed/` | oui |


| 31 | Latest from TechRadar | Securité | `https://www.techradar.com/rss` | oui |


| 32 | Security Labs – Qualys Blog | Securité | `https://community.qualys.com/blogs/securitylabs/feeds/posts` | oui |


| 33 | Information Security | Securité | `http://www.information-security.fr/feed/` | oui |


| 34 | The Hacker News | Securité | `http://thehackernews.com/feeds/posts/default` | oui |


| 35 | CERT-FR | Securité | `http://www.cert.ssi.gouv.fr/site/cert-fr_alerte.rss` | oui |


| 36 | RSS - Actualités CNIL | Securité | `http://www.cnil.fr/rss-cnil/` | oui |


| 37 | dirkjanm.io | Securité | `https://dirkjanm.io/feed.xml` | oui |


| 38 | Actualités du SSTIC | Securité | `http://www.sstic.org/feeds/latest/` | oui |


| 39 | CISA Cybersecurity Advisories | Securité | `https://www.us-cert.gov/ncas/alerts.xml` | oui |


| 40 | Zero Day Initiative - Blog | Securité | `https://www.zerodayinitiative.com/blog/?format=rss` | oui |


| 41 | Blaze's Security Blog | Securité | `http://bartblaze.blogspot.com/feeds/posts/default` | oui |


| 42 | Veille Cyber N59 – 11 janvier 2016 | Securité | `https://veillecyberland.wordpress.com/feed/` | oui |


| 43 | DATA SECURITY BREACH | Securité | `http://datasecuritybreach.fr/feed/` | oui |


| 44 | 360 Netlab Blog - Network Security Research Lab at 360 | Securité | `http://blog.netlab.360.com/rss/` | oui |


| 45 | NoLimitSecu | Securité | `http://www.nolimitsecu.fr/feed/` | oui |


| 46 | Have I Been Pwned latest breaches | Securité | `http://feeds.feedburner.com/HaveIBeenPwnedLatestBreaches` | oui |


| 47 | Linux Journal - The Original Magazine of the Linux Community | Linux | `http://www.linuxjournal.com/node/feed` | oui |


| 48 | :: onpk :: blog PHP, MySQL, développement web & connexe(s) | Php | `http://www.onpk.net/rss.xml` | oui |


| 49 | Slashdot: Linux | Informatique | `http://rss.slashdot.org/Slashdot/slashdotLinux` | oui |


| 50 | Tech2Tech \| News, Astuces, Tutos, Vidéos autour de l'informatique | Informatique | `http://www.tech2tech.fr/feed/` | oui |


| 51 | Announcements \| Microsoft Azure Blog \| Microsoft Azure | Actu societé IT | `http://azure.microsoft.com/blog/topics/announcements/feed/` | oui |


| 52 | Microsoft Azure Blog > Mark Russinovich | Actu societé IT | `https://azure.microsoft.com/en-us/blog/author/markruss/feed/` | oui |


| 53 | Channelnews.fr - l'actualité du business de l'informatique et des télécoms | Actu societé IT | `http://www.channelnews.fr/index.php?format=feed&type=rss` | oui |


| 54 | Azure Status | Actu societé IT | `https://azurestatuscdn.azureedge.net/en-us/status/feed/` | oui |


| 55 | Les news de Korben | Actu societé IT | `http://feeds.feedburner.com/KorbensBlog-UpgradeYourMind` | oui |


| 56 | Announcements \| Microsoft Azure Blog \| Microsoft Azure | Actu societé IT | `https://azure.microsoft.com/en-us/blog/topics/updates/feed/` | oui |


| 57 | Global Security Mag Online | Technologies | `http://www.globalsecuritymag.fr/spip.php?page=backend` | oui |


| 58 | AccessOWeb | Web-2.0 | `http://feeds.feedburner.com/accessoweb/XGSC` | oui |


| 59 | FredCavazza.net | Web-2.0 | `http://www.fredcavazza.net/feed/` | oui |


| 60 | Cybernetica | Cloud | `https://www.codesouverain.fr/rss/` | oui |


| 61 | Silicon | Divers | `http://www.silicon.fr/feed` | oui |


| 62 | Le Journal du Geek | Divers | `http://feeds2.feedburner.com/LeJournalduGeek` | oui |


| 63 | LesCasinos.org - Actualités | Divers | `http://www.lescasinos.org/rss/flux.xml` | oui |


### Ajouter ou désactiver un flux

```yaml
sources:
  - name: Exemple
    url: https://example.org/rss
    category: Securité
    enabled: true
```

Les champs observés sont `name`, `url`, `category`, `enabled`. Le collecteur fourni lit `SOURCES_FILE=/app/config/sources.yaml`, parcourt `sources` et ignore les entrées avec `enabled: false`. **Attention :** le Compose ne monte pas le répertoire `./config` dans `collector` ; les modifications sur l’hôte nécessitent potentiellement une reconstruction de son image, selon le Dockerfile non fourni. Les catégories sources ne sont pas les catégories métier MiniLM/Qwen.

## 8. Installation, exécution et diagnostics

### Contrôles de l'environnement

```bash
cd /opt/ai-news
sudo docker compose config
sudo docker compose ps
sudo docker compose run --rm embeddings python -m py_compile embeddings/briefing.py
```

### Mise à jour

```bash
cd /opt/ai-news
cp embeddings/briefing.py embeddings/briefing.py.backup
cp config/briefing.yaml config/briefing.yaml.backup
# Copier depuis l'archive V52 : embeddings/briefing.py et config/briefing.yaml
sudo docker compose build embeddings
```

Conserver les fichiers `watchlists.yaml` et `semantic_scoring.yaml` déjà personnalisés.

### DRY RUN V52 (Qwen appelé)

```bash
sudo docker compose run --rm \
  -v /opt/ai-news/config:/config:ro \
  -e BRIEFING_DRY_RUN=1 \
  embeddings python -m embeddings.briefing
```

Cette commande lit PostgreSQL, charge MiniLM, appelle Qwen pour les évaluations et affiche la sélection. **Elle ne génère pas de résumé, ne traduit pas et n'écrit pas le Markdown.** La durée peut être de plusieurs minutes sans GPU.

### Génération complète avec fichier persistant

```bash
mkdir -p /opt/ai-news/output
sudo docker compose run --rm \
  -v /opt/ai-news/config:/config:ro \
  -v /opt/ai-news/output:/output \
  -e BRIEFING_OUTPUT=/output/briefing.md \
  embeddings python -m embeddings.briefing
cat /opt/ai-news/output/briefing.md
```

**Important** : sans volume de sortie, `/tmp/ai-news-briefing.md` reste dans le conteneur éphémère et peut disparaître après `run --rm`.

### Tests unitaires rapides

```bash
sudo docker compose run --rm embeddings python - <<'PYTEST'
from embeddings.vulnerability_detector import detect
from embeddings.watchlist import load_rules, evaluate
print(detect('Max severity SonicWall SMA1000 flaw now exploited in attacks'))
print(evaluate('Steam Deck gaming news', '', config=load_rules()))
PYTEST
```

Pour les erreurs : vérifier `DATABASE_URL`, accès à PostgreSQL, résolution `ollama`, modèle installé, montage `/config`, RAM, permissions du répertoire de sortie et disponibilité des fichiers du glossaire. `sacremoses` est recommandé par MarianTokenizer mais son absence a seulement produit un avertissement lors du test V50.

## 9. Sécurité et exploitation

- Ne jamais enregistrer `DATABASE_URL` en clair dans les logs ou le dépôt Git.
- Compte SQL à privilèges minimaux ; le briefing nécessite uniquement des lectures.
- Considérer les résumés RSS comme des entrées externes potentiellement malveillantes.
- Conserver les liens sources et exiger une vérification humaine avant toute décision de remédiation ou de conformité.
- Surveiller les temps d'exécution CPU/RAM et les erreurs Ollama/MarianMT.
- Le Compose réel fourni ne définit **aucune planification** ; le lancement périodique doit être assuré à l’extérieur (cron, systemd timer ou autre orchestrateur).
- La proportion de flux cybersécurité (46/63) biaise l'approvisionnement ; compléter à terme avec des sources officielles LU/UE et casino terrestre si nécessaire.

## 10. État des validations et pistes d'amélioration

| Contrôle | État |
|---|---|
| Détection vulnérabilités et watchlists | Tests V50 validés |
| Génération de bout en bout V50 | Exécutée (~212 s, 5 événements) |
| DRY RUN V51 | Exécuté, newsletter exclue après correction regex |
| V52 syntaxe Python | Vérifiée à la préparation |
| V52 DRY RUN avec évaluation Qwen | **Exécuté** : 97 articles, 94 événements, 16 candidats Qwen, 9 sélectionnés |
| V52 génération complète | À exécuter |
| Disponibilité des 63 flux | Non vérifiée |
| Compose et collecteur | **Fournis et documentés** ; pas de validation indépendante du déploiement |
| Schéma SQL complet, Dockerfiles, ordonnanceur | Non fournis ; aucun ordonnanceur défini dans le Compose |

**Améliorations envisagées :** cache des notes Qwen, traçabilité du triage, prompts et contexte métier réellement pilotés par YAML, tests de non-régression de la déduplication, alertes P1 hors quota éditorial, stockage persistant et planification documentée.

---

Documentation V52 — 10 octobre 2026. Le fichier `sources.reference.yaml` fourni avec l'archive est une **copie de référence** de l'export, pas une nouvelle configuration déployée automatiquement.


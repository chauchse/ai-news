# AGENTS.md — AI News / règles pour Codex

> Source fonctionnelle : README.md du dépôt `chauchse/ai-news`, branche `main`, consulté le 13 octobre 2026. Ce document est une politique de contribution, pas une preuve que le code local correspond au README. Toujours examiner le dépôt ouvert dans VS Code avant d'agir.

## 0. Mandat impératif

- **Zéro régression** : préserver intégralement le comportement existant, les données, les interfaces, les formats de sortie et les mécanismes de reprise. Une optimisation ne doit pas changer le résultat fonctionnel sans demande explicite.
- **L'utilisateur réalise tous les tests.** Codex ne lance **aucun test**, build, commande de pipeline, appel réseau, migration, publication, envoi de mail ou opération qui écrit en base / dans les sorties. Il fournit les commandes exactes et les résultats attendus pour que l'utilisateur les exécute lui-même. Une inspection statique non destructive du code et de Git est permise.
- **Granularité stricte** : une anomalie ou une optimisation cohérente à la fois ; modifications localisées, petites et facilement réversibles. Pas de refonte générale, pas de changement non sollicité.
- **Aucun nouveau module** : ne pas créer de module Python, service Docker, dépendance, composant web, modèle, sous-système ou infrastructure. Optimiser les fichiers et fonctions existants. Un nouveau fichier de test, outil ou script n'est pas autorisé sans accord explicite.
- **Pas de changements silencieux** : annoncer les fichiers touchés, la justification, les risques et les validations manuelles avant d'élargir le périmètre. En cas d'incertitude, demander.
- **Ne pas modifier automatiquement** `README.md`, `AGENTS.md`, les fichiers `.env`, les secrets, les modèles, les fichiers générés, les historiques ou les données persistantes.
- Ne jamais présenter un test comme « réussi » si seul un résultat attendu a été rédigé.

## 1. Comprendre le projet avant de modifier

AI News est une veille RSS/Atom IA, IT, cybersécurité et risques, avec contexte métier de **casino physique au Luxembourg**. Exécution principalement **on-premise** ; publication optionnelle via SMTP et SFTP vers un hébergement web. Livrables : briefing Markdown, versions FR/EN, MP3 et flux RSS de podcast. Une boucle de feedback web/PHP et d'apprentissage local est préparée mais **l'import automatique et la personnalisation sont désactivés** selon le README consulté.

Chaîne actuelle documentée :

`config/sources.yaml → collecte/normalisation → PostgreSQL → scoring historique → vue unique_articles → filtres/watchlists/CVE → MiniLM (regroupement) → Qwen 2.5:3b via Ollama (déduplication, évaluation, sélection, rédaction EN) → MarianMT + glossaire (FR) → Markdown → [mail SMTP] + [Kokoro MP3 → podcast SFTP]`.

Feedback distinct : `liens dans le mail → PHP public /feedback/ → JSON privés → pull authentifié → PostgreSQL → personnalisation/apprentissage local → sélections futures` ; vérifier le statut réel dans le code/configuration, ne pas activer spontanément.

**Vérifications statiques préalables** (sans exécution fonctionnelle) : lire `README.md`, `docs/feedback.md` si concerné, `docker-compose.yml` ou équivalent, `run-daily.sh`, les configurations touchées et les implémentations associées ; regarder `git status --short` et `git diff`. Identifier les contrats de données, dépendances, effets de bord, appels externes et chemins montés. Ne pas présumer des noms de fonctions ou de champs sans lecture.

## 2. Contrats de compatibilité à préserver

### 2.1 Sources et collecte
- `config/sources.yaml` est le catalogue de référence dans la documentation historique ; vérifier si les sources ont été centralisées dans `config.yaml`. Les entrées utilisent la clé **`url`**, pas `feed_url` ; vérifier le schéma exact dans le code. Respecter `enabled`.
- Le collecteur lit les sources activées, normalise le HTML et conserve l'historique `collection_runs` ; une erreur de flux ne doit pas interrompre les autres flux.
- Comportement documenté à ne **pas changer implicitement** : une source `enabled: false` entraîne la suppression de ses articles en base au début de la collecte.
- `source_health.auto_disable: true` et `consecutive_failures: 3` : après trois erreurs de flux consécutives, désactivation YAML + base, purge des articles ; un flux valide sans articles n'est pas un échec ; les erreurs SQL/internes ne doivent pas compter. Le YAML est écrit sous verrou, remplacement atomique et sauvegarde `.bak` ; si l'écriture échoue, pas de purge automatique.
- Une modification du catalogue monté depuis l'hôte doit être prise en compte à la prochaine collecte ; une modification du code peut nécessiter `docker compose build collector`.
- Un article existant avec même source et même GUID n'est pas réécrit.

### 2.2 Scoring, sélection, qualité éditoriale
- Le scorer utilisait selon la documentation `config/scoring.yaml` et le bonus métier de `config/briefing.yaml` ; vérifier leur centralisation effective dans `config.yaml` ; `rescore_all` contrôle le recalcul total, sinon seuls les scores absents/anciens (>12 h) sont repris.
- La vue `unique_articles` déduplique par GUID, pas par événement ; préserver sa sémantique.
- Paramètres décrits dans le README : fenêtre **24 h**, score minimum **35**, plafond **15 événements**, cible **50 candidats Qwen**, jusqu'à **8 arbitrages** de déduplication ; pertinence technique min. **2/5**, importance min. **2** ; pertinence métier utilisée pour le classement, sans seuil bloquant. Ces valeurs peuvent évoluer dans le YAML : **lire la configuration locale** avant d'affirmer la valeur effective.
- MiniLM sert à la classification et au regroupement ; `embeddings.worker` est **diagnostique seulement**, sans scoring ni persistance de groupes.
- Qwen via Ollama assure arbitrage de doublons, pertinence, importance, sélection et rédaction EN. Ne pas substituer des heuristiques ou modifier les seuils/quotas sans demande.
- Les P1/P2 sont des **indices textuels**, jamais des certitudes de criticité ; ne pas inventer de CVE/CVSS ni de faits.
- Préserver l'équilibre IA / IT / cybersécurité / réglementation et le contexte métier ; ne pas inventer de quotas stricts nouveaux.
- Une sélection peut produire moins que le maximum configuré ; ne pas compléter avec du contenu médiocre ou ancien juste pour atteindre le plafond.
- La déduplication doit comparer **les événements**, pas seulement des titres ou un thème ; plusieurs sources pour le même événement ne doivent pas créer plusieurs sujets, et une évolution substantielle ne doit pas être supprimée.
- Historique inter-journalier : comparer les briefings des **7 jours précédents** (configurable par `briefing.history.days`, 0 pour désactiver) et présélection MiniLM `similarity_threshold: 0.72` selon le README. Comparer EN ou, à défaut, FR ; ne pas comparer deux éditions d'une même journée ni le briefing du jour. Ne pas créer d'historique JSON additionnel. Les erreurs de lecture/arbitrage doivent arrêter la génération plutôt que désactiver silencieusement le contrôle.
- Protéger les noms propres, sigles, termes techniques et liens. Conserver les titres/noms de sources dans leur langue d'origine, produire une synthèse naturelle et fidèle ; le français est obtenu par MarianMT et glossaire à partir de la synthèse EN. Une erreur de traduction bloque la génération, plutôt que publier une version FR incomplète.

### 2.3 Bilinguisme et sorties
- `briefing.languages: [en, fr]` : une seule sélection et synthèse EN, puis traduction FR des **mêmes événements**. Ne pas diverger les sujets ou casser les éditions.
- Sorties quotidiennes : `output/daily/AAAA-MM-JJ/{en,fr}/` ; Markdown, aperçu mail HTML, MP3, XML et marqueurs `mail.done`, `audio.done`, `podcast.done`. Préparer les deux Markdown avant les envois ; ordre du pipeline : opérations EN avant FR.
- Préserver les marqueurs de reprise et la sélection du jour. Ne pas supprimer `output/daily/` pour relancer un test : cela peut **renvoyer les mails** et republier.
- Date fixée au début du pipeline en `Europe/Paris` même si l'exécution franchit minuit. Les anciens fichiers peuvent être copiés vers la nouvelle arborescence sans suppression des originaux.
- Sans `BRIEFING_OUTPUT`, le chemin de sortie ponctuel est `/tmp/ai-news-briefing.md` et n'est pas persistant ; pour une sortie persistante utiliser `/output/briefing.md`.

### 2.4 Audio, mail, podcast
- Moteur du pipeline quotidien : **Kokoro** ; les alternatives Piper et XTTS existent mais ne doivent pas être activées automatiquement. Préserver `config/audio.yaml` (voix, pauses, débit), MP3 mono **22 050 Hz**, bitrates permis **24k/32k/40k/48k** et pauses dans les bornes documentées.
- Le mailer produit HTML + texte et utilise SMTP selon `config.yaml` et `.env` (ou l’ancien `config/mail.yaml` si encore utilisé). Un appel sans `--dry-run` **envoie réellement** ; ne jamais le lancer. TLS/certificats ne doivent pas être désactivés.
- Podcast : SFTP avec vérification de la clé `known_hosts`, MP3, couverture, RSS par langue `/en/` et `/fr/`. `PODCAST_SITE_URL` est une base sans suffixe de langue ; chaque édition dispose de son sous-répertoire SFTP et état séparé.
- Nom distant idempotent : `AAAA-MM-JJ-<SHA-256>-briefing.mp3`, également identifiant RSS ; republier le même contenu du même jour ne crée pas de nouvel épisode, un contenu différent peut en créer un. Préserver les dates initiales et `podcast/state/episodes*.json`.
- Publication : transfert MP3 + couverture avant remplacement atomique du RSS ; purge limitée aux anciens MP3 de briefing reconnus (rétention documentée 7 jours). Ne jamais élargir la purge.
- Les descriptions de podcast proviennent des titres/sources du Markdown ; toute modification de Markdown doit préserver le parsing du RSS et la distinction `description`/`itunes:summary`.

### 2.5 Feedback et apprentissage
- Consulter `docs/feedback.md` avant toute intervention. la configuration de feedback (centralisée dans `config.yaml` si la migration est effective) désactive par défaut le dispositif ; ne pas activer import automatique, personnalisation ou entraînement sans demande expresse.
- Front public : PHP sous `/feedback/` ; JSON en zone privée hors racine web ; pull authentifié et acquittement exact après stockage local. Aucun modèle/base distante pour l'apprentissage.
- Préserver les secrets distincts des liens et de l'API, l'anti-double vote, l'idempotence, les permissions, la protection CSRF et la séparation public/privé.
- GET ne doit pas créer de vote par défaut ; POST de confirmation est la sécurité prévue. Le mode expérimental `require_confirmation: false` existe mais peut produire des votes parasites via scanners de liens ; ne pas l'activer ni changer sa valeur implicitement.
- Ne jamais réimporter deux fois le même feedback, acquitter avant stockage, ni entraîner sur des présentations non résolues ou sans caractéristiques valides. Un entraînement supervisé CPU est une opération distincte ; ne jamais lancer ni activer un modèle automatiquement.
- Les liens signés, copies d'emails, journaux de feedback et exports de tests sont privés ; ne pas les exposer dans Git, logs ou réponses.

### 2.6 Exploitation et données
- PostgreSQL et Ollama sont persistants ; les autres conteneurs sont principalement lancés à la demande. Respecter profils Compose `ai`, `mail`, `xtts`.
- Contrainte RAM importante : limites documentées de **6 Go embeddings**, **4 Go Ollama**, **2 Go audio**, susceptibles de dépasser une machine de 8 Go si exécutées ensemble. Ne pas augmenter la concurrence, le contexte LLM, la taille des modèles ou les limites mémoire sans mesures et accord.
- Scripts SQL ordonnés `001_initial_schema.sql` à `005_feedback.sql` ; ne jamais appliquer une migration à la place de l'utilisateur. Pas de changement destructif ou réécriture de données sans accord explicite et plan de sauvegarde.
- `run-daily.sh` orchestre collecte, scoring, diagnostic, briefing, mail, audio et publication ; verrou `/run/ai-news.lock`, reprise quotidienne, journal `/var/log/ai-news.log`. `systemd` oneshot + cron à 04:00 selon fuseau serveur. Ne pas déclencher manuellement le service ni toucher au cron.
- Ne pas modifier `.env`, identifiants, chemins SFTP, clés SSH, modèles téléchargés, `podcast/state/`, `output/daily/` ni les journaux d'exploitation.

## Paramètres et configuration

- **Source de vérité unique** : tout paramètre applicatif configurable doit être déclaré dans le fichier central `config.yaml` du projet et chargé au moyen des mécanismes existants de `project_config.py`. Avant toute intervention, identifier le chemin réel de ce fichier et les fonctions d'accès déjà utilisées ; ne pas inventer une API de configuration parallèle.
- **Pas de valeurs métier codées en dur** : ne pas intégrer directement dans le code métier des listes de produits ou technologies, motifs de reconnaissance, seuils, pondérations, quotas, limites, règles propres à un fournisseur, à un pays ou à un environnement. Réutiliser les clés et les valeurs déjà présentes dans `config.yaml`.
- **Évolution minimale** : si un comportement nécessite un nouveau réglage, enrichir la section pertinente de `config.yaml`, puis adapter la lecture et la validation via `project_config.py` et les consommateurs existants. Préserver les valeurs par défaut et les comportements actuels pour éviter toute régression. Ne pas introduire de nouveau module, chargeur YAML ou fichier de configuration.
- **Privilégier les algorithmes génériques** : ne pas déplacer mécaniquement dans le YAML une accumulation de cas particuliers lorsque la règle peut être exprimée par une logique générique fiable et maintenable. Si une exception métier demeure indispensable, la rendre explicite et configurable.
- **Frontière code / configuration** : les constantes intrinsèques à un algorithme, une norme ou un protocole peuvent rester dans le code. Les décisions fonctionnelles, éditoriales et opérationnelles (délais, modèles, réglages LLM, priorités, seuils, limites, fournisseurs) appartiennent à `config.yaml`.
- **Secrets et environnements** : conserver les secrets et URL de déploiement dans `.env` ou le mécanisme de secrets existant, jamais dans `config.yaml` ni dans le code. Ne pas déplacer les secrets lors d'une optimisation.
- **Compatibilité et migration** : vérifier les éventuelles références historiques à `config/*.yaml` dans le README ou le code. Si le dépôt utilise désormais `config.yaml`, ce dernier fait autorité ; ne pas recréer les anciens fichiers ni modifier automatiquement leurs usages sans preuve. Signaler toute divergence entre documentation et implémentation et demander validation avant de changer un contrat de configuration.
- **Validation par l'utilisateur** : pour toute modification de configuration, fournir les commandes exactes permettant de vérifier le chargement, les valeurs résolues, les valeurs par défaut et la rétrocompatibilité, ainsi que les résultats attendus. Codex n'exécute pas ces tests.

## Rédaction du README

- Le README décrit simplement et factuellement les fonctionnalités présentes, leur configuration, leur utilisation et leurs limites.
- Ne pas y ajouter d'historique de changements, journal de migration, compte rendu d'intervention, résultats datés de tests, décisions de session ou propositions non implémentées.
- Lors d'une évolution, mettre à jour directement la description de la fonctionnalité concernée, sans raconter l'avant/après ni ajouter une note chronologique.
- Vérifier les descriptions dans le code et la configuration. Ne pas présenter une fonctionnalité prévue ou un déploiement non vérifié comme opérationnel.
- Les consignes de contribution appartiennent à `AGENTS.md` ; le README peut y renvoyer.

## 3. Bonnes pratiques d'optimisation autorisées

1. **Profilage d'abord** : proposer les métriques et commandes d'observation, ne pas supposer le goulet d'étranglement. Distinguer CPU, RAM, réseau, base, temps de chargement modèle, tokens générés.
2. **Changements locaux** : réduire duplication de code, boucles inutiles, conversions répétées, chargements redondants, requêtes SQL répétitives, lectures de config répétées, tout en préservant exactement les sorties et effets de bord.
3. **Pas de cache aveugle** : un cache d'évaluation LLM ne doit pas masquer les changements de prompt, modèle, paramètres, contenu, contexte temporel ou feedback ; demander validation avant tout mécanisme persistant nouveau.
4. **SQL sûr** : requêtes paramétrées, transactions bornées, pas de requêtes N+1 évitables, pas de changement de schéma ou index sans approbation et plan de migration.
5. **LLM déterministe là où utile** : proposer des profils de température/`num_ctx`/`num_predict` adaptés au scoring et à la rédaction, mais ne pas les modifier sans mesures ; préserver les contrats de parsing et la cohérence bilingue. Un schéma JSON strict est une proposition à évaluer, pas une modification implicite.
6. **Robustesse** : timeouts bornés, erreurs contextualisées, distinction erreurs transitoires/permanentes, fermeture correcte des connexions, libération de ressources. Ne pas transformer une erreur bloquante en succès silencieux.
7. **Sécurité** : pas de secret en dur, logs expurgés, validation des entrées RSS/HTML et web, TLS/SSH vérifiés, aucun assouplissement d'authentification.
8. **Lisibilité** : noms explicites, fonctions courtes si cela simplifie réellement, annotations de types ciblées, commentaires sur les raisons et invariants ; ne pas multiplier les abstractions ou helpers pour quelques lignes.
9. **Compatibilité** : conserver les signatures publiques, arguments CLI, variables d'environnement, clés YAML, chemins, formats Markdown/XML, ordre des étapes, sémantique des dates et codes de retour.
10. **Observabilité sobre** : logs utiles (comptes d'articles, sélection, doublons, durée par étape, erreurs), sans données sensibles ni avalanche de logs ; aucune nouvelle plateforme de monitoring.
11. **Pas de dépendance ajoutée** pour une optimisation faisable avec les bibliothèques déjà utilisées.
12. **Optimiser un problème réel** : si le gain n'est pas mesurable ou si le risque de régression est disproportionné, proposer de ne rien changer.

## 4. Procédure Codex obligatoire pour chaque demande

### Étape A — Diagnostic en lecture seule
- Reformuler l'objectif et le comportement actuel.
- Lire les fichiers exacts concernés et leur configuration, retrouver les appels entrants/sortants et les effets secondaires.
- Vérifier l'état Git et identifier les modifications préexistantes ; ne jamais les écraser.
- Identifier au maximum **une optimisation ciblée** à la fois, et noter les invariants à préserver.

### Étape B — Modification minimale
- Éditer uniquement les fichiers existants nécessaires.
- Ne pas reformater un fichier entier, déplacer massivement des fonctions ou renommer des API sans nécessité.
- Éviter les changements de seuils métier, prompts, YAML, SQL, déploiement ou dépendances hors demande.
- En cas de risque de perte de données, envoi réel, changement de contrat ou activation du learning : **arrêter et demander l'accord**.

### Étape C — Handoff de validation à l'utilisateur (sans exécuter)
Pour **chaque** modification, fournir :
1. **Fichiers et lignes/fonctions modifiés**.
2. **Avant / après** : comportement inchangé et gain attendu (ne pas annoncer un gain mesuré sans mesure).
3. **Risques de régression** et cas limites précis.
4. **Commandes à exécuter manuellement**, dans l'ordre, depuis `/opt/ai-news`, avec prérequis et indication claire si la commande écrit, modifie, envoie ou publie.
5. **Résultat attendu pour chaque commande**, y compris fichiers créés, messages attendus, absence d'effets de bord et critères d'échec.
6. **Procédure de retour arrière** ciblée, qui préserve les modifications de l'utilisateur et les données.
7. **Statut honnête** : « non exécuté — validation à réaliser par l'utilisateur ».

Ne pas inventer des sorties de commande exactes, nombres d'articles ou durées ; décrire des invariants vérifiables.

## 5. Catalogue de commandes de validation À FOURNIR, JAMAIS À LANCER PAR CODEX

Toutes les commandes ci-dessous sont à proposer selon le périmètre, **pas à exécuter automatiquement**. Ajouter `sudo` devant `docker` si nécessaire. Elles supposent le dépôt sous `/opt/ai-news`.

### A. Inspection non destructive
```bash
cd /opt/ai-news
git status --short
git diff --check
docker compose --profile ai --profile mail config --quiet
```
**Attendu** : `git diff --check` et `compose config --quiet` retournent le code 0 et aucune erreur ; `git status` ne liste que les fichiers voulus. `config` ne démarre pas les conteneurs, mais peut résoudre des variables d'environnement : ne pas partager la configuration résolue contenant des secrets.

### B. Vérification syntaxique Python (peut créer `__pycache__`)
```bash
python3 - <<'PY'
import ast
from pathlib import Path
for path in [Path('app/collector.py'), Path('embeddings/briefing.py'), Path('podcast/publish.py')]:
    if path.exists():
        ast.parse(path.read_text(encoding='utf-8'), filename=str(path))
        print(f'OK syntaxe: {path}')
PY
```
**Attendu** : une ligne `OK syntaxe` par fichier présent, sans `SyntaxError` ; adapter la liste aux fichiers réellement modifiés. Cette variante AST ne génère pas de bytecode.

### C. Collecte — ÉCRITURE EN BASE ET YAML POSSIBLE
```bash
docker compose run --rm -T collector
```
**Attendu** : collecte des flux actifs, normalisation, autres sources poursuivies malgré un flux défaillant ; nouveaux articles uniquement. **Attention** : les sources désactivées peuvent provoquer des suppressions en base ; la désactivation automatique peut modifier `config/sources.yaml` et créer `.bak`. Ne proposer que si ces effets sont acceptés.

### D. Scoring — ÉCRITURE EN BASE
```bash
docker compose run --rm -T collector python -m app.scorer
```
**Attendu** : scores calculés selon la configuration et aucune erreur SQL ; ne pas exiger un nombre fixe d'articles.

### E. Sélection sans publication — APPELS LLM, lecture des données
```bash
docker compose --profile ai run --rm -T -e BRIEFING_DRY_RUN=1 embeddings python -m embeddings.briefing
```
**Attendu** : logs de sélection/déduplication/évaluation ; aucun Markdown généré, aucune synthèse/traduction ni publication. Le README signale une traçabilité optionnelle des évaluations : **vérifier son activation** avant de promettre zéro écriture en base.

### F. Génération Markdown — ÉCRITURE LOCALE
```bash
docker compose --profile ai run --rm -T \
  -e BRIEFING_DRY_RUN=0 \
  -e BRIEFING_OUTPUT=/output/briefing.md \
  embeddings python -m embeddings.briefing
```
**Attendu** : `output/briefing.md` présent et lisible, sélection sourcée, aucune duplication évidente d'événements, aucune erreur de traduction. **Attention** : écrase potentiellement un briefing existant ; sauvegarder d'abord si nécessaire.

### G. Audio — ÉCRITURE LOCALE, traitement CPU
```bash
docker compose --profile ai run --rm -T audio
ls -lh "output/$(TZ=Europe/Paris date +%F)-briefing.mp3"
```
**Attendu** : MP3 non vide et lisible, date Europe/Paris, voix et prononciations vérifiées à l'écoute. Ne pas lancer si un fichier existant doit être préservé.

### H. Aperçu mail — SANS ENVOI SMTP, ÉCRITURE LOCALE
```bash
docker compose --profile mail run --rm -T mailer --dry-run
```
**Attendu** : aperçu HTML local ; aucun mail envoyé. **Ne jamais proposer `mailer` sans `--dry-run` comme simple test.**

### I. Vérification podcast — SANS TRANSFERT SFTP
```bash
docker compose --profile ai run --rm -T podcast --dry-run
```
**Attendu** : vérification des entrées MP3, Markdown, couverture et métadonnées, sans connexion SFTP ni publication. Nécessite des fichiers d'entrée présents. Ne jamais lancer la publication réelle pour un test.

### J. Diagnostic ressources et logs — lecture
```bash
docker compose --profile ai ps
docker compose logs --tail=100 postgres ollama
docker stats --no-stream
```
**Attendu** : services attendus disponibles, absence d'erreurs nouvelles, consommation compatible avec la RAM ; les valeurs varient selon la charge.

### K. Contrôles bilingues — sorties quotidiennes existantes
```bash
DAY=$(TZ=Europe/Paris date +%F)
ls -l "output/daily/$DAY/en/briefing.md" "output/daily/$DAY/fr/briefing.md"
```
**Attendu** : deux fichiers existants après génération bilingue, mêmes événements et liens sources, texte localisé. Si absents, ne pas déclencher automatiquement `run-daily.sh` : ce script peut envoyer des mails et publier.

### L. Feedback — protocole sensible
- Se reporter aux **commandes existantes** de `docs/feedback.md`, sans en inventer.
- Distinguer aperçu, simulation, import réel, acquittement, personnalisation et entraînement.
- **Attendu** : idempotence, pas de double annotation, pas d'acquittement avant stockage, pas de vote involontaire par GET, aucune fuite de secret.
- Toute commande qui importe, acquitte, purge ou entraîne doit être signalée comme **à effet de bord** et exécutée uniquement par l'utilisateur.

## 6. Retour arrière

- Avant modification : relever `git status --short` et les fichiers déjà modifiés par l'utilisateur.
- Préférer un patch minimal et réversible ; expliquer comment restaurer **seulement les lignes introduites par Codex**.
- Ne jamais conseiller `git reset --hard`, `git clean -fd`, suppression de volumes, `docker compose down -v`, suppression de `output/daily`, purge SQL ou suppression de `podcast/state` comme retour arrière ordinaire.
- Si le code est déjà intégré à une image, proposer le `docker compose build <service>` adapté **à exécuter par l'utilisateur**, avec l'impact attendu ; ne pas reconstruire ni redémarrer soi-même.

## 7. Format obligatoire de la réponse Codex

```text
Objectif : ...
État constaté dans le dépôt : ...
Fichiers modifiés : ...
Changement minimal : ...
Invariants préservés : ...
Risques / limites : ...
Validation MANUELLE — non exécutée par Codex :
  1. Commande : ...
     Attendu : ...
     Effets de bord : ...
  2. Commande : ...
     Attendu : ...
     Effets de bord : ...
Retour arrière : ...
Questions / accord nécessaire : ...
```

**Règle finale : privilégier une petite optimisation démontrable à une grande refonte supposée meilleure. L'utilisateur garde seul la main sur les tests, la base, les déploiements et la publication.**

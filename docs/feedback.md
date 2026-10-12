# Retours et apprentissage locaux

## État livré

Le code comprend le récepteur PHP/JSON, le protocole pull/commit/ack,
la migration PostgreSQL, la traçabilité, les liens dans les mails, la
personnalisation en parallèle et les commandes CPU d'apprentissage/évaluation.
`config/feedback.yaml` conserve `enabled: false` : aucun nouveau accès public,
aucune trace feedback ni personnalisation ne sont activés par défaut.

L'implémentation locale n'est pas un déploiement. PHP 8.4 est indiqué par le
propriétaire pour l'hébergement ; le chemin privé et ses permissions doivent
encore être vérifiés. HTTPS a répondu à un contrôle de lecture. Aucun script ni
secret n'a été envoyé, aucun mail réel n'a été expédié pour ces travaux.

## Préparer la base et les images

Depuis la racine du dépôt, après sauvegarde habituelle de PostgreSQL :

```bash
docker compose exec -T postgres sh -c \
  'psql -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d "$POSTGRES_DB"' \
  < app/sql/005_feedback.sql
docker compose --profile ai --profile mail build collector embeddings mailer audio
```

La migration est additive et réexécutable. Elle ne modifie pas les articles.
Les tables `feedback_*` ne dépendent pas de la conservation des sources : la
purge d'une source ne supprime pas les annotations. Ne pas supprimer ces tables
pour revenir à l'ancien comportement.

## Variables de déploiement

Les URL de déploiement sont exclusivement dans `.env` :

- `FEEDBACK_URL` : URL HTTPS complète terminant par `/feedback/feedback.php` ;
- `FEEDBACK_SIGNING_SECRET` : secret aléatoire d'au moins 32 octets ;
- `FEEDBACK_API_SECRET` : autre secret aléatoire d'au moins 32 octets.

Ne pas placer ces valeurs dans un YAML, un commit, une commande journalisée ou
une URL. Le secret de signature est transmis au mailer, celui d'API au collecteur.
Le script public reçoit les deux dans son fichier privé. La rotation du secret
de signature invalide les anciens liens ; ne pas la faire implicitement.
Le `.env` existant a été complété avec les nouvelles clés absentes, sans remplacer
ses valeurs préexistantes. Vérifier les valeurs localement sans les afficher ici.

## Arborescence distante

```text
RACINE_WEB/
    feedback/
        feedback.php
DOSSIER_PRIVE_HORS_WEB/
    config.php
    pending/                 # 0700, même système de fichiers que les temporaires
        .lock
        <identifiant>.json   # 0600
        .writing-...         # jamais exporté
```

Prévoir PHP >= 8.1 avec JSON, hash/HMAC, random_bytes, flock, fsync et rename,
un système de fichiers local permettant verrous et renommage atomique, et un
compte PHP pouvant lire le fichier privé et écrire dans `pending/`.
Ne pas placer le privé sous la racine Web, même avec un nom imprévisible.
Le script refuse un privé situé sous son `DOCUMENT_ROOT` effectif.
Vérifier aussi les autres racines/alias du serveur : ce contrôle ne découvre
pas une exposition par une autre configuration Apache.

Le code public ne nécessite ni base distante, ni cron, ni dépendance externe.
Configurer la limite PHP `post_max_size` conformément à la limite applicative.
Vérifier que l'en-tête Authorization est transmis à PHP par l'hébergement.
Ne pas activer de journal des corps POST, des jetons ou d'Authorization.
Les logs d'accès Apache peuvent inclure les query strings indépendamment du
script : vérifier auprès de l'hébergement leur exclusion ou anonymisation pour
`/feedback/` avant de considérer cette propriété comme garantie.

### Préparation du paquet, sans transfert

Les sources PHP et le modèle de configuration sont conservés dans
[web/](../web/). Le générateur de paquet lit directement ce module.

L'utilisateur a confirmé que `/home/chaucha/podcast/feedback/` est public.
Le paquet local `output/feedback-deploy/` est préparé avec le privé proposé
`/home/chaucha/ai-news-feedback-private/`. Ce chemin reste à vérifier côté
hébergeur : absence d'exposition Web, restrictions open_basedir et droits PHP.
Seul `public/feedback/feedback.php` doit aller dans le dossier public confirmé.

Après détermination du chemin privé réel, charger le `.env` de confiance dans
le shell local sans `set -x`, puis générer le paquet :

```bash
set -a
. ./.env
set +a
python3 -m feedback.prepare \
  --private-dir /CHEMIN_PRIVE_REEL/ai-news-feedback \
  --output output/feedback-deploy
```

`output/feedback-deploy/private/config.php` contient les secrets : conserver le
paquet privé et ne jamais publier tout son contenu sous la racine Web.
Le paquet existant n'est pas écrasé ; utiliser un autre dossier pour une reprise.
Le chemin absolu privé est inscrit dans la copie publique, pas ses secrets.
Une alternative est de définir `FEEDBACK_PRIVATE_CONFIG` dans l'environnement PHP.

Déploiement à effectuer uniquement après autorisation, dans une session SFTP
vers le même hôte que le podcast, avec les chemins réels vérifiés :

```text
mkdir /RACINE_WEB_REELLE/feedback
put output/feedback-deploy/public/feedback/feedback.php /RACINE_WEB_REELLE/feedback/feedback.php
mkdir /CHEMIN_PRIVE_REEL/ai-news-feedback
mkdir /CHEMIN_PRIVE_REEL/ai-news-feedback/pending
put output/feedback-deploy/private/config.php /CHEMIN_PRIVE_REEL/ai-news-feedback/config.php
chmod 700 /CHEMIN_PRIVE_REEL/ai-news-feedback
chmod 700 /CHEMIN_PRIVE_REEL/ai-news-feedback/pending
chmod 600 /CHEMIN_PRIVE_REEL/ai-news-feedback/config.php
```

Les permissions du script public dépendent du compte PHP et de l'hébergement ;
le serveur doit pouvoir le lire sans pouvoir exposer le privé. Aucun transfert
n'est automatisé par le générateur de paquet ni par la publication du podcast.

## Contrat de vote

GET `?action=vote&token=...` affiche les six motifs sans écrire de vote.
Chaque bouton POST porte un formulaire signé ; aucun commentaire n'est requis.
Une nouvelle visite GET crée une nouvelle soumission. Répéter le même POST est
idempotent ; changer de motif à partir du même formulaire déjà confirmé est
refusé. Le lien « Modifier mon choix » ouvre un nouveau formulaire.

Le jeton est `base64url(JSON_canonique_sans_padding).HMAC_SHA256_hex`.
La signature porte sur les octets ASCII du segment base64url. Le JSON est trié
par clé, compact, caractères ASCII échappés, sans espace. Les champs des liens
ne contiennent que des entiers et chaînes ASCII : `v=1`, `kind=link`,
`presentation_id`, `reason`, `link_id`, `exp` (secondes Unix).
Les formulaires utilisent `kind=form` avec `submission_id` et `submitted_at`.
Le motif fait partie de la signature. Les deux secrets sont distincts.

Le mail calcule une expiration stable à partir de la date de modification du
Markdown plus `link_days` (90 par défaut). Une reprise ne prolonge donc pas
silencieusement le lien. Un formulaire expire au plus après `form_seconds`.
Les anciens briefings sans marqueur d'identité restent envoyables sans boutons ;
ils ne sont pas réécrits ou régénérés pour ajouter des votes rétroactivement.

Le JSON immuable d'un retour a les champs suivants :

```json
{
  "schema_version": 1,
  "id": "64-caracteres-hexadecimaux",
  "submission_id": "meme-identifiant-que-id",
  "presentation_id": "64-caracteres-hexadecimaux",
  "reason": "off_topic",
  "received_at": "2026-10-12T12:00:01.000000Z",
  "submitted_at": "2026-10-12T12:00:00.000000Z"
}
```

`submitted_at` est l'heure signée de création du formulaire ; `received_at` est
l'heure de réception du POST. L'ordre effectif est `(submitted_at, id)` : un
ancien formulaire rejoué tardivement ne remplace pas une correction plus récente.
Des formulaires exactement simultanés sont départagés par l'identifiant, pas par
un ordre chronologique supposé des identifiants aléatoires. Le serveur ne conserve
aucun journal durable des votes purgés. Un POST rejoué après purge peut recréer
le même fichier ; PostgreSQL reconnaît la soumission et conserve son premier
horodatage et son annotation. Une autre raison sous le même ID est un conflit.

## Pull et purge

GET `?action=pull&limit=500` et POST `?action=ack` exigent
`Authorization: Bearer <FEEDBACK_API_SECRET>` sur HTTPS. Le client refuse les
redirections pour ne pas transmettre le secret à un autre hôte.
Les réponses ont `schema_version: 1` et ne sont pas mises en cache.

Pull renvoie `returns`, `invalid_count`, `invalid_files` (aperçu borné).
L'ordre est `(submitted_at, id)`. Les temporaires sont ignorés ; les JSON invalides
restent sur place, sont signalés et ne bloquent pas les autres retours valides.
Le lot est borné à 500. La lecture parcourt au plus la file limitée à 10 000
fichiers par défaut ; cette architecture vise quelques votes quotidiens.

Ack reçoit `{"ids":[...]}` et renvoie `deleted`, `absent`, `errors`.
Seuls les noms dérivés d'identifiants hexadécimaux strictement validés sont touchés.
Les opérations critiques sont sous verrou court ; le verrou n'est pas conservé
pendant l'envoi de la réponse HTTP.

Le client local verrouille les imports avec un advisory lock PostgreSQL :
pull → validation → transaction locale → commit → ack exact. Les retours vers des
présentations inconnues sont conservés sans FK comme annotations non résolues ;
une jointure les résout lorsque la présentation existe. Ils ne servent pas à
l'apprentissage avant résolution et présence effective dans une édition.

En cas d'échec réseau après commit, un prochain import retrouve la soumission
sans la doubler. Les votes reçus pendant l'import ne sont jamais supprimés par
une purge globale. Un retour invalide ou en conflit n'est pas acquitté ; les autres retours valides
du lot sont stockés et acquittés. Le rapport distingue invalides publics et
conflits locaux. Son ID
reste distant pour diagnostic. Les erreurs ne sont pas affichées avec secrets.

Après déploiement vérifié, activer `enabled: true` puis importer manuellement :

```bash
docker compose run --rm -T collector python -m feedback.importer
```

Le pipeline quotidien appelle l'import avant une nouvelle collecte/sélection.
Une reprise avec le marqueur `generated` ne refait ni import ni sélection.
Une indisponibilité publique laisse le pipeline continuer avec l'état local.
La génération manuelle directe du briefing n'appelle pas l'import automatiquement :
exécuter d'abord la commande ci-dessus si des votes récents sont attendus.

### Entretien sans cron public

Ne jamais supprimer un JSON non importé pour libérer de l'espace. À capacité,
le vote est refusé explicitement (507). Les JSON invalides nécessitent une revue
privée : les archiver localement avant toute suppression manuelle.

Pour nettoyer les temporaires abandonnés, opérer en maintenance avec le même
verrou `pending/.lock` tenu exclusivement ; le verrou garantit qu'aucune écriture
applicative n'est en cours. Supprimer seulement les `.writing-*` anciens, jamais
les `.json` ni `.lock`. Aucun nettoyage automatique ni cron n'est requis.
Une suppression manuelle sans ce verrou peut interrompre une écriture.

## Identité et traçabilité

La clé de source utilise l'URL canonique sans paramètres de tracking, qualifiée
par les identifiants CVE/ZDI présents pour les avis de vulnérabilité (ou l'ID
article si l'URL manque). Une table d'alias rattache les sources d'un groupe à
un événement. Le briefing est identifié par sa journée, la présentation par
briefing + événement. FR et EN utilisent cette même présentation.

Les évolutions du même événement sont des présentations distinctes entre jours,
avec leurs instantanés. Une source totalement nouvelle sans alias commun ne peut
pas à elle seule garantir l'identité historique ; le contrôle sémantique existant
reste nécessaire. Une URL déjà publiée avec un résumé disponible est revue
pour une éventuelle évolution substantielle, même si sa similarité est faible.
Les collisions d'alias d'anciens groupes sont enregistrées
pour diagnostic ; cette version ne fusionne pas destructivement l'historique.
Un thème ou produit commun n'est pas une identité factuelle.

Les candidats non évalués portent un motif de hors-pool ; ceux exclus par
l'historique sont identifiés séparément. Les évalués conservent éligibilité,
sélection, synthèse, présence par langue et erreurs de traitement. Ce ne sont
pas des accusés de réception SMTP ni des preuves d'écoute du podcast.

Les évaluations de base et les caractéristiques sont cachées avec les entrées,
modèles, versions et exemples. Le défaut AMD EPYC est adressé par reconnaissance
du produit et arbitrage Qwen borné. La fusion reste conditionnée à l'identité
factuelle, pas au produit seul. L'arbitrage général IA/IT est aussi borné ; son
budget commun peut limiter le rappel et doit être mesuré sur des données réelles.

## Personnalisation et modèles

`personalization.mode` : `off`, `shadow` (défaut), `active` (activation explicite).
La caractérisation est demandée dans le même appel Qwen que les scores de base,
avec une réponse plus longue. Un appel supplémentaire borné et caché est fait
seulement lorsqu’il existe des exemples humains proches. Elle utilise des
preuves textuelles vérifiées dans
la source. En cas d'échec, les champs restent inconnus et l'embedding est conservé.
Sans exemple pertinent, l'ajustement éditorial est forcé à zéro.

MiniLM retrouve des exemples annotés proches, dédupliqués par événement.
Le mode shadow enregistre référence/proposition sans modifier le mail.
Le mode active réordonne dans les mêmes contraintes de sélection ; un modèle
logistique compatible peut alors fournir l'ajustement borné d'utilité.
Il n'existe pas de promotion automatique ni d'activation sur un seuil unique.

L'emplacement de découverte est désactivé (`discovery_slots: 0`). La valeur 1,
en mode active, remplace au plus une place non critique par un candidat admissible
de même catégorie, tiré dans la moitié la plus incertaine. Seed, pool et probabilité
sont enregistrés. Ce mécanisme ne supprime pas tous les biais et ne peut découvrir
les candidats déjà exclus par les filtres amont.

### Apprentissage et évaluation CPU

```bash
docker compose --profile learning build feedback-learning
docker compose --profile learning run --rm -T feedback-learning evaluate \
  --output /output/feedback-models/evaluation.json --folds 3 --days 7
# Comparaison CatBoost facultative, même découpage :
docker compose --profile learning run --rm -T feedback-learning evaluate \
  --output /output/feedback-models/evaluation-catboost.json --catboost
# Entraînement candidat, sans activation :
docker compose --profile learning run --rm -T feedback-learning train \
  --output /output/feedback-models/candidate.json --target utility
# Après revue des résultats, activation locale explicite :
docker compose --profile learning run --rm -T feedback-learning activate \
  --artifact /output/feedback-models/candidate.json \
  --output /output/feedback-models/active.json
```

Le modèle logistique est exporté en JSON (poids, intercept, versions, paramètres,
empreinte des données, horodatage). Aucune désérialisation pickle n'est nécessaire
pour l'inférence quotidienne. CatBoost est un comparateur évalué localement ;
cette version n'active pas d'artefact CatBoost dans le pipeline.

Les autres `--target` sont `off_topic`, `not_applicable`,
`shallow_or_promotional`, `not_new`, `already_covered`. Ce sont des contrastes
conditionnels entre ce motif et les votes useful, pas des probabilités indépendantes
de qualités latentes. Les autres motifs restent inconnus pour cette cible.
La cible utility compare useful à off_topic/not_applicable/shallow_or_promotional ;
not_new et already_covered ne pénalisent pas l'intérêt pour le thème.
Les motifs de répétition servent au diagnostic ; le contrôle d'historique reste
factuel, une prédiction ne fusionne pas automatiquement des événements.

Seuils de données configurables ; classes insuffisantes donnent un statut explicite
et un code de sortie 2, sans écrire/remplacer de modèle. L'entraînement et CatBoost
utilisent un service séparé limité à 2 Go et 2 threads pour les bibliothèques BLAS
et CatBoost. Planifier l'entraînement à distance de la génération quotidienne.

La validation est chronologique ; la disponibilité des votes est `imported_at`,
les corrections sont reconstruites à chaque coupure, les événements présents en
entraînement sont exclus du test. Le dernier intervalle est un holdout sans
réglage dessus. Les candidats sans vecteur sont comptés et exclus explicitement.

Les rapports donnent effectifs, motifs, catégories, Brier, average precision,
intervalle Wilson du taux useful sur les événements notés, durée et pic RSS.
La référence numérique est un proxy de score, pas une probabilité calibrée.
La comparaison par voisins est un proxy déterministe des exemples proches,
pas un rejeu des décisions Qwen. Les rapports shadow fournissent les sélections
Qwen réellement proposées. L'évaluation porte sur des événements présentés et
notés, pas sur tous les articles possibles ; elle ne mesure pas directement
les articles utiles écartés. Les mesures de durée/RSS concernent la commande,
pas la consommation totale Docker/Ollama du pipeline.

## Retour arrière

1. Mettre `enabled: false` : pas de nouveaux accès ni de personnalisation.
2. Ou conserver la collecte avec `mode: off` pour arrêter la personnalisation.
3. Pour revenir au modèle précédent, recopier l'artefact local `active.json.previous`
   vers un nouveau candidat et utiliser la commande activate après vérification.
4. Revenir au code précédent et reconstruire les images concernées si nécessaire.
5. Conserver migration, annotations, fichiers en attente et marqueurs quotidiens.

Ne pas supprimer les marqueurs pour tester : cela peut renvoyer les mails.
Le script PHP peut être retiré du public après désactivation des liens, mais
conserver son privé jusqu'à import complet des retours et sauvegarde locale.

## Vérifications réalisées et limites

Les tests `tests/test_feedback.py` exercent un vrai serveur PHP local et une base
PostgreSQL jetable si `TEST_PHP` et `TEST_DATABASE_URL` sont définis. Sinon ces
intégrations sont explicitement ignorées. Les modèles LLM sont simulés pour
les tests de sélection ; aucun téléchargement de poids ni appel Ollama n'est
nécessaire. Les essais de modèles supervisés utilisent des données synthétiques,
qui vérifient le fonctionnement mais ne prouvent pas une meilleure pertinence.

Exécution reproductible, avec une base exclusivement réservée aux tests :

```bash
TEST_PHP=/chemin/php \
TEST_DATABASE_URL=postgresql://utilisateur@localhost:PORT/base_de_test \
python -m unittest discover -s tests -v
```

Ne jamais utiliser une base de production pour `TEST_DATABASE_URL`.
Les tests créent puis suppriment uniquement leurs schémas isolés.
PHP a été exécuté localement en version 8.3 ; PostgreSQL temporaire en version 16.
La dernière suite complète a exécuté 54 tests sans échec ni test ignoré :
4,91 secondes pour les tests, 6,01 secondes pour la commande, pic RSS 277280 Kio.
Ces mesures comprennent les essais synthétiques, pas l'inférence quotidienne.
La production PHP 8.4/PostgreSQL 17, les permissions distantes, l'accès Docker,
les performances Qwen et la qualité sur de vrais votes restent à vérifier.

## Déploiement public du 12 octobre 2026

Sources locales : `web/public/feedback.php`, `web/public/.htaccess` et
`web/private.example/config.php` (modèle sans secrets). Déploiement SFTP effectué
avec les paramètres de `.env`, sans transfert des podcasts ni du README.

- Public : `/home/chaucha/podcast/feedback/feedback.php` et `.htaccess`.
- Privé : `/home/chaucha/ai-news-feedback-private/config.php` (0600),
  répertoire privé et `pending/` (0700). Le public est lisible par le serveur Web.
- Le `.htaccess` transmet Authorization à PHP sur cet hébergement CGI/FastCGI.
- L’en-tête `X-Robots-Tag` demande de ne pas indexer ni suivre les liens.
  Ces directives sont volontaires : elles ne verrouillent pas l’accès et ne
  permettent pas de reconnaître les vérifications Gmail. Un filtrage User-Agent
  ne garantirait pas non plus cette distinction. La confirmation POST est conservée.

Vérifiés réellement en HTTPS : GET sans annotation, six confirmations possibles,
POST répété sans doublon, pull refusé sans authentification, pull authentifié,
ack exact puis répété. Le seul retour technique a été sauvegardé localement
puis purgé ; aucun vote utilisateur n’a été supprimé. Cela ne valide pas encore
l’import PostgreSQL de production ni l’apprentissage sur des retours réels.
Les journaux Apache et d’éventuels autres alias Web restent à contrôler côté
hébergeur ; aucun journal contenant des secrets n’a été consulté ou affiché.

Pour un déploiement futur, transférer aussi `web/public/.htaccess` dans le dossier
public. Pour suspendre la collecte publique, retirer uniquement `feedback.php`
ou le renommer hors Web ; conserver le privé et les JSON non importés.

### Premier import réel

Le 12 octobre 2026, migration additive 005 appliquée à PostgreSQL local,
puis deux retours reçus, validés, stockés et confirmés sans erreur. Le pull
de contrôle a trouvé zéro retour restant. Les deux présentations des mails
de test ne sont pas encore enregistrées : leurs retours sont conservés comme
non résolus et ne servent pas encore à l’apprentissage. `enabled: false`
reste inchangé ; cet import manuel n’active ni le pipeline ni la personnalisation.

## Essai de vote direct configurable

À la demande de l’utilisateur, `require_confirmation => false` est déployé
dans le fichier privé `config.php`. Le modèle versionné garde `true` par défaut.
Revenir à `true` dans ce fichier rétablit immédiatement GET sans vote / POST
avec confirmation pour les liens existants, sans modifier les mails.

En mode direct, GET signé enregistre le motif et affiche la réussite ; il ouvre
toujours une page du navigateur. Gmail peut également visiter ces liens et créer
des annotations : ne pas considérer ces votes comme certainement humains.
Le même lien/motif conserve un identifiant déterministe, y compris après purge.
Sa date de soumission est la date du lien (expiration moins `link_seconds`),
pas une mesure de l’heure du clic. `link_seconds` doit correspondre à la durée
des liens du mail (90 jours actuellement). Des motifs différents du même mail
ont donc la même date et sont départagés par identifiant, pas par ordre de clic.
Pour une correction ordonnée, utiliser « Modifier mon choix » sur la page :
ce parcours force un formulaire POST, avec nouvel identifiant et date réelle.

Test HTTPS réel : deux ouvertures du lien technique ont créé un seul retour,
sauvegardé localement puis purgé par son seul identifiant. Les votes utilisateur
n’ont pas été supprimés. Cinq tests PHP locaux passent ; un test PostgreSQL
jetable est ignoré faute de base de test active lors de cette vérification.

## Audit ciblé et correctifs — 12 octobre 2026

Audit du code PHP, signatures, opérations fichiers et configuration Apache
locale ; vérification HTTPS après déploiement, sans scan de charge.

- Abus identifié : un lien valide permettait de multiplier les corrections et
  de remplir la file. Limite `max_pending_per_presentation` : 12 par défaut,
  configurable de 1 à 100 dans le privé. HTTP 429 au plafond, sans suppression.
- Affichage des erreurs PHP désactivé ; warnings convertis en erreurs génériques
  sans chemins ni secrets. Les erreurs de démarrage restent à désactiver côté hébergeur.
- Verrous non bloquants pour éviter d’immobiliser des processus PHP ; HTTP 503
  en cas de contention, avec reprise possible.
- `.htaccess` interdit les listings et tout fichier autre que `feedback.php`.
- POST navigateur `Sec-Fetch-Site: cross-site` refusé. Défense complémentaire :
  le jeton signé reste obligatoire, même lorsque cet en-tête est absent.
- Query string et lectures de fichiers bornées ; limites de corps conservées.
- En-têtes anti-cadrage et anti-indexation également émis directement par PHP.

Treize tests ciblés passent ; un test PostgreSQL jetable est explicitement
ignoré. Vérifiés en HTTPS : API authentifiée, refus des requêtes non authentifiées
et malformées, formulaire GET sans vote en mode confirmation explicite,
en-têtes et refus du POST cross-site. Aucun vote créé ni purgé lors de ces tests.
Les copies antérieures au déploiement sont conservées localement sous
`output/feedback-deploy-tools/security-backup/` pour un retour arrière ciblé.

Limites : le lien signé est une capacité au porteur. En mode direct, les robots
Gmail et les ouvertures automatiques peuvent voter. Le plafond compte uniquement
les retours en attente et ne constitue pas un quota historique après purge.
Aucun journal durable public ni limiteur par IP n’est ajouté. La saturation réseau
ou HTTP nécessite une protection de l’hébergeur. Les query strings des journaux
Apache et les autres alias Web pouvant exposer le privé restent à vérifier.
X-Robots-Tag n’est pas un contrôle d’accès. Aucun audit complet de l’hébergement
ni garantie d’absence de vulnérabilité n’est revendiqué.

Références : [verrous PHP](https://www.php.net/manual/en/function.flock.php),
[erreurs PHP](https://www.php.net/manual/en/errorfunc.configuration.php),
[déni de service OWASP](https://cheatsheetseries.owasp.org/cheatsheets/Denial_of_Service_Cheat_Sheet.html).

### Collecte après le second envoi de test

Le 12 octobre 2026 : 11 retours reçus, validés, nouvellement stockés dans
PostgreSQL puis confirmés, sans conflit ni erreur. Le pull de contrôle trouve
zéro retour restant. Le total local des retours non résolus est de 13 : les
présentations des mails de test restent à rattacher. Ces chiffres comptent
des soumissions, pas nécessairement 11 événements ou 11 actions humaines ;
le mode direct ne permet pas de distinguer les vérificateurs automatiques.

## Première analyse statistique et lancement d’apprentissage

Le 12 octobre 2026 : 13 soumissions, 11 présentations distinctes notées sur les
14 présentations du manifeste (78,6 % de couverture), deux soumissions remplacées
par le classement effectif. Motifs effectifs : useful 6, off_topic 3,
already_covered 2. Le total brut inclut useful 7, off_topic 3,
already_covered 2, not_applicable 1. La différence ne prouve pas un changement
volontaire : les liens directs d’un même mail peuvent avoir la même date signée
et sont alors départagés par identifiant. Des robots peuvent aussi voter.

Pour la cible utilité, les annotations effectives donneraient 6 positives,
3 négatives et 2 inconnues, sous réserve de leur authenticité humaine.
« Déjà traité » ne devient pas un rejet thématique. Aucun gain de pertinence
ni statistique de généralisation n’est déduit de ces 11 présentations.

Commandes locales réellement exécutées : evaluate et train sur PostgreSQL.
Evaluate a écrit un rapport chronologique, mais les fenêtres ne disposent pas
d’exemples exploitables. Train s’est arrêté avec `insufficient_data` (code 2),
0 événement admissible : les 13 soumissions sont encore non résolues et les
caractéristiques des candidats historiques ne sont pas présentes dans la base.
Pas de reconstitution artificielle des caractéristiques, pas d’activation ni
remplacement de modèle. Même après rattachement, 11 présentations ne suffiraient
pas aux seuils actuels (100 événements, 10 exemples par classe).

Rapports : `output/feedback-analysis/20261012T122813Z/` : statistics.json,
evaluate.json, evaluate-status.json, train-status.json. Temps muraux des commandes
~0,42 seconde chacune ; pic RSS evaluate ~39 Mio, sans inférence Qwen/MiniLM.
Prochaine étape : rattacher explicitement les présentations des tests à leur
contenu disponible, en conservant leur origine rétrospective ; pour la validation
chronologique, collecter de nouveaux candidats avec leurs caractéristiques
figées avant décision et leurs votes ultérieurs. Ne pas antidater des analyses
calculées après réception des votes.

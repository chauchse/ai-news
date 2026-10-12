# Instructions pour travailler dans AI News

## Contexte à lire

Lire [CADRAGE.md](CADRAGE.md) puis les sections pertinentes de
[README.md](README.md) avant une analyse ou une modification. Le cadrage conserve
les exigences utilisateur, les limites et les décisions ouvertes. Le README
décrit l'exploitation. Vérifier les faits techniques dans le code ; signaler les
écarts. Ne pas présenter une proposition du cadrage comme une fonctionnalité
existante ou une décision validée.

## Périmètre et autonomie

- Respecter la demande active : une demande d'analyse seule n'autorise pas de
  modification. La création de ces deux documents n'autorise pas l'implémentation
  du plan. Une demande ultérieure de développement autorise les travaux locaux
  nécessaires à son périmètre ; ne pas redemander systématiquement confirmation.
- Tous les traitements IA et ML du produit doivent s'exécuter sur le serveur de
  l'utilisateur. Ne pas introduire d'API d'inférence, d'embeddings ou
  d'entraînement externe, ni lui transmettre articles ou votes.
- Les échanges RSS, SMTP et SFTP et les téléchargements initiaux documentés ne
  sont pas interdits par cette exigence de modèles locaux.
- Vérifier CPU, RAM, GPU éventuel et budget de temps avant de proposer un modèle
  plus lourd. Le cadrage utilisateur indique environ 4 vCPU, 8 Go de RAM, sans
  GPU : dimensionner pour CPU et limiter la concurrence et les appels LLM.
  Les plafonds Compose ne décrivent pas la capacité réelle du serveur.
- Les URL publiques de déploiement (podcast et feedback) sont définies dans
  `.env`, pas dans les YAML ni en dur dans le code. Lire `FEEDBACK_URL` pour
  le feedback ; les URL des flux RSS restent dans leur catalogue de sources.
- Ne pas lire ou afficher les secrets de `.env`, clés privées ou identifiants
  dans les sorties, journaux ou documents.
- Lire les fichiers concernés, les montages Compose, les noms de variables et
  les schémas avant de modifier. Ne pas supposer un chemin conteneur, une clé
  YAML ou une commande. Éviter `docker compose config` sans précaution : sa
  sortie peut contenir les secrets interpolés ; préférer `config --quiet`.
- Favoriser un correctif ciblé ; ne pas remplacer silencieusement PostgreSQL,
  MiniLM, Qwen, MarianMT ou Kokoro. Préserver données, volumes et artefacts.
- Garder les réglages dans les configurations appropriées ; vérifier que les
  paramètres sont effectivement lus plutôt que simplement ajoutés au YAML.
- Répondre en français, avec faits, hypothèses et limites distincts. Pour une
  modification fonctionnelle, fournir vérifications pertinentes, résultats et
  procédure de retour arrière. Une vérification non exécutée n'est pas validée.

## Repères techniques

- Collecte et scoring : `app/collector.py`, `app/normalizer.py`, `app/scorer.py`.
- Schéma : `app/sql/` ; `unique_articles` déduplique par GUID seulement.
- Configuration applicative unique : `config.yaml` à la racine, lue via
  `project_config.py`. Sélection : `embeddings/briefing.py`, section `briefing`.
- Profils sémantiques, veille et traduction : sections `semantic_scoring`,
  `watchlists` et `glossary` de `config.yaml`.
- Mail : `mailer/send.py` ; podcast : `podcast/publish.py` ; audio : `audio/`.
- Orchestration : `run-daily.sh`, `docker-compose.yml`, `post-install.sh`.
- Tests existants : `tests/`. Utiliser les vérifications pertinentes au changement.

## Propriétés à préserver

- Veille technique et décisionnelle IA/IT/cloud/cyber/risques/transformation,
  avec une place substantielle de l'IA et de l'IT. Ne pas réduire le produit à
  un bulletin de vulnérabilités ni filtrer exclusivement sur le casino physique.
- Ne pas confondre casino physique, jeux vidéo et casino en ligne. Séparer
  pertinence technique, importance et pertinence métier ; ne pas remplir un
  quota avec des articles faibles ni abaisser aveuglément les seuils.
- Une sélection commune EN/FR, synthèse anglaise puis traduction française.
- Français naturel, précis et prononçable, sans invention ; signaler les échecs
  de traduction/synthèse. Ne pas présenter comme livrée une révision Qwen des
  titres français seulement envisagée dans le cadrage historique.
- Conservation des sources et distinction entre faits et triage provisoire.
- Identité d'événement distincte de la langue, de l'article et de sa présentation.
- Déduplication prudente : un produit commun ne suffit pas à fusionner des avis ;
  traiter également le cas des traductions d'un même avis.
- Reprise quotidienne par dossiers et marqueurs, sans renvoi ou republication
  implicite. Ne pas supprimer ces marqueurs pour effectuer une vérification.
- Préserver la sélection figée du jour ; importer les nouveaux votes pour la
  prochaine sélection, sans régénérer implicitement les éditions déjà envoyées.
- Fuseau Europe/Paris pour les dates du produit ; cron dépend du fuseau hôte.
- Distinguer date de l'avis, publication, événement et reprise RSS. Ne pas
  présenter un avis ancien comme nouveau sans évolution substantielle.
- Respecter les plafonds configurés. Distinguer domaine et risque lorsque le
  changement le concerne ; ne pas assimiler systématiquement infrastructure
  et cybersécurité. La séparation complète domaine/risque reste un objectif.
- Préserver RSS/XML valide, sources renseignées et métadonnées cohérentes ;
  le même sommaire dans deux champs RSS n'est pas en soi un doublon d'événement.

## Collecte et diagnostic

Le catalogue utilise `sources[].url`, pas `feed_url`. Vérifier `SOURCES_FILE`
et le montage réel (`/app/config` pour le collecteur, `/config` pour certains
autres services). Parcourir toutes les sources actives, isoler les erreurs de
flux et compter séparément articles trouvés, nouveaux, normalisés et éligibles.
Zéro nouvelle normalisation ne suffit pas à conclure à une panne.

Les listes de sources, erreurs HTTP/XML et chiffres de tests historiques dans
`CADRAGE.md` ne décrivent pas nécessairement l'état présent. Vérifier avant de
réannoncer un incident ou d'ajouter une source ; détecter les URL en double et
les reprises, vérifier la fraîcheur du contenu des flux.

Le collecteur actuel peut écrire `sources.yaml` pour désactiver automatiquement
un flux. Ne pas imposer un montage en lecture seule sans tenir compte de cette
fonction. Toute action sur une source désactivée doit considérer les effets de
purge décrits dans le README.

## Feedback et apprentissage à venir

Suivre les exigences et le statut des propositions dans `CADRAGE.md`.
Les retours sont des votes par motif, sans commentaire obligatoire. L'absence
de vote et la non-sélection ne sont pas des étiquettes négatives. Distinguer
pertinence, contexte, substance, nouveauté et doublon. Ne pas transformer une
interprétation de LLM en annotation humaine.

Le récepteur validé est PHP + JSON individuels, sans base distante, sous
`/feedback/`. Configuration, pending, temporaires et verrous sont hors racine
Web. GET ne vote pas ; confirmation POST sans saisie. Voir `docs/feedback.md`
pour les contrats et commandes. Ne pas remplacer cette architecture par SQLite.

La collecte distante doit être temporaire et l'historique local durable.
Ne purger que les retours effectivement stockés et confirmés, avec import
idempotent et prise en compte des corrections. Vérifier les possibilités web
de l'hébergement ; SFTP seul ne fournit pas un service de collecte.

Évaluer chronologiquement, empêcher les fuites entre reprises/traductions,
documenter les biais de sélection, et versionner les modèles et données.
Prévoir un mode parallèle et un retour arrière avant activation. Les méthodes
et seuils proposés restent à valider, sans supposer un fine-tuning nécessaire.

## Vérifications et effets externes

Privilégier lecture, tests ciblés et aperçus. Le dry-run du briefing appelle les
modèles locaux ; celui du mail écrit un aperçu ; celui du podcast nécessite les
artefacts. Ils ne constituent pas tous une simple lecture sans consommation.

`run-daily.sh` et un lancement du service quotidien peuvent collecter, écrire en
base, envoyer des mails et publier. Le collecteur peut supprimer les articles
de sources désactivées. Ne pas utiliser ces commandes comme de simples tests.
Ne pas envoyer de mail, publier ou modifier l'exploitation sans autorisation
couvrant ces actions. L'absence de droits Docker/root ne justifie pas un
contournement ; signaler les vérifications impossibles.

Après un changement autorisé, résumer les fichiers modifiés, les vérifications
effectuées et les limites restantes. À chaque évolution, y compris une étape
de test ou de préparation, mettre systématiquement à jour le README : expliquer
brièvement le fonctionnement, l'utilisation et les limites effectives. Garder
ce document lisible, compréhensible et concis ; renvoyer les détails techniques
vers la documentation spécialisée. Ne jamais supprimer le flux de traitement ;
le conserver et l'actualiser si nécessaire, ainsi que le flux de retour utilisateur.
Mettre aussi à jour la documentation concernée ; ne pas réécrire l'historique des
propositions comme si elles avaient été validées dès l'origine.

Ne pas créer de README par module. Conserver le README principal comme point
d’entrée et placer les détails spécialisés dans `docs/`.

Exception validée pour l’essai du 12 octobre 2026 : le PHP public peut voter
sur GET si sa configuration privée `require_confirmation` vaut false.
Le défaut reste true ; documenter le risque de scanners et préserver le retour
arrière par cette option. Cette exception remplace l’exigence GET sans vote
uniquement pour ce mode explicitement demandé.

## Configuration globale — état actuel validé

L’utilisateur a confirmé de centraliser la version actuelle V2 sans réintroduire
les modules feedback/web absents de cette version. Tous les réglages applicatifs
sont dans `config.yaml` à la racine ; ne plus créer de YAML par module.
Secrets et URL de déploiement restent dans `.env`. Les schémas SQL, manifestes
Docker et artefacts de modèles ne sont pas des configurations applicatives.
Lire via `project_config.load_section`, conserver les commentaires et tester
la désactivation d’une source sans changement des autres sections.
Le catalogue utilise désormais `collection.sources` et `collection.source_health`.
Le collecteur lit `/workspace/config.yaml` dans le montage du projet permettant
son remplacement atomique ; les autres services lisent `/app/config.yaml`.
Reconstruire toutes les images Python après modification du lecteur partagé.
Ne pas lancer le pipeline complet pour vérifier cette migration.

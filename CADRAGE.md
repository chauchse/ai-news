# Cadrage du projet AI News

Date de référence : 12 octobre 2026. Ce document distingue le fonctionnement
existant, les exigences exprimées et les pistes de conception. Une piste décrite
ici ne constitue pas une fonctionnalité livrée ni une autorisation de déploiement.

## Sources et portée

- [README.md](README.md) : documentation du fonctionnement et de l'exploitation.
- Échange visible du 12 octobre 2026 : analyse des doublons, votes par motif,
  apprentissage personnalisé et obligation de modèles locaux.
- [Historique de chat indiqué par l'utilisateur](https://chatgpt.com/c/6aaa190a-4cb4-83eb-ab3e-1db2a14f2e14).
  Accès tenté le 12 octobre 2026 : seule une page de connexion était accessible.
  Son contenu non visible n'a pas été lu directement. L'utilisateur a ensuite
  fourni un texte de cadrage issu de ce chat ; ce texte a été lu et fusionné
  dans les présents documents. Ses constats historiques ne constituent pas une
  vérification de l'état actuel du serveur.
- Code et configurations du dépôt consultés pendant l'analyse. En cas d'écart
  avec le README, vérifier le code et documenter l'écart sans modifier le
  fonctionnement implicitement.

## Finalité

Produire une veille utile sur l'IA, l'IT, la cybersécurité et les risques, destinée
à un professionnel de la technologie travaillant notamment pour un casino
physique au Luxembourg. Améliorer progressivement le choix des événements à
partir de retours explicites, en comprenant les motifs d'inutilité sans réduire
automatiquement la veille aux seuls sujets directement liés au casino.

Le profil éditorial précis doit être affiné par les retours. Ne pas confondre
importance générale d'une actualité, proximité thématique et utilité personnelle.

Le lectorat est technique et décisionnel (CTO/RSSI). La veille doit laisser une
place substantielle à l'IA, à l'IT, au cloud et à la transformation numérique ;
elle ne doit pas devenir mécaniquement un bulletin de vulnérabilités. Le contexte
casino physique ne doit être confondu ni avec les jeux vidéo ni avec le casino
en ligne. Le français naturel et agréable à écouter reste une priorité, tout
en préservant les deux éditions EN/FR actuellement implémentées.

## Contraintes validées

- Rester dans le projet `/opt/ai-news` et son architecture exploitable sur le
  serveur de l'utilisateur.
- Exécuter localement les modèles de raisonnement, les embeddings, les
  entraînements et la sélection. Aucune API d'IA externe pour ces traitements.
- Les téléchargements initiaux de modèles et dépendances restent compatibles
  avec cette contrainte, comme dans l'installation actuelle.
- La collecte RSS, SMTP et SFTP demeurent des échanges réseau nécessaires au
  produit ; « modèles locaux » ne signifie pas fonctionnement intégral hors ligne.
- Proposer des boutons par raison sous chaque news du mail. Aucun commentaire
  ni champ libre ne doit être nécessaire pour donner un retour.
- Envoyer le retour avec un identifiant de news à une page web sur l'hébergement
  du podcast.
- Avant le briefing suivant, récupérer les retours, les conserver localement,
  puis purger les retours distants importés sans perte.
- Exploiter les retours par une analyse et un apprentissage mesurables, au-delà
  d'un simple ajout de mots-clés ou de consignes au prompt.
- L'absence de vote n'est ni un avis favorable ni un avis défavorable.

## Fonctionnement existant

Le pipeline Docker Compose collecte et normalise RSS/Atom, stocke les articles
dans PostgreSQL, calcule un score historique, puis regroupe et sélectionne les
événements avec MiniLM et Qwen via Ollama. Qwen synthétise en anglais ; MarianMT
et le glossaire produisent le français. Kokoro génère les MP3 ; SMTP envoie les
mails ; SFTP publie les podcasts et leurs flux RSS.

- `run-daily.sh` prépare les deux Markdown puis traite les éditions EN et FR.
- `output/daily/AAAA-MM-JJ/{en,fr}/` conserve les fichiers et marqueurs de reprise.
- La sélection du jour est figée pour les reprises ; préserver cette propriété.
- Les événements des sept jours précédents sont contrôlés à partir des Markdown.
- La vue `unique_articles` déduplique par GUID, pas par identité d'événement.
- Le pipeline historique ne conserve pas les évaluations Qwen en base.
  La nouvelle traçabilité optionnelle les conserve après activation et migration.
- Au début de l’analyse, aucune collecte de votes ni chaîne d’apprentissage
  personnalisé n’était livrée. Le code est désormais préparé, désactivé par
  défaut ; voir `docs/feedback.md` pour ses fonctions testées et limites.
- La publication SFTP ne prouve pas la disponibilité d'un moteur web dynamique.

Modèles observés : `qwen2.5:3b`,
`sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2`,
`Helsinki-NLP/opus-mt-en-fr` et Kokoro. Les alternatives audio documentées sont
optionnelles ; ne pas les substituer implicitement au pipeline quotidien.

Limites mémoire Compose observées : embeddings 6 Go, Ollama 4 Go, audio 2 Go.
Ce sont des plafonds de conteneurs, pas l'inventaire physique du serveur.
Le cadrage fourni par l'utilisateur indique environ **4 vCPU, 8 Go de RAM,
sans GPU**, sous Ubuntu/Linux. Dimensionner les propositions pour cette enveloppe
CPU ; confirmer l'inventaire et la mémoire réellement disponible avant de choisir
ou remplacer un modèle. Les plafonds de conteneurs s'additionnent et peuvent
dépasser cette RAM : limiter les chargements simultanés. La durée acceptable du
traitement reste à établir ; une ancienne génération avait pris environ huit
minutes, sans que cela constitue un engagement de performance actuel.

## Contexte historique fourni et points de vigilance

Les éléments de cette section proviennent du texte transmis par l'utilisateur.
Ils guident le diagnostic ; ne pas affirmer que les incidents persistent ni que
les corrections évoquées sont présentes sans vérifier le dépôt et l'exécution.

### Collecte et diversification

- Le catalogue utilise `sources` et la clé `url`, pas `feed_url`. Le chemin du
  collecteur est historiquement `/app/config/sources.yaml`, surchargeable par
  `SOURCES_FILE`. Vérifier montages et chemins propres à chaque service.
- Une ancienne proposition utilisait à tort `feed_url`. Des logs historiques
  montraient aussi des tentatives sur des sources déclarées désactivées : vérifier
  le fichier réellement lu plutôt que présumer un défaut encore présent.
- Parcourir toutes les sources actives et distinguer articles trouvés, nouveaux,
  normalisés et éligibles. Zéro article nouvellement normalisé n'établit pas une
  panne si les articles sont déjà connus.
- Isoler erreurs HTTP, timeout, XML/Atom invalide et flux vides ; préserver la
  collecte des autres sources et compter clairement les résultats par source.
- Incidents rapportés : BleepingComputer 403 ; Sophos timeout ; anciennes URL
  Azure 404 ; XML invalide pour des URL Qualys, CERT-FR, Channelnews et DeepMind.
  Toute affirmation de disponibilité actuelle demande une nouvelle vérification.
- Un diagnostic initial comptait 20 candidats vulnérabilités sur 26 articles
  exploitables. Diversifier la collecte avant de compenser par des seuils faibles.
- Répartition indicative historique des sources : IA 35 %, IT/cloud 30 %, cyber
  20 %, risques/réglementation 10 %, autres 5 %. Ce ne sont ni des quotas de news
  ni des paramètres approuvés pour l'apprentissage.
- Sources IA ajoutées ou envisagées : Anthropic, Mistral, Cohere, xAI, Ai2,
  The Batch, TLDR AI, Turing Blog, AISI, CETaS, Mila, OpenAI, MIT Technology Review.
  Certaines reposaient sur `alan-turing-institute/ai-rss-feeds` : vérifier la
  fraîcheur des articles et des flux GitHub, pas seulement leur accessibilité.
- Sources IT ajoutées ou envisagées : Ars Technica, Hacker News, TechCrunch,
  The Register, Slashdot, Stratechery, The Next Web, ReadWrite, Google The Keyword,
  The Verge et Engadget. Ces listes ne garantissent ni activation ni validité.
  Préférer un catalogue maîtrisé à un import massif OPML ; podcasts tiers et
  YouTube ne sont pas prioritaires sans transcription.
- Détecter doublons d'URL, de sources et reprises syndiquées. Reconstruire une
  image seulement si le fichier modifié y est copié. Ne pas imposer un montage
  en lecture seule à `sources.yaml` : l'auto-désactivation actuelle écrit ce YAML
  et peut supprimer les articles associés, comme documenté dans le README.

### Sélection, identité et dates

- Ne pas compléter artificiellement jusqu'à `max_events` avec des articles faibles.
  Réallouer les places uniquement vers des sujets pertinents et respecter les
  plafonds, y compris les règles configurées d'alertes critiques.
- Distinguer pertinence technologique, importance et pertinence métier. Le casino
  reste un contexte et un bonus, jamais un filtre exclusif de toute la veille.
- Objectif éditorial : distinguer domaine principal et nature du risque. Une faille
  d'agent IA peut être un sujet IA avec un risque sécurité ; l'infrastructure
  générique n'est pas automatiquement cyber. Le code actuel peut encore appliquer
  une catégorie sécurité déterministe : ne pas présenter cet objectif comme livré.
- Dédupliquer entre langues, éditeurs et catégories, y compris l'actualité IA/IT.
  Un doublon historique concernait un faux signalement de meurtre attribué à un
  modèle Anthropic. Un test rapporté avait fusionné 26 articles en 21 événements,
  avec des paires bilingues encore présentes (Python Core, GIMP, Linux, Icinga,
  Kitty). Ces chiffres ne décrivent pas le briefing courant.
- Utiliser CVE/ZDI, URL canonique et preuves factuelles, puis similarité et arbitrage
  local à budget limité ; produit et version seuls ne prouvent pas l'identité.
- Séparer date de publication, date de l'avis, date de l'événement et réapparition
  RSS. Des avis d'août avaient figuré dans un briefing d'octobre. Une reprise
  n'est pas une nouveauté ; une évolution substantielle peut justifier une reprise.
- Les anciens paramètres `strategic_topic_targets`, `min_business_relevance` et
  `min_security_business_relevance` ne doivent pas être réintroduits comme des
  clés actives sans auditer le schéma et leur lecture effective. Des versions
  anciennes ignoraient certains quotas, poids ou plafonds ; ne pas supposer que
  ces défauts persistent. Les valeurs courantes sont dans les YAML et le code.
- Un ancien test avait retenu 4 candidats sur 21 avec pertinence métier à zéro
  pour tous. Distinguer réponse du modèle, prompt et valeurs de repli avant
  d'attribuer une cause à des scores nuls.

### Français, audio et podcast

- Préférer une rédaction fluide, fidèle et prononçable aux traductions littérales.
  Une révision locale par Qwen des titres français est une piste à mesurer en
  temps et mémoire, pas un remplacement validé de MarianMT.
- Vérifier faits, dates, sigles et noms de produits. Ne pas transformer une
  reformulation éditoriale en affirmation non étayée par le contenu source.
- Rendre visibles les échecs de synthèse et traduction ; un éventuel repli doit
  être explicite. Préserver la cohérence des éditions et la reprise des artefacts.
- Un sommaire historique affichait `Source :` vide ; vérifier le parseur actuel
  et ses tests avant de corriger. Les champs RSS `description` et `itunes:summary`
  peuvent légitimement reprendre le même sommaire.
- Préserver validité XML, échappement, métadonnées, liens et publication d'un
  audio existant sans nouvelle analyse LLM. La lecture naïve d'un commentaire
  inline YAML avait causé une panne : vérifier le parseur actuel plutôt que
  supposer cet incident encore actif.

## Constats de l'analyse du briefing du 12 octobre

Les événements 2 et 3 AMD EPYC / SEV-SNP sont des versions FR et EN du même avis,
présentes aussi dans le briefing anglais : le doublon précède la traduction.
Les URL et titres diffèrent ; AMD EPYC n'est pas reconnu par la liste de produits
de la déduplication complémentaire. Le recouvrement des tokens distinctifs des
titres vaut 3 mots et environ 33 %, sous les seuils d'arbitrage (4 mots et 65 %)
et de filtrage final (45 %). Cette paire révèle un angle mort bilingue.
Il faut préserver la distinction entre avis différents d'un même produit.

Les journaux root et la base Docker n'étaient pas accessibles pendant cette
analyse. La présence d'identifiants dans les résumés sources et le déroulement
exact de l'exécution n'ont donc pas été vérifiés.

La sélection actuelle favorise la pertinence technologique et l'importance,
avec un faible bonus métier. Le prompt rend le lien direct au casino facultatif.
Le briefing affiche la pertinence métier mais pas le score technologique qui
contribue fortement au classement. Des news sans utilité personnelle démontrée
peuvent donc être sélectionnées sans dysfonctionnement du seuil actuel.

## Architecture cible proposée

### Identité et retours

Créer un identifiant stable d'événement commun aux éditions FR/EN, relié aux
articles sources, ainsi qu'un identifiant de briefing et de présentation.
Distinguer une reprise, une traduction et une évolution substantielle.

Motifs désormais validés pour la première version : utile ; hors de mes sujets ; pas applicable
chez moi ; trop superficiel/promotionnel ; pas assez nouveau ; déjà traité.
Séparer les signaux de pertinence, de contexte, de substance et de répétition.

Un lien signé associerait le motif à l'événement et au briefing. Prévoir une
correction de vote sans commentaire obligatoire. La demande d'implémentation
valide une confirmation POST sans saisie ; une visite GET n'enregistre rien,
car les robots de messagerie peuvent visiter les liens.

### Synchronisation

Le serveur du podcast conserve une file temporaire. Le service local récupère
un lot, l'enregistre durablement avec déduplication par identifiant de retour,
puis accuse réception des seuls éléments enregistrés. Le serveur ne purge que
ces éléments confirmés. Préserver les votes arrivés pendant l'import et permettre
la reprise après panne. L'historique d'apprentissage local doit être indépendant
de la rétention de sept jours des fichiers podcast.

Architecture validée : un script PHP public sous `/feedback/feedback.php`,
une configuration et un répertoire de JSON individuels hors racine Web. Aucune
base distante, aucun cron ni modèle public. Pull authentifié, stockage local
transactionnel puis ack des seuls identifiants enregistrés. Les corrections
créent de nouvelles soumissions ; les anciens POST ne changent pas l'ordre local.
L'utilisateur indique PHP 8.4. HTTPS a répondu lors d'un contrôle sans déploiement ;
Le dossier `/home/chaucha/podcast/feedback/` est confirmé public par l'utilisateur.
Le paquet local utilise le privé proposé `/home/chaucha/ai-news-feedback-private/` ;
son absence d'exposition et ses permissions PHP restent à vérifier sur l'hébergement.
Les URL de déploiement restent dans `.env` (`FEEDBACK_URL`, `PODCAST_SITE_URL`),
jamais dans les YAML ; les URL des flux RSS restent dans leur catalogue.

## Apprentissage proposé, non encore validé

1. Conserver un instantané des candidats retenus et écartés : contenu disponible,
   dates, sources, embeddings, caractéristiques, scores, décisions et versions.
   Un candidat écarté ne devient pas automatiquement un exemple négatif.
2. Un modèle de raisonnement local caractérise chaque candidat : sujet, nature,
   public, utilité opérationnelle, contexte, substance et nouveauté. Associer les
   caractéristiques aux preuves disponibles ; garder « inconnu » si nécessaire.
3. Le vote humain fournit l'étiquette. Les interprétations du modèle restent des
   hypothèses et ne deviennent pas des votes artificiels. Rejeter une anecdote
   sur Claude ne signifie pas rejeter les usages professionnels de Claude.
4. Démarrer avec la recherche d'exemples annotés proches. Comparer ensuite une
   régression logistique régularisée et un CatBoost peu profond exécutés localement.
   Le choix final dépend des données et du matériel, pas d'une décision acquise.
5. Prédire séparément utilité, hors sujet, inapplicabilité et manque de substance,
   sous réserve d'un volume suffisant par motif. Les doublons alimentent un
   contrôle distinct ; la nouveauté tient compte de l'historique des événements.
6. Produire un bilan local des erreurs, tendances, incertitudes et changements.
   Ne pas présenter une contribution statistique comme la cause certaine du vote.

Le démarrage des essais supervisés vers 100 à 200 événements notés est une
hypothèse de planification. Le fine-tuning d'un grand modèle ou d'un reranker
n'est pas retenu comme point de départ. Aucun remplacement de Qwen n'est décidé.

## Évaluation proposée, non encore validée

Comparer sélection actuelle, exemples proches, régression logistique et CatBoost
sur les mêmes informations disponibles au moment de la sélection. Réaliser une
validation chronologique ; regrouper traductions et reprises d'un même événement
pour éviter les fuites entre entraînement et test. Recalculer tout prétraitement
appris uniquement sur les données d'entraînement.

Mesurer votes utiles parmi les articles notés, rejets par motif, classement,
calibration, diversité et répétitions. Signaler le biais : les articles non
présentés n'ont généralement pas de vote ; les résultats ne démontrent pas
automatiquement la qualité sur l'ensemble des candidats.

Un emplacement de découverte par briefing est proposé pour obtenir des retours
sur des candidats plausibles et incertains, avec journalisation de leur mode de
sélection. Il n'est pas encore validé.

Déployer d'abord en mode parallèle, puis sur quelques emplacements, puis étendre
si les mesures confirment l'amélioration. Versionner les modèles, jeux de données,
caractéristiques et paramètres ; conserver une version de référence et un retour
arrière. Les seuils évoqués (4 semaines, 100 votes d'évaluation, gain de 10 points
de votes utiles) sont des propositions à examiner avec l'incertitude statistique,
pas des conditions d'acceptation approuvées.

## Étapes et questions ouvertes

1. Inventorier le matériel local et les capacités de l'hébergement podcast.
2. Valider identité, motifs, parcours de vote et protocole de synchronisation.
3. Livrer traçabilité et collecte ; traiter le défaut bilingue séparément.
4. Évaluer les caractéristiques et exemples proches sur les premiers retours.
5. Comparer les modèles supervisés puis activer progressivement le meilleur.

À décider : budget de traitement, modèle local de raisonnement, libellés et
possibilité de plusieurs motifs, confirmation des votes, durée de conservation
locale, cadence d'entraînement, comportement en cas d'import indisponible,
exploration et critères de promotion. Aucune de ces questions ne justifie
d'introduire implicitement un service d'IA externe.

## Autorisation actuelle

Après la phase d'analyse et de documentation, l'utilisateur a explicitement
autorisé l'implémentation locale de la boucle complète, ses migrations, tests et
préparatifs de déploiement. Cette demande ne couvre ni publication distante,
ni envoi réel de mails, ni modification de planification pour les tests. Les
fonctions restent désactivées jusqu'à application de la migration et configuration.
Voir `docs/feedback.md` pour les commandes et limites effectives.

### Essai sans confirmation autorisé

Le 12 octobre 2026, l’utilisateur demande explicitement un essai GET avec vote
direct, configurable et réversible. Déployé via `require_confirmation => false`
dans le privé PHP ; true rétablit le comportement initial. Les robots Gmail
peuvent voter et les clics successifs sur des motifs différents ne constituent
pas un historique chronologique fiable ; correction explicite POST disponible.

## Centralisation de la configuration — décision du 12 octobre 2026

Pendant l’inventaire, le dépôt est revenu à la version V2 et les modules feedback
et web ont disparu. L’utilisateur a explicitement confirmé : « Centraliser l’état
actuel ». Cette migration ne rétablit donc pas ces modules ; les descriptions
précédentes du feedback restent historiques et ne décrivent pas le code actuel.

Les neuf configurations actives sont réunies dans `config.yaml` commenté à la
racine. Les valeurs effectives sont conservées, avec suppression d’une répétition
identique de la clé `patched` du glossaire pour permettre les écritures ruamel.
Le catalogue est sous `collection`; secrets et URL publiques restent dans `.env`.
Le lecteur partagé, Docker et les scripts sont adaptés. Les YAML antérieurs sont
sauvegardés dans `output/config-migration/current-baseline/`. Les nouvelles images
doivent être reconstruites avant exploitation ; le pipeline les contrôle avant
ses effets. Aucun mail, podcast, modèle, volume ni planification n’est modifié
pour tester la migration.

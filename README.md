# AI News — étape 51

Version basée sur le briefing.py de l'étape 50, comparé au script fourni.

## Installation
Depuis /opt/ai-news, sauvegarder les fichiers existants, puis copier :
- embeddings/briefing.py -> /opt/ai-news/embeddings/briefing.py
- config/briefing.yaml -> /opt/ai-news/config/briefing.yaml

Les modules watchlist.py et vulnerability_detector.py sont inclus à titre de référence, mais **ne nécessitent pas d'être remplacés** s'ils fonctionnent déjà.
Le fichier watchlists.yaml est inclus à titre d'exemple : conserver vos règles personnalisées.

Le script suppose que /config/semantic_scoring.yaml est monté dans le conteneur (même format que MiniLM V2).
Il ne modifie pas PostgreSQL. Le mode DRY RUN n'écrit pas de briefing.

## Test rapide (sans Qwen)
cd /opt/ai-news
sudo docker compose build embeddings
sudo docker compose run --rm -e BRIEFING_DRY_RUN=1 embeddings python -m embeddings.briefing

Examiner les 10 événements sélectionnés et leur répartition. Si le montage de semantic_scoring.yaml manque, utiliser un volume :
sudo docker compose run --rm -v /opt/ai-news/config:/config:ro -e BRIEFING_DRY_RUN=1 embeddings python -m embeddings.briefing

## Test complet (Qwen + MarianMT)
sudo docker compose run --rm embeddings python -m embeddings.briefing

## Changements
- Exclusion éditoriale des newsletters/récapitulatifs génériques (sans suppression DB).
- Déduplication conservatrice : CVE et ZDI-CAN distincts ne fusionnent pas.
- Même URL canonique ou même titre normalisé fusionnés (sauf conflit d'identifiants).
- Regroupement sémantique seulement avec similarité élevée ET fort recouvrement des mots du titre.
- MiniLM classe chaque article selon les descriptions de semantic_scoring.yaml.
- Quotas souples : sécurité 4, IA 3, réglementation/risques 2, transformation/gaming 1.
- P1 passent avant les quotas, dans la limite de max_events.
- La classification est heuristique : une vérification du résultat reste nécessaire.
- Le score affiché reste historique/boosté et NON le score de similarité MiniLM V2.

## Limites
- Les catégories IA/risques/jeux sont déterminées par similarité aux descriptions et peuvent être imparfaites.
- Le regroupement conservateur peut laisser quelques doublons inter-sources ; c'est préférable à la fusion d'alertes différentes.
- Le script conserve la politique de blacklist de l'étape 50.
- Les articles de newsletters peuvent être exclus même s'ils mentionnent une CVE ; les alertes individuelles restent accessibles via leurs sources.
- Les avis ZDI distincts sont volontairement séparés.
- Si >10 P1, seules les dix premières selon le classement provisoire seront sélectionnées.

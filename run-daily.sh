#!/usr/bin/env bash
set -Eeuo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")"

#exec 9>/run/lock/ai-news.lock
exec 9>/run/ai-news.lock
flock -n 9 || {
    echo "[ERROR] Pipeline déjà en cours"
    exit 1
}

export TZ=Europe/Paris

COMPOSE=(docker compose --profile ai --profile mail)

log() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*"
}

log "Démarrage du pipeline AI News"

# 1. Infrastructure
log "Démarrage PostgreSQL et Ollama"
"${COMPOSE[@]}" up -d --wait postgres ollama

# 2. Collecte
log "Collecte des actualités"
"${COMPOSE[@]}" run --rm -T collector

# 3. Analyse des actualités
log "Analyse des actualités"
"${COMPOSE[@]}" run --rm -T embeddings

# 4. Génération du briefing
log "Génération du briefing"
rm -f output/briefing.md

"${COMPOSE[@]}" run --rm -T \
  -e BRIEFING_OUTPUT=/output/briefing.md \
  embeddings \
  python -m embeddings.briefing

test -s output/briefing.md || {
    log "ERREUR : briefing.md absent ou vide"
    exit 1
}

# 5. Envoi du briefing par mail
log "Envoi du briefing par mail"
"${COMPOSE[@]}" run --rm -T mailer

# 6. Synthèse vocale
log "Génération audio avec Kokoro"

ENGINE=$(awk '/^[[:space:]]*engine[[:space:]]*:/ {
    sub(/^[^:]*:[[:space:]]*/, "")
    sub(/[[:space:]]*#.*/, "")
    gsub(/^[[:space:]]+|[[:space:]]+$/, "")
    print
    exit
}' config/audio.yaml)

if [[ "$ENGINE" != "kokoro" ]]; then
    log "ERREUR : moteur audio '$ENGINE', attendu 'kokoro'"
    exit 1
fi

"${COMPOSE[@]}" run --rm -T audio

# 7. Vérification du MP3
TODAY=$(date +%F)
MP3="output/${TODAY}-briefing.mp3"

test -s "$MP3" || {
    log "ERREUR : MP3 absent ou vide"
    exit 1
}

# 8. Publication
log "Publication du podcast"
"${COMPOSE[@]}" run --rm -T podcast

log "Pipeline terminé avec succès"

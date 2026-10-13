#!/usr/bin/env bash
set -Eeuo pipefail
# Journaliser stdout et stderr, y compris les erreurs précoces et les sous-processus.
LOG_FILE=/var/log/ai-news.log
if [[ ! -e "$LOG_FILE" ]]; then
    (umask 027; touch "$LOG_FILE")
fi
[[ -w "$LOG_FILE" ]] || { echo "Journal inaccessible : $LOG_FILE" >&2; exit 1; }
exec > >(tee -a "$LOG_FILE") 2>&1
trap 'status=$?; echo "[$(TZ=Europe/Paris date "+%Y-%m-%d %H:%M:%S")] Fin du pipeline (code=$status)"; exit "$status"' EXIT
echo "[$(TZ=Europe/Paris date '+%Y-%m-%d %H:%M:%S')] Démarrage du pipeline (pid=$$)"
cd "$(dirname "${BASH_SOURCE[0]}")"
exec 9>/run/ai-news.lock
flock -n 9 || { echo 'Pipeline déjà en cours'; exit 1; }
export TZ=Europe/Paris
COMPOSE=(docker compose --profile ai --profile mail)
log() { echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*"; }
TODAY=$(date +%F)
STATE="output/daily/$TODAY"
mkdir -p "$STATE/en" "$STATE/fr"
# Reprendre les résultats de l'ancien layout sans renvoyer les mails.
for language in en fr; do
    edition="$STATE/$language"
    if [[ ! -s "$edition/briefing.md" && -s "output/.daily/$TODAY/briefing-$language.md" ]]; then
        cp "output/.daily/$TODAY/briefing-$language.md" "$edition/briefing.md"
    fi
    if [[ ! -s "$edition/$TODAY-briefing.mp3" && -s "output/$TODAY-briefing-$language.mp3" ]]; then
        cp "output/$TODAY-briefing-$language.mp3" "$edition/$TODAY-briefing.mp3"
    fi
    for stage in mail audio podcast; do
        if [[ -f "output/.daily/$TODAY/$language-$stage" && ! -f "$edition/$stage.done" ]]; then
            cp "output/.daily/$TODAY/$language-$stage" "$edition/$stage.done"
        fi
    done
done
if [[ -f "output/.daily/$TODAY/generated" && -s "$STATE/en/briefing.md" && -s "$STATE/fr/briefing.md" ]]; then
    touch "$STATE/generated"
fi
"${COMPOSE[@]}" up -d --wait postgres ollama

# Une seule sélection multilingue et une seule série de synthèses Qwen.
# Les deux Markdown sont figés pour les reprises de la même journée.
if [[ ! -f "$STATE/generated" ]]; then
    log 'Import des retours utilisateur'
    if ! "${COMPOSE[@]}" run --rm -T collector python -m feedback.importer; then
        log 'Import indisponible : poursuite avec les retours locaux, aucune purge non confirmée'
    fi
    log 'Collecte et scoring'
    "${COMPOSE[@]}" run --rm -T collector
    "${COMPOSE[@]}" run --rm -T collector python -m app.scorer
    log 'Génération des éditions EN puis FR'
    "${COMPOSE[@]}" run --rm -T \
        -e BRIEFING_DAILY_DIR="/output/daily/$TODAY" \
        embeddings python -m embeddings.briefing
    for language in en fr; do
        test -s "$STATE/$language/briefing.md"
    done
    touch "$STATE/generated"
fi
ENGINE=$(awk '/^[[:space:]]*engine[[:space:]]*:/ {print $2; exit}' config/audio.yaml)
[[ "$ENGINE" == kokoro ]] || { log 'Le pipeline quotidien exige Kokoro'; exit 1; }

for language in en fr; do
    log "Édition $language"
    edition="$STATE/$language"
    container_dir="/output/daily/$TODAY/$language"
    if [[ ! -f "$edition/mail.done" ]]; then
        "${COMPOSE[@]}" run --rm -T mailer --language "$language" \
            --input "$container_dir/briefing.md" --preview "$container_dir/briefing-email.html"
        touch "$edition/mail.done"
    elif [[ ! -s "$edition/briefing-email.html" ]]; then
        "${COMPOSE[@]}" run --rm -T mailer --language "$language" \
            --input "$container_dir/briefing.md" --preview "$container_dir/briefing-email.html" --dry-run
    fi
    if [[ ! -f "$edition/audio.done" || ! -s "$edition/$TODAY-briefing.mp3" ]]; then
        "${COMPOSE[@]}" run --rm -T audio --config /config/audio.yaml \
            --language "$language" --input "$container_dir/briefing.md" \
            --output "$container_dir/briefing.mp3" --date "$TODAY"
        test -s "$edition/$TODAY-briefing.mp3"
        touch "$edition/audio.done"
    fi
    if [[ ! -f "$edition/podcast.done" ]]; then
        "${COMPOSE[@]}" run --rm -T podcast --language "$language" --date "$TODAY"
        touch "$edition/podcast.done"
    elif [[ ! -s "$edition/podcast.xml" ]]; then
        "${COMPOSE[@]}" run --rm -T podcast --language "$language" --date "$TODAY" --dry-run
    fi
done
log 'Pipeline EN puis FR terminé'

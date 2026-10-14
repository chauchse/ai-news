#!/usr/bin/env bash
set -Eeuo pipefail
# Une date explicite prépare localement ; les effets externes sont opt-in.
EDITION_DATE=""
PREPARE=0
PUBLISH=0
SEND_MAIL=0
HEALTHCHECK=0
HEALTHCHECK_DRY_RUN=0
while (($#)); do
    case "$1" in
        --date) [[ $# -ge 2 ]] || { echo 'Date manquante' >&2; exit 2; }; [[ -n "$2" ]] || { echo 'Date vide' >&2; exit 2; }; EDITION_DATE="$2"; shift 2 ;;
        --prepare) PREPARE=1; shift ;;
        --publish) PUBLISH=1; shift ;;
        --send-mail) SEND_MAIL=1; shift ;;
        --healthcheck) HEALTHCHECK=1; shift ;;
        --dry-run) HEALTHCHECK_DRY_RUN=1; shift ;;
        *) echo "Argument inconnu : $1" >&2; exit 2 ;;
    esac
done
if ((HEALTHCHECK_DRY_RUN && !HEALTHCHECK)); then
    echo '--dry-run nécessite --healthcheck' >&2; exit 2
fi
if ((HEALTHCHECK && (PREPARE || PUBLISH || SEND_MAIL))); then
    echo '--healthcheck ne se combine pas avec les options de publication/préparation' >&2; exit 2
fi
if [[ -n "$EDITION_DATE" ]]; then
    python3 - "$EDITION_DATE" "$HEALTHCHECK" <<'PYDATE'
import sys
from datetime import date, datetime
from zoneinfo import ZoneInfo
value = sys.argv[1]
day = date.fromisoformat(value)
today = datetime.now(ZoneInfo('Europe/Paris')).date()
if day.isoformat() != value or day > today or (day == today and sys.argv[2] != '1'):
    raise SystemExit('La date doit être passée, au format AAAA-MM-JJ')
PYDATE
    PREPARE=1
elif ((PUBLISH || SEND_MAIL)); then
    echo '--publish et --send-mail nécessitent --date' >&2; exit 2
fi
if ((HEALTHCHECK_DRY_RUN)); then
    # Inspection only: bypass log creation, locks and all repair commands.
    cd "$(dirname "${BASH_SOURCE[0]}")"
    date_args=()
    [[ -z "$EDITION_DATE" ]] || date_args=(--date "$EDITION_DATE")
    docker compose --profile mail run --rm -T --no-deps --entrypoint python mailer \
        -m mailer.healthcheck "${date_args[@]}" --dry-run
    echo 'Politique de reprise : date, puis étapes autorisées (pas une liste d’actions garanties).'
    docker compose --profile mail run --rm -T --no-deps --entrypoint python mailer \
        -m mailer.healthcheck "${date_args[@]}" --repair-plan
    exit 0
fi
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
flock -n 9 || {
    if ((HEALTHCHECK)); then echo 'Contrôle différé : pipeline déjà en cours'; exit 0; fi
    echo 'Pipeline déjà en cours'; exit 1;
}
export TZ=Europe/Paris
COMPOSE=(docker compose --profile ai --profile mail)
log() { echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*"; }
if ((HEALTHCHECK)); then
    # Docker stays on the host; the mailer container only inspects and alerts.
    health_log=$("${COMPOSE[@]}" run --rm -T --no-deps --entrypoint python mailer \
        -m project_config healthcheck log_path)
    health_daily_dir=$("${COMPOSE[@]}" run --rm -T --no-deps --entrypoint python mailer \
        -m project_config healthcheck daily_dir)
    [[ "$health_daily_dir" == /output/daily ]] || {
        log 'Autocorrection incompatible avec un daily_dir différent du chemin du pipeline'; exit 1;
    }
    log_mount=()
    [[ ! -f "$health_log" ]] || log_mount=(-v "$health_log:$health_log:ro")
    date_args=()
    [[ -z "$EDITION_DATE" ]] || date_args=(--date "$EDITION_DATE")
    plan=$("${COMPOSE[@]}" run --rm -T --no-deps --entrypoint python mailer \
        -m mailer.healthcheck "${date_args[@]}" --repair-plan)
    mapfile -t repair_plan <<< "$plan"
    TODAY=${repair_plan[0]}
    [[ "$TODAY" =~ ^[0-9]{4}-[0-9]{2}-[0-9]{2}$ ]] || { log 'Plan de reprise invalide'; exit 1; }
    STATE="output/daily/$TODAY"
    allowed() {
        local step
        for step in "${repair_plan[@]:1}"; do
            [[ "$step" != "$1" ]] || return 0
        done
        return 1
    }
    finish_healthcheck() {
        local status=$? alert_status=0
        trap - EXIT
        "${COMPOSE[@]}" run --rm -T --no-deps "${log_mount[@]}" \
            --entrypoint python mailer -m mailer.healthcheck --date "$TODAY" --send \
            --repair-report "Reprise des étapes existantes selon leurs marqueurs (code=$status)." || alert_status=$?
        ((status != 0)) || status=$alert_status
        log "Fin du contrôle et de la reprise (code=$status)"
        exit "$status"
    }
    trap finish_healthcheck EXIT
    if ((${#repair_plan[@]} == 1)); then
        log 'Aucune reprise autorisée ou nécessaire'
        exit 0
    fi
    # Reuse the ordinary pipeline below. Only missing completion markers allow
    # their corresponding stage to run; no alternate translation path exists.
    PREPARE=1
    SEND_MAIL=0
    PUBLISH=0
    allowed mail && SEND_MAIL=1
    allowed podcast && PUBLISH=1
    EDITION_DATE=$TODAY
    [[ "$TODAY" != "$(date +%F)" ]] || EDITION_DATE=''
fi
# Refuser les anciennes images avant toute collecte, synthèse ou envoi.
services=(collector embeddings audio podcast mailer)
if ((HEALTHCHECK)); then
    services=(mailer)
    if allowed markdown; then services+=(embeddings); fi
    if allowed audio; then services+=(audio); fi
    if allowed podcast; then services+=(podcast); fi
fi
for service in "${services[@]}"; do
    if ! "${COMPOSE[@]}" run --rm -T --no-deps --entrypoint python "$service" \
        -c 'from project_config import load_section; load_section("audio")'; then
        log 'Images incompatibles : reconstruire collector embeddings audio podcast mailer avant le lancement'
        exit 1
    fi
done
if (( !HEALTHCHECK )) || allowed audio; then
    ENGINE=$("${COMPOSE[@]}" run --rm -T --no-deps --entrypoint python audio -m project_config audio engine)
    [[ "$ENGINE" == kokoro ]] || { log 'Le pipeline quotidien exige Kokoro'; exit 1; }
fi
if (( !HEALTHCHECK )); then
    TODAY=${EDITION_DATE:-$(date +%F)}
fi
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
if (( !HEALTHCHECK )); then
    "${COMPOSE[@]}" up -d --wait postgres ollama
fi

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
    fi
    if (( !HEALTHCHECK )) && [[ -n "$EDITION_DATE" && ( -s "$STATE/en/briefing.md" || -s "$STATE/fr/briefing.md" ) ]]; then
        log 'Markdown existant sans marqueur generated : vérifier les deux éditions avant toute reprise'
        exit 1
    fi
    log 'Génération des éditions EN puis FR'
    "${COMPOSE[@]}" run --rm -T \
        -e BRIEFING_DATE="$EDITION_DATE" \
        -e BRIEFING_DAILY_DIR="/output/daily/$TODAY" \
        embeddings python -m embeddings.briefing
    for language in en fr; do
        test -s "$STATE/$language/briefing.md"
    done
    touch "$STATE/generated"
fi
if ((HEALTHCHECK)); then
    for language in en fr; do
        [[ -s "$STATE/$language/briefing.md" ]] || {
            log "État incohérent : generated présent mais Markdown $language absent ou vide"; exit 1;
        }
        if [[ ! -f "$STATE/$language/audio.done" && -f "$STATE/$language/podcast.done" ]]; then
            log "État incohérent : podcast $language terminé mais marqueur audio absent"; exit 1
        fi
    done
fi
for language in en fr; do
    log "Édition $language"
    edition="$STATE/$language"
    container_dir="/output/daily/$TODAY/$language"
    if ((PREPARE && !SEND_MAIL)) && { (( !HEALTHCHECK )) || { allowed preview && [[ ! -s "$edition/briefing-email.html" ]]; }; }; then
        "${COMPOSE[@]}" run --rm -T mailer --language "$language" \
            --input "$container_dir/briefing.md" --preview "$container_dir/briefing-email.html" --date "$TODAY" --dry-run
    elif ((HEALTHCHECK)) && ! allowed mail; then
        log "Mail $language : reprise non autorisée par la configuration"
    elif [[ ! -f "$edition/mail.done" ]]; then
        "${COMPOSE[@]}" run --rm -T mailer --language "$language" \
            --input "$container_dir/briefing.md" --preview "$container_dir/briefing-email.html" --date "$TODAY"
        touch "$edition/mail.done"
    elif [[ ! -s "$edition/briefing-email.html" ]]; then
        "${COMPOSE[@]}" run --rm -T mailer --language "$language" \
            --input "$container_dir/briefing.md" --preview "$container_dir/briefing-email.html" --dry-run
    fi
    if { (( !HEALTHCHECK )) || allowed audio; } && \
       { [[ ! -f "$edition/audio.done" ]] || { (( !HEALTHCHECK )) && [[ ! -s "$edition/$TODAY-briefing.mp3" ]]; }; }; then
        "${COMPOSE[@]}" run --rm -T audio --config /app/config.yaml \
            --language "$language" --input "$container_dir/briefing.md" \
            --output "$container_dir/briefing.mp3" --date "$TODAY"
        test -s "$edition/$TODAY-briefing.mp3"
        touch "$edition/audio.done"
    fi
    if ((PREPARE && !PUBLISH)); then
        log "Podcast $language préparé localement ; aucun transfert"
    elif [[ ! -f "$edition/podcast.done" ]]; then
        "${COMPOSE[@]}" run --rm -T podcast --language "$language" --date "$TODAY"
        touch "$edition/podcast.done"
    elif [[ ! -s "$edition/podcast.xml" ]]; then
        "${COMPOSE[@]}" run --rm -T podcast --language "$language" --date "$TODAY" --dry-run
    fi
done
log 'Pipeline EN puis FR terminé'

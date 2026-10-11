#!/usr/bin/env bash
# Initialise une installation locale, sans collecte, envoi de mail ni publication.
set -Eeuo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")"

usage() {
  cat <<'HELP'
Usage: ./init-repo.sh [--models-only] [--with-piper]
  Sans option : dossiers, .env, Kokoro, images, PostgreSQL, SQL, Qwen,
                MiniLM et MarianMT. PostgreSQL et Ollama restent démarrés.
  --models-only : télécharge uniquement Kokoro (et Piper si demandé).
  --with-piper  : ajoute les voix Piper FR/EN de config/audio.yaml.
Les fichiers locaux existants ne sont pas remplacés. Internet est nécessaire.
HELP
}
models_only=false
with_piper=false
for arg in "$@"; do
  case "$arg" in
    --models-only) models_only=true ;;
    --with-piper) with_piper=true ;;
    -h|--help) usage; exit 0 ;;
    *) usage >&2; exit 2 ;;
  esac
done
command -v curl >/dev/null || { echo 'Installer curl avant de continuer.' >&2; exit 1; }
if ! "$models_only"; then
  docker compose version >/dev/null
  docker info >/dev/null
fi
mkdir -p output podcast/state audio/models/kokoro/voices audio/models/xtts audio/voices
if ! "$models_only" && [[ ! -e .env ]]; then
  # Mot de passe aléatoire compatible avec les URL PostgreSQL et Compose.
  password=$(od -An -N24 -tx1 /dev/urandom | tr -d ' \n')
  (umask 077; cat > .env <<ENV
POSTGRES_DB=ai_news
POSTGRES_USER=ai_news
POSTGRES_PASSWORD=$password
POSTGRES_PORT=5432
PODCAST_KNOWN_HOSTS=./podcast/known_hosts
PODCAST_SFTP_HOST=
PODCAST_SFTP_PORT=22
PODCAST_SFTP_REMOTE_PATH=
PODCAST_SITE_URL=
PODCAST_EMAIL=
PODCAST_SFTP_USER=
PODCAST_SFTP_PASSWORD=
SENDER=
RECIPIENT=
SMTP_USERNAME=
SMTP_PASSWORD=
ENV
  )
  echo '.env créé ; compléter SMTP/SFTP avant les envois.'
fi
# Ne pas remplacer un fichier de clés existant et ne pas accepter de clé à l'aveugle.
if [[ ! -e podcast/known_hosts ]]; then
  touch podcast/known_hosts
fi

download() {
  local url=$1 target=$2
  if [[ -s "$target" ]]; then
    echo "Déjà présent : $target"
    return
  fi
  echo "Téléchargement : $target"
  # Le fichier final n'apparaît qu'après un téléchargement réussi.
  curl --fail --location --retry 3 --connect-timeout 30 \
    --output "$target.part" "$url"
  test -s "$target.part"
  mv "$target.part" "$target"
}
base=https://huggingface.co/hexgrad/Kokoro-82M/resolve/main
download "$base/config.json" audio/models/kokoro/config.json
download "$base/kokoro-v1_0.pth" audio/models/kokoro/kokoro-v1_0.pth
for voice in ff_siwis af_heart bf_emma; do
  download "$base/voices/$voice.pt" "audio/models/kokoro/voices/$voice.pt"
done
if "$with_piper"; then
  base=https://huggingface.co/rhasspy/piper-voices/resolve/main
  for spec in fr/fr_FR/tom/medium/fr_FR-tom-medium en/en_US/lessac/high/en_US-lessac-high; do
    for ext in onnx onnx.json; do
      download "$base/$spec.$ext" "audio/models/${spec##*/}.$ext"
    done
  done
fi
if "$models_only"; then
  echo 'Modèles audio prêts.'
  exit 0
fi
compose=(docker compose --profile ai --profile mail)
"${compose[@]}" config --quiet
"${compose[@]}" build collector embeddings audio podcast mailer
"${compose[@]}" up -d --wait --wait-timeout 180 postgres ollama
for sql in app/sql/00*.sql; do
  echo "Application : $sql"
  "${compose[@]}" exec -T postgres sh -c \
    'psql -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d "$POSTGRES_DB"' < "$sql"
done
"${compose[@]}" exec -T ollama ollama pull qwen2.5:3b
# Réutilise les mêmes constantes et le même volume que le briefing.
"${compose[@]}" run --rm -T --no-deps embeddings python -c '
from sentence_transformers import SentenceTransformer
from transformers import MarianTokenizer, MarianMTModel
from embeddings.worker import MODEL_NAME
from embeddings.briefing import TRANSLATOR
SentenceTransformer(MODEL_NAME, device="cpu")
MarianTokenizer.from_pretrained(TRANSLATOR)
MarianMTModel.from_pretrained(TRANSLATOR)
'
echo 'Initialisation terminée. Configurer SMTP/SFTP avant de lancer run-daily.sh.'

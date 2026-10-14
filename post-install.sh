#!/usr/bin/env bash
# Installe le lancement quotidien ; ne lance pas le pipeline immédiatement.
set -Eeuo pipefail

if [[ ${1:-} == --help || ${1:-} == -h ]]; then
  echo 'Usage : sudo ./post-install.sh [--healthcheck-only]'
  echo 'Installe ai-news.service et /etc/cron.d/ai-news (tous les jours à 04:00, heure du serveur).'
  echo '--healthcheck-only : installe uniquement le contrôle horaire et active les alertes SMTP.'
  exit 0
fi
healthcheck_only=0
if [[ ${1:-} == --healthcheck-only ]]; then healthcheck_only=1; shift; fi
if (($#)); then echo 'Usage : sudo ./post-install.sh [--healthcheck-only]' >&2; exit 2; fi
if ((EUID != 0)); then echo 'Exécuter avec sudo : sudo ./post-install.sh' >&2; exit 1; fi
repo_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)
# Les chemins sont interpolés dans la syntaxe systemd.
if [[ "$repo_dir" =~ [[:space:]\%\"\\] ]]; then
  echo 'Installer le dépôt dans un chemin sans espaces, %, guillemets ni antislashs.' >&2
  exit 1
fi
required_commands=(systemctl docker flock install mktemp)
if ((healthcheck_only)); then
  required_commands+=(timedatectl)
else
  required_commands+=(tee logrotate)
fi
for cmd in "${required_commands[@]}"; do
  command -v "$cmd" >/dev/null || { echo "Commande manquante : $cmd" >&2; exit 1; }
done
[[ -x /usr/bin/systemctl ]] || { echo '/usr/bin/systemctl introuvable.' >&2; exit 1; }
[[ -f "$repo_dir/run-daily.sh" && -f "$repo_dir/.env" ]] || {
  echo 'Lancer ./init-repo.sh et configurer .env avant la post-installation.' >&2; exit 1;
}
cd "$repo_dir"
docker compose --profile ai --profile mail config --quiet
systemctl cat docker.service >/dev/null
if ((healthcheck_only)); then
  # The mailer image must first be rebuilt by the operator.
  calendar=$(docker compose --profile mail run --rm -T --no-deps \
    --entrypoint python mailer -m project_config healthcheck calendar)
  log_path=$(docker compose --profile mail run --rm -T --no-deps \
    --entrypoint python mailer -m project_config healthcheck log_path)
  health_timezone=$(docker compose --profile mail run --rm -T --no-deps \
    --entrypoint python mailer -m project_config healthcheck timezone)
  health_daily_dir=$(docker compose --profile mail run --rm -T --no-deps \
    --entrypoint python mailer -m project_config healthcheck daily_dir)
  [[ "$health_daily_dir" == /output/daily ]] || {
    echo 'Autocorrection : healthcheck.daily_dir doit correspondre au pipeline (/output/daily).' >&2; exit 1;
  }
  [[ "$health_timezone" == "$(timedatectl show --property=Timezone --value)" ]] || {
    echo 'Aligner healthcheck.timezone avec le fuseau du cron quotidien du serveur.' >&2; exit 1;
  }
  [[ -f "$log_path" && "$log_path" =~ ^/[a-zA-Z0-9_./-]+$ ]] || {
    echo 'Journal absent ou chemin incompatible avec le montage systemd.' >&2; exit 1;
  }
  [[ "$calendar" != *$'\n'* && "$calendar" != *$'\r'* && "$calendar" != *%* ]] || exit 1
  [[ "$repo_dir" != *'$'* ]] || { echo 'Chemin du dépôt incompatible avec systemd.' >&2; exit 1; }
  tmp_dir=$(mktemp -d)
  trap 'rm -rf -- "$tmp_dir"' EXIT
  cat > "$tmp_dir/ai-news-healthcheck.service" <<UNIT
[Unit]
Description=AI News - Contrôle des étapes incomplètes et alerte SMTP
Wants=docker.service
After=docker.service

[Service]
Type=oneshot
User=root
WorkingDirectory=$repo_dir
ExecStart=/bin/bash $repo_dir/run-daily.sh --healthcheck
Environment="PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
TimeoutStartSec=0
StandardOutput=journal
StandardError=journal
UNIT
  cat > "$tmp_dir/ai-news-healthcheck.timer" <<UNIT
[Unit]
Description=AI News - Surveillance périodique des éditions

[Timer]
OnCalendar=$calendar
Persistent=false
Unit=ai-news-healthcheck.service

[Install]
WantedBy=timers.target
UNIT
  if command -v systemd-analyze >/dev/null; then
    systemd-analyze verify "$tmp_dir/ai-news-healthcheck.service" "$tmp_dir/ai-news-healthcheck.timer"
  fi
  for name in ai-news-healthcheck.service ai-news-healthcheck.timer; do
    target="/etc/systemd/system/$name"
    if [[ -e "$target" ]] && ! cmp -s "$tmp_dir/$name" "$target"; then
      cp -p -- "$target" "$target.bak.$(date +%Y%m%d%H%M%S).$$"
    fi
    install -o root -g root -m 0644 "$tmp_dir/$name" "$target"
  done
  systemctl daemon-reload
  systemctl enable --now ai-news-healthcheck.timer
  echo 'Contrôle installé et timer activé : autocorrection selon config.yaml, puis alerte SMTP si nécessaire.'
  echo 'Étapes reprises sous le verrou quotidien ; aucune collecte ni scoring relancé.'
  exit 0
fi
cron_service=''
for candidate in cron.service crond.service; do
  if systemctl cat "$candidate" >/dev/null 2>&1; then
    cron_service=$candidate
    break
  fi
done
[[ -n "$cron_service" ]] || {
  echo 'Installer cron (Debian/Ubuntu : sudo apt install cron), puis relancer.' >&2; exit 1;
}

tmp_dir=$(mktemp -d)
trap 'rm -rf -- "$tmp_dir"' EXIT
cat > "$tmp_dir/ai-news.service" <<UNIT
[Unit]
Description=AI News - Pipeline quotidien
Wants=docker.service
After=docker.service

[Service]
Type=oneshot
User=root
WorkingDirectory=$repo_dir
ExecStart=/bin/bash $repo_dir/run-daily.sh
Environment="PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
TimeoutStartSec=0
StandardOutput=journal
StandardError=journal
UNIT
cat > "$tmp_dir/ai-news.logrotate" <<'ROTATE'
/var/log/ai-news.log {
    daily
    rotate 7
    maxage 7
    missingok
    notifempty
    compress
    delaycompress
    copytruncate
    su root root
}
ROTATE
logrotate --debug "$tmp_dir/ai-news.logrotate"
if [[ ! -e /var/log/ai-news.log ]]; then
    install -o root -g root -m 0640 /dev/null /var/log/ai-news.log
fi
chown root:root /var/log/ai-news.log
chmod 0640 /var/log/ai-news.log
cat > "$tmp_dir/ai-news.cron" <<'CRON'
SHELL=/bin/sh
PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
0 4 * * * root /usr/bin/systemctl start --no-block ai-news.service
CRON
if command -v systemd-analyze >/dev/null; then
  systemd-analyze verify "$tmp_dir/ai-news.service"
fi
# Préserver les versions précédentes si le contenu change.
for target in /etc/systemd/system/ai-news.service /etc/cron.d/ai-news /etc/logrotate.d/ai-news; do
  source_file="$tmp_dir/ai-news.service"
  [[ "$target" != /etc/cron.d/ai-news ]] || source_file="$tmp_dir/ai-news.cron"
  [[ "$target" != /etc/logrotate.d/ai-news ]] || source_file="$tmp_dir/ai-news.logrotate"
  if [[ -e "$target" ]] && ! cmp -s "$source_file" "$target"; then
    cp -p -- "$target" "$target.bak.$(date +%Y%m%d%H%M%S).$$"
  fi
  install -o root -g root -m 0644 "$source_file" "$target"
done
systemctl daemon-reload
systemctl enable --now "$cron_service"
# Selon la distribution, logrotate est lancé par ce timer ou par cron.daily.
if systemctl cat logrotate.timer >/dev/null 2>&1; then
    systemctl enable --now logrotate.timer
elif [[ ! -x /etc/cron.daily/logrotate ]]; then
    echo 'Attention : activer une exécution quotidienne de logrotate sur cette distribution.' >&2
fi
echo 'Service et cron installés. Prochain lancement à 04:00 (heure du serveur).'
echo 'Heure du serveur :'
timedatectl show --property=Timezone --value
echo 'Lancement manuel : sudo systemctl start --no-block ai-news.service'
echo 'Journaux : sudo tail -f /var/log/ai-news.log (également dans journalctl)'

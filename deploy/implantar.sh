#!/usr/bin/env bash
# Implanta o agent-grolla no Docker da própria máquina, a partir deste clone e de um arquivo de
# implantação (modelo em deploy/implantacao.exemplo.yaml), que tem tudo do ambiente:
#   1. confere a máquina e o arquivo;
#   2. atualiza o código (git pull, ou --local para usar a árvore como está);
#   3. constrói as imagens grolla-* (a imagem em uso fica como :anterior);
#   4. gera e valida a configuração da instância a partir do arquivo;
#   5. faz o backup do banco;
#   6. para o agente, troca as imagens e sobe de novo;
#   7. confere a saúde da api e do worker e, se falhar, volta para as imagens anteriores.
# O banco nunca é apagado: nada de `down`, nada de volume removido.
#
#   git clone https://github.com/viniciusfaleiro/grolla-agent.git && cd grolla-agent
#   cp deploy/implantacao.exemplo.yaml ~/grolla-implantacao.yaml   # preencha uma vez
#   deploy/implantar.sh
set -euo pipefail

REPO="$(cd "$(dirname "$0")/.." && pwd)"
CONFIG="$HOME/grolla-implantacao.yaml"
REF=""
LOCAL=0
KEEP_BACKUPS=5
HEALTH_TIMEOUT=180

usage() {
  cat <<'EOF'
Uso: deploy/implantar.sh [opções]
  --config <arquivo>  arquivo de implantação (padrão: ~/grolla-implantacao.yaml)
  --ref <ref>         branch, tag ou commit a implantar (padrão: o branch atual)
  --local             implanta a árvore como está, sem git pull (aceita mudança não commitada)
  -h, --help          esta ajuda
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --config) CONFIG="$2"; shift 2 ;;
    --ref) REF="$2"; shift 2 ;;
    --local) LOCAL=1; shift ;;
    -h | --help) usage; exit 0 ;;
    *) echo "opção desconhecida: $1" >&2; usage >&2; exit 2 ;;
  esac
done

# ---------- aparência ----------
TTY=0
[[ -t 1 ]] && TTY=1
if ((TTY)); then
  B=$'\e[1m' D=$'\e[2m' R=$'\e[31m' G=$'\e[32m' Y=$'\e[33m' BL=$'\e[34m' C=$'\e[36m' N=$'\e[0m'
else
  B="" D="" R="" G="" Y="" BL="" C="" N=""
fi
TOTAL_STEPS=8
STEP=0
STARTED=$SECONDS
LOG="$(mktemp -t grolla-implantacao.XXXXXX.log)"
STAGE="$(mktemp -d -t grolla-instancia.XXXXXX)"
trap 'rm -rf "$STAGE"' EXIT

elapsed() { local s=$(($1)); printf "%d:%02d" $((s / 60)) $((s % 60)); }
clear_line() { if ((TTY)); then printf "\r\e[2K"; fi; }
repeat() { local i out=""; for ((i = 0; i < $2; i++)); do out+="$1"; done; printf "%s" "$out"; }

banner() {
  printf "\n%s" "$BL"
  cat <<'EOF'
   ┌───────────────────────────────────────────────┐
   │   (•‿•)  Grolla · implantação                 │
   │   /|_|\  build no Docker local, banco a salvo │
   └───────────────────────────────────────────────┘
EOF
  printf "%s\n" "$N"
}

step() {
  STEP=$((STEP + 1))
  local width=24 fill=$(((STEP - 1) * 24 / TOTAL_STEPS))
  local pad=$((26 - ${#1})) # ${#} conta letras, o printf %-26s contaria bytes
  printf "\n%s[%d/%d]%s %s%s%*s%s %s%s%s%s %s%s%s\n" "$C" "$STEP" "$TOTAL_STEPS" "$N" "$B" "$1" "$pad" "" "$N" \
    "$G" "$(repeat █ "$fill")" "$D" "$(repeat ░ $((width - fill)))" "$N" "$(elapsed $((SECONDS - STARTED)))" "$N"
}
info() { printf "      %s\n" "$*"; }
ok() { printf "      %s✔%s %s\n" "$G" "$N" "$*"; }
warn() { printf "      %s!%s %s\n" "$Y" "$N" "$*"; }

fail() {
  clear_line
  printf "\n      %s✖ %s%s\n" "$R" "$*" "$N" >&2
  if [[ -s "$LOG" ]]; then
    printf "\n%s      últimas linhas do log (%s):%s\n" "$D" "$LOG" "$N" >&2
    tail -n 20 "$LOG" | sed 's/^/        /' >&2
  fi
  exit 1
}

# Roda um comando longo com spinner e tempo; a saída vai para o log.
run() {
  local label="$1"; shift
  local frames=(⠋ ⠙ ⠹ ⠸ ⠼ ⠴ ⠦ ⠧ ⠇ ⠏) i=0 start=$SECONDS
  echo "\$ $*" >> "$LOG"
  "$@" >> "$LOG" 2>&1 &
  local pid=$!
  if ((TTY)); then
    while kill -0 "$pid" 2> /dev/null; do
      printf "\r      %s%s%s %s %s%s%s " "$C" "${frames[i++ % ${#frames[@]}]}" "$N" "$label" "$D" "$(elapsed $((SECONDS - start)))" "$N"
      sleep 0.2
    done
    clear_line
  fi
  if wait "$pid"; then
    ok "$label ${D}$(elapsed $((SECONDS - start)))${N}"
  else
    fail "$label falhou"
  fi
}

banner

# ---------- 1. pré-checagens ----------
step "Conferindo a máquina"
# O que falta vira uma lista de comandos de instalação para o sistema desta máquina.
OS_ID=$(sed -n 's/^ID=//p' /etc/os-release 2> /dev/null | tr -d '"')
OS_LIKE=$(sed -n 's/^ID_LIKE=//p' /etc/os-release 2> /dev/null | tr -d '"')
COMPOSE_URL="https://github.com/docker/compose/releases/download/v2.29.7/docker-compose-linux-$(uname -m)"
BUILDX_URL="https://github.com/docker/buildx/releases/download/v0.17.1/buildx-v0.17.1.linux-$([[ $(uname -m) == aarch64 ]] && echo arm64 || echo amd64)"
plugin_install() { # $1 = nome do plugin, $2 = URL
  printf 'sudo mkdir -p /usr/local/lib/docker/cli-plugins && sudo curl -fsSL %s -o /usr/local/lib/docker/cli-plugins/docker-%s && sudo chmod +x /usr/local/lib/docker/cli-plugins/docker-%s' "$2" "$1" "$1"
}
suggest() { # $1 = o que falta: git, docker, compose, buildx, daemon, grupo
  case "$1:$OS_ID:$OS_LIKE" in
    git:amzn:* | git:fedora:* | git:rhel:* | git:*:*rhel* | git:*:*fedora*) echo "sudo dnf install -y git" ;;
    git:ubuntu:* | git:debian:* | git:*:*debian*) echo "sudo apt-get update && sudo apt-get install -y git" ;;
    docker:amzn:*) echo "sudo dnf install -y docker && sudo systemctl enable --now docker && sudo usermod -aG docker \$USER" ;;
    docker:ubuntu:* | docker:debian:* | docker:*:*debian*) echo "sudo apt-get update && sudo apt-get install -y docker.io docker-compose-v2 docker-buildx && sudo usermod -aG docker \$USER" ;;
    docker:*) echo "curl -fsSL https://get.docker.com | sudo sh && sudo usermod -aG docker \$USER" ;;
    compose:ubuntu:* | compose:debian:* | compose:*:*debian*) echo "sudo apt-get install -y docker-compose-v2" ;;
    compose:*) plugin_install compose "$COMPOSE_URL" ;;
    buildx:ubuntu:* | buildx:debian:* | buildx:*:*debian*) echo "sudo apt-get install -y docker-buildx" ;;
    buildx:*) plugin_install buildx "$BUILDX_URL" ;;
    daemon:*) echo "sudo systemctl enable --now docker" ;;
    grupo:*) echo "sudo usermod -aG docker \$USER   # depois saia e entre de novo na sessão" ;;
  esac
}
MISSING=()
tool_row() { # $1 = rótulo, $2 = versão ou vazio, $3 = obrigatório (1) ou recomendado (0), $4 = chave da sugestão
  if [[ -n "$2" ]]; then
    printf "      %s✔%s %-22s %s%s%s\n" "$G" "$N" "$1" "$D" "$2" "$N"
  elif (($3)); then
    printf "      %s✖%s %-22s %sfalta%s\n" "$R" "$N" "$1" "$R" "$N"
    MISSING+=("$4")
  else
    printf "      %s!%s %-22s %srecomendado%s\n" "$Y" "$N" "$1" "$Y" "$N"
    MISSING+=("$4")
  fi
}
version_of() { "$@" 2> /dev/null | grep -oE '[0-9]+\.[0-9]+(\.[0-9]+)?' | head -1 || true; }
has_docker=$(command -v docker > /dev/null && echo 1 || echo 0)
tool_row "git" "$(version_of git --version)" 1 git
tool_row "docker" "$( ((has_docker)) && version_of docker --version)" 1 docker
if ((has_docker)); then
  compose_version=$(version_of docker compose version)
  tool_row "docker compose (v2)" "$compose_version" 1 compose
  if [[ -n "$compose_version" ]] && [[ "$(printf '%s\n2.20\n' "$compose_version" | sort -V | head -1)" != 2.20 ]]; then
    printf "        %so compose %s é antigo: precisa do 2.20 ou mais novo%s\n" "$Y" "$compose_version" "$N"
    MISSING+=(compose)
  fi
  tool_row "docker buildx" "$(version_of docker buildx version)" 0 buildx
  if docker info > /dev/null 2>&1; then
    tool_row "acesso ao Docker" "daemon rodando" 1 daemon
  elif [[ -S /var/run/docker.sock ]] && ! id -nG | grep -qw docker; then
    tool_row "acesso ao Docker" "" 1 grupo
  else
    tool_row "acesso ao Docker" "" 1 daemon
  fi
fi
for tool in curl sha256sum find; do
  command -v "$tool" > /dev/null || fail "falta o $tool (vem com o sistema; instale o pacote coreutils/findutils/curl)"
done
if ((${#MISSING[@]})); then
  required_missing=0
  for item in "${MISSING[@]}"; do [[ "$item" != buildx ]] && required_missing=1; done
  printf "\n      %sPara instalar o que falta nesta máquina (%s):%s\n" "$B" "${OS_ID:-sistema desconhecido}" "$N"
  printf '%s\n' "${MISSING[@]}" | awk '!seen[$0]++' | while read -r item; do
    printf "        %s\n" "$(suggest "$item")"
  done
  if ((required_missing)); then
    printf "\n      Depois de instalar, rode o script de novo.\n"
    exit 1
  fi
  warn "sem o buildx, o Docker usa o construtor antigo (funciona, com aviso)"
fi
[[ -f "$CONFIG" ]] || fail "não achei o arquivo de implantação $CONFIG (modelo: deploy/implantacao.exemplo.yaml)"
CONFIG="$(cd "$(dirname "$CONFIG")" && pwd)/$(basename "$CONFIG")"
[[ "$(stat -c %a "$CONFIG")" == 600 ]] || warn "o arquivo tem segredos: rode chmod 600 $CONFIG"
free_gb=$(df -Pk "$(docker info -f '{{.DockerRootDir}}')" | awk 'NR==2 {print int($4 / 1048576)}')
((free_gb >= 4)) || fail "só ${free_gb} GB livres para o Docker; libere espaço (as imagens pedem uns 4 GB)"
ok "${free_gb} GB livres para o Docker"
ok "arquivo de implantação: $CONFIG"
info "log completo: $LOG"

# ---------- 2. código ----------
step "Atualizando o código"
cd "$REPO"
if ((LOCAL)); then
  warn "--local: implantando a árvore como está, sem git pull"
else
  [[ -z "$(git status --porcelain)" ]] || fail "há mudanças não commitadas em $REPO (use --local para implantá-las)"
  run "git fetch" git fetch --prune origin
  [[ -n "$REF" ]] && run "checkout de $REF" git checkout "$REF"
  if git symbolic-ref -q HEAD > /dev/null && git rev-parse -q --verify '@{u}' > /dev/null; then
    run "git pull" git pull --ff-only
  fi
fi
COMMIT=$(git rev-parse --short HEAD)
VERSION=$(sed -n 's/^version *= *"\(.*\)"/\1/p' pyproject.toml | head -1)
DIRTY=""
[[ -n "$(git status --porcelain)" ]] && DIRTY=" + mudanças locais"
ok "versão ${B}${VERSION}${N} · commit ${B}${COMMIT}${N}${DIRTY} · $(git log -1 --format='%s' | cut -c1-60)"

# ---------- 3. imagens ----------
step "Construindo as imagens"
# As imagens do agente; o compose da instância usa estes nomes.
IMAGES=(grolla-api:local grolla-worker:local grolla-fakes:local)
declare -A DOCKERFILE=(
  [grolla-api]=packages/grolla-api/Dockerfile
  [grolla-worker]=packages/grolla-worker/Dockerfile
  [grolla-fakes]=e2e/fakes/Dockerfile
)
# O tamanho dos embeddings sai do arquivo; a imagem da api ainda não existe para ler o YAML.
RAG=$(sed -n 's/^  rag: *"\{0,1\}\([a-z ]*\)"\{0,1\} *$/\1/p' "$CONFIG" | head -1)
[[ -n "$RAG" ]] || RAG=$(sed -n 's/^ *size: *\(small\|large\).*/\1/p' "$CONFIG" | head -1)
RAG=${RAG:-"small large"}
for image in "${IMAGES[@]}"; do
  name=${image%%:*}
  # Guarda a imagem em uso como :anterior, para voltar se a nova não subir.
  docker image inspect "$image" > /dev/null 2>&1 && docker tag "$image" "$name:anterior"
  args=(-f "${DOCKERFILE[$name]}" -t "$image" --label "org.opencontainers.image.revision=$COMMIT")
  label="$image"
  if [[ "$name" == grolla-worker ]]; then
    args+=(--build-arg "GROLLA_RAG_MODELS=$RAG")
    label="$image (embeddings: $RAG)"
  fi
  run "$label" docker build "${args[@]}" .
done

# ---------- 4. configuração ----------
step "Configurando o ambiente"
WEB_HASH=""
web_password=$(sed -n '/^https:/,/^[^ #]/ s/^ *senha: *//p' "$CONFIG" | head -1 | tr -d "\"'")
if [[ -n "$web_password" ]]; then
  WEB_HASH=$(docker run --rm caddy:2.8.4 caddy hash-password --plaintext "$web_password" 2>> "$LOG") ||
    fail "não consegui gerar o hash da senha da página"
fi
SUMMARY=$(docker run --rm --user "$(id -u):$(id -g)" -e GROLLA_WEB_HASH="$WEB_HASH" \
  -v "$REPO/scripts:/scripts:ro" -v "$CONFIG:/implantacao.yaml:ro" -v "$STAGE:/saida" \
  --entrypoint python grolla-api:local /scripts/configurar_instancia.py /implantacao.yaml /saida 2>> "$LOG") ||
  fail "o arquivo de implantação tem problemas (veja abaixo)"
declare -A CFG
while IFS='=' read -r key value; do CFG[$key]=$value; done <<< "$SUMMARY"
DIR="${CFG[pasta]/#\~/$HOME}"
mkdir -p "$DIR/backups" "$DIR/.anterior"
INSTANCE_FILES=(.env grolla.env grolla.yaml signoz-mcp.env Caddyfile kubeconfig.yaml compose.yml)
# Os arquivos em uso ficam em .anterior/, para a volta automática restaurar junto com as imagens.
for file in "${INSTANCE_FILES[@]}"; do
  [[ -f "$DIR/$file" ]] && cp -p "$DIR/$file" "$DIR/.anterior/$file"
done
caddy_before=$(sha256sum "$DIR/Caddyfile" 2> /dev/null || true)
find "$STAGE" -maxdepth 1 -type f -exec cp -p {} "$DIR/" \;
# O compose do repositório vai junto: na pasta da instância, `docker compose logs` etc. funcionam.
install -m 644 "$REPO/deploy/compose.yml" "$DIR/compose.yml"
if [[ -n "${CFG[kubeconfig]}" ]]; then
  source_kube="${CFG[kubeconfig]/#\~/$HOME}"
  [[ -f "$source_kube" ]] || fail "kubeconfig não encontrado: $source_kube"
  install -m 644 "$source_kube" "$DIR/kubeconfig.yaml"
else
  : > "$DIR/kubeconfig.yaml"
fi
caddy_changed=0
[[ "$caddy_before" != "$(sha256sum "$DIR/Caddyfile" 2> /dev/null || true)" ]] && caddy_changed=1
compose() { docker compose --project-directory "$DIR" "$@"; }
compose config --quiet 2>> "$LOG" || fail "o compose não aceitou a configuração gerada"
ok "time ${B}${CFG[time]}${N} · configuração validada como o agente valida"
ok "instância em $DIR · porta ${CFG[porta]} · perfis: ${CFG[perfis]}"
info "páginas: ${CFG[url]}"

# ---------- 5. backup ----------
step "Backup do banco"
if [[ -n "$(compose ps -q db 2> /dev/null)" ]]; then
  BACKUP="$DIR/backups/grolla-$(date +%Y%m%d-%H%M%S)-$COMMIT.dump"
  run "pg_dump" bash -c "docker compose --project-directory '$DIR' exec -T db pg_dump -U grolla -Fc grolla > '$BACKUP'"
  ok "$(du -h "$BACKUP" | cut -f1) em $BACKUP"
  find "$DIR/backups" -name 'grolla-*.dump' -printf '%T@ %p\n' | sort -rn | tail -n +$((KEEP_BACKUPS + 1)) | cut -d' ' -f2- | xargs -r rm --
  info "guardando os ${KEEP_BACKUPS} backups mais recentes"
else
  info "o banco ainda não está rodando: primeira implantação, nada a salvar"
fi

# ---------- 6. troca ----------
step "Trocando o agente"
running=$(compose ps --services --status running 2> /dev/null | grep -xE 'api|worker|fakes' || true)
if [[ -n "$running" ]]; then
  # shellcheck disable=SC2086 # um serviço por palavra
  run "parando $(echo "$running" | paste -sd' ')" compose stop $running
else
  info "o agente não estava rodando"
fi
SWAPPED_AT=$(date -u +%Y-%m-%dT%H:%M:%S)
run "subindo com as imagens novas" compose up -d
if ((caddy_changed)) && [[ -n "$(compose ps -q caddy 2> /dev/null)" ]]; then
  run "recarregando o HTTPS (Caddyfile mudou)" compose restart caddy
fi
ok "o banco segue com os mesmos dados"

# ---------- 7. saúde ----------
step "Conferindo a saúde"
# A api responde e, no worker, o heartbeat é posterior à troca (o do worker antigo não vale).
probe() {
  compose exec -T api python -c "
import json, sys, urllib.request as u
body = json.load(u.urlopen('http://localhost:8000$1', timeout=5))
beat = body.get('last_heartbeat')
sys.exit(0 if '$1' == '/health' or (beat and beat[:19] >= '$SWAPPED_AT') else 1)
" > /dev/null 2>&1
}
healthy() {
  local path="$1" label="$2" start=$SECONDS
  while ((SECONDS - start < HEALTH_TIMEOUT)); do
    if probe "$path"; then
      clear_line
      ok "$label ${D}$(elapsed $((SECONDS - start)))${N}"
      return 0
    fi
    ((TTY)) && printf "\r      %s…%s %s %s%s%s " "$C" "$N" "$label" "$D" "$(elapsed $((SECONDS - start)))" "$N"
    sleep 3
  done
  clear_line
  return 1
}
rollback() {
  warn "voltando para as imagens e a configuração anteriores"
  find "$DIR/.anterior" -maxdepth 1 -type f -exec cp -p {} "$DIR/" \;
  for image in "${IMAGES[@]}"; do
    name=${image%%:*}
    docker image inspect "$name:anterior" > /dev/null 2>&1 && docker tag "$name:anterior" "$image"
  done
  compose up -d >> "$LOG" 2>&1 || true
  compose logs --tail 30 api worker >> "$LOG" 2>&1 || true
  fail "$1: a versão nova não subiu e a anterior voltou (o banco não foi tocado; backup em $DIR/backups)"
}
healthy /health "api respondendo (migrações aplicadas)" || rollback "a api não respondeu em ${HEALTH_TIMEOUT}s"
healthy /health/worker "worker vivo (heartbeat novo)" || rollback "o worker não deu sinal em ${HEALTH_TIMEOUT}s"

# ---------- 8. fim ----------
step "Arrumando"
echo "$COMMIT" > "$DIR/.implantado"
run "removendo camadas órfãs (só imagens, nunca volumes)" docker image prune -f
printf "\n  %s✔ Grolla %s (%s) implantado em %s%s\n" "$G$B" "$VERSION" "$COMMIT" "$(elapsed $((SECONDS - STARTED)))" "$N"
printf "  %s%s%s\n\n" "$B" "${CFG[url]}" "$N"
compose ps --format '    {{.Service}}\t{{.Status}}' 2> /dev/null
printf "\n  %svoltar para a versão anterior:%s\n" "$D" "$N"
for image in "${IMAGES[@]}"; do printf "    docker tag %s:anterior %s\n" "${image%%:*}" "$image"; done
printf "    cd %s && docker compose up -d\n\n" "$DIR"

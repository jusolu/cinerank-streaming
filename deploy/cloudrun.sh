#!/usr/bin/env bash
# Deploy do CineRank no Cloud Run (GCP), pensado para ficar dentro da cota gratuita.
#
# Uso:  PROJECT_ID=cinerank-xxxx REGION=us-central1 BILLING_ACCOUNT=XXXXXX-XXXXXX-XXXXXX bash deploy/cloudrun.sh
#       (sem BILLING_ACCOUNT, assume que o projeto já existe e tem faturamento)
#
# O que faz:
#   1. Cria o projeto (se não existir) e vincula a conta de faturamento.
#   2. Ativa as APIs necessárias (Cloud Run, Artifact Registry, Budgets).
#   3. Constrói a imagem localmente (Docker), envia ao Artifact Registry e publica no Cloud Run.
#   4. Limita custos: no máximo 2 instâncias, 512 MiB, escala até zero.
#   5. Mantém só as 2 imagens mais recentes no Artifact Registry (cota grátis: 500 MB).
#   6. Cria um alerta de orçamento (padrão: 5 na moeda da conta de faturamento).
set -euo pipefail

: "${PROJECT_ID:?defina PROJECT_ID}"
REGION="${REGION:-us-central1}"
SERVICE="${SERVICE:-cinerank}"
BUDGET="${BUDGET:-5}"

cd "$(dirname "$0")/.."
test -f data/catalog.db || { echo "data/catalog.db não existe. Rode: python -m ingest.export"; exit 1; }

if ! gcloud projects describe "$PROJECT_ID" >/dev/null 2>&1; then
  echo "== Criando projeto $PROJECT_ID"
  gcloud projects create "$PROJECT_ID" --name="CineRank"
fi
gcloud config set project "$PROJECT_ID" >/dev/null

if [ -n "${BILLING_ACCOUNT:-}" ]; then
  echo "== Vinculando faturamento"
  gcloud billing projects link "$PROJECT_ID" --billing-account="$BILLING_ACCOUNT"
fi

echo "== Ativando APIs"
gcloud services enable run.googleapis.com artifactregistry.googleapis.com billingbudgets.googleapis.com

if ! gcloud artifacts repositories describe cloud-run-source-deploy --location="$REGION" >/dev/null 2>&1; then
  echo "== Criando repositório de imagens"
  gcloud artifacts repositories create cloud-run-source-deploy \
    --repository-format=docker --location="$REGION" --description="Imagens do CineRank (Cloud Run)"
fi

# Build local: o Docker respeita o .dockerignore (só o site e o data/catalog.db entram na imagem).
# O envio via `--source` empacotaria também o banco de trabalho e os arquivos brutos (GBs).
IMAGE="$REGION-docker.pkg.dev/$PROJECT_ID/cloud-run-source-deploy/$SERVICE:$(date +%Y%m%d-%H%M%S)"
echo "== Build da imagem $IMAGE"
gcloud auth configure-docker "$REGION-docker.pkg.dev" --quiet
docker build -t "$IMAGE" .
echo "== Enviando imagem"
docker push "$IMAGE"

echo "== Deploy no Cloud Run"
gcloud run deploy "$SERVICE"   --image "$IMAGE"   --region "$REGION"   --allow-unauthenticated   --memory 512Mi   --cpu 1   --min-instances 0   --max-instances 2   --concurrency 40   --timeout 30

echo "== Limpeza automática: manter só as 2 imagens mais recentes"
POLICY=$(mktemp)
cat > "$POLICY" <<'JSON'
[
  {"name": "manter-2-recentes", "action": {"type": "Keep"}, "mostRecentVersions": {"keepCount": 2}},
  {"name": "apagar-antigas", "action": {"type": "Delete"}, "condition": {"tagState": "ANY"}}
]
JSON
gcloud artifacts repositories set-cleanup-policies cloud-run-source-deploy \
  --location="$REGION" --policy="$POLICY" --no-dry-run || echo "(aviso) não foi possível aplicar a limpeza automática"
rm -f "$POLICY"

if [ -n "${BILLING_ACCOUNT:-}" ] && ! gcloud billing budgets list --billing-account="$BILLING_ACCOUNT" --format="value(displayName)" | grep -qx "CineRank"; then
  CURRENCY=$(gcloud billing accounts describe "$BILLING_ACCOUNT" --format="value(currencyCode)")
  echo "== Alerta de orçamento ($BUDGET $CURRENCY)"
  gcloud billing budgets create \
    --billing-account="$BILLING_ACCOUNT" \
    --display-name="CineRank" \
    --budget-amount="${BUDGET}${CURRENCY}" \
    --filter-projects="projects/$PROJECT_ID" \
    --threshold-rule=percent=0.5 --threshold-rule=percent=0.9 --threshold-rule=percent=1.0
fi

URL=$(gcloud run services describe "$SERVICE" --region "$REGION" --format="value(status.url)")
echo
echo "Publicado: $URL"

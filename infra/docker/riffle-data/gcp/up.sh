#!/usr/bin/env bash
# Creates the VM, its public address and firewall rule, and starts the store on it
# (ADR 0015). Run it yourself from this directory: it opens ports 80 and 443 of the
# project to the internet, which is the point (the pages and the HTTPS certificate).
#
#   PROJECT=riffle-data-miner BUCKET=riffle-data-miner-mined bash gcp/up.sh
#
# Safe to run again: existing resources are kept. The bucket and the service account
# `riffle-data` must already exist (infra/docker/riffle-data/README.md). Not yet run.
set -euo pipefail
cd "$(dirname "$0")/.."
P=${PROJECT:-riffle-data-miner}; R=${REGION:-us-central1}; Z=${ZONE:-us-central1-a}
B=${BUCKET:-riffle-data-miner-mined}; SA=riffle-data@$P.iam.gserviceaccount.com; VM=riffle-data

gcloud compute addresses create riffle-data-ip --region="$R" --project="$P" 2>/dev/null || true
IP=$(gcloud compute addresses describe riffle-data-ip --region="$R" --project="$P" --format='value(address)')
DOMAIN=${DOMAIN:-${IP//./-}.sslip.io}
echo "address $IP, name $DOMAIN"

gcloud compute firewall-rules create riffle-data-web --project="$P" --network=default \
  --allow=tcp:80,tcp:443 --target-tags=riffle-data --direction=INGRESS 2>/dev/null || true

gcloud compute instances create "$VM" --project="$P" --zone="$Z" --machine-type="${MACHINE:-e2-small}" \
  --boot-disk-size=30GB --image-family=debian-12 --image-project=debian-cloud \
  --service-account="$SA" --scopes=cloud-platform --tags=riffle-data --address="$IP" \
  --metadata-from-file=startup-script=gcp/startup.sh 2>/dev/null || echo "VM exists"

echo "waiting for Docker on the VM..."
until gcloud compute ssh "$VM" --project="$P" --zone="$Z" --command='test -f /var/lib/riffle-data-docker-ready' 2>/dev/null; do sleep 10; done

tar czf /tmp/riffle-data.tgz --exclude=tests --exclude=gcp --exclude=__pycache__ -C .. riffle-data
gcloud compute scp /tmp/riffle-data.tgz "$VM":/tmp/ --project="$P" --zone="$Z"
gcloud compute ssh "$VM" --project="$P" --zone="$Z" --command="
  set -e; sudo mkdir -p /opt && sudo tar xzf /tmp/riffle-data.tgz -C /opt && cd /opt/riffle-data
  sudo tee .env >/dev/null <<ENV
RIFFLE_BUCKET=$B
GOOGLE_CLOUD_PROJECT=$P
RIFFLE_BIND_ADDR=127.0.0.1
RIFFLE_DOMAIN=$DOMAIN
RIFFLE_TRUST_PROXY=1
RIFFLE_COOKIE_SECURE=1
ENV
  sudo docker compose --profile public up -d --build
  sudo docker compose ps"
echo
echo "Open https://$DOMAIN . Make tokens with:"
echo "  gcloud compute ssh $VM --project=$P --zone=$Z --command='cd /opt/riffle-data && sudo docker compose exec riffle-data python manage.py add-reader lead'"
echo "  gcloud compute ssh $VM --project=$P --zone=$Z --command='cd /opt/riffle-data && sudo docker compose exec riffle-data python manage.py add-pc pc1'"
echo "Each PC then sets RIFFLE_SYNC_URL=https://$DOMAIN and its RIFFLE_SYNC_TOKEN in the miner's .env."

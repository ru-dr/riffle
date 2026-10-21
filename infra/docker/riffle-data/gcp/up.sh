#!/usr/bin/env bash
# Creates the VM, its public address and firewall rule, and starts the store on it
# (ADR 0015). Run it yourself from the repo root. It opens ports 80 and 443 of the
# project to the internet, which is the point (the pages and the HTTPS certificate).
#
#   PROJECT=riffle-data-miner BUCKET=riffle-data-miner-mined bash infra/docker/riffle-data/gcp/up.sh
#
# Safe to run again: anything that exists is kept. The bucket and the service account
# `riffle-data` must already exist. Every command's output is shown, nothing is
# silenced, and gcloud is told never to ask a question (a hidden prompt looks like a hang).
set -euo pipefail
cd "$(dirname "$0")/.."
export CLOUDSDK_CORE_DISABLE_PROMPTS=1
P=${PROJECT:-riffle-data-miner}; R=${REGION:-us-central1}; Z=${ZONE:-us-central1-a}
B=${BUCKET:-riffle-data-miner-mined}; SA=riffle-data@$P.iam.gserviceaccount.com; VM=riffle-data
SSH=(--project="$P" --zone="$Z" --quiet --ssh-flag="-o ConnectTimeout=15" --ssh-flag="-o StrictHostKeyChecking=accept-new")
step() { printf '\n==> %s\n' "$*"; }

step "0/6 account $(gcloud config get-value account 2>/dev/null), project $P"
gcloud projects describe "$P" --format='value(projectId,lifecycleState)' \
  || { echo "cannot see project $P: run 'gcloud auth login' and retry"; exit 1; }
gcloud storage buckets describe "gs://$B" --format='value(name)' || { echo "bucket gs://$B is missing"; exit 1; }
gcloud iam service-accounts describe "$SA" --project="$P" --format='value(email)' \
  || { echo "service account $SA is missing"; exit 1; }

step "1/6 static address"
gcloud compute addresses describe riffle-data-ip --region="$R" --project="$P" >/dev/null 2>&1 \
  || gcloud compute addresses create riffle-data-ip --region="$R" --project="$P"
IP=$(gcloud compute addresses describe riffle-data-ip --region="$R" --project="$P" --format='value(address)')
DOMAIN=${DOMAIN:-${IP//./-}.sslip.io}
echo "address $IP, name $DOMAIN"

step "2/6 firewall: tcp 80 and 443 to VMs tagged riffle-data"
gcloud compute firewall-rules describe riffle-data-web --project="$P" >/dev/null 2>&1 \
  || gcloud compute firewall-rules create riffle-data-web --project="$P" --network=default \
       --allow=tcp:80,tcp:443 --target-tags=riffle-data --direction=INGRESS

step "3/6 VM $VM (${MACHINE:-e2-small}, $Z)"
gcloud compute instances describe "$VM" --project="$P" --zone="$Z" >/dev/null 2>&1 \
  || gcloud compute instances create "$VM" --project="$P" --zone="$Z" --machine-type="${MACHINE:-e2-small}" \
       --boot-disk-size=30GB --image-family=debian-12 --image-project=debian-cloud \
       --service-account="$SA" --scopes=cloud-platform --tags=riffle-data --address="$IP" \
       --metadata-from-file=startup-script=gcp/startup.sh

step "4/6 waiting for Docker on the VM (first boot takes 2 to 4 minutes; up to 12 tries)"
ok=0
for i in $(seq 1 12); do
  if gcloud compute ssh "$VM" "${SSH[@]}" --command='test -f /var/lib/riffle-data-docker-ready'; then ok=1; break; fi
  echo "   not ready yet (try $i of 12); waiting 20 s. If ssh itself is failing, the error is above."
  sleep 20
done
[ "$ok" = 1 ] || { echo "Docker never came up. Read the VM's startup log:"
  echo "  gcloud compute instances get-serial-port-output $VM --project=$P --zone=$Z | tail -50"; exit 1; }

step "5/6 copying the store to the VM and starting it"
tar czf /tmp/riffle-data.tgz --exclude=tests --exclude=gcp --exclude=__pycache__ -C .. riffle-data
gcloud compute scp /tmp/riffle-data.tgz "$VM":/tmp/ "${SSH[@]}"
gcloud compute ssh "$VM" "${SSH[@]}" --command="
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

step "6/6 done"
echo "Open https://$DOMAIN (the certificate can take a minute). Make tokens with:"
echo "  gcloud compute ssh $VM --project=$P --zone=$Z --command='cd /opt/riffle-data && sudo docker compose exec -T riffle-data python manage.py add-reader lead'"
echo "  gcloud compute ssh $VM --project=$P --zone=$Z --command='cd /opt/riffle-data && sudo docker compose exec -T riffle-data python manage.py add-admin lead'"
echo "Each PC's miner then uses RIFFLE_SYNC_URL=https://$DOMAIN (the lead's --issue-tokens puts it in the join codes)."

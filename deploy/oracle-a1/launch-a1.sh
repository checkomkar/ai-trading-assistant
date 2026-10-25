#!/usr/bin/env bash
# Keeps asking Oracle for an Always Free Ampere A1 VM until one is free ("Out of host capacity" is the usual answer in
# busy regions). Run it on your PC (Git Bash / WSL / Cloud Shell) with the OCI CLI configured (`oci setup config`).
#
#   COMPARTMENT_ID=ocid1.tenancy...  SUBNET_ID=ocid1.subnet...  IMAGE_ID=ocid1.image...  SSH_KEY=~/.ssh/striktek_a1.pub \
#     deploy/oracle-a1/launch-a1.sh
#
# Make the key with `ssh-keygen -t rsa -b 4096 -f ~/.ssh/striktek_a1 -N ""` (Cloud Shell runs in FIPS mode and refuses
# ed25519 keys). IMAGE_ID must be an aarch64 image (Canonical Ubuntu 24.04 aarch64). OCPUS/MEMORY_GB default to the free maximum
# (2 / 12); BOOT_GB to 100 (the free tier includes 200 GB of block storage in total). It tries every availability
# domain in your region in turn and waits between rounds; Ctrl+C stops it.
set -uo pipefail
: "${COMPARTMENT_ID:?}" "${SUBNET_ID:?}" "${IMAGE_ID:?}" "${SSH_KEY:?path to your public key}"
OCPUS=${OCPUS:-2}; MEMORY_GB=${MEMORY_GB:-12}; BOOT_GB=${BOOT_GB:-100}; WAIT=${WAIT:-90}; NAME=${NAME:-striktek}

ADS=$(oci iam availability-domain list --compartment-id "$COMPARTMENT_ID" --query 'data[].name' --raw-output | tr -d '[]", ' | grep -v '^$')
[ -n "$ADS" ] || { echo "Could not list availability domains (check oci setup config)"; exit 1; }
echo "Availability domains: $(echo $ADS)"

round=0
while true; do
  round=$((round + 1))
  for AD in $ADS; do
    echo "[$(date '+%H:%M:%S')] round $round: $AD, ${OCPUS} OCPU / ${MEMORY_GB} GB"
    out=$(oci compute instance launch \
      --availability-domain "$AD" --compartment-id "$COMPARTMENT_ID" --display-name "$NAME" \
      --shape VM.Standard.A1.Flex --shape-config "{\"ocpus\": $OCPUS, \"memoryInGBs\": $MEMORY_GB}" \
      --image-id "$IMAGE_ID" --subnet-id "$SUBNET_ID" --assign-public-ip true \
      --boot-volume-size-in-gbs "$BOOT_GB" --ssh-authorized-keys-file "$SSH_KEY" 2>&1)
    if echo "$out" | grep -q '"lifecycle-state"'; then
      echo "$out" | grep -E '"id"|"lifecycle-state"' | head -2
      echo "Got it. Open the console, Compute, Instances, $NAME, and reserve its public IP (docs/deploy-oracle.md)."
      exit 0
    fi
    if echo "$out" | grep -qiE 'out of (host )?capacity|InternalError|TooManyRequests'; then
      echo "  no capacity yet"
    else
      echo "$out" | tail -5
      echo "Stopped: that is not a capacity error (check the ids, the image architecture and your limits)."
      exit 1
    fi
    sleep 10
  done
  sleep "$WAIT"
done

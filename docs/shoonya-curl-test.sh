#!/usr/bin/env bash
# One Shoonya sign-in request with curl, outside the app, to see Shoonya's raw reply.
#   bash docs/shoonya-curl-test.sh
# It asks for each value (the password, API key and code are not echoed and are not saved anywhere).
# Do not run it more than 2-3 times in a row: repeated failures can lock the account.
# On success the reply contains a session token: do not share the full reply, only "stat" and "emsg".
read -rp "User ID (e.g. FA26323): " SUID
read -rp "Vendor code (e.g. FA26323_U): " VC
read -rsp "Password: " PW; echo
read -rsp "API key (full secret, revealed with the eye icon): " KEY; echo
read -rp "Current 6-digit code: " OTP
PWH=$(printf %s "$PW" | openssl dgst -sha256 | awk '{print $NF}')
APPKEY=$(printf %s "$SUID|$KEY" | openssl dgst -sha256 | awk '{print $NF}')
REPLY=$(curl -s -X POST https://api.shoonya.com/NorenWClientAPI/QuickAuth \
  --data "jData={\"source\":\"API\",\"apkversion\":\"1.0.0\",\"uid\":\"$SUID\",\"pwd\":\"$PWH\",\"factor2\":\"$OTP\",\"vc\":\"$VC\",\"appkey\":\"$APPKEY\",\"imei\":\"striktek\"}")
echo "stat: $(printf %s "$REPLY" | grep -o '"stat":"[^"]*"')"
echo "emsg: $(printf %s "$REPLY" | grep -o '"emsg":"[^"]*"')"
case "$REPLY" in *susertoken*) echo "A session token came back: sign-in works with these values.";; esac
echo "(key length ${#KEY}, password length ${#PW}, code length ${#OTP})"

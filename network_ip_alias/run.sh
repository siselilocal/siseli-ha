#!/usr/bin/with-contenv bashio

INTERFACE="$(bashio::config 'INTERFACE')"
IP_ADDRESS="$(bashio::config 'IP_ADDRESS')"

# Always attempted on exit, not gated on this run being the one that added it --
# a run that starts up and finds the address already present (because a prior
# run was SIGKILLed or OOM-killed before its own cleanup ran) used to skip
# removal entirely, leaving the address stuck on the host until a reboot. This
# add-on owns the address for as long as it runs, regardless of which run put
# it there, so a normal stop always removes it.
cleanup() {
    ip addr del "$IP_ADDRESS" dev "$INTERFACE" 2>/dev/null || true
    echo "[Network IP Alias] Removed $IP_ADDRESS from $INTERFACE"
}

trap cleanup TERM INT EXIT

if ! ip link show dev "$INTERFACE" >/dev/null 2>&1; then
    echo "[Network IP Alias] Interface '$INTERFACE' does not exist. Available interfaces:"
    ip -br link
    exit 1
fi

case "$IP_ADDRESS" in
    */*) ;;
    *)
        echo "[Network IP Alias] IP_ADDRESS must include a CIDR prefix, for example 192.168.1.20/24"
        exit 1
        ;;
esac

if ip -o -4 addr show dev "$INTERFACE" | awk '{print $4}' | grep -Fxq "$IP_ADDRESS"; then
    echo "[Network IP Alias] $IP_ADDRESS is already present on $INTERFACE"
else
    if ! ip addr add "$IP_ADDRESS" dev "$INTERFACE"; then
        echo "[Network IP Alias] Could not add $IP_ADDRESS to $INTERFACE"
        exit 1
    fi
    echo "[Network IP Alias] Added $IP_ADDRESS to $INTERFACE"
fi

while true; do
    sleep 3600
done
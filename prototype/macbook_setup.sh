#!/bin/bash
# Run the interlock chat client from a MacBook, through the FPGA, to Llama on the Spark.
#   MacBook (this) --eth--> FPGA --eth--> Spark (compute @ 10.10.10.2:5551)
#
# Usage:  IFACE=en7 ./macbook_setup.sh      (IFACE = your USB-C->Ethernet adapter)
# Run ./macbook_setup.sh with no IFACE to list interfaces.

IFACE="${IFACE:-}"
MY_IP="${MY_IP:-10.10.10.3}"
COMPUTE_IP="${COMPUTE_IP:-10.10.10.2}"
SPARK_MAC="${SPARK_MAC:-4c:bb:47:7e:c1:91}"   # Spark enP7s7 MAC (static-ARP fallback)
REPO_DIR="${REPO_DIR:-$HOME/interlock}"

if [ -z "$IFACE" ]; then
  echo "Pick your USB-C->Ethernet adapter's Device (enX) from below, then re-run:"
  echo "  IFACE=enX ./macbook_setup.sh"
  echo
  networksetup -listallhardwareports
  exit 1
fi

echo ">> setting $IFACE to $MY_IP/24 (sudo)"
sudo ifconfig "$IFACE" inet "$MY_IP" netmask 255.255.255.0 up || { echo "ifconfig failed"; exit 1; }

echo ">> fetching code into $REPO_DIR"
if [ -d "$REPO_DIR/.git" ]; then
  git -C "$REPO_DIR" pull --ff-only || true
else
  git clone https://github.com/JamesPetrie/interlock "$REPO_DIR" || { echo "git clone failed (auth?)"; exit 1; }
fi

echo ">> testing the path to compute ($COMPUTE_IP) through the FPGA"
if ! ping -c 3 -t 5 "$COMPUTE_IP" >/dev/null 2>&1; then
  echo "   no reply; adding static ARP and retrying"
  sudo arp -s "$COMPUTE_IP" "$SPARK_MAC" 2>/dev/null
  if ! ping -c 3 -t 5 "$COMPUTE_IP" >/dev/null 2>&1; then
    echo "   STILL no reply. Check: compute.py is running on the Spark; the cable is in"
    echo "   the FPGA; and if links are up but nothing forwards, power-cycle the eval board."
    exit 1
  fi
fi
echo "   link OK"

echo ">> starting chat client (commands: /challenge [id], /list, /quit)"
cd "$REPO_DIR/prototype" || exit 1
COMPUTE_HOST="$COMPUTE_IP" ./run_clients.sh

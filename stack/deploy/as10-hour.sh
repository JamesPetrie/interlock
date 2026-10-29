#!/bin/bash
# Hour run against the FPGA, hardened: the pacer's core (31) and its SMT sibling (63) get no device interrupts
# (NIC completion and async queues, the NVMe queue that may land there), the main process stays off both.
# Run as root from ~/ilstack (sudo -n nohup bash run/hour2.sh > run/hour2.out 2>&1 &). No logins during the run.
cd /home/amodo/ilstack || exit 1
IRQS="210 280 178 248 101"
declare -A SAVED
for i in $IRQS; do SAVED[$i]=$(cat /proc/irq/$i/smp_affinity_list); echo 27 > /proc/irq/$i/smp_affinity_list 2>/dev/null || echo "irq $i: affinity not changeable (managed)"; done
for i in $IRQS; do echo "irq $i -> $(cat /proc/irq/$i/smp_affinity_list) (was ${SAVED[$i]})"; done
rm -f run/hour2.done run/hour2-counters.txt
before_tx=$(ethtool -S enp1s0np0 | awk '/tx_packets_phy/{print $2}'); before_rxs=$(ethtool -S enp66s0np0 | awk '/rx_packets_phy/{print $2}'); before_rxc=$(ethtool -S enp1s0np0 | awk '/rx_packets_phy/{print $2}')
t0=$(date +%s)
env ILSTACK_LOWJITTER=1 taskset -c 0-26,28-30,32-58,60-62 env/bin/python -m ilstack.cli timing-send --stub raw:enp1s0np0 --bucket-ms 1 --bkt-num 1000 --num-slot 64 --slots 64 --buckets 3600000 --target 0.05 --fill 0.95 --in-flight 200 --ahead 200 --release process --rate-limit --core 31 --link-mbps 1000 --delta-unit 1e-8 > run/send-hour2.log 2> run/send-hour2.err
t1=$(date +%s)
after_tx=$(ethtool -S enp1s0np0 | awk '/tx_packets_phy/{print $2}'); after_rxs=$(ethtool -S enp66s0np0 | awk '/rx_packets_phy/{print $2}'); after_rxc=$(ethtool -S enp1s0np0 | awk '/rx_packets_phy/{print $2}')
tx=$((after_tx - before_tx)); rxs=$((after_rxs - before_rxs)); rxc=$((after_rxc - before_rxc)); secs=$((t1 - t0))
echo "seconds $secs tx $tx rx_server $rxs rx_client $rxc forwarded_est $((rxs - rxc))" > run/hour2-counters.txt
for i in $IRQS; do echo "${SAVED[$i]}" > /proc/irq/$i/smp_affinity_list 2>/dev/null; done
touch run/hour2.done

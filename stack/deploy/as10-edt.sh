#!/bin/bash
# Kernel release against the FPGA on as10: the dispatcher hands each bucket to the kernel ahead of time, stamped
# with its instant; an etf queue on the port releases it. Run as root from ~/ilstack:
#   sudo -n nohup bash run/edt.sh <buckets> <lead us> > run/edt.out 2>&1 &
# No logins during a long run.
cd /home/amodo/ilstack || exit 1
rm -f run/edt.done run/edt-counters.txt                        # first thing, so a waiter never sees the previous run's marker
BUCKETS=${1:-20000}; LEAD=${2:-10000}; IF=enp1s0np0; Q=31; H=$(printf ":%x" $((Q + 1)))
# The etf queue releases each frame at its departure time; --rate-limit stamps them a wire time apart, the spacing
# this bench's 25 G link into the 1 G core needs (the product's 1 Gb/s link spaces them itself: one stamp per bucket).
AHEAD=$(( (LEAD + 999) / 1000 ))
RUN="--release kernel --rate-limit --core none --ahead $AHEAD --in-flight $AHEAD"
IRQS="210 280 178 248"
declare -A SAVED
for i in $IRQS; do SAVED[$i]=$(cat /proc/irq/$i/smp_affinity_list); echo 27 > /proc/irq/$i/smp_affinity_list 2>/dev/null; done
# fq on the sender's queue: releases each frame at its stamped departure time; room for a few buckets per flow
# (the default flow_limit of 100 packets refuses the second bucket with ENOBUFS)
# one fq for the whole device (not per transmit queue), so the sender may run on any core
tc qdisc replace dev $IF root etf clockid CLOCK_TAI delta 300000
echo "queue on $IF: $(tc qdisc show dev $IF | head -1 | cut -c1-60)"
# the sender sleeps between hand-overs: keep its core out of deep idle states (C2 exit latency here is 800 us)
exec 3<>/dev/cpu_dma_latency; printf '\x00\x00\x00\x00' >&3
C2=/sys/devices/system/cpu/cpu$Q/cpuidle/state2/usage; C2_0=$(cat $C2 2>/dev/null)
C() { ethtool -S $1 | awk -v k=$2 '$1==k":" {print $2}'; }
TX0=$(C $IF tx_packets_phy); RX0=$(C enp66s0np0 rx_packets_phy); SRX0=$(C $IF rx_packets_phy); T0=$(date +%s)
# the sender on any core but the interrupt core (27) and the pacer bench's core pair (31/63)
env ILSTACK_LOWJITTER=1 taskset -c 0-26,28-30,32-58,60-62 env/bin/python -m ilstack.cli timing-send --stub raw:$IF --bucket-ms 1 --bkt-num 1000 --num-slot 64 --slots 64 --buckets $BUCKETS --target 0.05 --fill 0.95 --link-mbps 1000 --delta-unit 1e-8 $RUN > run/send-edt.log 2> run/send-edt.err
echo "sender exit $?" >> run/send-edt.err
sleep 1
TX1=$(C $IF tx_packets_phy); RX1=$(C enp66s0np0 rx_packets_phy); SRX1=$(C $IF rx_packets_phy); T1=$(date +%s)
echo "seconds $((T1-T0)) tx $((TX1-TX0)) rx_server $((RX1-RX0)) rx_client $((SRX1-SRX0)) forwarded_est $((RX1-RX0-(SRX1-SRX0))) c2_entries_cpu$Q $(( $(cat $C2 2>/dev/null) - C2_0 ))" > run/edt-counters.txt
exec 3>&-
tc qdisc del dev $IF root 2>/dev/null                         # back to the default: unnamed mq with fq_codel leaves
for i in $IRQS; do echo "${SAVED[$i]}" > /proc/irq/$i/smp_affinity_list 2>/dev/null; done
chown -R amodo:amodo /home/amodo/ilstack/run
touch run/edt.done

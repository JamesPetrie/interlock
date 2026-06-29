# Recomputation Interlock Core Design Specification

```
                                                        ┌───────────┐
                                                        │   bucket  │
                                                        │   timer   │
                                                        └───────────┘
  ──  ──  ──  ──  ──  ──  ──  ──  ──  ──  ──  ──  ──  ──  ──  ┬  ──  ──  ──  ──  ──  ──  ──  ──  ──  ──  ──
(x,o) in                                       outside region   inside region
                                                              │
        ┌──────────┐  ┌────────────┐    ┌────────────┐  ┌───────────┐    ┌────────────┐  ┌──────────┐
MAC ──▶ │   eth    │─▶│ canon proc │───▶│   traffic  │─▶│  buc│ket  │───▶│  recomp    │─▶│   eth    │──▶ MAC
FIFO    │ deframe  │  │ (bkt drop) │    │   commit   │  │  buf fer  │    │  feed      │  │ reframe  │    FIFO
        └──────────┘  └────────────┘    └──────┬─────┘  └─────│─────┘    └─────┬──────┘  └──────────┘
tuser:                      │      len@beat#0  │  len@beat#0       len@beat#0  │  ▲  len@beat#0
                            │     swap(inline) │ swap(inline) │   swap(inline) │  │
                           s│                  │                               │  │
                           y│                  │              │                │  │
                           n│                  │  ┌────────────────────────────┘  │
                           c│                  │  │           │                   │
(m,τ) out                   │                  │  │                               │
                            │                  │  │           │                   │
                            ▼                  ▼  ▼                               │
        ┌──────────┐  ┌────────────┐    ┌───────────┐         │                   │      ┌──────────┐
MAC ◀── │   eth    │◀─│  2×1 mux   │◀───│   cert    │                             └──────│   eth    │◀── MAC
FIFO    │ reframe  │  │            │    │   build   │         │                          │ deframe  │    FIFO
        └──────────┘  └────────────┘    └───────────┘                                    └──────────┘
tuser:           len@beat#0                                   │
```

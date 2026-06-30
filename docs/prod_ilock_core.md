# Production Interlock Core Design Specification

```
                                                        ┌───────────┐
                                                        │   bucket  │
                                                        │   timer   │
                                                        └───────────┘
  ──  ──  ──  ──  ──  ──  ──  ──  ──  ──  ──  ──  ──  ──  ──  ┬  ──  ──  ──  ──  ──  ──  ──  ──  ──  ──  ──
Request direction                              outside region   inside region
                                                              │
        ┌──────────┐  ┌────────────┐    ┌────────────┐  ┌───── ─────┐  ┌────────────┐  ┌──────────┐
MAC ──▶ │   eth    │─▶│ canon proc │───▶│   traffic  │─▶│  buc│ket  │─▶│  2×1 mux   │─▶│   eth    │──▶ MAC
FIFO    │ deframe  │  │ (int drop) │    │   commit   │  │  buf fer  │  │            │  │ reframe  │    FIFO
        └──────────┘  └─────┬─┬────┘    └──────┬─────┘  └─────│─────┘  └────────────┘  └──────────┘
tuser:          drop@tlast  │ │   len@beat#0   │   len@beat#0                ▲    len@beat#0
                            │ │   swap(inline) │ swap(inline) │              │
                            │ │                │                             │
                            │ └────────────┐   │ traffic      │              │
                           s│     nonce    ▼   ▼ digest                     s│
                           y│            ┌───────────┐        │             y│
                           n│         ┌──│   cert    │                      n│
                           c│         │  │   build   │        │             c│
                            │         │  └───────────┘                       │
                            │         │        ▲ traffic      │              │
Response direction          │         │        │ digest                      │
                            ▼         │        │              │              │
        ┌──────────┐  ┌────────────┐◀─┘ ┌──────┴─────┐  ┌───────────┐  ┌─────┴──────┐  ┌──────────┐
MAC ◀── │   eth    │◀─│  3×1 mux   │◀───│   traffic  │◀─│  buc│ket  │◀─│ canon proc │◀─│   eth    │◀── MAC
FIFO    │ reframe  │  │            │    │   commit   │  │  buf fer  │  │ (ext drop) │  │ deframe  │    FIFO
        └──────────┘  └────────────┘    └────────────┘  └─────│─────┘  └────────────┘  └──────────┘
tuser:           len@beat#0       len@beat#0       len@beat#0      len@beat#0     drop@tlast
                                                 swap(inline) │    drop@tlast
                                                                  swap(inline)
```

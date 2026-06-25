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
        └──────────┘  └─────┬──────┘    └──────┬─────┘  └─────│─────┘  └────────────┘  └──────────┘
tuser:          drop@tlast  │     len@beat#0   │   len@beat#0                ▲    len@beat#0
                            │    swap@beat#0   ▼              │              │
                           s│            ┌───────────┐                      s│
                           y│         ┌──│   cert    │        │             y│
                           n│         │  │   build   │                      n│
                           c│         │  └───────────┘        │             c│
Response direction          │         │        ▲                             │
                            ▼         │        │              │              │
        ┌──────────┐  ┌────────────┐◀─┘ ┌──────┴─────┐  ┌───────────┐  ┌─────┴──────┐  ┌──────────┐
MAC ◀── │   eth    │◀─│  3×1 mux   │◀───│  traffic   │◀─│  buc│ket  │◀─│ canon proc │◀─│   eth    │◀── MAC
FIFO    │ reframe  │  │            │    │ commitment │  │  buf fer  │  │ (ext drop) │  │ deframe  │    FIFO
        └──────────┘  └────────────┘    └────────────┘  └─────│─────┘  └────────────┘  └──────────┘
tuser:           len@beat#0       len@beat#0       len@beat#0      len@beat#0     drop@tlast
                                                  swap@beat#0 │    drop@tlast
```

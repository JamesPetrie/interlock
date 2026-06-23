# Production Interlock Core Design Specification

```
Request direction            ┌────────────────────────────────────────────────┐
                             ▼                                                │
        ┌──────────┐  ┌────────────┐    ┌────────────┐  ┌────────────┐        \         ┌──────────┐
MAC ──▶ │   eth    │─▶│ canon proc │───▶│   traffic  │─▶│   bucket   │─────────┼───────▶│   eth    │──▶ MAC
FIFO    │ deframe  │  │ (int drop) │    │   commit   │  │   buffer   │        /         │ reframe  │    FIFO
        └──────────┘  └────────────┘    └──────┬─────┘  └────────────┘        │         └──────────┘
tuser:          drop@tlast        len@beat#0   │   len@beat#0  ▲              │   len@beat#0
                                 swap@beat#0   ▼               │              │
                                         ┌───────────┐         │              │         ┌──────────┐
                                      ┌──│   cert    │         ├──────────────┼─────────│  bucket  │
                                      │  │   build   │         │              │         │  timer   │
Response direction                    │  └───────────┘         │              │         └──────────┘
                                      │        ▲               ▼              ▼
        ┌──────────┐  ┌────────────┐◀─┘ ┌──────┴─────┐  ┌────────────┐  ┌────────────┐  ┌──────────┐
MAC ◀── │   eth    │◀─│  2×1 mux   │◀───│  traffic   │◀─│   bucket   │◀─│ canon proc │◀─│   eth    │◀── MAC
FIFO    │ reframe  │  │            │    │ commitment │  │   buffer   │  │ (ext drop) │  │ deframe  │    FIFO
        └──────────┘  └────────────┘    └────────────┘  └────────────┘  └────────────┘  └──────────┘
tuser:           len@beat#0       len@beat#0        len@beat#0      len@beat#0     drop@tlast
                                                   swap@beat#0     drop@tlast

```

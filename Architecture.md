## System Architecture

```mermaid
flowchart TD
    A[Customer Events] --> B[Event Ingestion]
    B --> C[Hindsight Memory]
    C --> D[Recall / Reflect]
    D --> E[Renewal Risk Agent]
    E --> F[Risk Assessment]
    E --> G[Evidence & Precedent]
    F --> H[Recommended Actions]
    G --> H
    H --> I[Dashboard]

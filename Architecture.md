## System Architecture

This architecture separates memory retrieval from reflection, allowing the Renewal Risk Agent to combine historical context with current customer signals.

```mermaid
flowchart TD
    A["Customer Events<br/>Support • Usage • QBR • CSM • Finance"] 
    --> B["Event Ingestion"]

    B --> C["Hindsight Memory<br/>Retain + Timestamp + Tags"]

    C --> D["Agent Memory Layer"]

    D --> E["Recall<br/>Retrieve Account Evidence"]
    D --> F["Reflect<br/>Reason Across History"]

    E --> G["Renewal Risk Agent"]
    F --> G

    G --> H["Risk Assessment"]
    G --> I["Evidence"]
    G --> J["Historical Precedent"]

    H --> K["Recommended Action"]
    I --> K
    J --> K

    K --> L["Customer Success Dashboard"]

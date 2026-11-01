# Retrieval Experiment

- Date: 2026-11-01
- Embedding model(s): gemini-embedding-001, none
- Dataset: synthetic labels store procedure-code query handles only; gold descriptions are combined in memory and never persisted.

| Mode | Recall@1 | Recall@3 | Recall@4 | MRR | nDCG@4 | Cases |
|---|---:|---:|---:|---:|---:|---:|
| bm25 | 0.9302 | 1.0000 | 1.0000 | 1.0000 | 1.0000 | 43 |
| dense | 0.9302 | 1.0000 | 1.0000 | 1.0000 | 1.0000 | 43 |
| hybrid | 0.9302 | 1.0000 | 1.0000 | 1.0000 | 1.0000 | 43 |

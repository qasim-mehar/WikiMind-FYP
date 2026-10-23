# Parse fixtures (LOCKED)

This is the fixed small paper set for the parse lane and every later lane.
**Never regenerate, rename, or modify these files.** Their SHA-256 values are
part of the test contract.

## PDFs

Downloaded from arXiv on 2026-10-23.

| File | Paper | arXiv version | Source URL | License | SHA-256 | Bytes |
| --- | --- | --- | --- | --- | --- | --- |
| `papers/1706.03762-attention.pdf` | Attention Is All You Need (Vaswani et al.) | v7 | https://arxiv.org/pdf/1706.03762 | arXiv.org perpetual, non-exclusive distribution license v1.0 — https://arxiv.org/licenses/nonexclusive-distrib/1.0/ | `bdfaa68d8984f0dc02beaca527b76f207d99b666d31d1da728ee0728182df697` | 2215244 |
| `papers/1406.2661-gan.pdf` | Generative Adversarial Nets (Goodfellow et al.) | v1 | https://arxiv.org/pdf/1406.2661 | arXiv.org perpetual, non-exclusive distribution license v1.0 — https://arxiv.org/licenses/nonexclusive-distrib/1.0/ | `ff5819e3a7b713c3bd3107b7de3d51fe0a347aa5d8444f0efdcf2345ef0a8b63` | 530482 |
| `papers/2005.11401-retro.pdf` | Retrieval-Augmented Generation for Knowledge-Intensive NLP Tasks (Lewis et al.) | v4 | https://arxiv.org/pdf/2005.11401 | arXiv.org perpetual, non-exclusive distribution license v1.0 — https://arxiv.org/licenses/nonexclusive-distrib/1.0/ | `23e3249e9a1e75418d82efecab0ea8c4d033b89c93742f63208d47ce01f21233` | 885323 |

The arXiv license is a non-exclusive distribution license granted to arXiv;
copyright remains with the authors. These copies are kept only as local parser
test fixtures.

Download commands (recorded for provenance, not to be re-run):

```powershell
curl.exe -L --fail -A "WikiMind-fixture-downloader/1.0" -o tests/fixtures/papers/1706.03762-attention.pdf https://arxiv.org/pdf/1706.03762
curl.exe -L --fail -A "WikiMind-fixture-downloader/1.0" -o tests/fixtures/papers/1406.2661-gan.pdf https://arxiv.org/pdf/1406.2661
curl.exe -L --fail -A "WikiMind-fixture-downloader/1.0" -o tests/fixtures/papers/2005.11401-retro.pdf https://arxiv.org/pdf/2005.11401
```

## Synthetic contradiction fixture

`papers/contradiction-note.txt` is a **synthetic test fixture, not a real
citation**. It restates one concrete quantitative claim from the Attention
paper (28.4 BLEU on WMT 2014 English-to-German) and deliberately contradicts
it (claims 18.4 BLEU). Never cite it as fact.

- SHA-256: `4ef572f4fd21de0d5e1865f0055a4a11b60fdf82ec0ec14ebef3b8ae3bf22756`
- Bytes: 725

## Lock

Later lanes may read these files but must not edit or replace them. Parser
changes must keep the fixtures byte-identical.

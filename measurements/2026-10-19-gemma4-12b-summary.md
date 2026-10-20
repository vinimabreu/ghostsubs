# Measurement 2026-10-19

Model `gemma4:12b` (digest `6114515d63c17436a7c0417d82820ac65ad643e2806c5a3c89cb62846436ed0b`), prompt `names-v2`, temperature 0, thinking off, Ollama on http://127.0.0.1:11434. Darwin 27.0.0 arm64.

## Naming: 159 made-up descriptors, 3 runs, no cache

| | run 1 | run 2 | run 3 |
|---|---|---|---|
| named by the model, checked, correct | 148 | 148 | 148 |
| checked, correct but cut short | 2 | 2 | 2 |
| checked, wrong (passed the check) | 6 | 6 | 6 |
| rejected by the check | 3 | 3 | 3 |
| of those, code cleanup shown and correct | 0 | 0 | 0 |
| name shown is correct, all paths | 148 | 148 | 148 |
| kind label matches the truth | 141 | 141 | 141 |
| subscription or not, matches the truth | 154 | 154 | 154 |
| median seconds per call | 0.753 | 0.388 | 0.402 |

Code cleanup only (no model): 99 of 159 correct, 6 cut short, 54 wrong.

Descriptors that also appear in the example statements (looked at while the prompt and the check were written): 65 of 71 shown right (cleanup only 43). The others: 83 of 88 (cleanup only 56).

Stability: 159 of 159 descriptors got the same outcome, name and kind in all 3 runs; 159 got a character-identical answer.

## End to end: the four example statements

| | with the model | code cleanup only |
|---|---|---|
| truth | 42 | 42 |
| detected | 42 | 45 |
| found | 37 | 24 |
| found under another name | 5 | 18 |
| missed | 0 | 0 |
| false positives | 0 | 3 |
| precision | 0.881 | 0.533 |
| recall | 0.881 | 0.571 |

A series counts as found when merchant, cadence and current amount all match the truth. Found under another name: right cadence and amount, wrong merchant name.

## Time: command line, empty cache, then the cache it left

| file | cold | descriptors asked | cached | no model |
|---|---|---|---|---|
| bank-a.csv | 29.78 s | 28 | 0.11 s | 0.08 s |
| card-b.csv | 43.75 s | 44 | 0.3 s | 0.3 s |
| export-c.csv | 17.1 s | 17 | 0.08 s | 0.08 s |
| bank-d.csv | 11.15 s | 11 | 0.08 s | 0.07 s |

Total: 101.78 s cold, 0.57 s cached. The model was already loaded in Ollama memory (a warm-up call ran first).

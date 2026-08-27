---
type: Product Backlog Item
id:
parent: 2811
title: Tolerate HTR errors — fuzzy and variant matching evaluated
---

# S10 · Tolerate HTR errors — fuzzy and variant matching evaluated

**Story.** As a researcher, I want a search for "Anders Persson" to find pages where the HTR read "Anders Persson" as "Anders Perſson" or "Andcrs Persson", so that the recognition errors we know exist do not hide the pages I am looking for.

## Why it matters

HTR output has a character error rate; exact matching over it silently loses hits. Solr offers fuzzy queries, n-gram fields and phonetic filters, each with a cost in index size and false positives. This story measures rather than guesses.

## What this delivers

- An evaluation on a ground-truth set (pages with manual transcriptions): recall and precision for exact, fuzzy (edit distance 1–2), character n-gram and a Swedish phonetic filter, with index-size and latency numbers.
- The chosen configuration applied to the schema (S02) and the query builder (S06), with the evaluation kept as a regression test.

## Done when

- [ ] A written comparison with numbers; the chosen approach improves recall on the ground-truth set without an unacceptable precision loss, as judged by an archivist.

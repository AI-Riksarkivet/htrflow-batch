---
type: Product Backlog Item
id: 2897
parent: 2800
title: Let urgent volumes jump the queue
---

# B18 · Let urgent volumes jump the queue

**Story.** As an archivist with a researcher waiting, I want to mark a
volume or campaign as urgent so that it runs ahead of the bulk backlog,
without anyone having to pause the backlog by hand.

## Why it matters

Once a large campaign is queued, a single requested volume would otherwise
wait behind hundreds of others. Kueue supports priority classes natively;
the work is exposing that as one word in the campaign file.

## What this delivers

- Two priority classes (`htr-interactive` above `htr-bulk`) defined by the
  chart.
- A `priority:` field on a campaign (default bulk) that the reconciler
  maps onto the job.
- Utan `WorkloadPriorityClass`-objekt och preemption i ClusterQueue är `priority:` avvisad av Kueues webhook — de hör till den här storyn. (revision 2026-09-07, X17)
- Levererat som ordning, inte preemption (revision 2026-09-16): chartet
  renderar tre `WorkloadPriorityClass` (`htr-interactive` 1000, `htr-bulk` 0,
  `htr-idle` -10) via `queue.priorityClasses`; Kueue släpper in efter klass
  före ålder, men `withinClusterQueue` är fortfarande `Never`, så en högre
  klass går före allt som väntar och avbryter aldrig en kampanj som kör.
  Kueue avvisar inte ett okänt klassnamn (ingen Workload, ingen händelse,
  kampanjen står "Queued" för evigt), så `converter.yaml` fick
  `priority_classes` som speglar chartets lista och `validate` avvisar ett
  `priority:` utanför den. Kvar i storyn: beslutet om preemption.

## Done when

- [x] With the bulk queue full, an interactive-priority volume is admitted
      next (next admission, once quota comes back: preemption is off).
- [x] Documented in the campaign YAML reference.

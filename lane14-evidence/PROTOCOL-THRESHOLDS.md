# V0_PROTOCOL.md — verbatim quotes (read for work card P2)

Source: `V0_PROTOCOL.md` at pinned base `f45a67ce31a9ed1be52200ec77494bdcffd29b2b`.
The file exists; Phase 0 P2 is satisfied. Quotes below are verbatim.

## Question-set requirements (§ Setup, lines 21–27)

> **Questions.** At least 130, written by hand and frozen before either system runs.
>
> - 40 single-hop
> - 60 multi-hop
> - 30 contradiction or aggregation
>
> Draft questions from the papers and their cross-citations only. Never draft them from either system's output, because that favors that system. Write a grading rubric for each question.

Also verbatim from § Setup:

> **Corpus.** 40 to 50 papers from that field. It must include at least one real retraction or live contradiction. Add a hand-picked set of papers that cite each other, so multi-hop questions exist.

> **Models.** Use a Flash-class model for distilling and updating. Use a Pro-class model only for the final answer and for entailment checks. The judge comes from a different model family than the generator. It is blind to which system wrote the answer and sees answers in random order.

## Decision rule (§ Metrics and decision rule, line 37)

> Run each arm 3 times. Report bootstrap 95% confidence intervals over questions. The decision statistic for M1 and M2 is the lower bound L of that interval on the paired difference (compile minus baseline). The judge is blind. A human audits a random 20% sample, also blind.

## Pass thresholds (§ Metrics and decision rule, table lines 39–49)

| Metric | What it measures (verbatim) | Gate (verbatim) |
| --- | --- | --- |
| M1 | Paired accuracy difference on multi-hop and contradiction questions | Decides the outcome row |
| M2 | Paired accuracy difference on single-hop questions | L at or above -5 points |
| M3a | Anchor validity: cited passage exists and contains the quoted span (deterministic) | At or above 95% after repair |
| M3b | Entailment faithfulness: the passage supports the claim (judge, both systems) | At or above 90%. Below 80% is a kill. |
| M4 | Amortized cost per paper at 50 papers, with 1 removal drill and 1 repair pass, plus the cost curve at 10, 20, 30, 50 | At or below $0.08. Target $0.04. Curve must not grow faster than linear. |
| M5 | Cost per query | At or below $0.02. Target $0.01. |
| M6 | Propagation errors on the hand-built oracle (leaks plus wrongly deleted claims) | Exactly 0 |
| M7 | Interviews where the researcher confirms the pain from past behavior | At least 3 of 5 |
| M8 | Share of level 2 content that reduces to level 1 claim IDs (reported, no gate) | Informs the ledger design |

> The integrity gate is M3a, M3b, and M6. The cost gate is M4 and M5. The wedge gate is M7.

## Decision table (verbatim, lines 53–58)

| M1 result | All gates pass | Cost gate fails only | Integrity gate fails |
| --- | --- | --- | --- |
| WIN: L at or above +10 | PROCEED | PROCEED, then a cost-redesign sprint | KILL |
| PARITY: L from 0 to +10 | PROCEED if M7 passes, else RE-WEDGE | PIVOT, with M4 at or below $0.15 | KILL |
| INCONCLUSIVE: L below 0 and upper bound U at or above 0 | EXTEND once | EXTEND once, plus cost work | Fix as bugs, then EXTEND once with fresh questions |
| LOSS: U below 0 | KILL | KILL | KILL |

## Overrides (verbatim, line 60)

> If M2's L is below -5 points, a PROCEED becomes a PIVOT. If M3b is below 80%, the result is KILL whatever else passes. M3b between 80% and 90% is repairable, like M3a: fix the system, then re-run on fresh questions. Below 80% remains the unconditional kill. If M3a stays under 95% after repair attempts, treat it as an integrity failure. Before repair, treat it as a bug to fix and re-run. If M4 or M5 passes the gate but misses its target, apply the D12 levers before M0 starts.

## Power check (verbatim, line 70)

> About 100 paired questions give a confidence interval of roughly 8 to 10 points either side. Before freezing, compute the real interval from a pilot of 20 questions. If it is too wide for the +10 threshold, add questions before running, not after.

## Oracle requirements (verbatim, lines 74–80)

> **Oracle.** Build it by hand, before either system runs, and do not derive it from the system's own provenance. Otherwise the test is circular. It needs at least 20 entangled claims across at least 5 removal drills. Use the real retraction plus synthetic retractions on papers with low, medium, and high connectivity. Each drill must include these cases:
>
> - A claim supported by the removed paper and by another paper. It must survive.
> - A claim supported only by the removed paper. It must be flagged or dropped.
> - Prose that paraphrases the removed paper with no attribution. The leak scan must catch it.
>
> M6 counts both leaks and wrongly deleted claims across all drills. The pass value is 0. Also record the cost of each drill and one repair pass for M4. After all repairs, run one fresh synthetic retraction drill that was built after the repairs and never used during them. It must also show 0 errors, so the system is not tuned to the oracle.

## Checkpoint (verbatim, line 92)

> **Checkpoint.** If the anchor validity rate (M3a) is below 80% at the end of week 2, stop and repair before running the comparison. Citation integrity is the base of the thesis.

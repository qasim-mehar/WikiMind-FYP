# Compendium V0 PROTOCOL

V0 is a three-week test that decides whether to build Compendium, using rules written before any result exists.

Oct 16, 2026 · @Qasim Ali

## Purpose

V0 answers one question: does a two-level compiled wiki match or beat a strong RAG system on accuracy, at a cost we can sell, while keeping citations honest?

V0 is scripts and notebooks. It has no UI, no auth, no queue, and no multi-tenancy. It reuses the decisions in the DECISIONS doc.

Two rules bind this protocol. The decision rule below is frozen before any result exists. Commit this doc to the repo with a timestamp before day 1 of the build.

## Setup

**Target user.** Researchers who run literature reviews or evidence synthesis in one narrow field. Pick the field on day 1.

**Corpus.** 40 to 50 papers from that field. It must include at least one real retraction or live contradiction. Add a hand-picked set of papers that cite each other, so multi-hop questions exist.

**Questions.** At least 130, written by hand and frozen before either system runs.

- 40 single-hop
- 60 multi-hop
- 30 contradiction or aggregation

Draft questions from the papers and their cross-citations only. Never draft them from either system's output, because that favors that system. Write a grading rubric for each question.

**Arm A: strong RAG baseline.** Hybrid BM25 and vector search, a reranker, and sensible chunking. Give it 8 hours of tuning. Log the hours.

**Arm B: minimal compile.** A level 1 distiller with content-hash passage anchors, an anchor validator, entity and concept nodes with claim ledgers, and a cost meter. Give it the same 8 hours of tuning, logged. The build itself takes longer than the tuning budget.

**Models.** Use a Flash-class model for distilling and updating. Use a Pro-class model only for the final answer and for entailment checks. The judge comes from a different model family than the generator. It is blind to which system wrote the answer and sees answers in random order.

## Metrics and decision rule

Run each arm 3 times. Report bootstrap 95% confidence intervals over questions. The decision statistic for M1 and M2 is the lower bound L of that interval on the paired difference (compile minus baseline). The judge is blind. A human audits a random 20% sample, also blind.

| Metric | What it measures | Gate |
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

The integrity gate is M3a, M3b, and M6. The cost gate is M4 and M5. The wedge gate is M7.

| M1 result | All gates pass | Cost gate fails only | Integrity gate fails |
| --- | --- | --- | --- |
| WIN: L at or above +10 | PROCEED | PROCEED, then a cost-redesign sprint | KILL |
| PARITY: L from 0 to +10 | PROCEED if M7 passes, else RE-WEDGE | PIVOT, with M4 at or below $0.15 | KILL |
| INCONCLUSIVE: L below 0 and upper bound U at or above 0 | EXTEND once | EXTEND once, plus cost work | Fix as bugs, then EXTEND once with fresh questions |
| LOSS: U below 0 | KILL | KILL | KILL |

**Overrides.** If M2's L is below -5 points, a PROCEED becomes a PIVOT. If M3b is below 80%, the result is KILL whatever else passes. M3b between 80% and 90% is repairable, like M3a: fix the system, then re-run on fresh questions. Below 80% remains the unconditional kill. If M3a stays under 95% after repair attempts, treat it as an integrity failure. Before repair, treat it as a bug to fix and re-run. If M4 or M5 passes the gate but misses its target, apply the D12 levers before M0 starts.

**PIVOT** means repositioning as an evidence-integrity tool (retraction, provenance, audit) instead of a Q&A tool. It is allowed only if M4 is at or below $0.15, which stays under revenue per paper. Projected COGS must also be $9 or less per user-month.

**RE-WEDGE** means pick a new target user and run 5 new interviews. It does not mean pivot, because the interviews gave no demand evidence. If the new interviews pass M7, re-apply the decision table to the existing M1 result without re-running the comparison. If they fail too, the result is KILL.

**EXTEND once.** Write at least 50 new questions, freeze them, and re-run. Allow one extension only. If the result is still INCONCLUSIVE, the answer is no.

**Fresh questions.** After any system fix made after seeing results, the old questions are no longer held out. Use new questions and a new frozen run.

**Power check.** About 100 paired questions give a confidence interval of roughly 8 to 10 points either side. Before freezing, compute the real interval from a pilot of 20 questions. If it is too wide for the +10 threshold, add questions before running, not after.

## Retraction oracle and interviews

**Oracle.** Build it by hand, before either system runs, and do not derive it from the system's own provenance. Otherwise the test is circular. It needs at least 20 entangled claims across at least 5 removal drills. Use the real retraction plus synthetic retractions on papers with low, medium, and high connectivity. Each drill must include these cases:

- A claim supported by the removed paper and by another paper. It must survive.
- A claim supported only by the removed paper. It must be flagged or dropped.
- Prose that paraphrases the removed paper with no attribution. The leak scan must catch it.

M6 counts both leaks and wrongly deleted claims across all drills. The pass value is 0. Also record the cost of each drill and one repair pass for M4. After all repairs, run one fresh synthetic retraction drill that was built after the repairs and never used during them. It must also show 0 errors, so the system is not tuned to the oracle.

**Interviews.** Hold 5 interviews with evidence-synthesis researchers, in parallel with the build. Recruit at least 3 people outside your own network. Ask what they did the last time a cited paper was retracted, contradicted, or superseded. Ask how they keep a review current. Do not describe Compendium until the end. Score a participant as confirming the pain only if their past behavior shows it cost them time or caused an error.

## Schedule

Plan for about three weeks of part-time solo work. Question writing and the oracle take the most time.

1. **Week 1, setup.** Check the OKF repo (10 minutes: live, license, spec version). Pick the field. Assemble the corpus. Commit this protocol. Write the questions, run a 20-question pilot for the power check, then freeze the question set. Book the 5 interviews.
2. **Week 2, build.** Tune the baseline (8 hours, logged). Build the compile arm: distiller, anchor validator, claim ledger, cost meter (8 hours of tuning, logged). Build the retraction oracle by hand. Hold interviews.
3. **Week 3, run and decide.** Run both arms 3 times. Run the blind judge and the human audit. Run the removal drills and the repair pass. Measure the cost curve at 10, 20, 30, and 50 papers. Apply the decision table. Write the measured numbers into PROJECT.md v0.2.

**Checkpoint.** If the anchor validity rate (M3a) is below 80% at the end of week 2, stop and repair before running the comparison. Citation integrity is the base of the thesis.

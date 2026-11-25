"""Phase A2: author the Lane 14 V0 question set from the four source documents.

Questions are drafted ONLY from the full text of the four sources in the
document store (projects/documents/channels read via live.py). No claims, no
edges, and no system output are consulted while authoring. A grading rubric is
written for every question, as V0_PROTOCOL.md requires.

Composition (V0_PROTOCOL.md "Questions", verbatim): at least 130 questions,
40 single-hop, 60 multi-hop, 30 contradiction or aggregation. A fourth group
of 20 deliberately unanswerable questions (U) is added because the Lane 14
work card's B5 rubric requires testing honest insufficiency; "at least 130"
permits the addition and the specified 40/60/30 mix is preserved verbatim.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

EV = Path(__file__).resolve().parent

A = "1706.03762-attention"
G = "1406.2661-gan"
R = "2005.11401-retro"
N = "contradiction-note"


def q(qid, bucket, docs, question, target, rubric):
    return {
        "id": qid,
        "bucket": bucket,
        "docs": docs,
        "question": question,
        "target": target,
        "rubric": rubric,
    }


S = []
S += [
    q("S001", "single-hop", [A], "What new network architecture is proposed in 'Attention Is All You Need', and what two mechanisms does it dispense with?", "Transformer; recurrence and convolutions", "Must name Transformer and state that it dispenses entirely with recurrence and convolutions."),
    q("S002", "single-hop", [A], "What BLEU score did the Transformer (big) model achieve on WMT 2014 English-to-German?", "28.4 BLEU", "Must give 28.4, cite abstract or section 6.1/Table 2."),
    q("S003", "single-hop", [A], "How many identical layers are in the Transformer encoder stack and in the decoder stack?", "6 and 6", "Both stacks have N=6."),
    q("S004", "single-hop", [A], "What is the dimension d_model of the Transformer's sub-layer outputs and embeddings?", "512", "512."),
    q("S005", "single-hop", [A], "How many parallel attention heads does the Transformer use, and what are d_k and d_v?", "h=8, d_k=d_v=64", "8 heads and 64 for both key and value dimension."),
    q("S006", "single-hop", [A], "What is the inner dimensionality d_ff of the position-wise feed-forward network?", "2048", "2048."),
    q("S007", "single-hop", [A], "What positional encoding scheme does the Transformer use?", "sine and cosine functions of different frequencies", "Sinusoidal encoding; PE(pos,2i)=sin(pos/10000^(2i/d_model)) etc."),
    q("S008", "single-hop", [A], "What is the attention function of Scaled Dot-Product Attention?", "softmax(QK^T/sqrt(d_k))V", "Must include scaling by sqrt(d_k) and softmax weighting of values."),
    q("S009", "single-hop", [A], "What Adam settings (beta1, beta2, epsilon) are reported for the Transformer?", "0.9, 0.98, 1e-9", "All three values."),
    q("S010", "single-hop", [A], "How many warmup steps are used in the Transformer learning-rate schedule?", "4000", "4000."),
    q("S011", "single-hop", [A], "How many training steps and how many hours for the base Transformer model?", "100,000 steps; 12 hours", "Both values."),
    q("S012", "single-hop", [A], "According to the abstract, what single-model state-of-the-art BLEU did the Transformer reach on WMT 2014 English-to-French?", "41.8 BLEU", "41.8 from the abstract (note: body says 41.0, asked separately in C bucket)."),
    q("S013", "single-hop", [G], "How is training generative adversarial nets formulated as a game?", "minimax two-player game between generator G and discriminator D", "Minimax/two-player, G versus D."),
    q("S014", "single-hop", [G], "For a fixed generator G, what is the optimal discriminator D*_G(x)?", "p_data(x)/(p_data(x)+p_g(x))", "Exact formula."),
    q("S015", "single-hop", [G], "What is the global minimum value of the virtual training criterion C(G)?", "-log 4", "-log 4, attained iff p_g = p_data."),
    q("S016", "single-hop", [G], "C(G) equals -log 4 plus twice which divergence?", "Jensen-Shannon divergence", "JSD between p_data and p_g."),
    q("S017", "single-hop", [G], "Which three datasets were adversarial nets trained on?", "MNIST, Toronto Face Database (TFD), CIFAR-10", "All three."),
    q("S018", "single-hop", [G], "What value of k (discriminator steps per generator step) was used in the experiments?", "k = 1", "1."),
    q("S019", "single-hop", [G], "What Parzen-window log-likelihood did adversarial nets achieve on MNIST?", "225 +/- 2", "225 +/- 2 (not 214 etc.)."),
    q("S020", "single-hop", [G], "Why train G to maximize log D(G(z)) rather than minimize log(1-D(G(z)))?", "stronger gradients early because log(1-D) saturates", "Saturation / stronger gradients; same fixed point."),
    q("S021", "single-hop", [G], "Do adversarial nets require Markov chains or unrolled approximate inference?", "No", "No Markov chains, no unrolled inference; backprop and forward sampling only."),
    q("S022", "single-hop", [G], "What is the 'Helvetica scenario' in adversarial nets?", "G collapses many z to the same x; D and G must stay synchronized", "Mode collapse from insufficient D updates."),
    q("S023", "single-hop", [R], "What is the parametric memory component of RAG models?", "pre-trained seq2seq model (BART-large)", "BART-large seq2seq, about 400M parameters."),
    q("S024", "single-hop", [R], "What is the non-parametric memory of RAG?", "a dense vector index of Wikipedia", "Dense vector index of Wikipedia accessed by a neural retriever."),
    q("S025", "single-hop", [R], "Which retriever do RAG models use?", "Dense Passage Retriever (DPR), BERT-based bi-encoder", "DPR, BERTBASE bi-encoder, MIPS."),
    q("S026", "single-hop", [R], "What are the two RAG formulations and how do they differ?", "RAG-Sequence (same document for the whole sequence) vs RAG-Token (different document per token)", "Both names and the distinction."),
    q("S027", "single-hop", [R], "Which Wikipedia dump is used as the non-parametric knowledge source?", "December 2018 dump", "December 2018."),
    q("S028", "single-hop", [R], "How many documents are in the Wikipedia index and how long is each?", "21M documents of 100 words each", "21M disjoint 100-word chunks."),
    q("S029", "single-hop", [R], "What Exact Match score does RAG-Sequence achieve on Natural Questions?", "44.5 EM", "44.5."),
    q("S030", "single-hop", [R], "What Exact Match score does RAG-Token achieve on Natural Questions?", "44.1 EM", "44.1."),
    q("S031", "single-hop", [R], "What is RAG-Token's FEVER 3-way label accuracy?", "72.5", "72.5 (89.5 is the 2-way figure)."),
    q("S032", "single-hop", [R], "In human evaluation, what percent of Jeopardy comparisons found RAG more factual than BART?", "42.7%", "42.7%."),
    q("S033", "single-hop", [R], "How can RAG's world knowledge be updated without retraining?", "replace / hot-swap the non-parametric index", "Index hot-swapping."),
    q("S034", "single-hop", [R], "What is retrieval collapse in RAG training?", "retriever returns same documents regardless of input; generator ignores them; model equals BART", "Collapse, generator ignores docs, equivalent to BART."),
    q("S035", "single-hop", [R], "How many trainable parameters do RAG models have in total?", "626M", "626M (110M+110M BERT plus 406M BART)."),
    q("S036", "single-hop", [N], "What does the contradiction-note fixture say about itself?", "synthetic test fixture, not a real citation, deliberately false", "Must note it is synthetic/false and should not be cited."),
    q("S037", "single-hop", [N], "Which source paper does the contradiction note refer to?", "Attention Is All You Need (Vaswani et al., arXiv:1706.03762)", "Paper name and arXiv id."),
    q("S038", "single-hop", [N], "What 28.4 BLEU claim does the note quote from the Attention paper's abstract?", "28.4 BLEU on WMT14 EN-DE, more than 2 BLEU over best ensembles", "28.4 and >2 BLEU."),
    q("S039", "single-hop", [N], "What deliberately contradictory BLEU value does the note assert?", "18.4 BLEU, a full 10 BLEU below 28.4", "18.4 and the 10-point deficit."),
    q("S040", "single-hop", [N], "According to the note, did the Transformer improve on the best ensembles?", "the note says it did not", "No improvement claim."),
]

M = []
M += [
    q("M001", "multi-hop", [A], "Using the reported step time and step count, how long did base and big Transformer training take and on how many GPUs?", "base 0.4s x 100k = 12h; big 1.0s x 300k = 3.5 days; 8 NVIDIA P100", "Both durations and the 8 P100."),
    q("M002", "multi-hop", [A], "Why does the Transformer scale dot products by 1/sqrt(d_k), and what happens for large d_k without scaling?", "large dot products push softmax into small-gradient regions; scaling counteracts", "Gradient-saturation explanation."),
    q("M003", "multi-hop", [A], "The encoder has two sub-layers and the decoder three; across six layers each, how many sub-layer applications occur?", "6x2 + 6x3 = 30", "30; must combine counts."),
    q("M004", "multi-hop", [A], "With h=8 heads of d_k=d_v=64 and d_model=512, what is the concatenated head output dimension before the final projection?", "8 x 64 = 512", "512; must combine."),
    q("M005", "multi-hop", [A], "Table 1 lists maximum path lengths: how does the Transformer's path compare with ByteNet's and ConvS2S's?", "O(1) vs O(log_k n) for ByteNet and O(n/k) for ConvS2S", "All three values."),
    q("M006", "multi-hop", [A], "According to the complexity discussion, when is self-attention faster than recurrent layers?", "when sequence length n is smaller than representation dimension d", "n < d."),
    q("M007", "multi-hop", [A], "Table 3 row (E) found learned positional embeddings nearly identical to sinusoids; why keep sinusoids?", "they may allow extrapolation to longer sequences", "Extrapolation rationale."),
    q("M008", "multi-hop", [A], "Which checkpoint-averaging and beam settings were used for base versus big translation models?", "base last 5 checkpoints (10-min intervals); big last 20; beam 4, length penalty 0.6", "Both averaging counts plus beam and alpha."),
    q("M009", "multi-hop", [A], "For English constituency parsing, how did depth/width and decoding settings differ from the base translation model?", "4 layers, d_model=1024; beam 21, alpha 0.3, max output length +300", "Depth/width and decoding changes."),
    q("M010", "multi-hop", [A], "What do Table 3 rows (A)-(D) conclude about heads, d_k, model size and dropout?", "single-head 0.9 BLEU worse; too many heads hurt; smaller d_k hurts; bigger models better; dropout helpful", "At least three of five."),
    q("M011", "multi-hop", [G], "How do Proposition 1 and Theorem 1 connect the optimal discriminator to the global optimum?", "D* = p_data/(p_data+p_g); at p_g=p_data, D*=1/2 and C(G)=-log 4", "Must link D* to C(G)."),
    q("M012", "multi-hop", [G], "Why not optimize D to completion in the inner loop, and what is the practical alternative?", "prohibitive and overfits; alternate k D steps with one G step (k=1)", "Reason plus k-step scheme."),
    q("M013", "multi-hop", [G], "How do the generator/discriminator activation choices support backprop-only training?", "generator rectifier+sigmoid, discriminator maxout+dropout; no feedback loops allows piecewise linear units", "Activations plus no Markov chain."),
    q("M014", "multi-hop", [G], "Compare adversarial nets with Deep GSN on the MNIST and TFD Parzen scores.", "MNIST 225 vs 214; TFD 2057 vs 1890", "Both comparisons."),
    q("M015", "multi-hop", [G], "Per Table 2, how do adversarial nets compare with MCMC-based models on sampling and p(x) evaluation?", "no Markov chain for sampling; p(x) not explicitly represented, Parzen approximation", "Both dimensions."),
    q("M016", "multi-hop", [G], "What statistical advantage is claimed for updating G only through gradients flowing from D?", "input components not copied into generator parameters; can represent sharp/degenerate distributions", "Both points."),
    q("M017", "multi-hop", [G], "List the extensions proposed in the conclusion / future work.", "conditional GAN, learned approximate inference, semi-supervised learning, efficiency improvements", "At least three."),
    q("M018", "multi-hop", [R], "How does RAG-Sequence decoding approximate argmax and why need Thorough or Fast Decoding?", "beam search per document then marginalize; Thorough adds forward passes, Fast approximates for long outputs", "Both decoding procedures."),
    q("M019", "multi-hop", [R], "How does RAG's FEVER accuracy compare with state of the art and BART on 3-way and 2-way?", "3-way 72.5 vs 76.8 SotA vs 64.0 BART; 2-way 89.5 vs 92.2 vs 81.1", "Both tasks."),
    q("M020", "multi-hop", [R], "Why does BM25 outperform learned retrieval on FEVER but not on other tasks?", "FEVER claims are entity-centric so word overlap works; differentiable retrieval is crucial for QA", "Explanation for both."),
    q("M021", "multi-hop", [R], "Summarize the index hot-swapping experiment and its conclusion.", "82 world leaders; 70%/68% matched, 12%/4% mismatched; knowledge updated by replacing the index", "Numbers and conclusion."),
    q("M022", "multi-hop", [R], "What did the null-document experiments find?", "three logit models tried, none improved, mechanism omitted; MS-MARCO learns fixed retrieval for some questions", "Both findings."),
    q("M023", "multi-hop", [R], "Compare RAG with T5-11B and T5-large on Natural Questions including parameter counts.", "RAG-Seq 44.5 with 626M; T5-11B 34.5 with 11B; T5-large 28.9 with 770M", "All three."),
    q("M024", "multi-hop", [R], "How large is the non-parametric index in vectors, dimensions, values and memory?", "21M x 728 = 15.3B values; ~100GB, 36GB after 8-bit compression", "All figures."),
    q("M025", "multi-hop", [R], "How does RAG produce correct answers when the answer is absent from retrieved documents?", "parametric knowledge; 11.8% accuracy on NQ versus 0% for extractive models", "Both."),
    q("M026", "multi-hop", [R], "How does RAG performance change as more documents are retrieved?", "RAG-Sequence improves monotonically; RAG-Token peaks at 10; BLEU/Rouge trade-off", "Both model behaviours."),
    q("M027", "multi-hop", [R], "What retrieval counts were used at test time for each task and model?", "15 docs for RAG-Token QA; 50 for RAG-Sequence QA; 10 for generation tasks", "All three."),
    q("M028", "multi-hop", [R], "How do RAG and BART compare in the Jeopardy human evaluation for factuality and specificity?", "RAG better 42.7%/37.4%; BART better 7.1%/16.8%", "Both dimensions."),
    q("M029", "multi-hop", [R], "Why did the authors train a BART baseline for Jeopardy and MS-MARCO?", "to compare parametric-only generation with parametric plus non-parametric generation", "Rationale."),
    q("M030", "multi-hop", [R], "How is RAG related to memory networks and to retrieve-and-edit approaches?", "external memory of human-readable/writable raw text; aggregates several documents rather than lightly editing one", "Both relations."),
    q("M031", "multi-hop", [A, R], "RAG cites Vaswani et al. [58]. What architecture does that cited paper introduce and what are its headline translation results?", "Transformer; 28.4 BLEU EN-DE and 41.8/41.0 BLEU EN-FR", "Architecture and at least one BLEU."),
    q("M032", "multi-hop", [A, R], "How many GPUs did the Transformer and RAG papers use for training, and which GPU models?", "8 NVIDIA P100 for Transformer; 8 x 32GB NVIDIA V100 for RAG", "Both."),
    q("M033", "multi-hop", [A, R], "Compare the parameter counts of Transformer base (Table 3) and RAG's BART-large generator.", "65M versus 400M/406M", "Both."),
    q("M034", "multi-hop", [A, R], "Both papers use beam search: give the Transformer translation and RAG generation beam settings.", "Transformer beam 4, alpha 0.6; RAG beam 4 for generation, greedy for QA", "Both."),
    q("M035", "multi-hop", [A, R], "Which optimization techniques appear in the Transformer paper and which in the RAG paper?", "Transformer Adam with warmup schedule; RAG Adam with mixed precision via Fairseq", "Both."),
    q("M036", "multi-hop", [A, R], "What do the two papers say about code availability?", "Transformer tensor2tensor; RAG HuggingFace Transformers plus interactive demo", "Both."),
    q("M037", "multi-hop", [A, R], "Compare the experimental corpora: Transformer's WMT14 data versus RAG's Wikipedia index.", "4.5M EN-DE pairs and 36M EN-FR sentences versus 21M Wikipedia documents", "Both."),
    q("M038", "multi-hop", [A, R], "Which paper introduces 'non-parametric memory', and which paper's architecture family supplies RAG's generator?", "RAG introduces the term; Transformer is the attention-based seq2seq family behind BART", "Both."),
    q("M039", "multi-hop", [A, R], "How do the two papers handle long sequences: restricted attention versus retrieval?", "Transformer restricted attention neighbourhood r (O(n/r)); RAG retrieves top-K documents", "Both."),
    q("M040", "multi-hop", [A, G, R], "What regularization is reported in the Transformer, GAN and RAG papers?", "Transformer dropout 0.1 and label smoothing 0.1; GAN dropout in discriminator; RAG mixed precision training", "At least two."),
    q("M041", "multi-hop", [A, G, R], "Which BLEU-related scores appear across the corpus?", "Transformer 28.4/41.8/41.0/27.3; RAG Q-BLEU-1 22.2; GAN uses Parzen log-likelihood, not BLEU", "At least one from two papers."),
    q("M042", "multi-hop", [A, R], "Name the sequence-to-sequence baselines compared in RAG and the transduction baselines compared in the Transformer paper.", "RAG: BART, T5; Transformer: GNMT, ConvS2S, ByteNet, Deep-Att", "At least one from each."),
    q("M043", "multi-hop", [A, R], "Where does reference [58] of the RAG paper appear and what does it cite?", "Vaswani et al., Attention is all you need, NeurIPS 2017", "Correct reference."),
    q("M044", "multi-hop", [A, G, R], "Which papers report using eight GPUs and which GPU models?", "Transformer 8 P100; RAG 8 V100; GAN no GPU count given", "Both plus GAN absence."),
    q("M045", "multi-hop", [A, R], "What role does 'attention' play in the Transformer paper versus in RAG's DPR?", "core self-attention mechanism in Transformer; DPR uses a BERT bi-encoder with MIPS retrieval", "Both."),
    q("M046", "multi-hop", [A, R], "Which model has the smallest and which the largest parameter count across the Transformer and RAG tables?", "smallest about 28M in Transformer Table 3; largest T5-11B at 11B in RAG", "Both."),
    q("M047", "multi-hop", [A, R], "Transformer cites Bahdanau et al. [2] and RAG cites BART [32]; what common task family do these belong to?", "neural machine translation / sequence-to-sequence generation", "Task family."),
    q("M048", "multi-hop", [A, G, R], "Which of the documents explicitly mention Wikipedia?", "primarily the RAG document; the Transformer and GAN documents do not use Wikipedia as a corpus", "RAG and absence elsewhere."),
    q("M049", "multi-hop", [A], "Which length-penalty settings are reported for Transformer translation and for parsing?", "alpha = 0.6 for translation; alpha = 0.3 for constituency parsing", "Both."),
    q("M050", "multi-hop", [A, G, R], "Compare the experimental scales of the three papers.", "GAN MNIST/TFD/CIFAR-10; Transformer 4.5M/36M sentence pairs; RAG 21M Wikipedia documents", "All three."),
    q("M051", "multi-hop", [A, R], "Which paper reports a 40K-sentence training setting and which reports 21M documents?", "Transformer parsing on 40K WSJ sentences; RAG Wikipedia 21M documents", "Both."),
    q("M052", "multi-hop", [A, G, R], "What is the longest explicit training time reported in the corpus?", "Transformer big trained for 3.5 days on 8 P100; no explicit times for GAN or RAG", "Transformer figure plus absence."),
    q("M053", "multi-hop", [A, G, R], "Which papers use BLEU and which use Exact Match or human evaluation?", "Transformer reports BLEU; GAN uses Parzen log-likelihood; RAG uses EM, Q-BLEU-1, Rouge-L, label accuracy and human eval", "At least two papers."),
    q("M054", "multi-hop", [A, N], "How do the contradiction note and the Attention paper disagree about the EN-DE BLEU, and which is marked false?", "28.4 in the paper versus 18.4 in the note; the note is marked synthetic/false", "Both values and false marker."),
    q("M055", "multi-hop", [A, R], "Is RAG's BART generator in the same model family as the architecture introduced by the Transformer paper?", "yes, BART is a pre-trained seq2seq transformer in the attention family", "Family link."),
    q("M056", "multi-hop", [A, R], "Compare the state-of-the-art claims: Transformer on WMT14 and RAG on open-domain QA.", "Transformer SOTA on WMT14 EN-DE and EN-FR; RAG SOTA on NQ, WebQuestions and CuratedTrec", "Both."),
    q("M057", "multi-hop", [G, R], "Which model has a generator and discriminator, and which has a retriever and generator?", "GAN: generator and discriminator; RAG: retriever and generator", "Both."),
    q("M058", "multi-hop", [A, G, R], "Which evaluation metrics are shared across the papers?", "BLEU in Transformer; human evaluation in GAN (pairwise) and RAG (factuality/specificity)", "At least one shared or comparable metric."),
    q("M059", "multi-hop", [G, R], "Which document introduces 'Retrieval-Augmented Generation' and which discusses 'adversarial nets'?", "the RAG document and the GAN document", "Both."),
    q("M060", "multi-hop", [A, G, R], "Across all documents, list every explicit GPU count.", "8 P100 (Transformer), 8 V100 (RAG), none for GAN", "All plus absence."),
]

C = []
C += [
    q("C001", "contradiction", [A], "Does the corpus report one or more BLEU values for the Transformer on WMT14 English-to-French?", "conflict: 41.8 (abstract, Table 2) versus 41.0 (section 6.1)", "Must cite both sides."),
    q("C002", "contradiction", [A], "What BLEU values are given for EN-FR in the abstract versus section 6.1?", "41.8 versus 41.0", "Both values."),
    q("C003", "contradiction", [A], "Which parts of the Attention paper agree on 41.8 for EN-FR?", "the abstract and Table 2", "Both."),
    q("C004", "contradiction", [A], "Is 41.0 or 41.8 the value stated in section 6.1 'Machine Translation'?", "41.0", "41.0."),
    q("C005", "contradiction", [A], "What EN-FR BLEU is stated in Table 2 of the Attention paper?", "41.8", "41.8."),
    q("C006", "contradiction", [A], "What EN-FR BLEU is stated in the abstract of the Attention paper?", "41.8", "41.8."),
    q("C007", "contradiction", [A], "What does the paper say about the big model's EN-FR training cost relative to previous state of the art?", "less than one quarter of the training cost", "One quarter."),
    q("C008", "contradiction", [A], "Give the EN-DE BLEU values for Transformer big and base from Table 2.", "28.4 and 27.3", "Both."),
    q("C009", "aggregation", [A], "List all single-model EN-DE BLEU scores in Table 2 of the Attention paper.", "ByteNet 23.75, GNMT+RL 24.6, ConvS2S 25.16, MoE 26.03, Transformer base 27.3, Transformer big 28.4", "At least four."),
    q("C010", "aggregation", [A], "Which model in Table 2 has the highest EN-FR BLEU?", "Transformer (big), 41.8", "Name and value."),
    q("C011", "contradiction", [A, N], "Does the corpus say the Transformer's EN-DE BLEU is 28.4 or 18.4?", "conflict: 28.4 in the paper, 18.4 in the synthetic false note", "Both sides; flag note as false."),
    q("C012", "contradiction", [A, N], "Which of the two EN-DE BLEU values comes from the synthetic test fixture?", "18.4", "18.4 is synthetic/false."),
    q("C013", "contradiction", [A, N], "Quote the abstract sentence containing the 28.4 BLEU claim.", "'Our model achieves 28.4 BLEU ... improving ... by over 2 BLEU'", "Contains 28.4 and over 2 BLEU."),
    q("C014", "contradiction", [A, N], "Quote the note's contradictory restatement containing 18.4 BLEU.", "'only 18.4 BLEU ... a full 10 BLEU below ... did not improve'", "18.4 and the 10-point deficit."),
    q("C015", "contradiction", [A, N], "How large is the gap between the two reported EN-DE BLEU values?", "10 BLEU", "10."),
    q("C016", "contradiction", [A, N], "Does the corpus agree on whether the Transformer improved over the best ensembles?", "conflict: paper says over 2 BLEU improvement, note says no improvement", "Both sides."),
    q("C017", "contradiction", [N], "Which source paper does the contradiction note cite?", "Attention Is All You Need (arXiv:1706.03762)", "Correct paper."),
    q("C018", "contradiction", [N], "What does the note instruct readers about citing it?", "'Do not cite this file.'", "Quote or paraphrase."),
    q("C019", "contradiction", [A, N], "Which document is authoritative for the EN-DE BLEU, according to the note itself?", "the Attention paper; the note is a synthetic false restatement", "Paper authoritative."),
    q("C020", "aggregation", [A, N], "How many distinct EN-DE BLEU numbers are associated with the Transformer across the corpus?", "27.3 (base), 28.4 (big), and 18.4 (false note)", "All three with roles."),
    q("C021", "contradiction", [R], "Does the corpus give conflicting author lists for any reference?", "yes, reference R3 [62] appears with two different author lists", "Both lists or their differing names."),
    q("C022", "contradiction", [R], "Who are the authors of R3 in its first occurrence in the RAG paper?", "Shuohang Wang, Mo Yu, Xiaoxiao Guo, Zhiguo Wang, Tim Klinger, Wei Zhang, Shiyu Chang, Gerry Tesauro, Bowen Zhou, Jing Jiang", "At least several names in order."),
    q("C023", "contradiction", [R], "Who are the authors of R3 in its second occurrence in the RAG paper?", "Shuohang Wang, Mo Yu, Jing Jiang, Wei Zhang, Xiaoxiao Guo, Shiyu Chang, Zhiguo Wang, Tim Klinger, Gerald Tesauro, Murray Campbell", "At least several names in order."),
    q("C024", "contradiction", [R], "Which names differ between the two R3 author lists?", "Bowen Zhou versus Murray Campbell, Gerry Tesauro versus Gerald Tesauro, and reordered authors", "At least two differences."),
    q("C025", "aggregation", [R], "Aggregate the Natural Questions Exact Match scores of all models in RAG Table 1.", "T5-11B 34.5, T5-11B+SSM 36.6, REALM 40.4, DPR 41.5, RAG-Token 44.1, RAG-Sequence 44.5", "At least four."),
    q("C026", "aggregation", [R], "Which RAG variant wins each QA dataset in Table 1?", "NQ RAG-Sequence, TQA RAG-Sequence, WQ RAG-Token, CT RAG-Sequence", "All four."),
    q("C027", "aggregation", [A, G, R], "Aggregate all dropout rates explicitly mentioned across the three papers.", "Transformer base 0.1 and big 0.3 for EN-FR; GAN dropout in discriminator; RAG none specified", "At least Transformer rates plus absence."),
    q("C028", "aggregation", [A, G, R], "Aggregate all explicit parameter counts mentioned in the corpus.", "Transformer base 65M and big 213M; RAG 626M, BART 400M/406M, T5-11B 11B, T5-large 770M", "At least three."),
    q("C029", "aggregation", [A, G, R], "Which GPU count appears in more than one document?", "eight GPUs in Transformer (P100) and RAG (V100); none for GAN", "Both plus absence."),
    q("C030", "contradiction", [A, N], "Are the claims of a greater-than-2-BLEU improvement and a 10-BLEU deficit consistent?", "no: the paper claims improvement, the synthetic note claims a deficit", "Both sides and verdict."),
]

U = []
U += [
    q("U001", "unanswerable", [A], "What is the exact inference latency in milliseconds of the Transformer big model on the WMT 2014 test set?", "absent: no inference latency is reported", "Honest insufficiency; must not fabricate a number."),
    q("U002", "unanswerable", [A], "What weight decay coefficient did the Transformer authors use with Adam?", "absent: weight decay not stated", "Insufficiency; Adam beta/epsilon are present but weight decay is not."),
    q("U003", "unanswerable", [G], "How many NVIDIA V100 GPUs were used to train the generative adversarial nets model?", "absent: V100 GPUs belong to the RAG paper, not GAN", "Insufficiency; must not transfer the RAG fact."),
    q("U004", "unanswerable", [R], "Which December 2019 Wikipedia dump was used as the RAG knowledge source?", "absent: the corpus says December 2018", "Insufficiency; must not say 2019."),
    q("U005", "unanswerable", [G], "What learning-rate schedule was used for the GAN discriminator?", "absent: not stated", "Insufficiency."),
    q("U006", "unanswerable", [R], "What token-level batch size did RAG use during training?", "absent: 25,000-token batches belong to the Transformer paper", "Insufficiency; must not transfer."),
    q("U007", "unanswerable", [G], "Which named optimizer (e.g. Adam or SGD) was used to train adversarial nets?", "absent: only 'standard gradient-based learning rule' and momentum are mentioned", "Insufficiency; momentum is not a named optimizer here."),
    q("U008", "unanswerable", [A], "What numerical precision (e.g. FP16 mixed precision) was used to train the Transformer?", "absent: mixed precision appears only in the RAG paper", "Insufficiency."),
    q("U009", "unanswerable", [A], "What is the Transformer parsing model's F1 on the development set?", "absent: only WSJ section 23 test F1 is reported", "Insufficiency."),
    q("U010", "unanswerable", [G], "Which dataset did the adversarial nets paper use for fact verification?", "absent: FEVER fact verification belongs to RAG", "Insufficiency."),
    q("U011", "unanswerable", [R], "How many Wikipedia articles are in the December 2018 dump?", "absent: 21M refers to 100-word documents, not articles", "Insufficiency."),
    q("U012", "unanswerable", [A], "What sampling temperature was used for Transformer beam search?", "absent: beam size and length penalty are given, not temperature", "Insufficiency."),
    q("U013", "unanswerable", [G], "What is the squeeze-excitation ratio of the GAN generator?", "absent: no such concept in corpus", "Insufficiency."),
    q("U014", "unanswerable", [R], "What dropout rate was used for RAG-Token generation?", "absent: no RAG dropout rate is specified", "Insufficiency."),
    q("U015", "unanswerable", [N], "Who is the first author of the contradiction-note fixture?", "absent: no author is given", "Insufficiency."),
    q("U016", "unanswerable", [A], "What BLEU score did BART-large achieve on WMT14 English-to-German?", "absent: BART is only compared within RAG tasks", "Insufficiency."),
    q("U017", "unanswerable", [A], "On what exact date was 'Attention Is All You Need' first submitted to arXiv?", "absent: arXiv id is given, not a submission date", "Insufficiency."),
    q("U018", "unanswerable", [R], "How many attention heads does RAG's BART-large generator use?", "absent: not stated in the RAG paper", "Insufficiency."),
    q("U019", "unanswerable", [G], "What is the learning rate of the GAN discriminator update?", "absent: not stated", "Insufficiency."),
    q("U020", "unanswerable", [A], "What is the maximum path length of a restricted self-attention layer in Big Bird?", "absent: Big Bird is not in the corpus; only the Transformer's O(n/r) plan is mentioned", "Insufficiency; must not fabricate."),
]

questions = S + M + C + U
assert len(S) == 40 and len(M) == 60 and len(C) == 30 and len(U) == 20, (
    len(S), len(M), len(C), len(U)
)
assert len(questions) == 150 and len({x["id"] for x in questions}) == 150
payload = {
    "protocol_counts": {"single_hop": 40, "multi_hop": 60, "contradiction_or_aggregation": 30},
    "additional_group": {"unanswerable": 20, "rationale": "work-card B5 rubric requires honest-insufficiency testing; protocol says 'at least 130'"},
    "questions": questions,
}
path = EV / "questions.json"
path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8", newline="\n")
print(f"questions={len(questions)} sha256={hashlib.sha256(path.read_bytes()).hexdigest()}")

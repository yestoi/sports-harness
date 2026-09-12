**GPU research and host planning — roadmap handoff, September 12, 2026**

Status: proposed input for a future roadmap-planning session. The user requested that the research and recommendations be preserved for later integration. This document does not amend the active roadmap, register a strategy, authorize a host cutover, or schedule implementation.

The recommendation is to develop an offline study of quote fillability and adverse selection, followed by discrepancy-duration analysis. These studies could reveal an executable edge; none has demonstrated one in this project's data. Finish the trustworthy baseline work and evaluate simple models before making GPU research part of the trading path.

**User context and decisions to carry forward.**

The user owns an Apple M1 Mac mini (Late 2020), purchased for $699 in 2021, and a gaming PC with an NVIDIA RTX 3080. The mini's RAM/storage and the PC's CPU, RAM, storage, operating system and GPU memory remain unconfirmed. The user now believes the PC is the best host candidate. Treat that as the preferred candidate for inventory and benchmarking; do not assume its specifications or that migration has already occurred.

The original concern was that the system had only one open position while the NAS repeatedly encountered performance problems. The reviews identified execution and freshness/coverage defects, so low participation was not interpretable as a clean negative strategy result. The GPU is a research resource; ordinary CPU, RAM and local SSD capacity remain central to hosting the current application.

The user's latest instruction was to preserve the research for a future session to fold into the roadmap. No model was trained, no live data was freshly queried for this research, and no additional trading edge was measured.

**Where this belongs in the roadmap.**

The snapshot checked while preparing this handoff has 6B in progress, 6C deployed with acceptance work outstanding, and 6D–6F/7 not planned. The planning session must re-read current status and deployed evidence; these statements are historical context, not a current-state guarantee. Read the [active roadmap](../autopilot/roadmap.md), [state](../autopilot/state.md), [journal](../autopilot/journal.md), and [reconciled Phase 6 roadmap](2026-09-11-phase6-roadmap/ROADMAP.md).

| Roadmap area | Suggested integration | Boundary |
|---|---|---|
| 6B execution correctness | Reuse corrected book reconstruction, replay, queue-sensitivity results and order-157 audit as research prerequisites | GPU output cannot certify a defective simulator |
| 6C reporting | Reuse explicit units, eligibility/provenance, method separation and fresh-label coverage in research reports | No model-derived fill count is silently mixed into actual fills |
| 6D sustained evaluation | Preserve source/receipt/decision clocks, clean resting exposure, missing-stage reasons and capacity exclusions; define reusable opportunity units | Keep timely baseline evaluation first; research exports must not consume its budget |
| 6E operating environment | Add the PC as the user's preferred candidate; inventory, rehearse a complete restore and benchmark the corrected stack; measure spare capacity for research separately | GPU availability does not replace host acceptance, restore validation or cutover requirements |
| 6F prospective baseline | Record usable data/version boundaries and reserve genuinely unseen future games | Do not optimize away or contaminate the baseline confirmation period |
| 7 research expansion | Consider the ranked experiments below after the baseline checkpoint, with a separate research environment and registered candidates | Offline preparation may precede this; policy-changing adoption follows the applicable registration and acceptance requirements |

The existing 6D scope already allows holding/capacity policy comparisons after 6B. This handoff does not forbid those comparisons or require postponing an already authorized repair. Its new learned policies should be explicitly distinguished from the work needed to establish the baseline. Phase numbers and subdivisions remain proposals for the future planning session.

**Suggested work packages, in implementation order.**

| Package | Concrete deliverable | Completion evidence |
|---|---|---|
| R0: hardware and resource inventory | PC CPU/RAM/OS, SSD capacity/free space, GPU model/VRAM, runtime allocation, unattended-operation needs and research resource budget | Measurements recorded; small CPU/GPU benchmark; host acceptance evaluated independently |
| R1: reusable research dataset | Immutable local-SSD export with exact contract/game identity, observable-at timestamps, clean intervals, eligible/nonplaced opportunities and declared labels | Source/build/config and correction identities; row/game/episode counts; missingness report; deterministic regeneration and as-of checks |
| R2: simple baselines | Empirical fill rates, queue-consumption heuristic, logistic/survival baseline, existing sharp consensus | Chronological game-grouped evaluation with identical opportunity, risk, latency and capacity assumptions |
| R3: GPU challenger | One XGBoost challenger for fillability and adverse movement; compare CPU/GPU fitting and CPU inference | Reproducible model/split/feature manifest; measured runtime and memory; out-of-time comparison with baselines and queue-sensitivity bands |
| R4: prospective shadow study | Frozen predictions for each eligible opportunity and a periodic executable-opportunity report | Complete scoring/abstention coverage, fresh matured labels, game-level uncertainty and previously declared decision rules |
| R5: optional expansion | Discrepancy-duration analysis, then conditional calibration, coherent ladders, compact sequence models or local text extraction | Each new hypothesis justified by prior evidence and evaluated against the appropriate simple baseline |

R1 may require additional capture or schema work; first inspect what 6B–6D already provide. Keep new numerical/ML packages in an isolated research environment until a separately reviewed integration needs them. Preserve the CPU/Decimal execution implementation as the correctness reference.

**The first product to aim for.**

For each eligible quote, the proposed shadow output is: chance of any fill, expected filled quantity by the declared deadline, adverse-move risk, expected net value under a named benchmark, and an uncertainty/coverage indicator. Record quote policy, prediction time, feature cutoff, model version and the reason for any abstention.

Aggregate by variant, sport, market type and game: eligible opportunities, clean executable resting hours, capacity exclusions, distinct filled orders, independent filled games, predicted versus observed/replayed quantities, fee-adjusted markouts and settled outcomes as they mature. Keep actual paper-accounting fills, hypothetical replay fills and unavailable outcomes visibly separate. A later sharp probability and an executable liquidation price are different benchmarks.

**Acceptance and rejection rules to preserve.**

Compare on later unseen games and the same opportunity set. Keep all lines and overlapping observations from a game together, remove label-window leakage, and fit preprocessing/calibration without the final holdout. Count independent games alongside tape rows. Record all tried models, policies and failures.

A successful result needs more than prediction accuracy or more fills: it must improve the declared executable-value objective under realistic latency, fees, queue uncertainty, capacity and risk constraints. Stronger fillability with poorer fill quality is not success by itself. A simulated edge that disappears under a plausible queue assumption is unresolved. No improvement over the simple baseline is a valid negative result.

Choose the numerical improvement threshold, uncertainty rule, minimum sample, loss tolerance, missingness tolerance and stopping/extension rule before looking at prospective results. This memo deliberately does not invent replacement gate thresholds. If the data cannot resolve the chosen question, report insufficient evidence rather than selecting whichever model looked best.

**Open items for the future planning session.**

- Confirm PC specifications, available storage and whether gaming or shutdowns conflict with hosting.
- Refresh deployed 6B–6F status, the audit verdict and the usable measurement boundary.
- Determine whether enough clean tape exists for R1; simulated fill labels need explicit limitations and independent economic checks.
- Choose the first narrowly scoped target/horizon and opportunity population. Venue-mid movement, fresh-sharp markouts and final payoff need separate labels.
- Declare experiment resource limits so exports, joins and training do not reproduce the NAS contention on the PC.
- Select the held-out calendar, sample requirements and acceptance rule before a model search.
- Decide whether later text research needs a new contemporaneous news archive; historical model knowledge can leak game outcomes.

A suitable future-session instruction is: “Read this handoff and the current Phase 6/7 roadmap, reconcile completed work and standing decisions, and propose concrete roadmap additions for the PC host and offline fillability/adverse-selection study. Preserve the existing baseline and distinguish preparation, shadow evaluation and policy adoption.”

The full research memo follows, including all six research directions, supporting sources, limits of applicability and hardware considerations. The original standalone copy remains at /Users/trey/dev/sports-gpu-research-2026-09-12/RESEARCH.md. It is not needed to understand or use this handoff.

---

**RTX 3080 research opportunities for the sports harness — September 12, 2026**

The strongest research direction is to estimate the value of an executable quote: whether it will fill, how long that will take, and whether the apparent advantage survives the fill. A 3080 can support that work through GPU-trained tabular models, compact order-book sequence models, and batched numerical experiments. There is no demonstrated additional edge in this project's data yet. The proposals below are hypotheses, with explicit ways to reject them.

This memo combines primary research and official documentation with inspection of the local repository through observed HEAD `2910b99`. The repository is being changed by the implementation loop; its roadmap currently marks 6B in progress. No fresh production extraction, model training, GPU benchmark, or strategy deployment was performed for this memo. This research was originally saved outside the application repository; it is reproduced here as the evidence supporting the proposed handoff.

**What the project already records makes execution research particularly relevant.**

The schema includes sportsbook odds with source update and fetch timestamps; Kalshi depth snapshots, deltas, subscription sequences and trades; contract-specific fairs and gaps; strategy decisions, orders and fills; subsequent fair and venue-mid observations; closing benchmarks and settlements. This is substantially richer than a list of game results. Schema presence does not establish completeness or correctness for every historical interval. [Data models](../../../harness/db/models.py)

The sharp consensus currently uses fixed group weights: Pinnacle 0.65 and the BetOnline/LowVig group 0.35, combined in logit space. The latter books are grouped, so an experiment should preserve their dependence rather than count them as independent opinions. [Consensus implementation](../../../harness/pricing/consensus.py)

The existing research design already asks about mispricing, convergence and adverse selection. Its game-clustered analysis, frozen variants and prospective confirmation are useful foundations for ML research. The September 11 reviews found execution and measurement defects, including a potentially affected sole filled order. Repaired replay is a prerequisite for training on simulated fills. [Analysis plan](../../../docs/superpowers/reviews/2026-09-07-phase2-preregistration.md), [review](2026-09-11-phase6-roadmap/evidence/inputs/our-review/REVIEW.md)

| Priority | Research question | Possible economic benefit | Where the GPU helps |
|---|---|---|---|
| 1 | Which quotes have a realistic chance of filling while still valid? | Better use of scarce order capacity; fewer useless placements | Boosted-tree fitting and repeated chronological evaluation |
| 2 | Which fills are likely to be followed by an unfavorable price move? | Avoid adverse selection; improve entry timing | Tabular models first, then compact sequence networks |
| 3 | When does Kalshi follow a sharp-book move, and when does the sharp reference catch up instead? | Identify persistent, reachable discrepancies | Large feature tables, sequence models and conditional forecasts |
| 4 | Do the fixed consensus weights or price regions have predictable errors? | Improve probability estimates or abstain when uncertainty is high | Tabular model comparisons; optional foundation-model challenger |
| 5 | Do related spread/total/moneyline contracts disagree coherently? | Better relative-value research and identification of matching errors | Large score-distribution simulations; basic constraints need little GPU |
| 6 | Can stored news/context explain market moves? | Cheaper structured research and a testable information signal | Small local language models and embeddings |

**1. Build a fillability model first, paired with a value model.**

For each eligible quote opportunity, estimate the chance of receiving any fill and the expected filled quantity before a declared deadline. Features could include queue depth at the intended price, recent signed trade flow, spread, distance from the best price, order size, time to kickoff, source age, and recent liquidity replenishment. Evaluate a few prespecified quote/holding policies separately; the policy itself is part of the prediction problem.

Start with simple empirical rates and a queue-consumption heuristic, then logistic or survival models, then XGBoost. Queue-reactive research models order-arrival behavior conditional on the book state and provides a relevant foundation for execution-probability analysis. Its evidence is from financial order books and must be revalidated on football contracts. [Huang, Lehalle and Rosenbaum](https://arxiv.org/abs/1312.0563)

Kalshi describes price-time priority and offers queue-position information for an actual account order. The public depth response supplies price-level quantities, not the full ordering history of a hypothetical paper order. Consequently, cancellation position and simulated fills retain uncertainty. The account endpoint cannot retrospectively validate a paper order that was never submitted. [Queue-position endpoint](https://docs.kalshi.com/api-reference/orders/get-order-queue-position), [order-book response](https://docs.kalshi.com/api-reference/market/get-market-orderbook)

The current sole filled order cannot support a credible supervised fill classifier by itself. Clean tape can support many prespecified hypothetical quotes under repaired replay, but those are model-derived labels. Report optimistic and conservative queue assumptions and retain an unverifiable category. A model trained to reproduce the simulator is not independent validation of that simulator. A cancelled order is not automatically a negative example for whether it would have filled under a longer holding policy; cancellation changes observation and may be informative censoring.

The economic target should combine fillability and value. In a simplified single-contract calculation, expected contribution is the probability of filling multiplied by expected net value conditional on filling. For partial fills and inventory, use expected filled quantity, quantity-dependent fees and portfolio constraints explicitly. The benchmark value must be labeled: a later sharp probability or closing price is a proxy, while settled payoff is an outcome.

Hypothetical illustration: a quote with a 3% fill probability and a 4-cent net advantage conditional on filling contributes 0.12 cents per offered contract. A quote with a 25% fill probability and a 1.5-cent conditional advantage contributes 0.375 cents. That ranking could make capacity more productive even though the second quote has a smaller headline edge. These numbers are illustrative, not estimated from the harness.

Reject the hypothesis if ranking by the model fails to improve executable value or useful filled quantity versus simple baselines on later, unseen games under the same capacity and risk limits. More fills with worse post-fill value is a failure.

**2. Predict adverse selection and short-horizon market movement.**

An attractive fair-minus-price gap can disappear just as a seller reaches our bid. A useful model would estimate the probability and size of an adverse venue-mid move over one and five minutes, with later fresh sharp observations at longer horizons. Begin with all eligible clean observation points, including ones where the strategy did not place an order, so training is not limited to its selected examples. Then evaluate the resulting signal on repaired filled-order cohorts.

A concrete feature is bid/ask depth imbalance; others include signed aggressive flow, shrinking versus replenishing depth, cross-market moves and sharp-book disagreement. Gould and Bonart found predictive value in queue imbalance using simple logistic models on ten Nasdaq stocks. That is a strong reason to include a cheap baseline before a neural network. [Queue imbalance study](https://arxiv.org/abs/1512.03492)

DeepLOB combines convolutions with an LSTM to learn spatial and temporal order-book patterns. It demonstrated out-of-sample price-direction forecasting on equity data, including transfer to instruments outside training. A compact adaptation is a plausible 3080 experiment once clean football tape is available; the published equity results do not establish applicability or profitability here. [DeepLOB](https://arxiv.org/abs/1808.03668)

The later LOBFrame work explicitly distinguishes forecasting performance from usable trading signals. Our acceptance should therefore be improved fee-adjusted markouts and executable outcomes at the system's measured decision latency, rather than classification accuracy alone. [Deep Limit Order Book Forecasting](https://arxiv.org/html/2403.09267v2)

Start at horizons the application can observe and act on. A prediction lasting a fraction of a second is not useful to a process whose loop runs every 15 seconds. Likewise a one-minute sharp-price label is not fresh if the source only refreshed every fifteen minutes. Keep venue-mid and fresh-sharp targets separate. More frequent inference cannot supply missing source information.

One sensible initial output is a recorded shadow assessment of each quote's adverse-move risk. Any later change to order admission or cancellation becomes an explicitly registered policy experiment. A successful model might reduce some fills while increasing their quality.

**3. Learn the direction and duration of sharp-book/Kalshi discrepancies.**

For each newly observed discrepancy, measure which happens first: Kalshi moves toward the sharp reference, the sharp reference moves toward Kalshi, the quote becomes stale, or the participation window ends. Estimate the duration distribution and the price/depth still reachable after the actual collection and decision delay.

This extends the existing convergence research. The possible advantage is discovering a repeatable subset where the sharp move leads and an executable gap persists. It also allows rejection of an apparent opportunity where Kalshi already has the newer information.

Use source timestamps AND receipt timestamps. A quote published earlier but fetched later was unavailable until it arrived. Repeat the analysis at realistic delayed action times, and compare against the same fixed consensus without ML. Preserve exact side, team and threshold identity. Fit by sport and market type with pooling or shrinkage where samples are sparse; do not create dozens of unsupported submodels.

A 2026 Polymarket NBA preprint analyzed 173 games and reports rare single-market arbitrage episodes with a median duration of 3.6 seconds and substantial size constraints on combinatorial opportunities. This is a different venue, sport and largely in-game setting; I use it only as evidence that depth and elapsed time deserve explicit measurement, not as a forecast for our pregame Kalshi strategy. [Arbitrage Analysis in Polymarket NBA Markets](https://arxiv.org/html/2605.00864v1)

This experiment is useful even if it finds no actionable gap. It would distinguish slow collection, unavailable liquidity and a weak price reference from genuinely efficient pricing.

**4. Test probability calibration and conditional consensus weighting.**

The fixed sharp consensus supplies a clear benchmark. Candidate extensions include modest corrections conditioned on source age, disagreement, price region, sport and time to kickoff. Strong regularization toward the existing consensus is preferable to fitting a separate football-outcome model from a few weeks of games.

Measure both probability quality and trading value. The Walsh/Joshi sports-betting study found model selection based on calibration more useful than accuracy in its NBA experiment. Its reported returns from one test season are not an expected return for this project. Calibration alone also does not establish advantage over the price available to us. Use Brier/log loss, calibration diagnostics and executable fee-adjusted outcomes together. [Sports-betting calibration study](https://arxiv.org/abs/2303.06021)

A July 2026 preprint studies approximately 23 million Kalshi moneyline trades in NBA, MLB and NHL. It reports time-dependent calibration and cross-game parlay overpricing; transaction prices exclude fees. This motivates checking conditional bias, but it supplies no direct NFL/NCAAF result or validated execution strategy. Its final-minutes behavior is outside the current pregame executor. Cross-game independence and ex-post time-to-close definitions would also need careful treatment in a prospective replication. [Prices, Probabilities, and Parlays](https://arxiv.org/html/2607.14430v1)

For model choice, tree methods deserve a strong baseline: a 2022 benchmark found them competitive on medium-sized tabular datasets. More recent TabPFN research demonstrated strong results on small tabular tasks using a pretrained neural model; the 2025 Nature study evaluated up to 10,000 samples and 500 features. A compact, game-grouped feature table could support an optional TabPFN comparison. Neither benchmark tests this market or resolves its small independent-game count. [Tree-model benchmark](https://arxiv.org/abs/2207.08815), [TabPFN paper](https://www.nature.com/articles/s41586-024-08328-6)

Reject the extension if improved fit disappears on later games, if it only reproduces current market prices, or if better probabilities fail to survive executable prices and costs. A calibration model of game results needs substantially more independent games than the count of quote rows suggests.

**5. Explore coherent contract ladders and joint outcomes.**

For the same game and settlement rules, the probability of going over 55.5 cannot exceed the probability of going over 54.5. Spread ladders and moneylines impose related constraints on a distribution over final scores. Fit a coherent distribution to contemporaneous sharp lines and compare its implied contract values with reachable Kalshi prices and depth.

Basic monotonicity checks and constrained fits are inexpensive CPU work. A GPU becomes useful for large batched simulations of score distributions, parameter uncertainty and joint portfolios. CuPy provides GPU array operations and random-number generation suitable for such numerical experiments. [CuPy](https://cupy.dev/)

For NFL/NCAAF, any scoring model should respect discrete scores and key margins rather than importing a soccer goal model unchanged. Same-game parlay probabilities need a joint distribution; multiplying marginal probabilities does not capture their dependence. Separate games also need dependence assumptions stated rather than automatically assumed away.

A price inconsistency is only an executable opportunity after verifying every contract's resolution rules, fees, available size and leg-execution risk. This is promising research, but the GPU does not itself improve the underlying probability assumptions. Synthetic simulations increase computational precision conditional on a model; they do not create additional empirical games.

**6. Use a small local language model as a research assistant.**

Interesting uses include tagging timestamped injury/weather reports, extracting entities and event times, grouping similar market episodes, and searching stored notes for explanations of sudden moves. Qwen publishes an 8B model in several GGUF quantizations, including 4-bit options. An 8B-class quantized model is a reasonable small-model candidate for a 3080, subject to context length, runtime overhead and measured available VRAM. This is a capacity estimate, not a benchmark on the user's PC. [Official Qwen model card](https://huggingface.co/Qwen/Qwen3-8B-GGUF)

The economic hypothesis is that a timely, correctly extracted event adds predictive information beyond odds and tape. The operational hypothesis is cheaper or faster tagging. Test those separately. Stored research snippets are not necessarily a comprehensive contemporaneous news archive, and future collection may be required.

Keep timestamps, original text and extraction confidence. Test whether any useful information arrived before market repricing. For historical evaluation, account for knowledge embedded in model weights; a model trained after the evaluated games may know their outcomes. A frozen model making prospective annotations provides a cleaner test. Treat generated narratives as annotations rather than probability evidence. The current remote research models remain a comparison arm, not an assumed quality level for the local model.

**The 3080 is sufficient to start this research; memory and data layout determine scope.**

NVIDIA lists RTX 3080 variants with 10 GB or 12 GB VRAM. Confirm the actual card before sizing jobs. Compact tree models, minibatched sequence networks and small quantized language models are plausible workloads. No training-time or profit estimate is justified without the PC specifications and an actual benchmark. [NVIDIA specifications](https://www.nvidia.com/en-us/geforce/graphics-cards/30-series/rtx-3080-3080ti/)

XGBoost supports GPU training and permits deployment of a GPU-trained model on a CPU. That is particularly useful here: train offline, publish a small versioned model and keep routine scoring cheap. GPU training is not automatically faster for a small table, so compare wall time with CPU training. [XGBoost GPU documentation](https://xgboost.readthedocs.io/en/stable/gpu/)

For scale intuition, one million rows with 64 float32 features occupy about 256 MB before labels, indices and training overhead. Materializing one million sequences of 100 steps and 40 float32 features would occupy about 16 GB before model memory. Generate sequence batches from partitioned data rather than materializing the full tensor.

Use immutable, versioned exports on local SSD, partitioned by date/game/market. Reconstruct clean books once and reuse derived features. GPU dataframe tools can accelerate supported transformations, but copying data and spilling between host and device have costs. NVIDIA documents additional memory restrictions for cudf.pandas under WSL2. Large joins should be benchmarked rather than assumed to fit or accelerate. [cuDF memory behavior](https://docs.rapids.ai/api/cudf/latest/cudf_pandas/how-it-works/)

The current PostgreSQL workload and event-driven Decimal replay do not become GPU workloads automatically. Preserve the repaired CPU executor as the correctness reference. Only port suitable batched calculations after demonstrating equivalent semantics. If the PC hosts production too, cap research CPU/RAM, schedule exports and training outside protected workload periods, and monitor database latency. GPU work still consumes host memory and storage bandwidth.

**A credible experiment would use the following sequence.**

1. Complete the execution corrections and record an accepted observation boundary. Inventory usable tape by game, market and time window; keep excluded and unverifiable intervals explicit.
2. Freeze a feature/label specification for fillability and adverse movement. Export clean data once. Include all eligible opportunities, capacity exclusions and nonplacements, not merely successful orders.
3. Fit simple baselines and one GPU tree challenger. Initially restrict to a small declared family of hypotheses and a few economically motivated horizons.
4. Train on earlier games, validate on later games, and reserve a later untouched prospective period. Keep every line and overlapping observation from a game together; remove cross-boundary label overlap and wait for labels to mature. Preprocessing and calibration are fitted only on training/validation data.
5. Evaluate the identical opportunity set under the same latency, risk and shared-capacity assumptions. Report fillability calibration, expected filled quantity, conservative replay outcomes, fee-adjusted markouts, settled outcomes as they mature, and per-game uncertainty. Include the cost of waiting and adverse selection, not just successful fills.
6. Freeze the candidate and record shadow predictions prospectively. Any trading-policy change gets its own registered variant/configuration and evaluation period. Historical GPU exploration does not count toward the existing live gate.

Accelerating a broad search makes it easier to find chance winners. Record every attempted model and policy, including failures. The backtest-overfitting literature explains why selecting the best of many simulations can produce misleading results even with conventional validation. A fresh period, restricted search and transparent attempt count are central parts of this proposal. [Bailey et al., The Probability of Backtest Overfitting](https://www.davidhbailey.com/dhbpapers/backtest-prob.pdf)

The first deliverable should be an executable-opportunity report: for each variant and market class, how much trustworthy resting time existed, which quotes could plausibly fill, what happened after those fills, and whether a simple model improves that tradeoff on later games. This can begin as offline research while Phase 6 is completed; a policy-changing deployment belongs after its baseline acceptance and registration requirements. [Phase 6/7 roadmap](../../../docs/superpowers/autopilot/roadmap.md)

My recommendation is to start with fillability plus adverse selection, then add discrepancy-duration analysis. Those projects use the most distinctive data already being recorded and directly address the difference between an attractive quoted edge and a useful position. Conditional probability corrections, coherent score distributions and local text models are worthwhile later challengers. Each should earn its place by improving prospective executable results over a simple baseline.

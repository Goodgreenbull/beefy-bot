# Evidence filter trial v1

Version: `evidence-trial-v1`. This is an experimental delivery policy, not a
model fitted to successful projects. No claim of calibrated odds or profit.

The existing scorer still evaluates candidates. The new policy records that
baseline decision, then gates delivery. Generic PULSEs stay in research;
verified wallet discovery callbacks and protection warnings remain separate.

## Main-feed gates

- The existing scorer must qualify SCOUT, ACTION or A+.
- Market and flow observations must be no more than five minutes old (15s
  future skew tolerated). Unknown/naive dates fail.
- Flow must be checked; at least five unique buyers; unique buyers must be at
  least half the buy count and no greater than the buy count; buy share >=60%.
- Independent admin checks and successful sell simulation must be present;
  both taxes must be known, finite and below 5%; no honeypot/buy/sell block.
- No observed deployer or monitored-wallet selling.
- Corroboration: a fresh entry from Beefy's own pool/receipt wallet monitor,
  or two distinct exact-token X authors with dated HTTPS status citations.
  GMGN tags, plain links, narrative scores and unproven overlay wallet counts
  do not meet that gate.
- Wallet corroboration also requires a fresh registered V2/V3 paid-swap
  receipt from discovery. A pool transfer alone cannot qualify. V4 discovery
  remains immediate with its existing pool/token-link warning; it does not
  qualify the main feed through this wallet path until that link is verified.
- Social/momentum promotions additionally need a checked prior observation
  2–15 minutes old with >=5 buyers, >=60% buy share and <=20% liquidity loss.
  Fresh verified wallet corroboration does not wait for this extra snapshot.

These thresholds are hypotheses for the trial. Related-wallet clustering,
validated wallet profitability and independent product research are not yet
implemented. Two configured wallets still constrain coverage. No new paid
tools or social scraper is installed. The social path only works if the
configured overlay supplies `socialEvidence` records with `url`,
`tokenAddress`, and `publishedAt`; the overlay must be trustworthy.

## Alerts and research

All numeric upside targets are suppressed during this trial, even if legacy
target fields exist. A generic sell simulation is not described as a £20
execution quote. Missing flow/social inputs display as unknown. The old
net-new-wallet proxy is labelled buyer-only wallets.

Every enriched candidate's existing snapshot retains `raw.quality_trial`:
version, evaluation time, baseline tier/score/eligibility, main eligibility,
rejection reasons, wallet support, cited-author count and persistence.
`/health.quality_trial` exposes aggregate counts, not token or wallet details.
Counts are evaluations, not unique tokens, sent alerts or win rates.

Snapshots are pruned after seven days and depend on the configured SQLite
storage. A free Render instance's filesystem does not preserve this research
across redeploys; external durable storage is still outstanding. No permanent
winner/failure library or automated research-return attribution is claimed.
Existing delivered-alert outcomes continue as before. Sample counts no longer
set `calibrated=true`, because no fitted policy exists.

## Validation and rollback

Tests cover the two screenshot patterns, verified-entry acceptance, stale and
unknown evidence, repeated buys, duplicate citations, legacy-risk rejection,
SQLite retention and suppressed PULSE delivery. These synthetic cases test
behaviour; they are not historical backtests.

Assess alert volume, source coverage, false positives, missed runners, entry
latency and net outcomes using later unseen observations. The policy may be
very quiet where sellability/corroboration is missing, especially Robinhood.
Do not relax failed safety gates merely to produce alerts.

Rollback: revert the quality-trial PR and redeploy; no secret/environment
changes or destructive schema migration is required. The additive snapshot
index and JSON research fields are backwards compatible.

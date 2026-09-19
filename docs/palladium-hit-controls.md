# Palladium verified HIT vs matched market controls

This research layer compares independently verified **Palladium M / DOGE HIT**
pre-context with nearby public market states that did not occur inside the
winner's plausible active ticket window.

It is public, offline and descriptive only.

## Why this exists

The verified-HIT dataset contains winners only. That makes it useful for
checking blockchain truth and pre-HIT trajectories, but not enough to decide
whether a pattern is unusual.

This control layer provides a second reference population using nearby
Palladium M market states. These are **not completed EasyMining MISS tickets**;
they are matched market controls.

## Matching rules

For every usable Palladium M / DOGE HIT:

1. use the exact Palladium M series only;
2. retain the HIT's observed quote-to-block age;
3. search within ±24 hours of the HIT;
4. create a synthetic control event at the candidate quote plus the same
   endpoint age as the HIT;
5. exclude controls from the preceding package-duration window through
   15 minutes after any verified Palladium M HIT;
6. retain at most one control per 60-minute slot;
7. cap each HIT at 24 controls.

Each control is then evaluated with the exact same 15/30/60-minute
pre-event-window logic used for verified HITs.

## Compared features

The report compares HIT and control medians for:

- work per native cost;
- inverse ticket cost per work;
- public SCRYPT market-price statistic;
- Litecoin primary difficulty;
- Dogecoin merge difficulty;
- feed expected-return change.

It also reports the empirical percentile of the HIT median inside the matched
control distribution.

## Interpretation

A low or high percentile can identify a candidate pattern worth following.
It does not establish causality, profitability or a usable BUY signal.

Controls are market states rather than purchased tickets, can be reused across
different HIT age matches, and are not randomized independent observations.
The separate private completed-order path is still the stronger source for a
true HIT/MISS denominator.

This report cannot change CURRENT/final_signal, buy or cancel anything.

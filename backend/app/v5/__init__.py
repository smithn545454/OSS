"""v5 dual-conviction scoring.

Two conviction scores, both calibrated to historical outcomes:

- **HR Conviction** (0–20 scale): Wilson lower bound × archetype fit × regime,
  derived from P(MFE ≥ 200%). Sharpshooter score for grand-slam hunts.
- **P Conviction** (0–100 scale, Phase 3): Wilson lower bound on win rate ×
  normalized P&L × fit × regime. Captures consistent winners (grinders, quality).

Phase 2 (this module wave): HR archetype library + matcher + conviction
calculator. Phase 3 adds the P-archetype symmetric set. Phase 5 wires
both into the decision pipeline; until then the v5 package is dark in
production.
"""

# RANKING ENGINE V1 - OFFICIAL FREEZE

Date: 2026-02-28  
Project: NODO Marketplace  
Status: FROZEN (Production Stable)

---

## 1. Scope

This document formalizes the freeze of Ranking Engine V1 after multi-slice validation across:

- Province: QC
- Province: ON
- Cities: Laval, Montreal, Toronto
- Categories: 1 and 2
- Dataset size: 110 synthetic providers + pre-existing records
- Slice limits tested: up to 50 rows

No production logic was modified during analysis.

---

## 2. Current Production Configuration

```text
verified_multiplier = 1.0
```

The hybrid ranking formula remains unchanged.

Zone filtering is implemented but does NOT affect score.

---

## 3. Sensitivity Analysis Summary

Three scenarios were tested:

| Scenario | Description |
|----------|-------------|
| 1.0x     | Current production multiplier |
| 0.5x     | Reduced verified multiplier |
| 0x       | Verified removed |

### Observed Pattern (Consistent Across QC + ON)

- Removing verified (0x) increases ranking instability.
- 0.5x significantly reduces average displacement.
- 0.5x maintains Top 3 stability across all slices.
- 1.0x shows amplified movement in dense competitive slices.
- The pattern is structural, not city-specific.

Example (ON / Toronto):

```text
avg_0.5 ≈ 0.4-0.6
avg_0.0 ≈ 1.4
top3_0.5 = 0%
top3_0.0 = 33%
```

---

## 4. Engineering Conclusion

- Verified is a structural stabilizer.
- The multiplier intensity affects sensitivity.
- 1.0x is stable but relatively strong in dense markets.
- 0.5x is a mathematically valid V2 candidate.
- No urgent need to modify production.

---

## 5. Official Decision

```text
Ranking Engine V1 is officially frozen.
No weight changes authorized in V1.
```

Future changes must be versioned under:

```text
RANKING_ENGINE_V2
```

---

## 6. V2 Hypothesis (Documented, Not Active)

```text
verified_multiplier = 0.5
```

To be reconsidered after:

- Real user volume
- Live marketplace behavior
- Conversion impact analysis

---

## 7. Isolation Guarantee

- Zone filtering does not impact score.
- Ranking formula unchanged.
- Production logic untouched during analysis.
- All experiments executed offline.

---

## 8. Next Review Trigger

Ranking V2 evaluation may be reopened when:

- Real traffic exceeds statistical threshold
- Verified adoption rate changes materially
- Competitive compression increases significantly

Until then:

```text
V1 remains locked.
```

---

Approved: NODO Core Engineering

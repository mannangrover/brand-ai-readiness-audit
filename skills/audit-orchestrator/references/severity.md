# Deterministic severity function

Severity is **computed, not asserted**, so the same site always yields the same
result and severities are comparable across every sub-skill.

```
layer_weight = { L1: 4, L2: 4, L3: 3, L4: 2, L5: 2, ENG: 2 }

breadth      = affected_pages / sampled_pages          # 0.0 .. 1.0
criticality  = 1.0 if the affected fact is PRIMARY (what-it-is / price /
                    location / contact), else 0.6
damping      = 0.6 when external corroboration is strong (L4-01 passes with
                    >= 3 identity anchors) AND the finding is L3 or L5,
               else 1.0

score    = layer_weight * (0.4 + 0.6 * breadth) * criticality * damping

severity = critical  if score >= 3.4
           high      if score >= 2.4
           medium    if score >= 1.2
           low        otherwise
```

## Why the damping term exists (research observation O6)
Two of the most-cited sites in our field-research panel ship **no JSON-LD at
all**, and three ship multiple `<h1>`s — classic "defects" that clearly do not
hurt them, because massive external corroboration compensates. So an L3/L5 gap on
a heavily-corroborated brand is genuinely less severe than the same gap on an
unknown brand. The damping term encodes exactly that, and prevents the audit from
crying wolf on the very sites that prove on-site signals are not the whole story.

## Confidence is separate from severity
- `confirmed` — directly observed (e.g. robots line quoted, status code seen).
- `probable` — inferred from a sample (e.g. "0/12 sampled pages"; the other
  pages were not checked).
- `advisory` — best practice with no defect observed (proactive suggestion).

Never present an `advisory` item as a defect, and never let a `skipped`
measurement (e.g. render failed) score as a pass — record it in
`audit_meta.checks_skipped` instead.

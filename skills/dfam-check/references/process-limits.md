# DfAM Process Limits

Design-rule limits per additive process, for comparison against measured
facts from `scripts/dfam_tool.py`. Values are conservative defaults from
published process design guides and consistent with ISO/ASTM 52910
(general DfAM guidance) and ISO/ASTM 52911-1 (laser powder bed fusion of
metals). Machine-, material-, and parameter-specific datasheets override
these defaults when the user provides them — cite whichever source is used.

| Limit | FDM/FFF | SLS (PA12) | SLA/DLP | PBF-LB metal (SLM/DMLS) | MJF |
| --- | --- | --- | --- | --- | --- |
| Min supported wall (mm) | 1.2 | 0.7 | 0.5 | 0.4 | 0.5 |
| Min unsupported wall (mm) | 1.6 | 0.7 | 1.0 | 0.5 | 0.5 |
| Self-supporting angle (deg from horizontal) | 45 | n/a (powder supports) | 30 | 45 | n/a (powder supports) |
| Min hole diameter (mm) | 2.0 | 1.5 | 0.5 | 1.5 | 1.0 |
| Min positive feature (mm) | 0.8 | 0.8 | 0.2 | 0.4 | 0.5 |
| Max unsupported bridge (mm) | 10 | n/a | 5 | 2 | n/a |

Sources: Hubs FDM/SLS/SLA/metal design guides, Formlabs design guides,
HP MJF design guidelines, EOS design rules; ISO/ASTM 52910 §6 for the
category structure (feature limits §6.5, support structures §6.7).

## Interpretation notes

- **Wall thickness facts** come from ray-cast sampling, so `min_mm` is a
  sampled minimum, not an exhaustive one. Treat `p05_mm` below the limit as
  a strong violation signal even when `min_mm` alone might be an outlier.
- **Overhang facts** exclude faces resting on the build plate. For powder
  processes (SLS, MJF) the surrounding powder supports all geometry:
  support-area findings do not apply, but trapped-powder escape holes
  become the relevant check instead.
- **Support volume** is a prism upper-bound estimate for cost and
  post-processing effort, not a slicer-accurate figure. Ratios above
  ~30% of part volume usually justify reorientation or redesign for
  support-requiring processes.
- **Watertightness** (`mesh.watertight: false`) blocks slicing for every
  process and should be reported before any other finding.
- **Orientation candidates** are the six axis-aligned rotations only.
  A candidate reaching materially lower support area than the current
  orientation is a finding worth reporting with its build-height tradeoff.

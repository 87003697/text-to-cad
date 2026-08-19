# Provider-Free Browser Deep-Module Design Resources

## Knowledge

- [John Ousterhout: _A Philosophy of Software Design_](https://web.stanford.edu/~ouster/cgi-bin/aposd.php)
  Primary source for deep modules, information hiding, pulling complexity down,
  and designing an interface twice.
- [Stanford CS 190: APOSD discussion](https://web.stanford.edu/~ouster/cs190-winter24/lectures/aposd/)
  Concise primary lecture outline for interface versus implementation, deep
  modules, dependencies, tactical programming, and Design It Twice.
- Local codebase-design vocabulary: Module, Interface, Seam, Adapter, Depth,
  Leverage, and Locality. The repository applies that vocabulary in the
  [fallback deep-module design](../../design/provider-free-browser-runtime-deep-module.md).
- [Provider-free browser authority ADR](../../adr/0004-own-provider-free-browser-lifecycle-by-authority.md)
  The decisions and invariants that any candidate interface must preserve.

## Wisdom (Communities)

- Repository dual review on the eventual refactor range.
  Use Standards review to challenge module shape and Spec review to challenge
  security and lifecycle behavior; neither substitutes for the other.

## Gaps

- The fallback deep-module design was superseded before implementation; it is
  useful only as a case study.
- The current Development runtime does not claim ADR 0004 Formal conformance.
- The user has not yet completed the interface-comparison exercise, so no
  learning record should claim mastery.

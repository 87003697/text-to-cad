# Toys4K depth-8 Development gate

Status: Development/MVP — Not Sealed, Not Formal, Not Verified, Not Production

`scripts/pilot/toys4k-depth8-development.py` is the closed public admission and
terminal-evidence seam for the four-fixture milestone. It accepts one fixture
key, derives the immutable input identity and route internally, validates the
serialized prefix and cumulative Development ledger, and either emits a
provider-free preparation receipt or validates one terminal evidence package.
It never reads a credential, accepts a provider URL, or retries a job.

The provider-free conformance override can replace only bytes and digest. It
requires the fixed route, records `paidDispatchCount=0`, and cannot authorize a
transport. Production use has no override and resolves `models/toys4k/<key>.ply`
against the four constants embedded in the runner.

## Closed route decisions

| Fixture | Route | Authority |
| --- | --- | --- |
| `bottle_bottle_089` | `cad` | selected Toys4K operation map: revolve |
| `toaster_toaster_005` | `cad` | selected Toys4K operation map: boolean subtract |
| `mushroom_mushroom_018` | `implicit-cad` | selected Toys4K operation map: smooth difference and organic-plant rubric |
| `airplane_airplane_016` | `implicit-cad` | current PLY header has 305,796 faces; routing-rubric rule 2 wins at over 100k |

The airplane decision is derived from the frozen input's currently observed
header, not from the historical pilot route.

## TDD evidence matrix

The fixed seam is exercised only through the CLI by
`tests/python/global/test_toys4k_depth8_development.py`. The first allowlist
slice was observed RED when the CLI did not exist, then GREEN. Each following
slice was introduced as a failing mutation of an otherwise complete public
receipt and made GREEN through the smallest corresponding gate.

| Contract defect class | Public-seam test |
| --- | --- |
| allowlist, path and traversal | `test_unknown_key_and_path_inputs_fail_before_dispatch` |
| missing, LFS pointer, digest mismatch; attempt zero | `test_missing_pointer_and_mismatch_are_attempt_zero_preparation_failures` |
| bottle, toaster and mushroom route substitution | `test_closed_routes_cannot_be_substituted` |
| current airplane rubric evidence | `test_airplane_records_current_face_count_rubric_evidence` |
| missing/cross-run source, route, STEP and GLB artifacts | `test_missing_cross_run_or_wrong_route_artifacts_are_rejected` |
| Workspace node presence, order, digest and run identity | `test_workspace_missing_reordered_tampered_and_cross_run_nodes_are_rejected` |
| depths 1–8, depth 8 and independent measurement authority | `test_depths_one_through_eight_and_independent_authority_are_required` |
| Final Delivery and Observable Geometry | `test_final_delivery_and_observable_geometry_verification_are_required` |
| attempts, request, token, job and USD caps | `test_attempt_request_token_job_and_total_caps_fail_closed` |
| reserve/settle order, duplicates, release, pricing and replay | `test_ledger_order_duplicate_release_pricing_and_replay_are_rejected` |
| ambiguous timeout reservation, no retry, process-group absence | `test_ambiguous_timeout_retains_reservation_with_no_whole_job_retry_and_cleanup_absence` |
| secret/capability non-disclosure | `test_secret_or_capability_material_is_rejected_from_evidence_and_logs` |
| exact cleanup ownership and absence | `test_cleanup_requires_exact_owned_resources_and_absence` |
| honest evidence-complete unaccepted selection | `test_valid_unaccepted_selection_stays_unaccepted_and_is_evidence_complete` |
| serialized next-sample safety barrier | `test_next_sample_blocks_only_on_serialized_safety_barrier` |

## Boundary discovered during implementation

The merged Development supervisor and real-Colima launcher remain hard-coded to
`cup_cup_033`: they require its implicit source and input digests and publish a
Cup-specific receipt. Therefore this change does **not** claim that the four
route-aware candidate jobs can yet be transported by that runtime. Turning the
gate into the requested execution runner requires a separately decided change
to the Development supervisor/launcher public protocol plus current backend
closure for both CAD and implicit routes. Reusing the Cup transport without
that decision would silently substitute source/input authority and violate the
fixture and route contracts above.

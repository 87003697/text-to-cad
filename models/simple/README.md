# Simple CAD Examples

These examples cover compact part generators that are not already represented
by the benchmark suite.

The remaining prompts are:

1. Cylindrical spacer sleeve with a central through-bore and rounded rim edges.
2. Square mounting block with a vertical through-hole and two side clearance holes.
3. Gusset plate with a triangular web, base holes, and softened perimeter edges.
4. Rectangular clamp block with a split slot and two transverse screw holes.
5. Shaft collar with a central bore, radial set-screw hole, and chamfered faces.
6. Pulley wheel with a central hub, outer groove, and circular through-bore.
7. Spur gear blank with central bore, raised hub, and simplified perimeter teeth.
8. Flywheel disk with central bore, annular rim, and lightening holes.
9. Cam follower roller with central bearing bore and rounded outer profile.
10. Small enclosure cover with raised rim, corner screw holes, and shallow recessed center.
11. Cylindrical cap with hollow interior, top boss, and rounded external edges.
12. Retainer plate with elongated slot, two circular holes, and chamfered perimeter.
13. Keyed shaft hub with central bore, keyway slot, and bolt-hole pattern.
14. T-slot slider block with central channel, side relief cuts, and mounting holes.
15. Mounting plate with central circular cutout, elongated side slot, four corner holes, and rounded edges.
16. Basic shape mating test fixture for assembly-helper surface and collision checks.

The flat rectangular plate, circular flange, L-bracket, U/clevis bracket, and
open-top electronics enclosure examples are intentionally omitted here because
`models/benchmarks/` already carries richer versions.

## Files

- `*.step`: Primary STEP files for each simple example.
- `*.py`: Python generators for each example.
- `simple_model_library.py`: Shared build123d implementation helpers used by the generators.

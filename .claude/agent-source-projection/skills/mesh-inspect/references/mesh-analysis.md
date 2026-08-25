# Mesh Analysis Reference

How to interpret `mesh-inspect` JSON output and combine it with snapshot renders
to describe a mesh.

## JSON Fields

### stats

| Field | Meaning |
|-------|---------|
| `vertices` / `faces` / `edges` | Mesh complexity. <1K faces = simple primitive; 10K+ = detailed model |
| `bounding_box.size` | Physical extents [x, y, z]. Ratio reveals shape class (see below) |
| `volume` | Interior volume (null if not watertight). Units³ |
| `surface_area` | Total face area. High area/volume ratio = thin shell or fins |

**Shape class from bounding box ratio:**
- All dimensions similar (1:1:1 ± 30%) → blocky/cubic
- One dimension much smaller → plate/panel (e.g. 100×80×5)
- One dimension much larger → rod/beam (e.g. 10×10×200)
- Two small, one large → thin shell or tube

### quality

| Field | Meaning |
|-------|---------|
| `watertight` | Closed manifold — suitable for boolean ops and volume computation |
| `volume_valid` | Volume computation is meaningful (requires watertight + consistent normals) |
| `degenerate_faces` | Zero-area triangles — indicates meshing artifacts |
| `euler_number` | V - E + F. Equals 2 for a closed genus-0 surface (sphere-like) |

### canonical_frame

| Field | Meaning |
|-------|---------|
| `center` | Centroid of vertex cloud |
| `pca_axes` | Principal axes sorted by variance (largest first) |
| `eigenvalues` | Variance along each axis — ratio reveals symmetry |

**Reading eigenvalues:**
- λ1 ≈ λ2 ≈ λ3 → roughly spherical/cubic symmetry
- λ1 ≈ λ2 >> λ3 → flat/planar object (plate)
- λ1 >> λ2 ≈ λ3 → elongated (rod/beam)
- λ1 >> λ2 >> λ3 → no rotational symmetry

## Combining with Snapshot Renders

1. **Overall silhouette** — Does the render match the bounding box aspect ratio?
2. **Topology** — Count visible holes/through-features; cross-check with euler_number
3. **Surface detail** — Look for fillets, chamfers, ribs. These won't show in JSON
4. **Symmetry** — If eigenvalues suggest symmetry, verify in front/side/top views
5. **Scale** — Use bounding_box.size to annotate approximate dimensions

## Description Template

> A [shape_class] mesh of approximately [size_x × size_y × size_z] units.
> [watertight status]. [vertex/face count] complexity.
> Visual inspection shows [features from renders].
> PCA indicates [symmetry/elongation from eigenvalues].

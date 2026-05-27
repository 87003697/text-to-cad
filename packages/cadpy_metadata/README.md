# cadpy-metadata

Small, dependency-free metadata helpers shared by Python-generated URDF, SRDF,
and SDF skill runtimes.

The source of truth lives under `packages/cadpy_metadata`. Consuming skills
must use generated, installable copies under their own
`scripts/packages/cadpy_metadata` runtime directory.

Those skill-local copies can be installed from the consuming skill directory:

```bash
python -m pip install -r requirements.txt
```

Refresh those generated copies with the skill-specific build scripts:

```bash
scripts/build/build-urdf-skill.sh
scripts/build/build-srdf-skill.sh
scripts/build/build-sdf-skill.sh
```

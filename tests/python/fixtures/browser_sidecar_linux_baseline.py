#!/usr/bin/env python3
"""Emit the fixed public residual fixture from the reviewed Linux baseline image."""

from __future__ import annotations

import base64
import hashlib
import json
import sys

sys.path.insert(0, "/opt/browser-sidecar/meshshot-src")
from meshshot import MeshGeometry, render_residual_preview


def main() -> int:
    reference = MeshGeometry(
        vertices=[[-0.46, -0.2, 0.0], [-0.2, -0.2, 0.0], [-0.33, 0.2, 0.0]],
        faces=[[0, 1, 2]],
    )
    candidate = MeshGeometry(
        vertices=[[0.2, -0.2, 0.0], [0.46, -0.2, 0.0], [0.33, 0.2, 0.0]],
        faces=[[0, 1, 2]],
    )
    rendered = render_residual_preview(
        reference,
        candidate,
        variant="step",
        exterior_directions=[],
    )
    print(
        json.dumps(
            {
                "pngDataUrl": "data:image/png;base64,"
                + base64.b64encode(rendered.png_bytes).decode("ascii"),
                "pngSha256": hashlib.sha256(rendered.png_bytes).hexdigest(),
                "profileSha256": rendered.profile_sha256,
                "views": list(rendered.views),
            },
            sort_keys=True,
            separators=(",", ":"),
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

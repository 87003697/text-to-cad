"""Immutable private identities for the sealed conformance browser surface."""

CONFORMANCE_SURFACE_SCHEMA = "meshshot.browser-sidecar.conformance-surface/1"
CONFORMANCE_REQUIRED_ROOTS = ("/opt", "/usr")
CONFORMANCE_OPTIONAL_ROOTS = (
    "/app",
    "/etc",
    "/home",
    "/srv",
    "/var",
    "/workspace",
)
CONFORMANCE_TOP_LEVEL_BROWSER_ROOTS = (
    "/chrome",
    "/chromium",
    "/google-chrome",
    "/ms-playwright",
    "/playwright",
)

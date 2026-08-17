"""Immutable private identities for the sealed conformance browser surface."""

CONFORMANCE_SURFACE_SCHEMA = "meshshot.browser-sidecar.conformance-surface/1"
CONFORMANCE_REQUIRED_ROOTS = ("/opt", "/usr")
CONFORMANCE_SYSTEM_ALIAS_ROOTS = ("/bin", "/lib", "/lib64", "/sbin")
AGENT_SYSTEM_EMPTY_ROOTS = (
    "/usr/lib/.build-id",
    "/usr/lib/debug",
    "/usr/lib/firmware",
    "/usr/local",
    "/usr/share/doc",
    "/usr/share/licenses",
    "/usr/share/sgml",
    "/usr/src",
)
AGENT_EXTERNAL_EMPTY_ROOTS = ("/var/tmp",)
CONFORMANCE_OPTIONAL_ROOTS = (
    "/app",
    "/etc",
    "/home",
    "/run",
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

"""Which routes need which role.

Kept in one table rather than scattered across decorators so the whole
access-control surface can be read at once, and so a route added without
a policy fails closed instead of silently becoming public.

The awkward constraint this table exists to handle: **an OBS Browser
Source cannot send an Authorization header**, and neither can a browser
WebSocket client, because neither API lets you set one. If the projector
display path required a header, the OBS integration would simply not
work. So the display surfaces are treated as read-only and are public by
default, while everything that *changes* something requires a token.

An operator who wants the display locked down too can set
`VERSESYNC_PUBLIC_PROJECTOR=false` and pass `?token=<device token>` on
the Browser Source URL, which is the only mechanism OBS leaves available.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

# Roles in ascending order of privilege.
ROLE_PROJECTOR = "projector"
ROLE_OPERATOR = "operator"
ROLE_ADMIN = "admin"


@dataclass(frozen=True)
class Rule:
    """One access-control rule.

    `required_role` of None means no authentication at all.
    """
    pattern: re.Pattern[str]
    methods: frozenset[str] | None     # None = every method
    required_role: str | None
    # Display surfaces: public when VERSESYNC_PUBLIC_PROJECTOR is on,
    # otherwise they need the role named above.
    display_surface: bool = False
    # Human-readable action name for the audit log.
    action: str = ""

    def matches(self, path: str, method: str) -> bool:
        if self.methods is not None and method.upper() not in self.methods:
            return False
        return bool(self.pattern.fullmatch(path))


def _rx(pattern: str) -> re.Pattern[str]:
    return re.compile(pattern)


_GET = frozenset({"GET", "HEAD", "OPTIONS"})
_WRITE = frozenset({"POST", "PUT", "PATCH", "DELETE"})


# Order matters: the first match wins, so specific rules precede general
# ones. Everything not matched here is denied by default.
RULES: tuple[Rule, ...] = (
    # ---- always public: liveness, docs, and the auth bootstrap itself ----
    Rule(_rx(r"/"), _GET, None, action="meta.health"),
    Rule(_rx(r"/healthz"), _GET, None, action="meta.health"),
    Rule(_rx(r"/docs|/redoc|/openapi\.json|/docs/oauth2-redirect"), _GET, None),

    # The bootstrap endpoints cannot require a token: there is nobody to
    # issue one until they have been called. Each is protected by
    # something other than a token -- setup-pin only works once, login
    # and approve need the PIN, register is rate-limited by a cap on
    # pending devices.
    Rule(_rx(r"/auth/status"), _GET, None, action="auth.status"),
    Rule(_rx(r"/auth/setup-pin"), _WRITE, None, action="auth.setup_pin"),
    Rule(_rx(r"/auth/login"), _WRITE, None, action="auth.login"),
    Rule(_rx(r"/auth/change-pin"), _WRITE, None, action="auth.change_pin"),
    Rule(_rx(r"/auth/register-device"), _WRITE, None,
         action="auth.register_device"),
    Rule(_rx(r"/auth/approve-device"), _WRITE, None,
         action="auth.approve_device"),
    Rule(_rx(r"/auth/revoke-device"), _WRITE, None,
         action="auth.revoke_device"),
    # NOT PIN-authenticated: the PIN is exactly what is locked out, so a
    # PIN-gated escape hatch could only ever be used when it was not
    # needed. An already-approved admin device can clear it instead.
    Rule(_rx(r"/auth/clear-lockout"), _WRITE, ROLE_ADMIN,
         action="auth.clear_lockout"),
    # Reading the audit log or the device list needs admin.
    Rule(_rx(r"/auth/devices"), _GET, ROLE_ADMIN, action="auth.list_devices"),
    Rule(_rx(r"/auth/audit"), _GET, ROLE_ADMIN, action="auth.read_audit"),
    Rule(_rx(r"/auth/me"), _GET, ROLE_PROJECTOR, action="auth.me"),

    # ---- display surfaces (public unless locked down) ----
    Rule(_rx(r"/projector"), _GET, ROLE_PROJECTOR,
         display_surface=True, action="projector.view"),
    Rule(_rx(r"/projector/static/.*"), _GET, ROLE_PROJECTOR,
         display_surface=True),
    Rule(_rx(r"/projector/config"), _GET, ROLE_PROJECTOR,
         display_surface=True),
    Rule(_rx(r"/projector/state"), _GET, ROLE_PROJECTOR,
         display_surface=True),
    Rule(_rx(r"/ws/transcripts"), None, ROLE_PROJECTOR,
         display_surface=True, action="projector.subscribe"),

    # ---- scripture reads: harmless, and a projector needs them ----
    Rule(_rx(r"/translations"), _GET, ROLE_PROJECTOR,
         display_surface=True, action="bible.translations"),
    Rule(_rx(r"/verse/.*"), _GET, ROLE_PROJECTOR,
         display_surface=True, action="bible.verse"),
    Rule(_rx(r"/passage/.*"), _GET, ROLE_PROJECTOR,
         display_surface=True, action="bible.passage"),
    Rule(_rx(r"/projector/obs-url"), _GET, ROLE_OPERATOR,
         action="projector.obs_url"),

    # ---- control: everything that changes state or spends money ----
    Rule(_rx(r"/projector/show"), _WRITE, ROLE_OPERATOR,
         action="projector.show"),
    Rule(_rx(r"/projector/clear"), _WRITE, ROLE_OPERATOR,
         action="projector.clear"),

    # The parser can call out to Groq, which costs money, so it is not
    # an anonymous endpoint even though it only reads.
    Rule(_rx(r"/parse"), _WRITE, ROLE_OPERATOR, action="parser.parse"),
    Rule(_rx(r"/parse-and-fetch"), _WRITE, ROLE_OPERATOR,
         action="parser.parse_and_fetch"),

    Rule(_rx(r"/stt/status"), _GET, ROLE_OPERATOR, action="stt.status"),
    Rule(_rx(r"/stt/devices"), _GET, ROLE_OPERATOR, action="stt.devices"),
    Rule(_rx(r"/stt/start"), _WRITE, ROLE_OPERATOR, action="stt.start"),
    Rule(_rx(r"/stt/stop"), _WRITE, ROLE_OPERATOR, action="stt.stop"),
    Rule(_rx(r"/stt/language"), _WRITE, ROLE_OPERATOR, action="stt.language"),

    Rule(_rx(r"/obs/status"), _GET, ROLE_OPERATOR, action="obs.status"),
    Rule(_rx(r"/obs/guide"), _GET, ROLE_OPERATOR, action="obs.guide"),
    Rule(_rx(r"/obs/connect"), _WRITE, ROLE_ADMIN, action="obs.connect"),
    Rule(_rx(r"/obs/disconnect"), _WRITE, ROLE_ADMIN, action="obs.disconnect"),
)

# Actions worth an audit row even when they succeed. Reads are omitted:
# logging every verse lookup would bury the entries that matter.
AUDITED_ACTIONS = frozenset({
    "projector.show", "projector.clear",
    "stt.start", "stt.stop", "stt.language",
    "obs.connect", "obs.disconnect",
})


def find_rule(path: str, method: str) -> Rule | None:
    """First matching rule, or None if the path is not covered.

    A path with no rule is denied. Adding a route without adding a rule
    should be a visible failure, not an accidental hole.
    """
    for rule in RULES:
        if rule.matches(path, method):
            return rule
    return None

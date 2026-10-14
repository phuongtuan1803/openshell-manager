"""Fail-closed capability gate for the pinned OpenShell CLI.

v0.1.2 DraftCommands::Get exposes name/status, no structured output or
exact-chunk retrieval. Do not parse terminal output into approval authority.
"""
BLOCKER = ('OpenShell 0.1.2 exact structured proposal/scope contract is unverified; '
           'Telegram approval disabled. Review native proposals on the host.')


class NativeUnavailable(ValueError):
    pass


class NativeAdapter:
    def discover(self):
        raise NativeUnavailable(BLOCKER)

    def get(self, sandbox, chunk_id):
        raise NativeUnavailable(BLOCKER)

    def approve(self, sandbox, chunk_id):
        raise NativeUnavailable(BLOCKER)

    def reject(self, sandbox, chunk_id):
        raise NativeUnavailable(BLOCKER)

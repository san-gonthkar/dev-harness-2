"""Typed error taxonomy for the Dev Harness (V11 0.4).

Every exception subclasses HarnessError and carries a non-empty remediation.
Never raise bare Exception/RuntimeError - the AST scan test enforces this.
"""

from __future__ import annotations

from typing import Any


class HarnessError(Exception):
    """Root of the Dev Harness error taxonomy.

    Every subclass must provide a non-empty ``remediation`` describing what the
    operator can do to recover.
    """

    remediation: str = ""

    def __init__(self, message: str, *, remediation: str | None = None) -> None:
        super().__init__(message)
        if remediation is not None:
            self.remediation = remediation
        if not self.remediation:
            raise ValueError(f"{type(self).__name__} requires a non-empty remediation")

    def __str__(self) -> str:
        return f"{super().__str__()} (remediation: {self.remediation})"


# --- contracts / config / secrets / paths -----------------------------------


class ConfigError(HarnessError):
    """Invalid or missing configuration."""

    remediation = "Fix the configuration file or environment override, then retry."


class InsecureKeyFileError(HarnessError):
    """A secrets key file has insecure permissions."""

    remediation = "chmod 600 the key file, or move the secret to the OS keyring."


class PathError(HarnessError):
    """Invalid or ambiguous path derivation."""

    remediation = "Provide a canonical, absolute workspace path."


# --- storage ----------------------------------------------------------------


class StorageError(HarnessError):
    """Base for storage-layer failures."""

    remediation = "Check the database file and workspace permissions, then retry."


class UnscopedQueryError(StorageError):
    """A query was attempted without a project/thread scope."""

    remediation = "Pass an explicit project_id and thread_id scope to the query."


class LockTimeout(StorageError):
    """A workspace lock could not be acquired within the timeout."""

    remediation = (
        "Another process holds the workspace lock; wait for it to exit or "
        "reclaim a stale lock (dead PID)."
    )


class CorruptCheckpointError(StorageError):
    """A checkpoint failed its integrity digest."""

    remediation = (
        "The checkpoint was quarantined; the prior valid checkpoint is served. "
        "Investigate the cause of the corruption before resuming."
    )


# --- vcs --------------------------------------------------------------------


class VcsError(HarnessError):
    """Base for version-control failures."""

    remediation = "Inspect the git repository state and retry the operation."


class DirtyWorktreeError(VcsError):
    """A destructive git operation was refused on a dirty tree."""

    remediation = (
        "Commit, stash, or autostash the uncommitted changes before restoring."
    )


class WorktreeExistsError(VcsError):
    """A worktree already exists at the requested path."""

    remediation = "Remove the existing worktree or choose a different worker id."


class NoCommitsError(VcsError):
    """The repository has no commits yet."""

    remediation = "Create an initial commit before running the harness."


# --- ipc --------------------------------------------------------------------


class IpcError(HarnessError):
    """Base for IPC transport failures."""

    remediation = "Check the socket path and that the peer process is running."


class FrameTooLargeError(IpcError):
    """An IPC frame exceeded its per-type size limit."""

    remediation = "Reduce the payload size or raise the per-type frame limit."


class IncompleteFrameError(IpcError):
    """An IPC frame was truncated before completion."""

    remediation = "Retry the send; the peer may have closed mid-frame."


class InsecureSocketError(IpcError):
    """A socket had insecure permissions or ownership."""

    remediation = "chmod 600 the socket and ensure it is owned by the current user."


class UnsupportedPlatformError(IpcError):
    """The transport is not supported on this platform."""

    remediation = "Run under WSL2 or another POSIX environment."


# --- providers --------------------------------------------------------------


class ProviderError(HarnessError):
    """Base for LLM provider failures."""

    remediation = "Check provider connectivity and credentials, then retry."


class RateLimitedError(ProviderError):
    """The provider returned 429; retry after the given delay."""

    remediation = "Wait for the Retry-After window or reduce request rate."
    retryable = True
    retry_after: float = 0.0


class ProviderOverloadedError(ProviderError):
    """The provider returned 529 or similar overload signal."""

    remediation = "Retry with backoff; the provider is temporarily overloaded."
    retryable = True


class ContextOverflowError(ProviderError):
    """The request exceeded the model context window."""

    remediation = "Reduce the prompt size or switch to a larger-context model."
    retryable = False


class AuthError(ProviderError):
    """Authentication failed; not retryable."""

    remediation = "Check the API key in env, keyring, or the 0600 key file."
    retryable = False


class TransientError(ProviderError):
    """A transient transport failure; retryable."""

    remediation = "Retry with backoff; the failure is transient."
    retryable = True


class UnknownModelError(ProviderError):
    """A model id is not in the registry."""

    remediation = "Add the model to the config registry or use a known model id."


# --- broker -----------------------------------------------------------------


class BrokerError(HarnessError):
    """Base for rate-limit broker failures."""

    remediation = "Restart the broker daemon and retry."


class BrokerUnavailableError(BrokerError):
    """The broker daemon is not reachable; fail closed."""

    remediation = (
        "Start dev-harness-broker, or set allow_unbrokered=true to bypass "
        "rate limiting explicitly."
    )


class BudgetExceededError(BrokerError):
    """The configured spend ceiling has been reached."""

    remediation = "Raise the budget ceiling or wait for the daily window to reset."


class UnknownProviderError(BrokerError):
    """A provider has no policy registered."""

    remediation = "Add a policy block for the provider in the config."


# --- engine / core ----------------------------------------------------------


class EngineError(HarnessError):
    """Base for engine failures."""

    remediation = "Check the engine daemon log and restart if needed."


class SessionExistsError(EngineError):
    """A session already exists for this workspace."""

    remediation = "Attach to the existing session or stop it first."


class UnknownCommandError(EngineError):
    """An unknown command was sent to the engine."""

    remediation = "Send one of the documented commands; the connection stays open."


class EngineVersionMismatch(EngineError):
    """The client and daemon versions disagree."""

    remediation = "Reinstall the matching dev-harness version on both sides."


class IllegalTransitionError(EngineError):
    """A critic state transition is not permitted by the 0.22 table."""

    remediation = "Send a legal command for the current state per the 0.22 table."

    def __init__(
        self,
        message: str,
        *,
        state: Any = None,
        command: Any = None,
        remediation: str | None = None,
    ) -> None:
        self.state = state
        self.command = command
        super().__init__(message, remediation=remediation)


class CyclicDependencyError(EngineError):
    """The chunk DAG contains a cycle."""

    remediation = "Fix the chunk dependency declarations to remove the cycle."


class OrphanDependencyError(EngineError):
    """A chunk depends on an unknown chunk."""

    remediation = "Declare the missing chunk or remove the dangling dependency."


class IntegrationConflict(EngineError):
    """Chunk branches conflict on merge."""

    remediation = "Resolve the conflicting file between the named chunk branches."


class WorkspaceEscapeError(EngineError):
    """A worker attempted to write outside its worktree."""

    remediation = "Fix the worker prompt to write only inside its worktree root."


class CriticScopeViolation(EngineError):
    """The critic node attempted to write outside its scope."""

    remediation = "Constrain the critic node to its read-only scope."


class PersonaOutputError(EngineError):
    """A persona produced malformed output after repair retries."""

    remediation = "Retry the node or adjust the persona prompt and output contract."


class AllProvidersUnavailable(EngineError):
    """Every provider in the fallback chain is down."""

    remediation = (
        "Restore at least one provider, or resume from the last checkpoint "
        "once connectivity returns."
    )


# --- recovery ---------------------------------------------------------------


class RecoveryError(HarnessError):
    """Base for recovery failures."""

    remediation = "Inspect the recovery log and resume manually if needed."

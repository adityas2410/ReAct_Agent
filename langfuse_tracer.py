import os
from contextlib import contextmanager
from typing import Any, Iterator


class _NoopObservation:
    def update(self, **_: Any) -> None:
        return None


class LangfuseTracer:
    """
    Optional Langfuse observer for the custom ReAct loop.

    Missing credentials, missing SDK, or Langfuse runtime errors never stop agent
    execution. Local run memory remains the durable trace source.
    """

    def __init__(self, enabled: bool = True, host: str | None = None) -> None:
        self.enabled = enabled
        self.host = host or os.environ.get("LANGFUSE_HOST") or "http://localhost:3000"
        self.client: Any = None
        self.trace_id: str | None = None
        self.disabled_reason: str | None = None

        if not enabled:
            self.disabled_reason = "disabled by CLI flag"
            return

        public_key = os.environ.get("LANGFUSE_PUBLIC_KEY")
        secret_key = os.environ.get("LANGFUSE_SECRET_KEY")
        if not public_key or not secret_key:
            self.disabled_reason = "LANGFUSE_PUBLIC_KEY or LANGFUSE_SECRET_KEY missing"
            return

        try:
            from langfuse import Langfuse

            self.client = Langfuse(
                public_key=public_key,
                secret_key=secret_key,
                base_url=os.environ.get("LANGFUSE_BASE_URL") or self.host,
            )
        except Exception as exc:
            self.disabled_reason = f"{type(exc).__name__}: {exc}"
            self.client = None

    @property
    def active(self) -> bool:
        return self.client is not None

    def config(self) -> dict[str, Any]:
        return {
            "enabled": self.enabled,
            "active": self.active,
            "host": self.host,
            "trace_id": self.trace_id,
            "disabled_reason": self.disabled_reason,
        }

    @contextmanager
    def observe(
        self,
        name: str,
        as_type: str = "span",
        input: Any | None = None,
        metadata: dict[str, Any] | None = None,
        model: str | None = None,
    ) -> Iterator[Any]:
        if self.client is None:
            yield _NoopObservation()
            return

        kwargs: dict[str, Any] = {
            "name": name,
            "as_type": as_type,
        }
        if input is not None:
            kwargs["input"] = input
        if metadata is not None:
            kwargs["metadata"] = metadata
        if model is not None:
            kwargs["model"] = model

        observation_cm = None
        try:
            observation_cm = self.client.start_as_current_observation(**kwargs)
            observation = observation_cm.__enter__()
            self._capture_trace_id()
        except Exception as exc:
            self.disabled_reason = f"{type(exc).__name__}: {exc}"
            yield _NoopObservation()
            return

        try:
            yield observation
        except BaseException as exc:
            try:
                observation_cm.__exit__(type(exc), exc, exc.__traceback__)
            except Exception as exit_exc:
                self.disabled_reason = f"{type(exit_exc).__name__}: {exit_exc}"
            raise
        else:
            try:
                observation_cm.__exit__(None, None, None)
            except Exception as exc:
                self.disabled_reason = f"{type(exc).__name__}: {exc}"

    def update(self, observation: Any, **kwargs: Any) -> None:
        try:
            observation.update(**kwargs)
        except Exception as exc:
            self.disabled_reason = f"{type(exc).__name__}: {exc}"

    def event(
        self,
        name: str,
        input: Any | None = None,
        output: Any | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        with self.observe(name=name, as_type="event", input=input, metadata=metadata) as event:
            if output is not None:
                self.update(event, output=output)

    def flush(self) -> None:
        if self.client is None:
            return

        try:
            self.client.flush()
        except Exception as exc:
            self.disabled_reason = f"{type(exc).__name__}: {exc}"

    def _capture_trace_id(self) -> None:
        if self.trace_id or self.client is None:
            return

        getter = getattr(self.client, "get_current_trace_id", None)
        if getter is None:
            return

        try:
            trace_id = getter()
        except Exception:
            return

        if trace_id:
            self.trace_id = str(trace_id)

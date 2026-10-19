"""Poll-based bridge from background work state to shared loading snapshots."""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import replace
from typing import cast

from .footer import FooterStatus, StatusKind
from .loading_lifecycle import LoadingLifecycle
from .loading_model import (
    FinishLoading,
    LoadingInteraction,
    LoadingPlacement,
    LoadingProgress,
    LoadingTaskSpec,
    LoadingToken,
    UpdateLoading,
)
from .message_choices import MessageInput, normalize_messages
from .spinner import spinner_frame
from .transition import call_on_ui_thread


class BackgroundLoadingStatus:
    def __init__(
        self, spec: LoadingTaskSpec, spinner_style: object,
        *, clock: Callable[[], float] = time.monotonic,
        frame: Callable[[], str] | None = None,
    ) -> None:
        if not isinstance(spec, LoadingTaskSpec):
            raise TypeError("BackgroundLoadingStatus requires LoadingTaskSpec")
        if (
            spec.interaction is not LoadingInteraction.BACKGROUND
            or spec.placement is not LoadingPlacement.FOOTER
            or spec.transition
        ):
            raise ValueError("BackgroundLoadingStatus requires a background footer task")
        if frame is not None and not callable(frame):
            raise TypeError("Background loading frame requires a callable")
        self._spec = spec
        self._style = spinner_style
        self._clock = clock
        self._frame = frame
        self._lifecycle: LoadingLifecycle | None = None
        self._token: LoadingToken | None = None

    def read(
        self, active: bool, *, progress: LoadingProgress | None = None,
        message: MessageInput | None = None,
    ) -> FooterStatus | None:
        if not isinstance(active, bool):
            raise TypeError("Background loading state requires bool")
        if progress is not None and not isinstance(progress, LoadingProgress):
            raise TypeError("Background loading progress requires LoadingProgress")
        wording = self._spec.message
        if message is not None:
            choices = normalize_messages(message, field="background.message")
            wording = choices[0] if isinstance(message, str) else choices
        # Preparation may inspect status before opening the picker. Both reads
        # must use the UI pump, including task creation and retirement.
        return call_on_ui_thread(lambda: self._read(active, progress or self._spec.progress, wording))

    def _read(self, active: bool, progress: LoadingProgress | None, message: MessageInput) -> FooterStatus | None:
        if not active:
            if self._lifecycle is not None and self._token is not None:
                self._lifecycle.apply(FinishLoading(self._token))
                self._token = None
            return None
        if self._lifecycle is None:
            self._lifecycle = LoadingLifecycle(clock=self._clock)
        if self._token is None:
            self._token = self._lifecycle.start(replace(self._spec, progress=progress, message=message))
        else:
            self._lifecycle.apply(UpdateLoading(self._token, message, progress))
        snapshot = self._lifecycle.snapshots(self._spec.owner)[0]
        if not snapshot.visible:
            return None
        indicator = (
            self._frame() if self._frame is not None
            else cast(Callable[[object], str], spinner_frame)(self._style)
        )
        return FooterStatus(snapshot.message, StatusKind.LOADING, indicator)

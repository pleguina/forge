"""forge.analyze.dashboards.attachments
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
A generic extension point letting a project contribute extra sections to
``forge report``'s dashboard without FORGE core understanding what those
sections mean.

FORGE core only ever sees a :class:`ReportAttachment` — a title, a kind
(``"image"``, ``"markdown"``, or ``"html"``), and a path to a file the
provider already wrote. It never inspects pixel data, tile geometry, or
any other domain semantic; that stays entirely in the project's own
provider implementation.

Provider registration mirrors this codebase's existing plugin-extension
pattern (``forge.verify.golden_model.register_golden_model_provider``,
``forge.verify.dataset_service``'s adapter registry): a project's own
``bootstrap.py`` calls :func:`register_report_attachment_provider` at
import time, and ``forge report --plugin <id>`` bootstraps that plugin
before collecting attachments — the same bootstrap mechanism
``forge verify run --plugin`` already uses.
"""
from __future__ import annotations

import dataclasses
from pathlib import Path
from typing import Any, Dict, List, Protocol, Sequence, runtime_checkable

_VALID_KINDS = ("image", "markdown", "html")


@dataclasses.dataclass(frozen=True)
class ReportAttachment:
    """One extra section a project contributes to the report dashboard.

    ``path`` must be a file the provider has already written, relative
    to the ``output_dir`` passed into ``build_attachments`` (so it
    survives being written into ``attachments.json`` and read back by
    a separate process, e.g. `forge analyze dashboard` run later against
    the same output directory).
    """
    provider_id: str
    title: str
    kind: str
    path: str
    description: str = ""

    def __post_init__(self) -> None:
        if self.kind not in _VALID_KINDS:
            raise ValueError(f"ReportAttachment.kind must be one of {_VALID_KINDS}, got {self.kind!r}")

    def to_dict(self) -> Dict[str, Any]:
        return dataclasses.asdict(self)

    @staticmethod
    def from_dict(data: Dict[str, Any]) -> "ReportAttachment":
        return ReportAttachment(
            provider_id=data["provider_id"],
            title=data["title"],
            kind=data["kind"],
            path=data["path"],
            description=data.get("description", ""),
        )


@runtime_checkable
class ReportAttachmentProvider(Protocol):
    provider_id: str

    def build_attachments(self, design_path: Path, output_dir: Path) -> Sequence[ReportAttachment]:
        """Write whatever files this provider wants to contribute into
        *output_dir* (or a subdirectory of it) and return a
        :class:`ReportAttachment` describing each one. *design_path* is
        the same design.yml `forge report` was invoked against.
        """
        ...


_PROVIDERS: Dict[str, ReportAttachmentProvider] = {}


def register_report_attachment_provider(provider: ReportAttachmentProvider) -> None:
    """Register *provider*, keyed by its own ``provider_id``. Re-registering
    the same id replaces the previous provider (idempotent for a plugin
    whose bootstrap() runs more than once, matching the golden-model
    provider registry's own re-registration behavior).
    """
    _PROVIDERS[provider.provider_id] = provider


def get_report_attachment_providers() -> List[ReportAttachmentProvider]:
    return list(_PROVIDERS.values())


def _reset_for_testing() -> None:
    _PROVIDERS.clear()

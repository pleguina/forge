#!/usr/bin/env python3
"""vision_pipeline_demo dataset CLI.

Example-local commands, until FORGE has a generic top-level dataset
command -- see docs/development/adr/0001-dataset-ownership-boundary.md
for why dataset loading is deliberately owned by this plugin rather than
FORGE core:

    python3 datasets/cli.py generate-synthetic --output <dir> --dataset-id <id> \\
        --width 8 --height 8 --pattern ramp --event-count 1
    python3 datasets/cli.py import-images --output <dir> --dataset-id <id> \\
        --source <image-dir> --width 32 --height 32
    python3 datasets/cli.py import-numpy --output <dir> --dataset-id <id> \\
        --source <file.npy> --layout NHW
    python3 datasets/cli.py inspect  <manifest.yml>
    python3 datasets/cli.py validate <manifest.yml> [--source-root <dir>]
    python3 datasets/cli.py rebuild  <manifest.yml> --source-root <dir> --output <dir>

BSDS500/Fashion-MNIST importers (``import-bsds500``/
``import-fashion-mnist``) are optional and explicitly out of scope: both
would require network downloads, which conflicts with keeping the
mandatory tutorial path offline-capable -- not wired up here. See the
ADR above for the full reasoning.
"""
from __future__ import annotations

import argparse
import importlib.util as _ilu
import sys
from pathlib import Path
from typing import Any

# Loaded by explicit file path, not via sys.path -- see the matching
# comment in forge/verify/tools/dataset_adapter.py for why adding the
# plugin root to sys.path breaks `import forge` (its own `forge/` asset
# subdirectory shadows the real package as a namespace package).
_PLUGIN_ROOT = Path(__file__).resolve().parents[1]


def _load_datasets_package():
    if "datasets" in sys.modules:
        return sys.modules["datasets"]
    datasets_dir = _PLUGIN_ROOT / "datasets"
    spec = _ilu.spec_from_file_location(
        "datasets", datasets_dir / "__init__.py", submodule_search_locations=[str(datasets_dir)],
    )
    module = _ilu.module_from_spec(spec)   # type: ignore[union-attr]
    sys.modules["datasets"] = module
    spec.loader.exec_module(module)        # type: ignore[union-attr]
    return module


_load_datasets_package()

from datasets.adapters.image_folder import DEFAULT_EXTENSIONS, ImageFolderAdapter  # noqa: E402
from datasets.adapters.numpy_array import NumpyArrayAdapter  # noqa: E402
from datasets.adapters.synthetic import SyntheticPatternAdapter  # noqa: E402
from datasets.manifest import build_manifest, check_staleness, load_manifest, write_manifest  # noqa: E402
from datasets.model import DatasetEvent  # noqa: E402
from datasets.serialize_xml import write_xml  # noqa: E402


def _write_dataset(
    *,
    events: "list[DatasetEvent]",
    output_dir: Path,
    dataset_id: str,
    adapter_id: str,
    adapter_version: str,
    preprocessing_config: "dict[str, Any]",
    preprocessing: "dict[str, Any]",
    adapter_config: "dict[str, Any]",
    source_paths: "list[Path] | None" = None,
    source_root: "Path | None" = None,
) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    xml_path = output_dir / f"{dataset_id}.xml"
    write_xml(events, xml_path)

    manifest = build_manifest(
        dataset_id=dataset_id,
        adapter_id=adapter_id,
        adapter_version=adapter_version,
        events=events,
        preprocessing_config=preprocessing_config,
        preprocessing=preprocessing,
        source_paths=source_paths or [],
        source_root=source_root,
        serialized_xml_path=xml_path,
        serialization={"format": "forge-xml", "schema_version": "1.0", "output_files": [xml_path.name]},
        adapter_config=adapter_config,
    )
    manifest_path = output_dir / f"{dataset_id}.manifest.yml"
    write_manifest(manifest, manifest_path)
    print(f"Wrote {xml_path}")
    print(f"Wrote {manifest_path}")
    return manifest_path


def cmd_generate_synthetic(args: argparse.Namespace) -> int:
    adapter_config = {
        "width": args.width,
        "height": args.height,
        "patterns": args.pattern,
        "seed": args.seed,
        "event_count": args.event_count,
    }
    adapter = SyntheticPatternAdapter(**adapter_config)
    events = list(adapter.iter_events())
    _write_dataset(
        events=events,
        output_dir=Path(args.output),
        dataset_id=args.dataset_id,
        adapter_id=adapter.name,
        adapter_version=adapter.version,
        preprocessing_config={"adapter_id": adapter.name, "adapter_version": adapter.version, **adapter_config},
        preprocessing={},
        adapter_config=adapter_config,
    )
    return 0


def cmd_import_images(args: argparse.Namespace) -> int:
    source_dir = Path(args.source)
    adapter_config = {
        "width": args.width,
        "height": args.height,
        "extensions": list(args.extensions) if args.extensions else list(DEFAULT_EXTENSIONS),
        "max_events": args.max_events,
    }
    adapter = ImageFolderAdapter(source=source_dir, **adapter_config)
    events = list(adapter.iter_events())
    source_paths = [Path(ev.source_metadata["source_file"]) for ev in events]
    _write_dataset(
        events=events,
        output_dir=Path(args.output),
        dataset_id=args.dataset_id,
        adapter_id=adapter.name,
        adapter_version=adapter.version,
        preprocessing_config={
            "adapter_id": adapter.name, "adapter_version": adapter.version,
            "grayscale": {"method": "luma_integer", "coefficients": [77, 150, 29], "shift": 8},
            "resize": {"method": "nearest", "width": args.width, "height": args.height},
            **adapter_config,
        },
        preprocessing={
            "grayscale": "luma_integer",
            "resize": "nearest",
            "output_size": [args.width, args.height],
            "pixel_format": "uint8",
        },
        adapter_config=adapter_config,
        source_paths=source_paths,
        source_root=source_dir,
    )
    return 0


def cmd_import_numpy(args: argparse.Namespace) -> int:
    source_path = Path(args.source)
    adapter_config = {
        "layout": args.layout,
        "array_key": args.array_key,
        "value_range": tuple(args.value_range) if args.value_range else (0, 255),
        "label_key": args.label_key,
        "max_events": args.max_events,
    }
    adapter = NumpyArrayAdapter(source=source_path, **adapter_config)
    events = list(adapter.iter_events())
    _write_dataset(
        events=events,
        output_dir=Path(args.output),
        dataset_id=args.dataset_id,
        adapter_id=adapter.name,
        adapter_version=adapter.version,
        preprocessing_config={"adapter_id": adapter.name, "adapter_version": adapter.version, **adapter_config},
        preprocessing={"value_range": list(adapter_config["value_range"]), "layout": args.layout},
        adapter_config=adapter_config,
        source_paths=[source_path],
        source_root=source_path.parent,
    )
    return 0


def cmd_inspect(args: argparse.Namespace) -> int:
    manifest = load_manifest(args.manifest)
    print(f"dataset id:       {manifest.dataset_id}")
    print(f"adapter:          {manifest.adapter_id} v{manifest.adapter_version}")
    print(f"event count:      {manifest.event_count}")
    print(f"source files:     {len(manifest.source_files)}")
    for name, digest in sorted(manifest.source_files.items()):
        print(f"  {name}: {digest}")
    print(f"preprocessing hash: {manifest.preprocessing_config_hash}")
    print(f"canonical events hash: {manifest.canonical_events_hash}")
    print(f"serialized dataset hash: {manifest.serialized_dataset_hash}")
    return 0


def cmd_validate(args: argparse.Namespace) -> int:
    manifest = load_manifest(args.manifest)
    problems: "list[str]" = []

    manifest_dir = Path(args.manifest).resolve().parent
    output_files = manifest.serialization.get("output_files") or []
    if manifest.serialized_dataset_hash is not None and output_files:
        from forge.core.utils.content_hash import hash_file
        for name in output_files:
            candidate = manifest_dir / name
            if not candidate.exists():
                problems.append(f"declared output file missing: {candidate}")
                continue
            actual = hash_file(candidate)
            if actual != manifest.serialized_dataset_hash:
                problems.append(
                    f"serialized dataset hash mismatch for {candidate}: "
                    f"manifest says {manifest.serialized_dataset_hash}, file hashes to {actual}"
                )

    if args.source_root is not None:
        problems.extend(check_staleness(manifest, source_root=Path(args.source_root)))

    if problems:
        for p in problems:
            print(f"INVALID: {p}")
        return 1
    print(f"{args.manifest}: valid, fresh.")
    return 0


_REBUILDERS = {
    "synthetic": lambda config, source_paths: SyntheticPatternAdapter(**config),
    "image-folder": lambda config, source_paths: ImageFolderAdapter(source=source_paths, **config),
    "numpy-array": lambda config, source_paths: NumpyArrayAdapter(source=source_paths[0], **config),
}


def cmd_rebuild(args: argparse.Namespace) -> int:
    manifest = load_manifest(args.manifest)
    builder = _REBUILDERS.get(manifest.adapter_id)
    if builder is None:
        print(f"Don't know how to rebuild adapter {manifest.adapter_id!r}", file=sys.stderr)
        return 1

    source_root = Path(args.source_root) if args.source_root else None
    source_paths = (
        [source_root / rel for rel in sorted(manifest.source_files)] if source_root is not None else []
    )
    adapter = builder(dict(manifest.adapter_config), source_paths)
    events = list(adapter.iter_events())

    output_dir = Path(args.output) if args.output else Path(args.manifest).resolve().parent
    rebuilt_manifest_path = _write_dataset(
        events=events,
        output_dir=output_dir,
        dataset_id=manifest.dataset_id,
        adapter_id=manifest.adapter_id,
        adapter_version=manifest.adapter_version,
        preprocessing_config={"adapter_id": manifest.adapter_id, "adapter_version": manifest.adapter_version, **manifest.adapter_config},
        preprocessing=dict(manifest.preprocessing),
        adapter_config=dict(manifest.adapter_config),
        source_paths=source_paths,
        source_root=source_root,
    )
    rebuilt = load_manifest(rebuilt_manifest_path)
    if rebuilt.canonical_events_hash != manifest.canonical_events_hash:
        print(
            "REBUILD MISMATCH: canonical_events_hash changed "
            f"(was {manifest.canonical_events_hash}, now {rebuilt.canonical_events_hash}) "
            "-- source data or adapter behavior has drifted since this manifest was built."
        )
        return 1
    print("Rebuild matches recorded manifest exactly.")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="datasets", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("generate-synthetic")
    p.add_argument("--output", required=True)
    p.add_argument("--dataset-id", required=True)
    p.add_argument("--width", type=int, required=True)
    p.add_argument("--height", type=int, required=True)
    p.add_argument("--pattern", action="append", required=True, dest="pattern")
    p.add_argument("--seed", type=int, default=0)
    p.add_argument("--event-count", type=int, default=1)
    p.set_defaults(func=cmd_generate_synthetic)

    p = sub.add_parser("import-images")
    p.add_argument("--output", required=True)
    p.add_argument("--dataset-id", required=True)
    p.add_argument("--source", required=True)
    p.add_argument("--width", type=int, required=True)
    p.add_argument("--height", type=int, required=True)
    p.add_argument("--extensions", nargs="*", default=None)
    p.add_argument("--max-events", type=int, default=None, dest="max_events")
    p.set_defaults(func=cmd_import_images)

    p = sub.add_parser("import-numpy")
    p.add_argument("--output", required=True)
    p.add_argument("--dataset-id", required=True)
    p.add_argument("--source", required=True)
    p.add_argument("--layout", required=True, choices=["HW", "NHW", "NHWC"])
    p.add_argument("--array-key", default=None, dest="array_key")
    p.add_argument("--value-range", type=float, nargs=2, default=None, dest="value_range")
    p.add_argument("--label-key", default=None, dest="label_key")
    p.add_argument("--max-events", type=int, default=None, dest="max_events")
    p.set_defaults(func=cmd_import_numpy)

    p = sub.add_parser("inspect")
    p.add_argument("manifest")
    p.set_defaults(func=cmd_inspect)

    p = sub.add_parser("validate")
    p.add_argument("manifest")
    p.add_argument("--source-root", default=None, dest="source_root")
    p.set_defaults(func=cmd_validate)

    p = sub.add_parser("rebuild")
    p.add_argument("manifest")
    p.add_argument("--source-root", default=None, dest="source_root")
    p.add_argument("--output", default=None)
    p.set_defaults(func=cmd_rebuild)

    return parser


def main(argv: "list[str] | None" = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())

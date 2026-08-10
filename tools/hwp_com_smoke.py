from __future__ import annotations

import argparse
import ctypes
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from hwpmate.constants import FORMAT_TYPES
from hwpmate.models import ConversionSummary, ConversionTask
from hwpmate.services.hwp_converter import HWPConverter, get_registered_hwp_progids


def is_admin() -> bool:
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


def parse_format_set(value: str) -> list[str]:
    formats: list[str] = []
    for raw in value.split(","):
        format_type = raw.strip().upper()
        if not format_type:
            continue
        if format_type not in FORMAT_TYPES:
            raise ValueError(f"Unsupported format: {format_type}")
        if format_type not in formats:
            formats.append(format_type)
    if not formats:
        raise ValueError("At least one output format is required")
    return formats


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run real HWP COM smoke conversions and write one JSON report."
    )
    parser.add_argument("--input", required=True, help="Input .hwp or .hwpx file")
    parser.add_argument("--format", default="", help="Single output format (compatibility alias)")
    parser.add_argument("--formats", default="PDF", help="Comma-separated formats, e.g. PDF,DOCX,PNG,HTML")
    parser.add_argument("--output-dir", default="", help="Output directory. Defaults to input folder.")
    parser.add_argument("--result-json", default="", help="Result JSON path. Defaults to output directory.")
    parser.add_argument("--allow-non-admin", action="store_true", help="Run even when not elevated.")
    return parser.parse_args()


def run_smoke(
    input_file: Path,
    formats: list[str],
    output_dir: Path,
    *,
    allow_non_admin: bool,
) -> tuple[ConversionSummary, int]:
    warnings: list[str] = []
    if not is_admin():
        message = "Administrator privileges are recommended for HWP COM smoke verification."
        if not allow_non_admin:
            raise PermissionError(message)
        warnings.append(message)
    if not get_registered_hwp_progids():
        warnings.append("No registered HWP COM ProgID was detected before conversion.")

    started = time.perf_counter()
    tasks: list[ConversionTask] = []
    for format_type in formats:
        format_dir = output_dir / format_type
        format_dir.mkdir(parents=True, exist_ok=True)
        task = ConversionTask(
            input_file=input_file,
            output_file=format_dir / f"{input_file.stem}{FORMAT_TYPES[format_type].ext}",
        )
        converter = HWPConverter()
        try:
            converter.initialize()
            success, error = converter.convert_file(input_file, task.output_file, format_type)
            task.status = "성공" if success else "실패"
            task.error = error
            task.created_files = list(converter.last_created_files)
            task.output_size = converter.last_output_size
            task.output_mtime = converter.last_output_mtime
            task.save_format = converter.last_save_format
            task.export_method = converter.last_export_method
            task.progid_used = converter.progid_used
            if converter.security_module_registered is False:
                warnings.append(f"{format_type}: security module registration failed: {converter.security_module_error or ''}")
            if converter.process_tracking_warning:
                warnings.append(f"{format_type}: {converter.process_tracking_warning}")
        except Exception as exc:
            task.status = "실패"
            task.error = str(exc)
        finally:
            converter.cleanup()
        tasks.append(task)

    summary = ConversionSummary(
        format_type=",".join(formats),
        tasks=tasks,
        warnings=warnings,
        elapsed_seconds=time.perf_counter() - started,
    )
    return summary, 0 if summary.failed_count == 0 else 1


def main() -> int:
    args = parse_args()
    input_file = Path(args.input).resolve()
    if not input_file.is_file():
        print(f"Input file not found: {input_file}", file=sys.stderr)
        return 2
    try:
        formats = parse_format_set(args.format or args.formats)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2

    output_dir = Path(args.output_dir).resolve() if args.output_dir else input_file.parent
    output_dir.mkdir(parents=True, exist_ok=True)
    result_json = Path(args.result_json).resolve() if args.result_json else output_dir / f"hwp_com_smoke_{int(time.time())}.json"
    try:
        summary, exit_code = run_smoke(input_file, formats, output_dir, allow_non_admin=args.allow_non_admin)
    except PermissionError as exc:
        print(str(exc), file=sys.stderr)
        return 3

    with result_json.open("w", encoding="utf-8") as handle:
        json.dump(summary.to_json_dict(), handle, ensure_ascii=False, indent=2)
        handle.write("\n")
    print(f"Result JSON: {result_json}")
    for task in summary.tasks:
        if task.status == "성공":
            print(f"{task.output_file.suffix}: {', '.join(str(path) for path in task.created_files) or task.output_file}")
        else:
            print(f"{task.output_file.suffix}: {task.detail}", file=sys.stderr)
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())

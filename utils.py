from dataclasses import dataclass
from functools import lru_cache
from typing import Any

import click
import yt_dlp
from yt_dlp import list_extractor_classes
from yt_dlp.extractor.generic import GenericIE
from yt_dlp.extractor.lazy_extractors import GenericIE as LazyGenericIE
from yt_dlp.plugins import load_all_plugins


class ArgsConverter:
    def __init__(self) -> None:
        self.default_options: dict[str, Any] = yt_dlp.parse_options([]).ydl_opts

    def convert(self, args: list[str]) -> dict[str, Any]:
        parsed_opts: dict[str, Any] = yt_dlp.parse_options(args).ydl_opts
        opts = {
            opt: val
            for opt, val in parsed_opts.items()
            if self.default_options[opt] != val
        }
        return opts


class LogCatcher:
    def __init__(self, print_to_stdout: bool = False) -> None:
        self.logs: dict[str, list[str]] = {
            "debug": [],
            "info": [],
            "warning": [],
            "error": [],
        }
        self.print_to_stdout = print_to_stdout

    def log(self, level: str, msg: str) -> None:
        self.logs[level].append(msg)
        if self.print_to_stdout:
            click.echo(msg, err=level == "error")

    def debug(self, msg: str) -> None:
        self.log("debug", msg)

    def info(self, msg: str) -> None:
        self.log("info", msg)

    def warning(self, msg: str) -> None:
        self.log("warning", msg)

    def error(self, msg: str) -> None:
        self.log("error", msg)

    def messages(self, level: str) -> list[str]:
        return self.logs[level]

    @property
    def debug_messages(self) -> list[str]:
        return self.messages("debug")

    @property
    def info_messages(self) -> list[str]:
        return self.messages("info")

    @property
    def warning_messages(self) -> list[str]:
        return self.messages("warning")

    @property
    def error_messages(self) -> list[str]:
        return self.messages("error")


def has_downloadable_formats(info: dict) -> bool:
    if not info:
        return False

    entries: list[dict] = info.get("entries", [info])
    for entry in entries:
        if len(entry.get("formats", [])) > 0:
            return True
    return False


@dataclass(init=False)
class Option:
    names: list[str]
    has_values: bool = False
    restricted: bool = False
    unique: bool = False

    def __init__(self, *names: str, restricted: bool = False, unique: bool = False):
        self.names = []
        self.restricted = restricted
        self.unique = unique
        self.key: str = ""
        for name in names:
            name = name.strip()
            if not name.startswith("-"):
                raise ValueError(f"Invalid name: {name}")
            if name not in self.names:
                self.names.append(name)
            if not self.key:
                self.key = name
                continue
            if name.startswith("--") and not self.key.startswith("--"):
                self.key = name
        if not self.key:
            raise ValueError("No valid name found")


CLI_OPTIONS = [
    Option("-h", "--help", restricted=True),
    Option("-U", "--update", restricted=True),
    Option("--update-to", restricted=True),
    Option("--no-ignore-no-formats-error", restricted=True),
    Option("--newline", restricted=True),
    Option("--no-progress", restricted=True),
    Option("-a", "--batch-file", restricted=True),
    Option("--merge-output-format", unique=True),
]

CLI_OPTIONS_MAP: dict[str, Option] = {}
for opt in CLI_OPTIONS:
    for name in opt.names:
        CLI_OPTIONS_MAP[name] = opt


def sanitize_args(raw_args: list[str]) -> list[str]:
    grouped_args: list[list[str] | None] = []
    unique_args: dict[str, int] = {}
    option_group = None
    for arg in raw_args:
        if arg.startswith("-"):
            option_group = None
            if arg in CLI_OPTIONS_MAP:
                opt = CLI_OPTIONS_MAP[arg]
                if opt.restricted:
                    continue
                elif opt.unique:
                    if opt.key in unique_args:
                        index = unique_args[opt.key]
                        grouped_args[index] = None
                    unique_args[opt.key] = len(grouped_args)
            option_group = [arg]
            grouped_args.append(option_group)
        elif option_group:
            option_group.append(arg)
    sanitized_args = []
    for grp in grouped_args:
        if grp:
            sanitized_args.extend(grp)
    return sanitized_args


@lru_cache(maxsize=1)
def get_all_extractors(plugins_first: bool = True) -> list:
    load_all_plugins()
    extractors = list_extractor_classes()
    if plugins_first:
        plugins = []
        base_extractors = []
        for extractor in extractors:
            if extractor.__module__.startswith("yt_dlp_plugins."):
                plugins.append(extractor)
            else:
                base_extractors.append(extractor)
        extractors = plugins
        extractors.extend(base_extractors)
    return extractors


def determine_extractor(url: str) -> str | None:
    extractors = get_all_extractors()
    for extractor in extractors:
        if extractor in [GenericIE, LazyGenericIE]:
            continue
        try:
            if extractor.suitable(url):
                return extractor.IE_NAME
        except Exception:
            pass
    return GenericIE.IE_NAME


def safe_dict(d: dict, *keys, default=None) -> Any:
    value = d
    for key in keys:
        if key in value:
            value = value[key]
        else:
            value = default
            break
    return value


def validate_slice(spec: str) -> str:
    try:
        parts = spec.split(":")
        assert 0 < len(parts) <= 3
        spec = ":".join(str(int(p)) if p.strip() else "" for p in parts)
    except (AssertionError, ValueError):
        raise ValueError(f"Invalid slice spec: {spec}")
    return spec


def convert_playlist_slice(spec: str, list_size: int) -> str:
    # spec should have already been validated
    parts = []
    for i, part in enumerate(spec.split(":")):
        if i == 0 and not part:
            part = "1"
        elif i == 1 and not part:
            part = str(list_size)
        elif i < 2 and part:
            index = int(part)
            if index < 0:
                index += list_size + 1
            part = str(index)
        parts.append(part)
    return ":".join(parts)

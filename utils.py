from typing import Any

import click
import yt_dlp
from yt_dlp import list_extractor_classes


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


def sanitize_args(raw_args: list[str], restricted_args: list[str] = None) -> list[str]:
    sanitized_args = []
    skip_args = False
    for arg in raw_args:
        if arg.startswith("-"):
            skip_args = False
        if skip_args:
            continue
        if arg in restricted_args:
            skip_args = True
            continue
        sanitized_args.append(arg)
    return sanitized_args


def determine_extractor(url: str) -> str | None:
    extractors = list_extractor_classes()
    for extractor in extractors:
        try:
            if extractor.suitable(url):
                return extractor.IE_NAME
        except Exception:
            pass
    return None


def safe_dict(d: dict, *keys, default=None) -> Any:
    value = d
    for key in keys:
        if key in value:
            value = value[key]
        else:
            value = default
            break
    return value

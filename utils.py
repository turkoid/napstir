import re
import sys
from typing import Any

import click
import yt_dlp


class ArgsConverter:
    def __init__(self):
        self.default_options: dict[str, Any] = yt_dlp.parse_options([]).ydl_opts

    def convert(self, args: list[str]) -> dict[str, Any]:
        parsed_opts: dict[str, Any] = yt_dlp.parse_options(args).ydl_opts
        opts = {
            opt: val
            for opt, val in parsed_opts.items()
            if self.default_options[opt] != val
        }
        return opts


type LogMessage = tuple[str | None, str]


class LogCatcher:
    def __init__(self, print_to_stdout: bool = False):
        self.logs: dict[str, list[LogMessage]] = {
            "debug": [],
            "info": [],
            "warning": [],
            "error": [],
        }
        self.print_to_stdout = print_to_stdout

    def log(self, level: str, msg: str) -> None:
        if match := re.match(r"(.+?: )?(\[(?P<extractor>.+?)] )?(?P<msg>.+)", msg):
            extractor = match.group("extractor")
            message = match.group("msg")
        else:
            extractor = None
            message = msg
        self.logs[level].append((extractor, message))
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
        messages = [msg for _, msg in self.logs[level]]
        return messages

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

    @property
    def debug_logs(self) -> list[LogMessage]:
        return self.logs["debug"]

    @property
    def info_logs(self) -> list[LogMessage]:
        return self.logs["info"]

    @property
    def warning_logs(self) -> list[LogMessage]:
        return self.logs["warning"]

    @property
    def error_logs(self) -> list[LogMessage]:
        return self.logs["error"]


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


if __name__ == "__main__":
    cli_options = ArgsConverter()
    opts = cli_options.convert(sys.argv[1:])
    click.echo(opts)

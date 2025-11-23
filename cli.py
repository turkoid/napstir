import os
from dataclasses import dataclass
from dataclasses import field

import click
import tomlkit
import yt_dlp
from tomlkit import array
from yt_dlp import YoutubeDL

from utils import ArgsConverter
from utils import has_downloadable_formats
from utils import LogCatcher
from utils import sanitize_args


@dataclass
class ExtractorConfig:
    id: str
    aliases: list[str]
    args: list[str]

    def __post_init__(self) -> None:
        self.id = self.id.lower()
        self.aliases = [alias.lower() for alias in self.aliases]

    def add_alias(self, alias: str) -> None:
        if alias not in self.aliases:
            self.aliases.append(alias)


class Config:
    def __init__(
        self,
        file_path: str,
        input_file: str,
        output_dir: str,
        verbose: bool,
        urls: list[str],
    ) -> None:
        self.file_path = file_path
        with open(file_path, "r") as fp:
            self.doc = tomlkit.load(fp)
        self.ytp_dlp_dir = self.doc["yt-dlp"]["directory"]
        self.ytp_dlp_path = os.path.join(self.ytp_dlp_dir, "yt_dlp", "__main__.py")
        self.input_file = input_file
        self.output_dir = output_dir
        self.verbose = verbose
        self.urls = urls
        self.extractor_configs: dict[str, ExtractorConfig] = {}
        self.default_extractor = ExtractorConfig(
            "default", [], args=self.doc["extractor"]["default"]["args"].unwrap()
        )
        self.global_extractor = ExtractorConfig(
            "global", [], args=self.doc["extractor"]["global"]["args"].unwrap()
        )
        self.aliased_extractors: dict[str, str] = {}
        for id, cfg_data in self.doc.get("extractor", {}).items():
            if id in ("default", "global"):
                continue
            cfg = ExtractorConfig(
                id, cfg_data.get("aliases", []), cfg_data["args"].unwrap()
            )
            self.extractor_configs[id.lower()] = cfg
            for alias in cfg.aliases:
                self.aliased_extractors[alias] = cfg.id

    def save(self):
        for extractor in self.extractor_configs.values():
            if not extractor.aliases:
                continue
            toml_extractor = self.doc["extractor"][extractor.id]
            if "aliases" not in toml_extractor:
                toml_aliases = array()
                toml_aliases.multiline(True)
                new_aliases = extractor.aliases
                toml_extractor.append("aliases", toml_aliases)
            else:
                toml_aliases = toml_extractor["aliases"]
                new_aliases = [
                    alias for alias in extractor.aliases if alias not in toml_aliases
                ]
            toml_aliases.extend(new_aliases)

        with open(self.file_path, "w") as fp:
            tomlkit.dump(self.doc, fp)


RESTRICTED_ARGS = [
    "-h",
    "--help",
    "-U",
    "--update" "--update-to",
    "--list-extractor",
    "--extractor-descriptions",
    "--use-extractors",
    "--no-quiet",
    "-s",
    "--simulate",
    "--no-simulate",
    "--ignore-no-formats-error",
    "--no-ignore-no-formats-error",
    "--skip-download",
    "-O",
    "--print",
    "-j",
    "--dump-json",
    "-J",
    "--dump-single-json",
    "--newline",
    "--no-progress",
    "--dump-pages",
    "--print-traffic",
    "-a",
    "--batch-file",
]


@dataclass
class Metadata:
    url: str
    src: str
    args: list[str] = field(default_factory=list)
    extractor: str | None = None
    info: dict | None = None
    errors: list[str] = field(default_factory=list)
    config_extractor: str | None = field(default=None, init=False)

    def __post_init__(self):
        converted_args = []
        for arg in self.args:
            if arg == "-a" or arg.startswith("-a:"):
                converted_args.append("--extract-audio")
                if ":" in arg and (audio_format := arg[3:]):
                    converted_args.append("--audio-format")
                    converted_args.append(audio_format)
        sanitized_args = sanitize_args(converted_args, RESTRICTED_ARGS)
        self.args = sanitized_args

    @property
    def id(self) -> str | None:
        return self.extractor.lower() if self.extractor else None


class Cli:
    def __init__(self, config: Config) -> None:
        self.config = config
        self.args_converter = ArgsConverter()

    def extract_info(
        self, url: str, custom_args: list[str], logger: LogCatcher
    ) -> dict:
        args = [
            "--quiet",
            "--no-warnings",
            "--no-playlist",
            "--simulate",
            "--flat-playlist",
            "--check-formats",
            "--ignore-errors",
        ]
        args.extend(custom_args)
        opts = self.args_converter.convert(args)
        opts["logger"] = logger

        with yt_dlp.YoutubeDL(opts) as ydl:
            click.echo(f"Extracting info for {url}")
            info = ydl.extract_info(url, download=False)
            return info

    def create_metadatas(self, entries: list[str], src: str) -> list[Metadata]:
        metadatas = []
        for entry in entries:
            if not entry or entry.startswith("#"):
                continue
            parts = entry.split()
            args = parts[:-1]
            url = parts[-1]
            logger = LogCatcher(self.config.verbose)
            info = self.extract_info(url, args, logger)
            errors = logger.error_messages
            # default to extractor from error message if one exists
            if logger.error_logs:
                extractor = logger.error_logs[-1][0]
            else:
                extractor = None
            # override extractor key if it exists in info
            if info and "extractor_key" in info:
                extractor = info["extractor_key"]
            if has_downloadable_formats(info):
                errors = None
            else:
                info = None
            if extractor:
                errors = None
            metadata = Metadata(url, src, args, extractor, info, errors)
            metadatas.append(metadata)
        return metadatas

    def find_the_next_best_thing(self, metadata: Metadata) -> str | None:
        # Good Mythical Morning
        for extractor, cfg in self.config.extractor_configs.items():
            if extractor in metadata.id:
                confirm = click.confirm(
                    f"[{metadata.extractor}] config not found. Closest match is {extractor}. Would you like to use this one?",
                    default=True,
                )
                if confirm:
                    self.config.extractor_configs[extractor].add_alias(metadata.id)
                    self.config.aliased_extractors[metadata.id] = extractor
                    return extractor
        return None

    def set_config_extractors(self, metadatas: list[Metadata]) -> None:
        for metadata in metadatas:
            if metadata.errors or metadata.id is None:
                continue
            if metadata.id in self.config.extractor_configs:
                config_extractor = metadata.id
            elif metadata.id in self.config.aliased_extractors:
                config_extractor = self.config.aliased_extractors[metadata.id]
            else:
                config_extractor = self.find_the_next_best_thing(metadata)
            metadata.config_extractor = config_extractor

    def download(self, metadata: Metadata) -> None:
        args = self.config.global_extractor.args[:]
        if self.config.output_dir:
            args.extend(["--paths", self.config.output_dir])
        if metadata.config_extractor is None:
            args.extend(self.config.default_extractor.args)
        else:
            args.extend(self.config.extractor_configs[metadata.config_extractor].args)
        args.extend(metadata.args)
        if self.config.verbose:
            args.append("-v")
        opts = self.args_converter.convert(args)
        with YoutubeDL(opts) as ytdl:
            if metadata.info:
                ytdl.process_ie_result(metadata.info)
            else:
                ytdl.download(metadata.url)

    def run(self) -> None:
        metadatas = []

        if self.config.input_file:
            with open(self.config.input_file) as fp:
                input_data = fp.read()
            lines = [line.strip() for line in input_data.splitlines()]
            file_metadatas = self.create_metadatas(lines, "file")
            metadatas.extend(file_metadatas)
        if self.config.urls:
            args_metadata = self.create_metadatas(self.config.urls, "cli")
            metadatas.extend(args_metadata)

        if not metadatas:
            return

        self.set_config_extractors(metadatas)
        self.config.save()

        header_len = max([len(md.url) for md in metadatas])
        header_line = "-" * (header_len + 2)
        header_line = f"+{header_line}+"
        for metadata in metadatas:
            click.echo(
                f"{header_line}\n| {metadata.url:<{header_len + 1}}|\n{header_line}"
            )
            if metadata.errors:
                for error in metadata.errors:
                    click.echo(f"[{metadata.extractor}] {error}")
            else:
                click.echo(f"[{metadata.extractor}] Downloading {metadata.url}")
                self.download(metadata)


@click.command(no_args_is_help=True)
@click.option(
    "-c", "--config", "config_file", type=click.Path(exists=True), default="config.toml"
)
@click.option("-i", "--input", "input_file", type=click.Path(exists=True))
@click.option("-o", "--output", "output_dir", type=click.Path())
@click.option("-v", "--verbose", is_flag=True)
@click.argument("urls", nargs=-1)
def run(
    config_file: str, input_file: str, output_dir: str, verbose: bool, urls: list[str]
):
    config = Config(config_file, input_file, output_dir, verbose, urls)
    cli = Cli(config)
    cli.run()


if __name__ == "__main__":
    run()

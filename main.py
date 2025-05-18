import os
import subprocess
from dataclasses import dataclass
from dataclasses import field
from operator import attrgetter

import click
import tomlkit
from tomlkit import array
from tomlkit import document
from tomlkit import table


def fatal_error(msg: str) -> None:
    click.echo(msg, err=True)
    quit(1)


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
        self, file_path: str, input_file: str, output_dir: str, urls: list[str]
    ) -> None:
        self.file_path = file_path
        with open(file_path, "rb") as fp:
            doc = tomlkit.load(fp)
            self.doc = doc
        self.ytp_dlp_dir = doc["yt-dlp"]["directory"]
        self.ytp_dlp_path = os.path.join(self.ytp_dlp_dir, "yt_dlp", "__main__.py")
        self.input_file = input_file
        self.output_dir = output_dir
        self.urls = urls
        self.extractor_configs: dict[str, ExtractorConfig] = {}
        self.default_extractor = ExtractorConfig(
            "default", [], args=doc["extractor"]["default"]["args"]
        )
        self.global_extractor = ExtractorConfig(
            "global", [], args=doc["extractor"]["global"]["args"]
        )
        self.aliased_extractors: dict[str, str] = {}
        for id, cfg_data in doc.get("extractor", {}).items():
            if id in ("default", "global"):
                continue
            cfg = ExtractorConfig(id, cfg_data.get("aliases", []), cfg_data["args"])
            self.extractor_configs[id.lower()] = cfg
            for alias in cfg.aliases:
                self.aliased_extractors[alias] = cfg.id

    def save(self):
        doc = document()

        yt_dlp = table()
        yt_dlp.add("directory", self.ytp_dlp_dir)
        doc.add("yt-dlp", yt_dlp)

        extractors = table(True)
        custom_extractors = sorted(
            self.extractor_configs.values(), key=attrgetter("id")
        )
        all_extractors = [
            self.global_extractor,
            self.default_extractor,
            *custom_extractors,
        ]
        for cfg in all_extractors:
            extractor = table()
            if cfg.aliases:
                aliases = array()
                aliases.extend(cfg.aliases)
                aliases.multiline(True)
                extractor.add("aliases", aliases)
            args = array()
            args.extend(cfg.args)
            args.multiline(True)
            extractor.add("args", args)
            extractors.add(cfg.id, extractor)
        doc.add("extractor", extractors)

        with open("auto-generation.toml", "w") as fp:
            tomlkit.dump(doc, fp)


@dataclass
class Metadata:
    url: str
    src: str
    options: list[str] = field(default_factory=list)
    extractor: str | None = None
    error: str | None = None

    @property
    def id(self) -> str | None:
        return self.extractor.lower() if self.extractor else None


class Cli:
    def __init__(self, config: Config) -> None:
        self.config = config

    def update_yt_dlp(self):
        subprocess.run(
            ["git", "fetch", "origin"], cwd=self.config.ytp_dlp_dir, check=True
        )
        subprocess.run(
            ["git", "merge", "origin/master", "--no-edit"],
            cwd=self.config.ytp_dlp_dir,
            check=True,
        )

    def determine_extractors(self, urls: list[str]) -> str:
        cp = subprocess.run(
            [
                "python",
                self.config.ytp_dlp_path,
                "--no-playlist",
                "--print",
                "%(extractor)s",
                *urls,
            ],
            universal_newlines=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
        )
        return cp.stdout.strip()

    def create_metadatas(self, lines: list[str], src: str) -> list[Metadata]:
        items = []
        for line in lines:
            if not line or line.startswith("#"):
                continue
            parts = line.split()
            opts = parts[:-1]
            url = parts[-1]
            items.append((opts, url))
        output = self.determine_extractors([url for _, url in items])
        output_lines = output.splitlines()
        metadatas = []
        for (opts, url), line in zip(items, output_lines):
            if line.startswith("ERROR"):
                left_bracket = len("ERROR: ")
                if line[left_bracket] != "[":
                    extractor = None
                    error = line[left_bracket:]
                else:
                    right_bracket = line.index("]", left_bracket + 1)
                    extractor = line[left_bracket + 1 : right_bracket]
                    error = line[right_bracket + 2 :]
            else:
                extractor = line
                error = None
            metadata = Metadata(url, src, opts, extractor, error)
            metadatas.append(metadata)
        return metadatas

    def get_metadatas(self) -> list[Metadata]:
        metadatas = []
        if self.config.input_file:
            with open(self.config.input_file) as fp:
                input_data = fp.read()
            lines = [line.strip() for line in input_data.splitlines()]
            src_metadatas = self.create_metadatas(lines, "file")
            metadatas.extend(src_metadatas)
        if self.config.urls:
            src_metadatas = self.create_metadatas(self.config.urls, "arg")
            metadatas.extend(src_metadatas)
        return metadatas

    def get_extractors(
        self, metadatas: list[Metadata]
    ) -> list[tuple[Metadata, str | None]]:
        for metadata in metadatas:
            if metadata.error:
                continue
            if metadata.id in self.config.extractor_configs:
                continue
            if metadata.id in self.config.aliased_extractors:
                continue
            for id, cfg in self.config.extractor_configs.items():
                if id in metadata.id:
                    confirm = click.confirm(
                        f"[{metadata.extractor}] config not found. Closest match is {id}. Would you like to use this one?",
                        default=True,
                    )
                    if confirm:
                        self.config.extractor_configs[id].add_alias(metadata.id)
                        self.config.aliased_extractors[metadata.id] = id
                        break
            if metadata.id not in self.config.aliased_extractors:
                self.config.aliased_extractors[metadata.id] = "default"
        extractors = [
            (md, self.config.aliased_extractors.get(md.id, md.id)) for md in metadatas
        ]
        return extractors

    def download(self, metadata: Metadata, extractor_id: str):
        cmd = [
            "python",
            self.config.ytp_dlp_path,
        ]
        cmd.extend(self.config.global_extractor.args)
        if extractor_id == "default":
            cmd.extend(self.config.default_extractor.args)
        else:
            cmd.extend(self.config.extractor_configs[extractor_id].args)
        cmd.extend(metadata.options)
        cmd.append(metadata.url)
        subprocess.run(cmd, cwd=self.config.output_dir)

    def main(self) -> None:
        metadatas = self.get_metadatas()
        extractors = self.get_extractors(metadatas)
        self.config.save()
        for metadata, extractor_id in extractors:
            if metadata.error:
                click.echo(f"[{metadata.extractor}] ERROR: {metadata.error}")
            else:
                click.echo(f"[{metadata.extractor}] Downloading {metadata.url}")
                self.download(metadata, extractor_id)


@click.command(no_args_is_help=True)
@click.option("-i", "--input", "input_file", type=click.Path(exists=True))
@click.option("-o", "--output", "output_dir", type=click.Path())
@click.argument("urls", nargs=-1)
def run(input_file: str, output_dir: str, urls: list[str]):
    config = Config("config.toml", input_file, output_dir, urls)
    cli = Cli(config)
    cli.config.save()
    cli.update_yt_dlp()
    cli.main()


if __name__ == "__main__":
    run()

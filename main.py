import os
import subprocess
import tomllib
from dataclasses import dataclass

import click


def fatal_error(msg: str) -> None:
    click.echo(msg, err=True)
    quit(1)


@dataclass
class ExtractorConfig:
    id: str
    aliases: list[str]
    args: list[str]


class Config:
    def __init__(
        self, file_path: str, input_file: str, output_dir: str, urls: list[str]
    ) -> None:
        with open(file_path, "rb") as fp:
            data: dict = tomllib.load(fp)
        self.ytp_dlp_dir = data["yt-dlp"]["directory"]
        self.ytp_dlp_path = os.path.join(self.ytp_dlp_dir, "yt_dlp", "__main__.py")
        self.input_file = input_file
        self.output_dir = output_dir
        self.urls = urls
        self.extractor_configs: dict[str, ExtractorConfig] = {}
        self.default_extractor = ExtractorConfig(
            "default", [], args=data["extractor"]["default"]["args"]
        )
        self.global_extractor_args = data["extractor"]["global"]["args"]
        for id, cfg_data in data.get("extractor", {}).items():
            if id in ("default", "global"):
                continue
            cfg = ExtractorConfig(id, cfg_data.get("aliases", []), cfg_data["args"])
            self.extractor_configs[id.lower()] = cfg


@dataclass
class Metadata:
    url: str
    src: str
    key: str | None
    extractor: str | None
    error: str | None = None

    @property
    def id(self) -> str | None:
        return self.key.lower() if self.key else None


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

    def get_metadata_output(self, *urls: str) -> str:
        cp = subprocess.run(
            [
                "python",
                self.config.ytp_dlp_path,
                "--print",
                "%(extractor_key)s#|#%(extractor)s",
                *urls,
            ],
            universal_newlines=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
        )
        return cp.stdout

    def get_source_metadatas(
        self, output: str, urls: list[str], src: str
    ) -> list[Metadata]:
        metadatas = []
        lines = [line.strip() for line in output.splitlines() if line]
        if len(lines) != len(urls):
            fatal_error(
                f"ERROR: Invalid output from yt-dlp: Expected {len(urls)} lines, got {len(lines)}\n{output}"
            )

        for url, line in zip(urls, lines):
            if line.startswith("ERROR"):
                left_bracket = len("ERROR: ")
                if line[left_bracket] != "[":
                    key = None
                    extractor = None
                    error = line[left_bracket:]
                else:
                    right_bracket = line.index("]", left_bracket + 1)
                    key = line[left_bracket + 1 : right_bracket]
                    extractor = key
                    error = line[right_bracket + 2 :]
            else:
                key, extractor = line.split("#|#")
                error = None
            metadata = Metadata(url, src, key, extractor, error)
            metadatas.append(metadata)
        return metadatas

    def get_metadatas(self) -> list[Metadata]:
        metadatas = []
        if self.config.input_file:
            with open(self.config.input_file) as fp:
                input_data = fp.read()
            lines = input_data.splitlines()
            urls = []
            for line in lines:
                url = line.strip()
                if not url or url.startswith("#"):
                    continue
                urls.append(url)
            output = self.get_metadata_output(*urls)
            parsed_metadatas = self.get_source_metadatas(output, urls, "file")
            metadatas.extend(parsed_metadatas)
        if self.config.urls:
            output = self.get_metadata_output(*self.config.urls)
            parsed_metadatas = self.get_source_metadatas(
                output, self.config.urls, "arg"
            )
            metadatas.extend(parsed_metadatas)
        return metadatas

    def get_extractors(
        self, metadatas: list[Metadata]
    ) -> list[tuple[Metadata, str | None]]:
        alias_configs: dict[str, str] = {}
        for metadata in metadatas:
            if metadata.error:
                continue
            if metadata.id in self.config.extractor_configs:
                continue
            if metadata.id in alias_configs:
                continue
            for id, cfg in self.config.extractor_configs.items():
                if id in metadata.id:
                    confirm = click.confirm(
                        f"[{metadata.extractor}] config not found. Closest match is {id}. Would you like to use this one?",
                        default=True,
                    )
                    if confirm:
                        self.config.extractor_configs[id].aliases.append(metadata.id)
                        alias_configs[metadata.id] = id
                        break
            if metadata.id not in alias_configs:
                alias_configs[metadata.id] = "default"
        extractors = [(md, alias_configs.get(md.id, md.id)) for md in metadatas]
        return extractors

    def download(self, url: str, extractor_id: str):
        cmd = [
            "python",
            self.config.ytp_dlp_path,
        ]
        cmd.extend(self.config.global_extractor_args)
        if extractor_id == "default":
            cmd.extend(self.config.default_extractor.args)
        else:
            cmd.extend(self.config.extractor_configs[extractor_id].args)
        cmd.append(url)
        subprocess.run(cmd, cwd=self.config.output_dir)

    def main(self) -> None:
        metadatas = self.get_metadatas()
        extractors = self.get_extractors(metadatas)
        for metadata, extractor_id in extractors:
            if metadata.error:
                click.echo(f"[{metadata.extractor}] ERROR: {metadata.error}")
            else:
                click.echo(f"[{metadata.extractor}] Downloading {metadata.url}")
                self.download(metadata.url, extractor_id)


@click.command()
@click.option("-i", "--input", "input_file", type=click.Path(exists=True))
@click.option("-o", "--output", "output_dir", type=click.Path())
@click.argument("urls", nargs=-1)
def run(input_file: str, output_dir: str, urls: list[str]):
    config = Config("config.toml", input_file, output_dir, urls)
    cli = Cli(config)
    cli.update_yt_dlp()
    cli.main()


if __name__ == "__main__":
    run()

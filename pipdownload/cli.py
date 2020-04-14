import itertools
import json
import logging
import os
import subprocess
import sys
import warnings
from collections import OrderedDict

import click
import pip_api
import requests
from cachecontrol import CacheControl
from pathlib import Path

from pipdownload import logger
from pipdownload.settings import SETTINGS_FILE
from pipdownload.utils import (
    TempDirectory,
    download as normal_download,
    get_file_links,
    mkurl_pypi_url,
    quiet_download,
    resolve_package_file,
)
from tzlocal import get_localzone

sess = requests.Session()
session = CacheControl(sess)


@click.command()
@click.argument("packages", nargs=-1)
@click.option(
    "-i",
    "--index-url",
    "index_url",
    default="https://pypi.org/simple",
    type=click.STRING,
    help="Pypi index.",
)
@click.option(
    "-r",
    "--requirement",
    "requirement_file",
    type=click.Path(exists=True, file_okay=True, resolve_path=True),
    help="Requirements File.",
)
@click.option(
    "-d",
    "--dest",
    "dest_dir",
    type=click.Path(exists=False, file_okay=False, writable=True, resolve_path=True),
    help="Destination directory.",
)
@click.option(
    "-s",
    "--suffix",
    "whl_suffixes",
    type=click.STRING,
    multiple=True,
    hidden=True,
    help="Suffix of whl packages except `none-any` `tar.gz` `zip`.\n"
    'Deprecated, Using "-p/--platform-tag instead!"',
)
@click.option(
    "-p",
    "--platform-tag",
    "platform_tags",
    type=click.STRING,
    multiple=True,
    help="Suffix of whl packages except 'none-any', like 'win_amd64', 'manylinux1_x86_64', 'linux_i386' "
    "and so on. It can be specified multiple times. This is an option to replace option 'suffix'. "
    "You can even specify 'manylinux' to download packages contain 'manylinux1_x86_64', "
    "'manylinux2010_x84_64', 'manylinu2014_x86_64'."
)
@click.option(
    "-py",
    "--python-version",
    "python_versions",
    type=click.STRING,
    multiple=True,
    help="Version of python to be downloaded. More specifically, this is the abi tag of the Python package. "
    "It can be specified multiple times. Like: 'cp38', 'cp37', 'cp36', 'cp35', 'cp27' and so on.",
)
@click.option(
    "-q",
    "--quiet",
    is_flag=True,
    help="When specified, logs and progress bar will not be shown.",
)
@click.option(
    "--no-source",
    "no_source",
    is_flag=True,
    help="When specified, the source package of the project that provides wheel package will not be "
    "downloaded.",
)
@click.option(
    "--show-config",
    "show_config",
    is_flag=True,
    help="When specified, the config file will be created if not exists and the path will be shown later.",
)
def pipdownload(
    packages,
    index_url,
    requirement_file,
    dest_dir,
    whl_suffixes,
    platform_tags,
    python_versions,
    quiet,
    no_source,
    show_config
):
    """
    pip-download is a tool which can be used to download python projects and their dependencies listed on
    pypi's `download files` page. It can be used to download Python packages across system platforms and
    Python versions.
    """
    if show_config:
        if not Path(SETTINGS_FILE).exists():
            Path(SETTINGS_FILE).parent.mkdir(parents=True, exist_ok=True)
            Path(SETTINGS_FILE).touch()
        click.echo(f"The config file is {SETTINGS_FILE}.")

    if Path(SETTINGS_FILE).exists():
        with open(SETTINGS_FILE, 'r') as f:
            try:
                settings_dict = json.loads(f.read(), object_pairs_hook=OrderedDict)
            except json.decoder.JSONDecodeError:
                settings_dict = {}

        if not python_versions:
            python_versions = settings_dict.get("python_versions", None)
            if not python_versions:
                click.echo(f"Using `python_versions` in config file.")

        if not whl_suffixes or not platform_tags:
            platform_tags = settings_dict.get("platform_tags", None)
            if not platform_tags:
                click.echo(f"Using `platform_tags` in config file.")

    tz = get_localzone()
    if tz.zone in ["Asia/Shanghai", "Asia/Chongqing"]:
        index_url = "https://mirrors.aliyun.com/pypi/simple/"

    if whl_suffixes:
        warnings.warn(
            "Option '-s/--suffix' has been deprecated. Please use '-p/--platform-tag' instead."
        )
        platform_tags = whl_suffixes

    if quiet:
        logger.setLevel(logging.ERROR)
        download = quiet_download
    else:
        download = normal_download

    if not dest_dir:
        dest_dir = os.getcwd()
    else:
        if not os.path.exists(dest_dir):
            os.makedirs(dest_dir)
    # dest_dir = os.path.abspath(dest_dir)
    if requirement_file:
        packages_extra_dict = pip_api.parse_requirements(requirement_file)
        packages_extra = {str(value) for value in packages_extra_dict.values()}
    else:
        packages_extra = set()
    for package in itertools.chain(packages_extra, packages):
        with TempDirectory(delete=True) as directory:
            logger.info(
                "We are using pip download command to download package %s" % package
            )
            logger.info("-" * 50)

            try:
                command = [
                    sys.executable,
                    "-m",
                    "pip",
                    "download",
                    "-i",
                    index_url,
                    "--dest",
                    directory.path,
                    package,
                ]
                if quiet:
                    command.extend(["--progress-bar", "off", "-qqq"])
                subprocess.check_call(command)
            except subprocess.CalledProcessError as e:
                logger.error(
                    "Sorry, we can not use pip download to download the package %s,"
                    " and Exception is below" % package
                )
                logger.error(e)
                raise
            file_names = os.listdir(directory.path)

            for file_name in file_names:
                python_package = resolve_package_file(file_name)
                if python_package.name is None:
                    logger.warning(
                        "Can not resolve a package's name and version from a downloaded package. You shuold "
                        "create an issue maybe."
                    )
                    continue
                url = mkurl_pypi_url(index_url, python_package.name)
                try:
                    r = session.get(url)
                    for file in get_file_links(r.text, url, python_package):

                        if "none-any" in file:
                            download(file, dest_dir)
                            continue

                        if (file_name.endswith(".tar.gz") or file_name.endswith(".zip")) and not no_source:
                            download(file, dest_dir)
                            continue
                            # if ".tar.gz" in file:
                            #     download(file, dest_dir)
                            #     continue
                            # if ".zip" in file:
                            #     download(file, dest_dir)
                            #     continue
                        eligible = True
                        if platform_tags:
                            for tag in platform_tags:
                                if tag in file:
                                    eligible = True
                                    break
                                else:
                                    eligible = False
                        if not eligible:
                            continue

                        if python_versions:
                            for version in python_versions:
                                if version in file:
                                    eligible = True
                                    break
                                else:
                                    eligible = False

                        if eligible:
                            download(file, dest_dir)

                except ConnectionError as e:
                    logger.error(
                        "Can not get information about package %s, and the Exception is below.",
                        python_package.name,
                    )
                    logger.error(e)
                    raise
            logger.info("All packages have been downloaded successfully!")


if __name__ == "__main__":
    pipdownload()

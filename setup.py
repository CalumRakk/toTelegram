from pathlib import Path

from setuptools import find_packages, setup

from totelegram import __version__

this_directory = Path(__file__).parent
long_description = (this_directory / "README.md").read_text(encoding="utf-8")

setup(
    name="totelegram",
    version=__version__,
    description="toTelegram sube archivos a telegram sin importar el tamaño.",
    author="Leo",
    url="https://github.com/CalumRakk/toTelegram",
    long_description=long_description,
    long_description_content_type="text/markdown",
    install_requires=[
        "filetype==1.2.0",
        "peewee>=3.17.9",
        "pydantic>=2.11.7",
        "pydantic-settings>=2.10.1",
        "Pyrogram @ git+https://github.com/CalumRakk/pyrogram.git",
        "TgCrypto>=1.2.5",
        "typer>=0.21.1",
        "filelock>=3.20.3",
        "tartape>=2.2.0",
    ],
    packages=find_packages(),
    entry_points={
        "console_scripts": ["totelegram=totelegram.cli.__main__:run_script"],
    },
    python_requires=">=3.10.0",
)

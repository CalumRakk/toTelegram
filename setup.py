from setuptools import find_packages, setup

from totelegram import __version__

setup(
    name="totelegram",
    version=__version__,
    description="toTelegram sube archivos a telegram sin importar el tamaño.",
    author="Leo",
    url="https://github.com/CalumRakk/toTelegram",
    install_requires=[
        "filetype==1.2.0",
        "peewee==3.17.9",
        "pydantic==2.11.7",
        "pydantic-settings==2.10.1",
        "Pyrogram @ git+https://github.com/CalumRakk/pyrogram.git@0e81b6a6ea259db0f08ac28fe5541f6d032f28e8",
        "TgCrypto==1.2.5",
        "rich==14.2.0",
        "typer==0.21.1",
        "filelock==3.20.3",
    ],
    packages=find_packages(),
    entry_points={
        "console_scripts": ["totelegram=totelegram.cli:run_script"],
    },
    python_requires=">=3.10.0",
)

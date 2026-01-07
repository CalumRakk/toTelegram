from setuptools import find_packages, setup

setup(
    name="totelegram",
    version="0.2.1",
    description="toTelegram sube archivos a telegram sin importar el tamaño.",
    # long_description=open("README.md", encoding="utf-8").read(),
    # long_description_content_type="text/markdown",
    # readme="README.md",
    author="Leo",
    url="https://github.com/CalumRakk/toTelegram",
    install_requires=[
        "filetype==1.2.0",
        "peewee==3.17.9",
        "pydantic==2.11.7",
        "pydantic-settings==2.10.1",
        "Pyrogram==2.0.106",
        "TgCrypto==1.2.5",
        "rich==14.2.0",
        "typer==0.21.1",
        "filelock==3.20.2",
    ],
    packages=find_packages(),
    entry_points={
        "console_scripts": ["totelegram=totelegram.cli:run_script"],
    },
)

from setuptools import setup
import os
import pkg_resources


setup(
    name="toTelegram",
    version="0.1.1",
    description="toTelegram sube archivos a telegram sin importar el tamaño.",
    long_description=open("README.md", encoding="utf-8").read(),
    long_description_content_type="text/markdown",
    readme="README.md",
    author="Leo",
    url="https://github.com/CalumRakk/toTelegram",
    install_requires=[
        "filetype>=1.1",
        "PyExifTool>=0.5.4",
        "Pyrogram>=2.0.59",
        "PyYAML>=6.0",
        "TgCrypto>=1.2.4",
        "humanfriendly",
        "ffmpeg-python>=0.2.0",
    ],
    packages=[
        "toTelegram",
        "toTelegram.managers",
        "toTelegram.split",
        "toTelegram.types",
    ],
    entry_points={
        "console_scripts": ["toTelegram=toTelegram.cli:run_script"],
    },
)

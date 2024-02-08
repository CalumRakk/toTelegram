from setuptools import setup, find_packages

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
        "pyrogram",
        "PyYAML>=6.0",
        "humanfriendly",
        "ffmpeg-python>=0.2.0",
    ],
    packages=find_packages(),
    entry_points={
        "console_scripts": ["toTelegram=toTelegram.cli:run_script"],
    },
)

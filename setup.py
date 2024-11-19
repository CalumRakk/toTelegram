from setuptools import setup, find_packages

setup(
    name="toTelegram",
    version="0.1.4",
    description="toTelegram sube archivos a telegram sin importar el tamaño.",
    long_description=open("README.md", encoding="utf-8").read(),
    long_description_content_type="text/markdown",
    readme="README.md",
    author="Leo",
    url="https://github.com/CalumRakk/toTelegram",
    install_requires=[
        "filetype",
        "PyExifTool",
        "pyrogram",
        "PyYAML>=6.0",
        "humanfriendly",
        "ffmpeg-python",
        "tqdm",
    ],
    packages=find_packages(),
    entry_points={
        "console_scripts": ["toTelegram=toTelegram.cli:run_script"],
    },
)

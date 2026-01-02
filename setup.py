from setuptools import setup, find_packages
import os

with open("README.md", "r", encoding="utf-8") as fh:
    long_description = fh.read()

setup(
    name="pyfastdl",  
    version="1.0.2",      
    author="RootedSudo",
    author_email="rootedsudo@proton.me",
    description="A smart async downloader with hybrid chunking strategy",
    long_description=long_description,
    long_description_content_type="text/markdown",
    url="https://github.com/rootedSudo/PyFastDL",
    packages=find_packages(), 
    classifiers=[
        "Programming Language :: Python :: 3",
        "License :: OSI Approved :: MIT License",
        "Operating System :: OS Independent",
    ],
    python_requires='>=3.7',
    install_requires=[
        "aiohttp",
        "aiofiles",
    ],
)

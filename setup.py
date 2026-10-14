from os import environ
from pathlib import Path
from setuptools import find_packages, setup

setup(
    name="kde-notification-center",
    version=environ.get("VERSION", "001"),
    author="Michael Cassaniti",
    author_email="michael@cassaniti.id.au",
    description="KDE Notification Center",
    license="GPL-3.0-or-later",
    packages=find_packages(exclude=["tests", "tests.*"]),
    package_data={"knotification_center": ["VERSION"]},
    install_requires=(
        Path("requirements.txt").read_text().splitlines()
        if Path("requirements.txt").exists()
        else list()
    ),
    entry_points={
        "console_scripts": [
            "knotification-center = knotification_center.main:main",
        ]
    },
)

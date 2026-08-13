from glob import glob
import os

from setuptools import find_packages, setup


PACKAGE_NAME = "realtime_dialog"


setup(
    name=PACKAGE_NAME,
    version="0.1.0",
    packages=find_packages(exclude=("test",)),
    data_files=[
        ("share/ament_index/resource_index/packages", ["resource/realtime_dialog"]),
        (f"share/{PACKAGE_NAME}", ["package.xml"]),
        (os.path.join("share", PACKAGE_NAME, "launch"), glob("launch/*.launch.py")),
        (os.path.join("share", PACKAGE_NAME, "config"), glob("config/*.yaml")),
    ],
    install_requires=["setuptools", "aiohttp>=3.8,<4"],
    zip_safe=True,
    maintainer="CASBOT Yandex Migration Team",
    maintainer_email="devnull@example.invalid",
    description="CASBOT-compatible Yandex Realtime dialog node",
    license="UNLICENSED",
    entry_points={
        "console_scripts": [
            "realtime_dialog_node = realtime_dialog.realtime_dialog_node:main",
        ],
    },
)

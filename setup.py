from setuptools import setup, find_packages


setup(
    name="rcjktools",
    python_requires=">=3.7",
    package_dir={"": "Lib"},
    packages=find_packages("Lib"),
    install_requires=[
        "fonttools[ufo,lxml,unicode]",
        "ufoLib2",
    ],
    entry_points={
        'console_scripts': ['ttxv=rcjktools.ttxv:main'],
    },
)

from setuptools import setup, find_packages


setup(
    name="rcjktools",
    use_scm_version={"write_to": "Lib/rcjktools/_version.py"},
    python_requires=">=3.7",
    package_dir={"": "Lib"},
    packages=find_packages("Lib"),
    install_requires=[
        "fonttools[ufo,unicode] >= 4.17.0",
        "ufo2ft",
        "ufoLib2",
    ],
    extras_require={
        "colrv1": ["blackrenderer"],
    },
    setup_requires=["setuptools_scm"],
    entry_points={
        "console_scripts": [
            "buildvarc=rcjktools.buildVarC:main",
            "buildcolrv1=rcjktools.buildCOLRv1:main",
            "rcjk2ufo=rcjktools.project:rcjk2ufo",
            "rcjklint=rcjktools.lint:main",
            "rcjkproofer=rcjktools.proofer:main",
            "ttf2woff2=rcjktools.ttf2woff2:main",
            "ttxv=rcjktools.ttxv:main",
        ],
    },
)

from setuptools import setup, find_packages


setup(
    name="rcjktools",
    use_scm_version={"write_to": "Lib/rcjktools/_version.py"},
    python_requires=">=3.7",
    package_dir={"": "Lib"},
    packages=find_packages("Lib"),
    install_requires=[
        "fonttools[ufo,lxml,unicode] >= 4.17.0",
        "ufoLib2",
    ],
    setup_requires=["setuptools_scm"],
    entry_points={
        'console_scripts': [
            'ttxv=rcjktools.ttxv:main',
            'rcjk2ufo=rcjktools.project:rcjk2ufo',
            'buildvarc=rcjktools.buildVarC:main',
            'ttf2woff2=rcjktools.ttf2woff2:main',
        ],
    },
)

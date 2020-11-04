import pathlib
import sys
from fontTools.ttLib import TTFont


def main():
    p = pathlib.Path(sys.argv[1])
    f = TTFont(p)
    f.flavor = "woff2"
    f.save(p.parent / (p.stem + ".woff2"))

import argparse
import pathlib
import sys
from fontTools.ttLib import TTFont


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("fonts", nargs="+", help="One or more TTF or OTF fonts")
    parser.add_argument("--output-dir", help="The output dir for the compressed fonts")

    args = parser.parse_args()

    if args.output_dir:
        outputDir = pathlib.Path(args.output_dir).resolve()
        outputDir.mkdir(parents=True, exist_ok=True)
    else:
        outputDir = None

    for p in args.fonts:
        p = pathlib.Path(p).resolve()
        parentFolder = outputDir if outputDir is not None else p.parent
        fileName = p.stem + ".woff2"
        outputPath = parentFolder / fileName
        print("source:", p)
        print("destination:", outputPath)
        f = TTFont(p)
        f.flavor = "woff2"
        f.save(outputPath)


if __name__ == "__main__":
    main()

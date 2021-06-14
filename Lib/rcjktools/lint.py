import argparse
import logging
import re
from .project import RoboCJKProject
from .objects import InterpolationError


checks = {}


def lintcheck(checkName):
    def wrap(checkFunc):
        checks[checkName] = checkFunc
        return checkFunc

    return wrap


glyphSetNames = [
    "characterGlyphGlyphSet",
    "deepComponentGlyphSet",
    "atomicElementGlyphSet",
]


def iterGlyphSets(project):
    for glyphSetName in glyphSetNames:
        yield glyphSetName, getattr(project, glyphSetName)


def iterGlyphs(project):
    for glyphSetName, glyphSet in iterGlyphSets(project):
        for glyphName in glyphSet.getGlyphNamesAndUnicodes():
            glyph = glyphSet.getGlyph(glyphName)
            yield glyphSetName, glyphName, glyph


glyphNamePat = re.compile(r"[a-zA-Z0-9_.\\*-]+$")


@lintcheck("glyphname")
def checkGlyphNames(project):
    for glyphSetName, glyphSet in iterGlyphSets(project):
        for glyphName in glyphSet.getGlyphNamesAndUnicodes():
            m = glyphNamePat.match(glyphName)
            if m is None:
                yield f"invalid glyph name '{glyphName}' (in {glyphSetName})"


@lintcheck("interpolate")
def checkInterpolation(project):
    for glyphSetName, glyphName, glyph in iterGlyphs(project):
        location = {
            axisTag: (v1 + v2) / 2 for axisTag, (v1, v2) in glyph.axes.items()
        }
        try:
            inst = glyph.instantiate(location)
        except InterpolationError as e:
            yield f"interpolation error '{glyphName}', {e} (in {glyphSetName})"


@lintcheck("layer")
def checkGlyphExistsInLayer(project):
    for glyphSetName, glyphName, glyph in iterGlyphs(project):
        for layerName in getattr(glyph, "glyphNotInLayer", ()):
            yield f"'{glyphName}' does not exist in layer '{layerName}'"


@lintcheck("variations")
def checkGlyphVariations(project):
    for glyphSetName, glyphName, glyph in iterGlyphs(project):
        for vg in glyph.variations:
            if vg.variations:
                yield f"'{glyphName}' variation glyph for {vg.location} has variations"


# - does glyph interpolate?
# - mix of outlines and components
# - does unicode match uni1234?
# - are glyph unicodes unique? (maybe)
# - is glyph advance 1000/XXXX? check variations, too
# - are var compo axis values within min/max range?
# - are all axes used?
# - are the axes used within the axes defined? (no "stray" axis tags)
# - are all variations locations unique?
# - are there any unused atomic elements?
# - are there any unused deep components?
# - are outlines closed?
# - outline consist of more than two points?


def commaSeparatedList(arg):
    return set(arg.split(","))


def main():
    checkNames = ", ".join(checks)
    parser = argparse.ArgumentParser(
        description=f"Perform lint checks on one or more rcjk projects: {checkNames}")
    parser.add_argument("rcjkproject", nargs="+")
    parser.add_argument(
        "--include",
        type=commaSeparatedList,
        default=set(),
        help="Comma separated list of checks to include",
    )
    parser.add_argument(
        "--exclude",
        type=commaSeparatedList,
        default=set(),
        help="Comma separated list of checks to exclude",
    )
    args = parser.parse_args()

    logging.basicConfig(level=logging.ERROR)

    for projectPath in args.rcjkproject:
        project = RoboCJKProject(projectPath)
        for checkName, checkFunc in checks.items():
            if args.include and not checkName in args.include:
                continue
            if checkName in args.exclude:
                continue
            for msg in checkFunc(project):
                print(f"{projectPath}:{checkName}: {msg}")


if __name__ == "__main__":
    main()

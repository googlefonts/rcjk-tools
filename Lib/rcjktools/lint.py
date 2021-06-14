import argparse
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
    for glyphSetName, glyphSet in iterGlyphSets(project):
        for glyphName in glyphSet.getGlyphNamesAndUnicodes():
            glyph = glyphSet.getGlyph(glyphName)
            location = {axisTag: (v1 + v2) / 2 for axisTag, (v1, v2) in glyph.axes.items()}
            try:
                inst = glyph.instantiate(location)
            except InterpolationError as e:
                yield f"interpolation error '{glyphName}', {e} (in {glyphSetName})"


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


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("rcjkproject", nargs="+")
    args = parser.parse_args()

    for projectPath in args.rcjkproject:
        project = RoboCJKProject(projectPath)
        for checkname, checkFunc in checks.items():
            for msg in checkFunc(project):
                print(f"{projectPath}:{checkname}: {msg}")


if __name__ == "__main__":
    main()

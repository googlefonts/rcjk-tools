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
        location = {axisTag: (v1 + v2) / 2 for axisTag, (v1, v2) in glyph.axes.items()}
        try:
            inst = glyph.instantiate(location)
        except InterpolationError as e:
            yield f"'{glyphName}' {e} (in {glyphSetName})"


@lintcheck("layer")
def checkGlyphExistsInLayer(project):
    for glyphSetName, glyphName, glyph in iterGlyphs(project):
        for layerName in getattr(glyph, "glyphNotInLayer", ()):
            yield f"'{glyphName}' does not exist in layer '{layerName}'"


@lintcheck("nested_variations")
def checkGlyphVariations(project):
    for glyphSetName, glyphName, glyph in iterGlyphs(project):
        for vg in glyph.variations:
            if vg.variations:
                yield f"'{glyphName}' variation glyph for {vg.location} has variations"


@lintcheck("mix_outlines_components")
def checkGlyphMixOutlinesAndComponents(project):
    for glyphSetName, glyphName, glyph in iterGlyphs(project):
        if not glyph.outline.isEmpty() and glyph.components:
            yield f"'{glyphName}' mixes outlines and components (in {glyphSetName})"


hexAllCaps = re.compile("[0-9A-F]+$")


@lintcheck("uni_name")
def checkGlyphUnicodeName(project):
    for glyphSetName, glyphName, glyph in iterGlyphs(project):
        if glyphName.startswith("uni"):
            base = glyphName.split(".")[0]
            if len(base[3:]) < 4:
                yield f"'{glyphName}' unicode value in glyph name should be at least 4 hex digits"
            m = hexAllCaps.match(base[3:])
            if m is None:
                yield f"'{glyphName}' unicode value in glyph name must be uppercase hexadecimal"


@lintcheck("uni_name_vs_unicodes")
def checkGlyphUnicodeNameVsUnicodes(project):
    for glyphSetName, glyphName, glyph in iterGlyphs(project):
        if glyphName.startswith("uni") and "." not in glyphName:
            uni = int(glyphName[3:], 16)
            if uni not in glyph.unicodes:
                unis = ",".join(f"U+{u:04X}" for u in glyph.unicodes)
                yield f"'{glyphName}' unicode in glyph name does not occur in glyph.unicodes ({unis})"


@lintcheck("unused_deep_component")
def checkUnusedDeepComponents(project):
    glyphSet = project.characterGlyphGlyphSet
    compoGlyphSet = project.deepComponentGlyphSet
    yield from _checkUnusedComponents(glyphSet, compoGlyphSet)


@lintcheck("unused_atomic_element")
def checkUnusedDeepComponents(project):
    glyphSet = project.deepComponentGlyphSet
    compoGlyphSet = project.atomicElementGlyphSet
    yield from _checkUnusedComponents(glyphSet, compoGlyphSet)


def _checkUnusedComponents(glyphSet, compoGlyphSet):
    usedComponents = set()
    for glyphName in glyphSet.getGlyphNamesAndUnicodes():
        glyph = glyphSet.getGlyph(glyphName)
        for compo in glyph.components:
            if compo.name not in glyphSet:
                usedComponents.add(compo.name)
    for name in sorted(compoGlyphSet.getGlyphNamesAndUnicodes()):
        if name not in usedComponents:
            yield f"component glyph '{name}' is not used"


@lintcheck("unused_deep_component_axis")
def checkUnusedDeepComponentAxes(project):
    glyphSet = project.characterGlyphGlyphSet
    compoGlyphSet = project.deepComponentGlyphSet
    yield from _checkUnusedAxes(glyphSet, compoGlyphSet)


@lintcheck("unused_atomic_element_axis")
def checkUnusedAtomicElementAxes(project):
    glyphSet = project.deepComponentGlyphSet
    compoGlyphSet = project.atomicElementGlyphSet
    yield from _checkUnusedAxes(glyphSet, compoGlyphSet)


def _checkUnusedAxes(glyphSet, compoGlyphSet):
    availableAxes = {}
    for glyphName in compoGlyphSet.getGlyphNamesAndUnicodes():
        glyph = compoGlyphSet.getGlyph(glyphName)
        if glyph.axes:
            availableAxes[glyphName] = set(glyph.axes)

    usedComponentGlyphs = set()
    for glyphName in glyphSet.getGlyphNamesAndUnicodes():
        glyph = glyphSet.getGlyph(glyphName)
        for compo in glyph.components:
            usedComponentGlyphs.add(compo.name)
            for axisName in compo.coord:
                availableAxes[compo.name].discard(axisName)

    for glyphName, axisNames in availableAxes.items():
        if glyphName not in usedComponentGlyphs:
            # this is reported separately, see _checkUnusedComponents
            continue
        for axisName in axisNames:
            yield f"Axis {axisName} of glyph '{glyphName}' is not used"


@lintcheck("contour")
def checkContours(project):
    for glyphSetName, glyphName, glyph in iterGlyphs(project):
        pen = ContourCheckerPointPen()
        glyph.drawPoints(pen)
        if pen.hasOpenContours:
            yield f"'{glyphName} has one or more open contours (in {glyphSetName})"
        if pen.hasShortContours:
            yield f"'{glyphName} has one or more contours that have fewer than three points (in {glyphSetName})"


class ContourCheckerPointPen:
    hasOpenContours = False
    hasShortContours = False

    def beginPath(self):
        self.numPoints = 0

    def addPoint(self, pt, segmentType, *args, **kwargs):
        if segmentType == "move":
            hasOpenContours = True
        self.numPoints += 1

    def endPath(self):
        if self.numPoints <= 2:
            self.hasShortContours = True

    def addComponent(self, *args, **kwargs):
        pass


@lintcheck("advance")
def checkAdvance(project):
    defaultAdvanceWidth = project.lib.get("robocjk.defaultGlyphWidth")
    if defaultAdvanceWidth is None:
        yield f"robocjk.defaultGlyphWidth has not been set in *.rcjk/fontLib.json"
    else:
        glyphSet = project.characterGlyphGlyphSet
        for glyphName in project.keys():
            if glyphName.startswith("_"):
                continue
            glyph = glyphSet.getGlyph(glyphName)
            for g in [glyph] + glyph.variations:
                if g.width != defaultAdvanceWidth:
                    if not g.location:
                        locStr = ""
                    else:
                        locStr = f"at {formatLocation(g.location)} "
                    yield (
                        f"'{glyphName}' {locStr}does not have the default advance "
                        f"width, {g.width} instead of {defaultAdvanceWidth}"
                    )


def formatLocation(location):
    return ",".join(
        f"{axisName}={formatAxisValue(axisValue)}"
        for axisName, axisValue in sorted(location.items())
    )


def formatAxisValue(value):
    i = int(value)
    if i == value:
        return i
    return value


# - are glyph unicodes unique? (maybe)
# - is glyph advance 1000/XXXX? check variations, too
# - are var compo axis values within min/max range?
# - are all axes used?
# - are the axes used within the axes defined? (no "stray" axis tags)
# - are all variations locations unique?


def commaSeparatedList(arg):
    return set(arg.split(","))


def main():
    checkNames = ", ".join(checks)
    parser = argparse.ArgumentParser(
        description=f"Perform lint checks on one or more rcjk projects: {checkNames}"
    )
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

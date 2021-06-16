import argparse
import logging
import re
import traceback
from .project import RoboCJKProject
from .objects import InterpolationError


VERBOSE = False  # can be overridden by command line


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
        for glyphName in sorted(glyphSet.getGlyphNamesAndUnicodes()):
            glyph, _ = getGlyphWithError(glyphSet, glyphName)
            if glyph is not None:
                yield glyphSetName, glyphName, glyph
            # error is handled in checkLoadGlyph()


def getGlyphWithError(glyphSet, glyphName):
    glyph = None
    error = None
    try:
        glyph = glyphSet.getGlyph(glyphName)
    except Exception as e:
        error = f"error loading '{glyphName}' {e!r}"
        if VERBOSE:
            traceback.print_exc()
    return glyph, error


glyphNamePat = re.compile(r"[a-zA-Z0-9_.\\*-]+$")


@lintcheck("glyphname")
def checkGlyphNames(project):
    """Check whether glyph names are well formed."""
    for glyphSetName, glyphSet in iterGlyphSets(project):
        for glyphName in glyphSet.getGlyphNamesAndUnicodes():
            m = glyphNamePat.match(glyphName)
            if m is None:
                yield f"invalid glyph name '{glyphName}' (in {glyphSetName})"


@lintcheck("load_glyph")
def checkLoadGlyph(project):
    """Check whether a glyph can be successfully loaded."""
    for glyphSetName, glyphSet in iterGlyphSets(project):
        for glyphName in glyphSet.getGlyphNamesAndUnicodes():
            _, error = getGlyphWithError(glyphSet, glyphName)
            if error is not None:
                yield error


@lintcheck("interpolate")
def checkInterpolation(project):
    """Check whether a variable glyph can interpolate."""
    for glyphSetName, glyphName, glyph in iterGlyphs(project):
        location = {axisTag: (v1 + v2) / 2 for axisTag, (v1, v2) in glyph.axes.items()}
        try:
            _ = glyph.instantiate(location)
        except InterpolationError as e:
            yield f"'{glyphName}' {e} (in {glyphSetName})"


@lintcheck("layer")
def checkGlyphExistsInLayer(project):
    """Check whether a glyph in a layer exists, if the parent glyph specifies a
    layerName for a variation.
    """
    for glyphSetName, glyphName, glyph in iterGlyphs(project):
        for layerName in getattr(glyph, "glyphNotInLayer", ()):
            yield f"'{glyphName}' does not exist in layer '{layerName}'"


@lintcheck("nested_variations")
def checkGlyphVariations(project):
    """Check whether variation glyphs have variations themselves."""
    for glyphSetName, glyphName, glyph in iterGlyphs(project):
        for vg in glyph.variations:
            if vg.variations:
                yield (
                    f"'{glyphName}' variation glyph for "
                    f"{formatLocation(vg.location)} has variations"
                )


@lintcheck("orphan_glyph")
def checkGlyphIsOrphan(project):
    """Check whether glyphs in layers have a corresponding glyph in the default layer."""
    for glyphSetName, glyphSet in iterGlyphSets(project):
        glyphNames = set(glyphSet.getGlyphNamesAndUnicodes())
        for layerName in glyphSet.getLayerNames():
            layer = glyphSet.getLayer(layerName)
            layerGlyphNames = set(layer.getGlyphNamesAndUnicodes())
            unreachableGlyphNames = layerGlyphNames - glyphNames
            for glyphName in sorted(unreachableGlyphNames):
                yield f"'{glyphName}' of layer '{layerName}' has no parent (in {glyphSetName})"


@lintcheck("mix_outlines_components")
def checkGlyphMixOutlinesAndComponents(project):
    """Check whether a glyph uses a mix of outlines and components."""
    for glyphSetName, glyphName, glyph in iterGlyphs(project):
        if not glyph.outline.isEmpty() and glyph.components:
            yield f"'{glyphName}' mixes outlines and components (in {glyphSetName})"


hexAllCaps = re.compile("[0-9A-F]+$")


@lintcheck("uni_name")
def checkGlyphUnicodeName(project):
    """Check validity of uniXXXX glyph names."""
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
    """Check uniXXXX glyph names against glyph.unicodes."""
    for glyphSetName, glyphName, glyph in iterGlyphs(project):
        if glyphName.startswith("uni") and "." not in glyphName:
            uni = int(glyphName[3:], 16)
            if uni not in glyph.unicodes:
                unis = ",".join(f"U+{u:04X}" for u in glyph.unicodes)
                yield f"'{glyphName}' unicode in glyph name does not occur in glyph.unicodes ({unis})"


@lintcheck("unused_deep_component")
def checkUnusedDeepComponents(project):
    """Check for unused Deep Components."""
    glyphSet = project.characterGlyphGlyphSet
    compoGlyphSet = project.deepComponentGlyphSet
    yield from _checkUnusedComponents(glyphSet, compoGlyphSet)


@lintcheck("unused_atomic_element")
def checkUnusedAtomicElements(project):
    """Check for unused Atomic Elements."""
    glyphSet = project.deepComponentGlyphSet
    compoGlyphSet = project.atomicElementGlyphSet
    yield from _checkUnusedComponents(glyphSet, compoGlyphSet)


def _checkUnusedComponents(glyphSet, compoGlyphSet):
    usedComponents = set()
    for glyphName in glyphSet.getGlyphNamesAndUnicodes():
        glyph, error = getGlyphWithError(glyphSet, glyphName)
        if error:
            continue
        for compo in glyph.components:
            if compo.name not in glyphSet:
                usedComponents.add(compo.name)
    for name in sorted(compoGlyphSet.getGlyphNamesAndUnicodes()):
        if name not in usedComponents:
            yield f"component glyph '{name}' is not used"


@lintcheck("deep_component_axis")
def checkDeepComponentAxes(project):
    """Check Deep Component axes:
    - Are all defined axes used?
    - Are all used axes defined?
    - Are all axis values within the defined range?
    """
    glyphSet = project.characterGlyphGlyphSet
    compoGlyphSet = project.deepComponentGlyphSet
    yield from _checkComponentAxes(glyphSet, compoGlyphSet)


@lintcheck("atomic_element_axis")
def checkAtomicElementAxes(project):
    """Check Atomic Element axes:
    - Are all defined axes used?
    - Are all used axes defined?
    - Are all axis values within the defined range?
    """
    glyphSet = project.deepComponentGlyphSet
    compoGlyphSet = project.atomicElementGlyphSet
    yield from _checkComponentAxes(glyphSet, compoGlyphSet)


def _checkComponentAxes(glyphSet, compoGlyphSet):
    axisRanges = {}
    for glyphName in compoGlyphSet.getGlyphNamesAndUnicodes():
        glyph, error = getGlyphWithError(compoGlyphSet, glyphName)
        if not error and glyph.axes:
            axisRanges[glyphName] = glyph.axes

    unusedAxes = {
        glyphName: set(axisNames) for glyphName, axisNames in axisRanges.items()
    }

    usedComponentGlyphs = set()
    for glyphName in glyphSet.getGlyphNamesAndUnicodes():
        glyph, error = getGlyphWithError(glyphSet, glyphName)
        if error:
            continue
        for compo in glyph.components:
            usedComponentGlyphs.add(compo.name)
            coordAxisRanges = axisRanges.get(compo.name, {})
            # The following is very common and perhaps not worth a warning, as the behavior
            # is well defined:
            # for axisName in sorted(set(coordAxisRanges) - set(compo.coord)):
            #     yield f"Axis '{axisName}' not set by '{glyphName}' but is defined for '{compo.name}'"
            for axisName, axisValue in compo.coord.items():
                axisRange = coordAxisRanges.get(axisName)
                if axisRange is None:
                    yield f"Axis '{axisName}' set by '{glyphName}' but is not defined for '{compo.name}'"
                else:
                    minValue, maxValue = sorted(axisRange)
                    if not (minValue <= axisValue <= maxValue):
                        yield (
                            f"Axis value {axisValue} for '{axisName}' as used by '{glyphName}' for "
                            f"'{compo.name}' is not between {minValue} and {maxValue}"
                        )
                unusedAxes.get(compo.name, set()).discard(axisName)

    for glyphName, axisNames in unusedAxes.items():
        if glyphName not in usedComponentGlyphs:
            # this is reported separately, see _checkUnusedComponents
            continue
        for axisName in axisNames:
            yield f"Axis '{axisName}' of '{glyphName}' is not used"


@lintcheck("contour")
def checkContours(project):
    """Check for open contours, and contours that are made of less than three points."""
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
            self.hasOpenContours = True
        self.numPoints += 1

    def endPath(self):
        if self.numPoints <= 2:
            self.hasShortContours = True

    def addComponent(self, *args, **kwargs):
        pass


@lintcheck("advance")
def checkAdvance(project):
    """Check the advance width of character glyphs against the value of
    "robocjk.defaultGlyphWidth" in fontLib.json.
    Skip glyphs that have a name starting with "_".
    """
    defaultAdvanceWidth = project.lib.get("robocjk.defaultGlyphWidth")
    if defaultAdvanceWidth is None:
        yield f"robocjk.defaultGlyphWidth has not been set in *.rcjk/fontLib.json"
    else:
        glyphSet = project.characterGlyphGlyphSet
        for glyphName in project.keys():
            if glyphName.startswith("_"):
                continue
            glyph, error = getGlyphWithError(glyphSet, glyphName)
            if error:
                continue
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


def commaSeparatedList(arg):
    return set(arg.split(","))


def main():
    checkNames = ", ".join(checks)
    parser = argparse.ArgumentParser(
        description=f"Perform lint checks on one or more rcjk projects: {checkNames}"
    )
    parser.add_argument("rcjkproject", nargs="+")
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Print a full traceback when an exception occurs",
    )
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
    if args.verbose:
        global VERBOSE
        VERBOSE = True

    for projectPath in args.rcjkproject:
        project = RoboCJKProject(projectPath)
        for checkName, checkFunc in checks.items():
            if args.include and checkName not in args.include:
                continue
            if checkName in args.exclude:
                continue
            try:
                for msg in checkFunc(project):
                    print(f"{projectPath}:{checkName}: {msg}")
            except Exception as e:
                print(f"{projectPath}:{checkName}: ERROR {e!r}")
                if args.verbose:
                    traceback.print_exc()


if __name__ == "__main__":
    main()

import argparse
import logging
import os
import re
import traceback
from fontTools.pens.recordingPen import RecordingPointPen
from .project import RoboCJKProject
from .objects import InterpolationError
from .utils import tuplifyLocation


VERBOSE = False  # can be overridden by command line


checks = {}


def lintcheck(checkName):
    def wrap(checkFunc):
        assert checkName not in checks, f"Check '{checkName}' already exists"
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


def iterGlyphs(project, onlyGlyphSetName=None):
    for glyphSetName, glyphSet in iterGlyphSets(project):
        if onlyGlyphSetName is not None and onlyGlyphSetName != glyphSetName:
            continue
        for glyphName in sorted(glyphSet.getGlyphNamesAndUnicodes()):
            glyph, _ = getGlyphWithError(glyphSet, glyphName)
            if glyph is not None:
                yield glyphSetName, glyphSet, glyphName, glyph
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
        for glyphName in sorted(glyphSet.getGlyphNamesAndUnicodes()):
            m = glyphNamePat.match(glyphName)
            if m is None:
                yield f"invalid glyph name '{glyphName}' (in {glyphSetName})"


@lintcheck("load_glyph")
def checkLoadGlyph(project):
    """Check whether a glyph can be successfully loaded."""
    for glyphSetName, glyphSet in iterGlyphSets(project):
        for glyphName in sorted(glyphSet.getGlyphNamesAndUnicodes()):
            _, error = getGlyphWithError(glyphSet, glyphName)
            if error is not None:
                yield error


@lintcheck("interpolate")
def checkInterpolation(project):
    """Check whether a variable glyph can interpolate."""
    for glyphSetName, glyphSet, glyphName, glyph in iterGlyphs(project):
        try:
            for varGlyph in glyph.variations:
                _ = glyph + varGlyph
        except InterpolationError as e:
            yield f"'{glyphName}' {e} (in {glyphSetName})"


@lintcheck("layer")
def checkGlyphExistsInLayer(project):
    """Check whether a glyph in a layer exists, if the parent glyph specifies a
    layerName for a variation.
    """
    for glyphSetName, glyphSet, glyphName, glyph in iterGlyphs(project):
        for layerName in getattr(glyph, "glyphNotInLayer", ()):
            yield f"'{glyphName}' does not exist in layer '{layerName}'"


@lintcheck("layer_name")
def checkGlyphWithOutlineHasLayerNames(project):
    """For glyphs with outlines, check whether all variation glyphs have
    their 'layerName' set.
    """
    for glyphSetName, glyphSet, glyphName, glyph in iterGlyphs(project):
        if glyph.outline.isEmpty():
            continue
        for varDict in glyph.lib.get("robocjk.variationGlyphs"):
            if not varDict.get("layerName"):
                sourceName = varDict.get("sourceName", "")
                location = formatLocation(varDict.get("location"))
                yield (
                    f"Glyph '{glyphName}' has outline but does not set 'layerName' "
                    f"field; sourceName='{sourceName}', location={location}"
                )


@lintcheck("nested_variations")
def checkGlyphVariations(project):
    """Check whether variation glyphs have variations themselves."""
    for glyphSetName, glyphSet, glyphName, glyph in iterGlyphs(project):
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
                yield (
                    f"'{glyphName}' of layer '{layerName}' has no parent "
                    f"(in {glyphSetName})"
                )


@lintcheck("mix_outlines_components")
def checkGlyphMixOutlinesAndComponents(project):
    """Check whether a glyph uses a mix of outlines and components."""
    for glyphSetName, glyphSet, glyphName, glyph in iterGlyphs(project):
        if not glyph.outline.isEmpty() and glyph.components:
            yield f"'{glyphName}' mixes outlines and components (in {glyphSetName})"


hexAllCaps = re.compile("[0-9A-F]+$")


@lintcheck("uni_name")
def checkGlyphUnicodeName(project):
    """Check the validity of uniXXXX glyph names."""
    for glyphSetName, glyphSet, glyphName, glyph in iterGlyphs(project):
        if glyphName.startswith("uni"):
            base = glyphName.split(".")[0]
            if len(base[3:]) < 4:
                yield (
                    f"'{glyphName}' unicode value in glyph name should be at least 4 "
                    f"hex digits"
                )
            m = hexAllCaps.match(base[3:])
            if m is None:
                yield (
                    f"'{glyphName}' unicode value in glyph name must be uppercase "
                    f"hexadecimal"
                )


@lintcheck("uni_name_vs_unicodes")
def checkGlyphUnicodeNameVsUnicodes(project):
    """Check uniXXXX glyph names against glyph.unicodes."""
    for glyphSetName, glyphSet, glyphName, glyph in iterGlyphs(project):
        if glyphName.startswith("uni") and "." not in glyphName:
            uni = int(glyphName[3:], 16)
            if uni not in glyph.unicodes:
                unis = ",".join(f"U+{u:04X}" for u in glyph.unicodes)
                yield (
                    f"'{glyphName}' unicode in glyph name does not occur in "
                    f"glyph.unicodes ({unis})"
                )


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
    for glyphName in sorted(glyphSet.getGlyphNamesAndUnicodes()):
        glyph, error = getGlyphWithError(glyphSet, glyphName)
        if error:
            continue
        for compo in glyph.components:
            usedComponentGlyphs.add(compo.name)
            coordAxisRanges = axisRanges.get(compo.name, {})
            # The following is very common and perhaps not worth a warning, as the
            # behavior is well defined:
            # for axisName in sorted(set(coordAxisRanges) - set(compo.coord)):
            #     yield (
            #         f"Axis '{axisName}' not set by '{glyphName}' but is defined for "
            #         f"'{compo.name}'"
            #     )
            for axisName, axisValue in compo.coord.items():
                axisRange = coordAxisRanges.get(axisName)
                if axisRange is None:
                    yield (
                        f"Axis '{axisName}' set by '{glyphName}' but is not defined "
                        f"for '{compo.name}'"
                    )
                else:
                    minValue, maxValue = sorted(axisRange)
                    if not (minValue <= axisValue <= maxValue):
                        yield (
                            f"Axis value {axisValue} for '{axisName}' as used by "
                            f"'{glyphName}' for '{compo.name}' is not between "
                            f"{minValue} and {maxValue}"
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
    for glyphSetName, glyphSet, glyphName, glyph in iterGlyphs(project):
        pen = ContourCheckerPointPen()
        glyph.drawPoints(pen)
        if pen.hasOpenContours:
            yield f"'{glyphName} has one or more open contours (in {glyphSetName})"
        if pen.hasShortContours:
            yield (
                f"'{glyphName} has one or more contours that have fewer than three "
                f"points (in {glyphSetName})"
            )


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
        yield "robocjk.defaultGlyphWidth has not been set in *.rcjk/fontLib.json"
    else:
        glyphSet = project.characterGlyphGlyphSet
        for glyphName in sorted(project.keys()):
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


@lintcheck("alt_glyph")
def checkGlyphAlternates(project):
    """Check whether alternate glyphs have base glyphs, and whether they are
    different from the base glyph.
    """
    glyphSet = project.characterGlyphGlyphSet
    for glyphName in sorted(glyphSet.getGlyphNamesAndUnicodes()):
        glyph = glyphSet.getGlyph(glyphName)
        if "." not in glyphName or glyphName.startswith("_"):
            continue
        baseGlyphName, _ = glyphName.split(".", 1)
        if baseGlyphName not in glyphSet:
            yield f"Alternate glyph '{glyphName}' has no base glyph '{baseGlyphName}'"
            continue
        baseGlyph = glyphSet.getGlyph(baseGlyphName)
        locations = {tuplifyLocation(vg.location) for vg in glyph.variations}
        locations &= {tuplifyLocation(vg.location) for vg in baseGlyph.variations}
        locations = [dict(loc) for loc in sorted(locations)]
        locations.insert(0, {})
        for loc in locations:
            rpen1 = RecordingPointPen()
            rpen2 = RecordingPointPen()
            project.drawPointsCharacterGlyph(glyphName, loc, rpen1)
            project.drawPointsCharacterGlyph(baseGlyphName, loc, rpen2)
            if rpen1.value == rpen2.value:
                yield (
                    f"Glyph '{glyphName}' is identical to '{baseGlyphName}' at "
                    f"location {formatLocation(loc)}"
                )


def formatLocation(location):
    if not location:
        return "<default>"
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
    parser.add_argument(
        "--custom-checks",
        type=existingPythonSource,
        action="append",
        default=[],
        help="A custom Python file containing custom lint checks",
    )
    args = parser.parse_args()

    logging.basicConfig(level=logging.ERROR)
    if args.verbose:
        global VERBOSE
        VERBOSE = True

    for customChecksSource in args.custom_checks:
        execFile(customChecksSource)

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


def existingPythonSource(path):
    if not os.path.isfile(path):
        raise argparse.ArgumentTypeError(f"not an existing file: '{path}'")
    if os.path.splitext(path)[1].lower() != ".py":
        raise argparse.ArgumentTypeError(f"not a Python source file: '{path}'")
    return path


def execFile(path):
    with open(path) as f:
        code = compile(f.read(), path, "exec")
        exec(code, {})


if __name__ == "__main__":
    main()

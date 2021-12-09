from collections import defaultdict
from itertools import groupby
import re
import traceback
import unicodedata
from fontTools.pens.recordingPen import RecordingPointPen
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
                    minValue, defaultValue, maxValue = axisRange
                    assert minValue <= defaultValue <= maxValue
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


def _getDefaultAdvanceWidth(defaultAdvanceWidths, varLoc):
    for defaultLoc, value in defaultAdvanceWidths:
        for axisName, axisValue in defaultLoc.items():
            if varLoc.get(axisName) != axisValue:
                break
        else:
            return value
    return None


def _getDefaultAdvanceWidths(projectLib):
    defaultAdvanceWidths = projectLib.get("robocjk.defaultGlyphWidths")
    if defaultAdvanceWidths is None:
        defaultAdvanceWidth = projectLib.get("robocjk.defaultGlyphWidth")
        if defaultAdvanceWidth is not None:
            defaultAdvanceWidths = [[{}, defaultAdvanceWidth]]
    else:
        # Sort defaultAdvanceWidths so the more specific locations come first
        defaultAdvanceWidths.sort(lambda key: len(key[0]), reverse=True)
        if all(defaultLoc for defaultLoc, value in defaultAdvanceWidths):
            # No {} default location found, let's try to find the defaultest
            # default (a location with all values at 0), and append that to
            # the list. This does not take the axis default value into account
            # so is a rather poor fallback. But is good enough for for GS CJK
            # wght and opsz.
            defaultDefault = None
            for defaultLoc, value in defaultAdvanceWidths:
                if all(v == 0 for v in defaultLoc.values()):
                    defaultDefault = value
                    break
            assert defaultDefault is not None, "can't find defaultAdvanceWidth"
            defaultAdvanceWidths.append([{}, defaultDefault])
    return defaultAdvanceWidths


@lintcheck("advance")
def checkAdvance(project):
    """Check the advance width of character glyphs against the value of
    "robocjk.defaultGlyphWidth" in fontLib.json.
    Skip glyphs that have a name starting with "_".
    """
    defaultAdvanceWidths = _getDefaultAdvanceWidths(project.lib)
    if defaultAdvanceWidths is None:
        yield "robocjk.defaultGlyphWidth has not been set in *.rcjk/fontLib.json"
    else:
        glyphSet = project.characterGlyphGlyphSet
        revMap = project.characterGlyphGlyphSet.getGlyphNamesAndUnicodes()
        for glyphName in sorted(project.keys()):
            if glyphName.startswith("_"):
                continue
            glyph, error = getGlyphWithError(glyphSet, glyphName)
            if error:
                continue
            unicodes = revMap[glyphName]
            if not unicodes:
                if glyphName.startswith("uni"):
                    uni = int(glyphName[3:].split(".")[0], 16)
                else:
                    continue
            else:
                uni = unicodes[0]
            eastAsianWidth = unicodedata.east_asian_width(chr(uni))
            for g in [glyph] + glyph.variations:
                defaultAdvanceWidth = _getDefaultAdvanceWidth(
                    defaultAdvanceWidths, g.location
                )
                assert defaultAdvanceWidth is not None, (glyphName, g.location)
                if eastAsianWidth == "H":
                    targetWidth = defaultAdvanceWidth / 2
                elif eastAsianWidth in {"W", "F"}:
                    targetWidth = defaultAdvanceWidth
                else:
                    category = unicodedata.category(chr(uni))
                    if category in {"Mn"}:
                        # Non-spacing marks
                        targetWidth = 0
                    else:
                        targetWidth = None  # advance must be greater than 0

                if (targetWidth is None and g.width <= 10) or (
                    targetWidth is not None and g.width != targetWidth
                ):
                    if not g.location:
                        locStr = ""
                    else:
                        locStr = f"at {formatLocation(g.location)} "
                    yield (
                        f"'{glyphName}' {locStr}does not have the expected advance "
                        f"width, {g.width} instead of "
                        f"{'greater than 0' if targetWidth is None else targetWidth}"
                    )


@lintcheck("alt_glyph")
def checkGlyphAlternates(project):
    """Check whether alternate glyphs have base glyphs, and whether they are
    different from the base glyph.
    """
    glyphSet = project.characterGlyphGlyphSet
    glyphNames = sorted(glyphSet.getGlyphNamesAndUnicodes())
    for baseName, altNames in groupby(glyphNames, key=lambda gn: gn.split(".")[0]):
        altNames = list(altNames)
        if len(altNames) == 1:
            continue
        glyphs = [glyphSet.getGlyph(glyphName) for glyphName in altNames]
        locations = set()
        for g in glyphs:
            locations.update(tuplifyLocation(vg.location) for vg in g.variations)
        locations = [dict(loc) for loc in sorted(locations)]
        locations.insert(0, {})

        for loc in locations:
            outlines = defaultdict(list)
            for g in glyphs:
                rpen = RecordingPointPen()
                project.drawPointsCharacterGlyph(g.name, loc, rpen)
                outlines[tuplifyOutline(rpen.value)].append(g.name)
            for sameNames in outlines.values():
                if len(sameNames) > 1:
                    sameNames = [f"'{n}'" for n in sameNames]
                    sameNames = ", ".join(sameNames[:-1]) + " and " + sameNames[-1]
                    yield (
                        f"Glyphs {sameNames} are identical "
                        f"at location {formatLocation(loc)}"
                    )


def tuplifyOutline(valueList):
    return tuple(
        (m, args, tuple(sorted(kwargs.items()))) for m, args, kwargs in valueList
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

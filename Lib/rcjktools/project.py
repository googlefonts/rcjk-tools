import logging
import re
import pathlib

from fontTools.misc.fixedTools import otRound
from fontTools.pens.roundingPen import RoundingPointPen
from fontTools.ufoLib.filenames import userNameToFileName
from fontTools.varLib.models import VariationModel
from ufo2ft.filters import UFO2FT_FILTERS_KEY
from ufoLib2.objects import Font as UFont, Glyph as UGlyph

from .objects import Component, Glyph, InterpolationError, MathDict, normalizeLocation
from .utils import convertOffsetFromRCenterToTCenter, makeTransform


class RoboCJKProject:

    def __init__(self, path, scaleUsesCenter=False):
        self._path = pathlib.Path(path)
        self.characterGlyphGlyphSet = GlyphSet(self._path / "characterGlyph")
        self.deepComponentGlyphSet = GlyphSet(self._path / "deepComponent")
        self.atomicElementGlyphSet = GlyphSet(self._path / "atomicElement")

    def getGlyphNamesAndUnicodes(self):
        return self.characterGlyphGlyphSet.getGlyphNamesAndUnicodes()

    def drawPointsCharacterGlyph(self, glyphName, location, pen):
        outline, dcItems, width = self.instantiateCharacterGlyph(glyphName, location)
        outline.drawPoints(pen)
        for dcName, atomicElements in dcItems:
            for aeName, atomicOutline in atomicElements:
                atomicOutline.drawPoints(pen)
        return width

    def instantiateCharacterGlyph(self, glyphName, location):
        glyph = self.characterGlyphGlyphSet.getGlyph(glyphName)
        glyph = glyph.instantiate(location)
        deepItems = []
        for component in glyph.components:
            deepItem = self.instantiateDeepComponent(
                component.name, component.coord,
                makeTransform(**component.transform),
            )
            deepItems.append((component.name, deepItem))
        return glyph.outline, deepItems, glyph.width

    def instantiateDeepComponent(self, glyphName, location, transform):
        glyph = self.deepComponentGlyphSet.getGlyph(glyphName)
        glyph = glyph.instantiate(location)
        atomicOutlines = []
        for component in glyph.components:
            t = transform.transform(makeTransform(**component.transform))
            atomicOutline = self.instantiateAtomicElement(
                component.name, component.coord, t,
            )
            atomicOutlines.append((component.name, atomicOutline))
        return atomicOutlines

    def instantiateAtomicElement(self, glyphName, location, transform):
        glyph = self.atomicElementGlyphSet.getGlyph(glyphName)
        glyph = glyph.instantiate(location)
        return glyph.outline.transform(transform)

    def saveFlattenedUFO(self, ufoPath, location, familyName, styleName, numDecimalsRounding=0):
        ufo = setupFont(familyName, styleName)
        self.addFlattenedGlyphsToUFO(ufo, location, numDecimalsRounding)
        ufo.save(ufoPath, overwrite=True)

    def addFlattenedGlyphsToUFO(self, ufo, location, numDecimalsRounding=0):
        revCmap = self.getGlyphNamesAndUnicodes()
        glyphNames = filterGlyphNames(sorted(revCmap))
        for glyphName in glyphNames:
            glyph = UGlyph(glyphName)
            glyph.unicodes = revCmap[glyphName]
            if numDecimalsRounding == 1:
                roundFunc = roundFuncOneDecimal
            elif numDecimalsRounding != 0:
                assert 0, numDecimalsRounding
            else:
                roundFunc = otRound
            pen = RoundingPointPen(glyph.getPointPen(), roundFunc)
            try:
                width = self.drawPointsCharacterGlyph(glyphName, location, pen)
            except InterpolationError as e:
                print(f"glyph {glyphName} can't be interpolated ({e})")
            else:
                glyph.width = max(0, width)  # can't be negative
                ufo[glyphName] = glyph

    def saveVarCoUFO(self, ufoPath, familyName, styleName):
        """Save a UFO with Variable Components glyph.lib extensions."""
        # NOTE: this has quite a few GS-CJK assumptions that may or may
        # not be fair for RoboCJK projects in general.
        globalAxes = [
            dict(name="Weight", tag="wght", minimum=300, default=300, maximum=600),
        ]
        globalAxisNames = set(axis["tag"] for axis in globalAxes)

        ufo = setupFont(familyName, styleName)
        ufo.lib[UFO2FT_FILTERS_KEY] = [
            dict(
                namespace="rcjktools",
                name="AddBaseGlyphs",
                pre=False,
            ),
        ]

        revCmap = self.characterGlyphGlyphSet.getGlyphNamesAndUnicodes()
        characterGlyphNames = []
        for glyphName in filterGlyphNames(sorted(revCmap)):
            glyph = self.characterGlyphGlyphSet.getGlyph(glyphName)
            try:
                glyph.instantiate({"wght": 0.5})
            except InterpolationError as e:
                print(f"glyph {glyphName} can't be interpolated ({e})")
            else:
                characterGlyphNames.append(glyphName)

        dcNames = getComponentNames(self.characterGlyphGlyphSet, characterGlyphNames)
        # check whether all DC glyphnames start with "DC_"
        ensureDCGlyphNames(dcNames)
        aeNames = getComponentNames(self.deepComponentGlyphSet, sorted(dcNames))
        # rename all AE glyph names so they start with "AE_"
        aeRenameTable = makeAERenameTable(aeNames)

        for glyphName in characterGlyphNames:
            addRCJKGlyphToVarCoUFO(
                ufo,
                self.characterGlyphGlyphSet,
                glyphName,
                glyphName,
                revCmap[glyphName],
                {},
                self.deepComponentGlyphSet,
                globalAxisNames,
            )

        for glyphName in dcNames:
            addRCJKGlyphToVarCoUFO(
                ufo,
                self.deepComponentGlyphSet,
                glyphName,
                glyphName,
                (),
                aeRenameTable,
                self.atomicElementGlyphSet,
                None,
            )

        for glyphName in aeNames:
            addRCJKGlyphToVarCoUFO(
                ufo,
                self.atomicElementGlyphSet,
                glyphName,
                aeRenameTable[glyphName],
                (),
                {},
                None,
                None,
            )

        doc = self.buildDesignSpaceDocument(ufo, ufoPath, globalAxes, globalAxisNames)

        ufoPath = pathlib.Path(ufoPath)
        designspacePath = ufoPath.parent / (ufoPath.stem + ".designspace")
        doc.write(designspacePath)
        ufo.save(ufoPath, overwrite=True)

    def buildDesignSpaceDocument(self, ufo, ufoPath, globalAxes, globalAxisNames):
        from fontTools.designspaceLib import DesignSpaceDocument

        globalAxisMapping = {
            axis["tag"]: (axis["name"], axis["minimum"], axis["maximum"])
            for axis in globalAxes
        }

        doc = DesignSpaceDocument()

        localAxes = set()
        for layerName in ufo.layers.keys():
            if layerName == "public.default":
                location = {}
                layerName = None
            else:
                location = parseLayerName(layerName)
            for axisName, axisValue in location.items():
                assert axisValue == 1  # for now, we don't support intermediates
                if axisName not in globalAxisNames:
                    localAxes.add(axisName)

            unnormalizedLocation = {}
            for axisName, axisValue in location.items():
                if axisName in globalAxisMapping:
                    axisName, minimum, maximum = globalAxisMapping[axisName]
                    axisValue = minimum + (maximum - minimum) * axisValue
                unnormalizedLocation[axisName] = axisValue

            doc.addSourceDescriptor(path=ufoPath, layerName=layerName, location=unnormalizedLocation)

        for axisDict in globalAxes:
            doc.addAxisDescriptor(**axisDict)

        for axisName in sorted(localAxes):
            assert axisName.startswith("vcaxis")
            assert len(axisName) == 9
            doc.addAxisDescriptor(
                name=axisName,
                tag="V" + axisName[-3:],
                minimum=0, default=0, maximum=1,
                hidden=True,
            )
        return doc


def roundFuncOneDecimal(value):
    """When exporting flat UFOs, keep a limited amount of fractional digits."""
    value = round(value, 1)
    i = int(value)
    if i == value:
        return i
    else:
        return value


def getComponentNames(glyphSet, glyphNames):
    componentNames = set()
    for glyphName in glyphNames:
        glyph = glyphSet.getGlyph(glyphName)
        for dc in glyph.components:
            componentNames.add(dc.name)
    return componentNames


def makeAERenameTable(glyphNames):
    renameTable = {glyphName: glyphName for glyphName in glyphNames}
    for glyphName in glyphNames:
        if not glyphName.startswith("AE_"):
            renameTable[glyphName] = "AE_" + glyphName
    return renameTable


def ensureDCGlyphNames(glyphNames):
    for glyphName in glyphNames:
        assert glyphName.startswith("DC_"), glyphName


def addRCJKGlyphToVarCoUFO(
        ufo,
        rcjkGlyphSet,
        srcGlyphName,
        dstGlyphName,
        unicodes,
        renameTable,
        componentSourceGlyphSet,
        globalAxisNames):

    if renameTable is None:
        renameTable = {}
    rcjkGlyph = rcjkGlyphSet.getGlyph(srcGlyphName)

    glyph = UGlyph(dstGlyphName)
    glyph.unicodes = unicodes
    glyph.width = max(0, rcjkGlyph.width)  # width can't be negative
    rcjkGlyphToVarCoGlyph(rcjkGlyph, glyph, renameTable, componentSourceGlyphSet)

    axisNames = list(rcjkGlyph.axes.keys())
    glyph.lib["varco.axisnames"] = axisNames
    axisIndices = {axisName: axisIndex for axisIndex, axisName in enumerate(axisNames)}

    variationInfo = []

    for varIndex, rcjkVarGlyph in enumerate(rcjkGlyph.variations):
        if globalAxisNames is not None:
            layerName = layerNameFromGlobalLocation(rcjkVarGlyph.location, globalAxisNames)
        else:
            layerName = layerNameFromLocalLocation(rcjkVarGlyph.location, axisIndices)
        layer = getUFOLayer(ufo, layerName)
        varGlyph = UGlyph(dstGlyphName)
        varGlyph.width = max(0, rcjkVarGlyph.width)  # width can't be negative
        rcjkGlyphToVarCoGlyph(rcjkVarGlyph, varGlyph, renameTable, componentSourceGlyphSet)
        variationInfo.append(dict(layerName=layerName, location=rcjkVarGlyph.location))
        layer[dstGlyphName] = varGlyph

    if variationInfo:
        glyph.lib["varco.variations"] = variationInfo

    ufo[dstGlyphName] = glyph


def rcjkGlyphToVarCoGlyph(rcjkGlyph, glyph, renameTable, componentSourceGlyphSet):
    pen = glyph.getPointPen()
    rcjkGlyph.drawPoints(pen)
    compoVarInfo = []
    for compo in rcjkGlyph.components:
        # (x, y, rotation, scalex, scaley, rcenterx, rcentery)
        transform = compo.transform
        x, y = convertOffsetFromRCenterToTCenter(**transform)
        pen.addComponent(renameTable.get(compo.name, compo.name), (1, 0, 0, 1, x, y))
        # the transformation center goes into varco data
        varCoTransform = dict(
            # TODO: We could skip values that are default (0, or 1 for scale values)
            rotation=transform["rotation"],
            scalex=transform["scalex"],
            scaley=transform["scaley"],
            tcenterx=transform["rcenterx"],
            tcentery=transform["rcentery"],
        )
        baseGlyph = componentSourceGlyphSet.getGlyph(compo.name)
        info = dict(
            coord=normalizeLocation(compo.coord, baseGlyph.axes),
            transform=varCoTransform,
        )
        compoVarInfo.append(info)
    if compoVarInfo:
        glyph.lib["varco.components"] = compoVarInfo


def setupFont(familyName, styleName):
    ufo = UFont()
    ufo.info.familyName = familyName
    ufo.info.styleName = styleName
    ufo.info.unitsPerEm = 1000
    ufo.info.descender = -120
    ufo.info.ascender = ufo.info.unitsPerEm + ufo.info.descender
    return ufo


def filterGlyphNames(glyphNames):
    okGlyphNames = []
    for glyphName in glyphNames:
        try:
            glyphName.encode("ascii")
        except UnicodeEncodeError:
            print(f"WARNING glyph name {glyphName} is not ASCII, and can not be exported")
        else:
            okGlyphNames.append(glyphName)
    return okGlyphNames


def getUFOLayer(ufo, layerName):
    if layerName not in ufo.layers:
        layer = ufo.newLayer(layerName)
    else:
        layer = ufo.layers[layerName]
    return layer


def layerNameFromGlobalLocation(location, axisNames):
    nameParts = []
    for axisName, axisValue in sorted(location.items()):
        assert axisName in axisNames
        if isinstance(axisValue, float) and axisValue.is_integer():
            axisValue = int(axisValue)
        nameParts.append(f"{axisName}={axisValue}")
    return "+".join(nameParts)


def layerNameFromLocalLocation(location, axisIndices):
    loc = {axisIndices[axisName]: axisValue for axisName, axisValue in location.items()}
    nameParts = []
    for axisIndex, axisValue in sorted(loc.items()):
        if isinstance(axisValue, float) and axisValue.is_integer():
            axisValue = int(axisValue)
        nameParts.append(f"vcaxis{axisIndex:03}={axisValue}")
    return "+".join(nameParts)


def parseLayerName(layerName):
    location = {}
    for part in layerName.split("+"):
        axisName, axisValue = part.split("=")
        axisValue = float(axisValue)
        location[axisName] = axisValue
    return location


_glyphNamePat = re.compile(rb'<glyph\s+name\s*=\s*"([^"]+)"')
_unicodePat = re.compile(rb'<unicode\s+hex\s*=\s*"([^"]+)"')


class GlyphSet:

    def __init__(self, path):
        self._path = path
        self._glyphs = {}
        self._layers = {}
        self._revCmap = None

    def getGlyphNamesAndUnicodes(self):
        if self._revCmap is None:
            glyphNames = {}
            for path in self._path.glob("*.glif"):
                with open(path, "rb") as f:
                    # assuming all unicodes are in the first 1024 bytes of the file
                    data = f.read(1024)
                m = _glyphNamePat.search(data)
                if m is None:
                    raise ValueError(
                        f"invalid .glif file, glyph name not found ({path})"
                    )
                glyphName = m.group(1).decode("utf-8")
                refFileName = userNameToFileName(glyphName, suffix=".glif")
                if refFileName != path.name:
                    logging.warning(
                        f"actual file name does not match predicted file name: "
                        f"{refFileName} {path.name} {glyphName}"
                    )
                unicodes = [int(u, 16) for u in _unicodePat.findall(data)]
                glyphNames[glyphName] = unicodes
            self._revCmap = glyphNames
        return self._revCmap

    def __contains__(self, glyphName):
        if glyphName in self._glyphs:
            return True
        fileName = userNameToFileName(glyphName, suffix=".glif")
        glyphPath = self._path / fileName
        return glyphPath.exists()

    def getGlyph(self, glyphName):
        glyph = self._glyphs.get(glyphName)
        if glyph is None:
            fileName = userNameToFileName(glyphName, suffix=".glif")
            glyph = RCJKGlyph.loadFromGLIF(self._path / fileName)
            glyph._postParse(self)
            self._glyphs[glyphName] = glyph
        return glyph

    def getLayer(self, layerName):
        layer = self._layers.get(layerName)
        if layer is None:
            layer = GlyphSet(self._path / layerName)
            self._layers[layerName] = layer
        return layer


class RCJKGlyph(Glyph):

    def _postParse(self, glyphSet):
        """This gets called soon after parsing the .glif file. Any layer glyphs
        and variation info is unpacked here, and put into a subglyph, as part
        of the self.variations list.
        """
        dcNames = []
        for dc in self.lib.get("robocjk.deepComponents", []):
            dcNames.append(dc["name"])
            self.components.append(_unpackDeepComponent(dc))

        self.axes = {
            axisDict["name"]: (axisDict["minValue"], axisDict["maxValue"])
            for axisDict in self.lib.get("robocjk.axes", [])
        }

        variationGlyphs = self.lib.get("robocjk.variationGlyphs")
        if variationGlyphs is None:
            return

        for varDict in variationGlyphs:
            layerName = varDict["layerName"]
            if not self.outline.isEmpty() and layerName:
                layer = glyphSet.getLayer(layerName)
                if self.name in layer:
                    varGlyph = layer.getGlyph(self.name)
                else:
                    # Layer glyph does not exist, make one up by copying
                    # self.width and self.outline
                    varGlyph = self.__class__()
                    varGlyph.width = self.width
                    varGlyph.outline = self.outline
            else:
                varGlyph = self.__class__()
                varGlyph.width = self.width

            varGlyph.location = varDict["location"]

            deepComponents = varDict["deepComponents"]
            assert len(dcNames) == len(deepComponents)
            for dc, dcName in zip(deepComponents, dcNames):
                varGlyph.components.append(_unpackDeepComponent(dc, dcName))
            assert len(varGlyph.components) == len(self.components)

            self.variations.append(varGlyph)

        locations = [{}] + [
            normalizeLocation(variation.location, self.axes)
            for variation in self.variations
        ]
        self.model = VariationModel(locations)


def _unpackDeepComponent(dc, name=None):
    if name is None:
        # "name" is defined in neutral components, but is implied in variations
        name = dc["name"]
    coord = dc["coord"]
    transform = dc["transform"]
    return Component(name, MathDict(coord), MathDict(transform))


if __name__ == "__main__":
    # DrawBot test snippet
    from drawBot import BezierPath, translate, scale, fill, stroke, drawPath

    def drawOutline(outline):
        bez = BezierPath()
        outline.drawPoints(bez)
        drawPath(bez)

    testPath = "/Users/just/code/git/BlackFoundry/gs-cjk-rcjk/Hanzi.rcjk/"
    project = RoboCJKProject(testPath)

    glyphName = "uni3A00"
    # glyphName = "uni2EBB"
    # glyphName = "uni3A10"
    # glyphName = "uni3A69"  # outlines
    glyphName = "uni540E"  # mix outlines / components

    translate(100, 200)
    scale(0.8)
    fill(0, 0.02)
    stroke(0)
    steps = 3
    for i in range(steps):
        f = i / (steps - 1)
        outline, deepItems, width = project.instantiateCharacterGlyph(glyphName, {"wght": f})
        if outline is not None:
            drawOutline(outline)
        for dcName, atomicOutlines in deepItems:
            for atomicName, atomicOutline in atomicOutlines:
                drawOutline(atomicOutline)

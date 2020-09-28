import logging
import math
import re
import pathlib

from fontTools.misc.fixedTools import otRound
from fontTools.misc.transform import Transform
from fontTools.pens.roundingPen import RoundingPointPen
from fontTools.ufoLib.filenames import userNameToFileName
from fontTools.varLib.models import VariationModel
from ufoLib2.objects import Font as UFont, Glyph as UGlyph

from .objects import Component, Glyph, InterpolationError, MathDict


class RoboCJKProject:

    def __init__(self, path, scaleUsesCenter=False):
        self._path = pathlib.Path(path)
        self.characterGlyphGlyphSet = GlyphSet(self._path / "characterGlyph", scaleUsesCenter=scaleUsesCenter)
        self.deepComponentGlyphSet = GlyphSet(self._path / "deepComponent", scaleUsesCenter=scaleUsesCenter)
        self.atomicElementGlyphSet = GlyphSet(self._path / "atomicElement", scaleUsesCenter=scaleUsesCenter)
        self._scaleUsesCenter = scaleUsesCenter

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
                makeTransform(**component.transform, scaleUsesCenter=self._scaleUsesCenter),
            )
            deepItems.append((component.name, deepItem))
        return glyph.outline, deepItems, glyph.width

    def instantiateDeepComponent(self, glyphName, location, transform):
        glyph = self.deepComponentGlyphSet.getGlyph(glyphName)
        glyph = glyph.instantiate(location)
        atomicOutlines = []
        for component in glyph.components:
            t = transform.transform(makeTransform(**component.transform, scaleUsesCenter=self._scaleUsesCenter))
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
        ufo = setupFont(familyName, styleName)

        revCmap = self.characterGlyphGlyphSet.getGlyphNamesAndUnicodes()
        characterGlyphNames = filterGlyphNames(sorted(revCmap))

        dcNames = getComponentNames(self.characterGlyphGlyphSet, characterGlyphNames)
        # check whether all DC glyphnames start with "DC_"
        ensureDCGlyphNames(dcNames)
        aeNames = getComponentNames(self.deepComponentGlyphSet, sorted(dcNames))
        # rename all AE glyph names so they start with "AE_"
        aeRenameTable = makeAERenameTable(aeNames)

        for glyphName in characterGlyphNames:
            addRCJKGlyphToVarCoUFO(ufo, self.characterGlyphGlyphSet, glyphName, glyphName, revCmap[glyphName])

        for glyphName in dcNames:
            addRCJKGlyphToVarCoUFO(ufo, self.deepComponentGlyphSet, glyphName, glyphName, (), aeRenameTable)

        for glyphName in aeNames:
            addRCJKGlyphToVarCoUFO(ufo, self.atomicElementGlyphSet, glyphName, aeRenameTable[glyphName], ())

        ufo.save(ufoPath, overwrite=True)


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


def addRCJKGlyphToVarCoUFO(ufo, rcjkGlyphSet, srcGlyphName, dstGlyphName, unicodes, renameTable=None):
    if renameTable is None:
        renameTable = {}
    rcjkGlyph = rcjkGlyphSet.getGlyph(srcGlyphName)

    glyph = UGlyph(dstGlyphName)
    glyph.unicodes = unicodes
    glyph.width = rcjkGlyph.width
    rcjkGlyphToVarCoGlyph(rcjkGlyph, glyph, renameTable)

    packedAxes = packAxes(rcjkGlyph.axes)
    if packedAxes:
        glyph.lib["varco.axes"] = packedAxes

    variationInfo = []

    for varIndex, rcjkVarGlyph in enumerate(rcjkGlyph.variations):
        layerName = f"varco_{varIndex:03}"
        layer = getUFOLayer(ufo, layerName)
        varGlyph = UGlyph(dstGlyphName)
        varGlyph.width = rcjkVarGlyph.width
        rcjkGlyphToVarCoGlyph(rcjkVarGlyph, varGlyph, renameTable)
        variationInfo.append(dict(layerName=layerName, location=rcjkVarGlyph.location))
        layer[dstGlyphName] = varGlyph

    if variationInfo:
        glyph.lib["varco.variations"] = variationInfo

    ufo[dstGlyphName] = glyph


def rcjkGlyphToVarCoGlyph(rcjkGlyph, glyph, renameTable):
    pen = glyph.getPointPen()
    rcjkGlyph.drawPoints(pen)
    compoVarInfo = []
    for compo in rcjkGlyph.components:
        # (x, y, rotation, scalex, scaley, rcenterx, rcentery)
        transform = compo.transform
        xx, xy, yx, yy, _, _ = makeTransform(**transform)
        x, y = convertOffsetFromRCenterToTCenter(**transform)
        t = (xx, xy, yx, yy, x, y)
        pen.addComponent(renameTable.get(compo.name, compo.name), t)
        # the transformation center goes into varco data
        varCoTransform = dict(
            tcenterx=transform["rcenterx"],
            tcentery=transform["rcentery"],
        )
        compoVarInfo.append(dict(coord=compo.coord, transform=varCoTransform))
    if compoVarInfo:
        glyph.lib["varco.components"] = compoVarInfo


def packAxes(axes):
    return [dict(name=axisName, minValue=minValue, maxValue=maxValue)
            for axisName, (minValue, maxValue) in axes.items()]


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


def layerNameFromLocation(location):
    # TODO: unused, remove
    location = sorted(location.items())
    nameParts = []
    for name, value in location:
        if isinstance(value, float) and value.is_integer():
            value = int(value)
        nameParts.append(f"{name}={value}")
    return "+".join(nameParts)


_glyphNamePat = re.compile(rb'<glyph\s+name\s*=\s*"([^"]+)"')
_unicodePat = re.compile(rb'<unicode\s+hex\s*=\s*"([^"]+)"')


class GlyphSet:

    def __init__(self, path, scaleUsesCenter=False):
        self._path = path
        self._glyphs = {}
        self._layers = {}
        self._revCmap = None
        self._scaleUsesCenter = scaleUsesCenter

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
            glyph = RCJKGlyph.loadFromGLIF(self._path / fileName, scaleUsesCenter=self._scaleUsesCenter)
            glyph._postParse(self)
            self._glyphs[glyphName] = glyph
        return glyph

    def getLayer(self, layerName):
        layer = self._layers.get(layerName)
        if layer is None:
            layer = GlyphSet(self._path / layerName, scaleUsesCenter=self._scaleUsesCenter)
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
            self.components.append(_unpackDeepComponent(dc, scaleUsesCenter=self._scaleUsesCenter))

        varKey = _getVarKey(self.lib)
        if varKey is None:
            return

        for axisName, varDict in self.lib[varKey].items():
            layerName = varDict["layerName"]
            minValue = varDict["minValue"]
            maxValue = varDict["maxValue"]
            if not self.outline.isEmpty() and layerName:
                layer = glyphSet.getLayer(layerName)
                if self.name in layer:
                    varGlyph = layer.getGlyph(self.name)
                else:
                    # Layer glyph does not exist, make one up by copying
                    # self.width and self.outline
                    varGlyph = self.__class__(scaleUsesCenter=self._scaleUsesCenter)
                    varGlyph.width = self.width
                    varGlyph.outline = self.outline
            else:
                varGlyph = self.__class__(scaleUsesCenter=self._scaleUsesCenter)
                varGlyph.width = self.width

            varGlyph.location = {axisName: 1.0}
            self.axes[axisName] = (minValue, maxValue)

            deepComponents = varDict["content"]["deepComponents"]
            assert len(dcNames) == len(deepComponents)
            for dc, dcName in zip(deepComponents, dcNames):
                varGlyph.components.append(_unpackDeepComponent(dc, dcName, scaleUsesCenter=self._scaleUsesCenter))
            assert len(varGlyph.components) == len(self.components)

            self.variations.append(varGlyph)

        locations = [{}] + [variation.location for variation in self.variations]
        self.model = VariationModel(locations)


def makeTransform(x, y, rotation, scalex, scaley, rcenterx, rcentery, scaleUsesCenter=False):
    rotation = math.radians(rotation)
    if not scaleUsesCenter:
        rcenterx *= scalex
        rcentery *= scaley
        t = Transform()
        t = t.translate(x + rcenterx, y + rcentery)
        t = t.rotate(rotation)
        t = t.translate(-rcenterx, -rcentery)
        t = t.scale(scalex, scaley)
    else:
        t = Transform()
        t = t.translate(x + rcenterx, y + rcentery)
        t = t.rotate(rotation)
        t = t.scale(scalex, scaley)
        t = t.translate(-rcenterx, -rcentery)
    return t


_rcjkTransformParameters = {"x", "y", "rotation", "scalex", "scaley", "rcenterx", "rcentery"}


def _unpackDeepComponent(dc, name=None, scaleUsesCenter=False):
    if name is None:
        # "name" is defined in neutral components, but is implied in variations
        name = dc["name"]
    coord = dc["coord"]
    transform = {k: v for k, v in dc.items() if k in _rcjkTransformParameters}
    if scaleUsesCenter:
        # abscenterx, abscentery = (x + rcenterx * scalex, y + rcentery * scaley)
        # newx, newy = abscenterx - rcenterx, abscentery - rcentery
        transform["x"] = transform["x"] + (transform["scalex"] - 1) * transform["rcenterx"]
        transform["y"] = transform["y"] + (transform["scaley"] - 1) * transform["rcentery"]
    return Component(name, MathDict(coord), MathDict(transform))


def _getVarKey(lib):
    roboVarKeys = (
        "robocjk.fontVariationGlyphs",
        "robocjk.glyphVariationGlyphs",
    )
    for varKey in roboVarKeys:
        if varKey in lib:
            return varKey
    return None


def recenterTransform(x, y, rotation, scalex, scaley, rcenterx, rcentery, newrcenterx, newrcentery):
    """Take a set of transformation parameters, new values for rcenterx and rcentery, and it will
    return new values for x and y, so that

        t1 = makeTransform(x, y, rotation, scalex, scaley, rcenterx, rcentery)
        t2 = makeTransform(newx, newy, rotation, scalex, scaley, newrcenterx, newrcentery)

    return the same transformation (bar floating point rounding errors).
    """
    t = makeTransform(x, y, rotation, scalex, scaley, rcenterx, rcentery)
    tmp = makeTransform(x, y, rotation, scalex, scaley, newrcenterx, newrcentery)
    newx = x + t[4] - tmp[4]
    newy = y + t[5] - tmp[5]
    return newx, newy


def convertOffsetFromRCenterToTCenter(x, y, rotation, scalex, scaley, rcenterx, rcentery):
    """Take a set of transformation parameters that use a center only for rotation
    ("rcenter"), and return the new x, y offset for the equivalent transform that
    uses a center for rotation and scaling ("tcenter"), so that

        t1 = makeTransform(x, y, rotation, scalex, scaley, rcenterx, rcentery)
        t2 = makeTransform(newx, newy, rotation, scalex, scaley, rcenterx, rcentery, scaleUsesCenter=True)

    return the same transformation (bar floating point rounding errors).
    """
    t = makeTransform(x, y, rotation, scalex, scaley, rcenterx, rcentery)
    tmp = makeTransform(x, y, rotation, scalex, scaley, rcenterx, rcentery, scaleUsesCenter=True)
    newx = x + t[4] - tmp[4]
    newy = y + t[5] - tmp[5]
    return newx, newy


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

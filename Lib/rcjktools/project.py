import logging
import json
import math
import re
import pathlib

from fontTools.misc.fixedTools import otRound
from fontTools.pens.roundingPen import RoundingPointPen
from fontTools.pens.pointPen import PointToSegmentPen
from fontTools.ufoLib.filenames import userNameToFileName
from fontTools.varLib.models import VariationModel, normalizeLocation

try:
    from ufo2ft.constants import FILTERS_KEY
except ImportError:
    # older ufo2ft
    from ufo2ft.filters import UFO2FT_FILTERS_KEY as FILTERS_KEY
from ufoLib2.objects import Font as UFont, Glyph as UGlyph

from .objects import Component, Glyph, InterpolationError, MathDict, MathOutline
from .utils import decomposeTwoByTwo, makeTransform


logger = logging.getLogger(__name__)


class ComponentMismatchError(Exception):
    pass


class LocationOutOfBoundsError(Exception):
    pass


class GlyphNotFoundError(KeyError):
    pass


class RoboCJKProject:
    def __init__(self, path, decomposeClassicComponents=False):
        self._path = pathlib.Path(path).resolve()
        self._decomposeClassicComponents = decomposeClassicComponents
        assert self._path.is_dir(), f"No .rcjk project found: {path}"
        self._loadDesignSpace(self._path / "designspace.json")
        self._loadLib(self._path / "fontLib.json")

        self.characterGlyphGlyphSet = GlyphSet(self._path / "characterGlyph")
        self.deepComponentGlyphSet = GlyphSet(self._path / "deepComponent")
        self.atomicElementGlyphSet = GlyphSet(self._path / "atomicElement")

    def _loadDesignSpace(self, path):
        self.designspace = {}
        self.axes = {}
        self.axisNames = {}
        if path.exists():
            with open(path) as f:
                self.designspace = json.load(f)
            for axis in self.designspace["axes"]:
                self.axes[axis["tag"]] = (
                    axis["minValue"],
                    axis["defaultValue"],
                    axis["maxValue"],
                )
                self.axisNames[axis["tag"]] = axis["name"]

    def _loadLib(self, path):
        with open(path) as f:
            self.lib = json.load(f)

    @property
    def features(self):
        featuresPath = self._path / "features.fea"
        if featuresPath.exists():
            with open(featuresPath, encoding="utf-8") as f:
                return f.read()
        return None

    def keys(self):
        return self.characterGlyphGlyphSet.getGlyphNamesAndUnicodes().keys()

    def __contains__(self, glyphName):
        return glyphName in self.characterGlyphGlyphSet

    def drawGlyph(self, pen, glyphName, location):
        self.drawPointsCharacterGlyph(glyphName, location, PointToSegmentPen(pen))

    def getGlyphNamesAndUnicodes(self):
        return self.characterGlyphGlyphSet.getGlyphNamesAndUnicodes()

    def drawPointsCharacterGlyph(self, glyphName, location, pen):
        outline, dcItems, classicComponents, width = self.instantiateCharacterGlyph(
            glyphName, location
        )
        outline.drawPoints(pen)
        for dcName, atomicElements in dcItems:
            for aeName, atomicOutline in atomicElements:
                atomicOutline.drawPoints(pen)
        for baseGlyphName, transform in classicComponents:
            pen.addComponent(baseGlyphName, transform)
        return width

    def instantiateCharacterGlyph(self, glyphName, location):
        glyph = self.characterGlyphGlyphSet.getGlyph(glyphName)
        # glyph.ensureComponentCoords(self.deepComponentGlyphSet)
        componentScalesVary = checkComponentScaleVariation(glyph)
        glyph = glyph.instantiate(location)
        deepItems = []
        classicComponents = []
        for component in glyph.components:
            if component.name not in self.deepComponentGlyphSet:
                assert not component.coord, (glyphName, component.name, component.coord)
                transform = makeTransform(**component.transform)
                if self._decomposeClassicComponents or componentScalesVary:
                    compoOutline, cdc, ccc, cw = self.instantiateCharacterGlyph(
                        component.name, location
                    )
                    assert not ccc, ccc
                    compoOutline = compoOutline.transform(transform)
                    deepItems.append(
                        (component.name, [("<classic component>", compoOutline)])
                    )
                else:
                    classicComponents.append((component.name, transform))
            else:
                deepItem = self.instantiateDeepComponent(
                    component.name,
                    component.coord,
                    makeTransform(**component.transform),
                )
                deepItems.append((component.name, deepItem))
        return glyph.outline, deepItems, classicComponents, glyph.width

    def instantiateDeepComponent(self, glyphName, location, transform):
        glyph = self.deepComponentGlyphSet.getGlyph(glyphName)
        # glyph.ensureComponentCoords(self.atomicElementGlyphSet)
        glyph = glyph.instantiate(location)
        atomicOutlines = []
        for component in glyph.components:
            t = transform.transform(makeTransform(**component.transform))
            atomicOutline = self.instantiateAtomicElement(
                component.name, component.coord, t
            )
            atomicOutlines.append((component.name, atomicOutline))
        return atomicOutlines

    def instantiateAtomicElement(self, glyphName, location, transform):
        glyph = self.atomicElementGlyphSet.getGlyph(glyphName)
        glyph = glyph.instantiate(location)
        return glyph.outline.transform(transform)

    def saveFlattenedUFO(
        self,
        ufoPath,
        location,
        familyName,
        styleName,
        numDecimalsRounding=0,
        characterSet=None,
        glyphSet=None,
    ):
        ufo = setupFont(familyName, styleName)
        self.addFlattenedGlyphsToUFO(
            ufo, location, numDecimalsRounding, characterSet, glyphSet
        )
        ufo.save(ufoPath, overwrite=True)

    def addFlattenedGlyphsToUFO(
        self, ufo, location, numDecimalsRounding=0, characterSet=None, glyphSet=None
    ):
        if characterSet is not None and glyphSet is not None:
            raise TypeError("can't pass both characterSet and glyphSet")
        if numDecimalsRounding == 1:
            roundFunc = roundFuncOneDecimal
        elif numDecimalsRounding != 0:
            assert 0, numDecimalsRounding
        else:
            roundFunc = otRound
        revCmap = self.getGlyphNamesAndUnicodes()
        glyphNames = filterGlyphNames(sorted(revCmap))
        for glyphName in glyphNames:
            if glyphSet is not None:
                if glyphName not in glyphSet:
                    continue
            elif characterSet is not None:
                codePoints = set(revCmap[glyphName])
                if not codePoints & characterSet:
                    continue
            glyph = UGlyph(glyphName)
            glyph.unicodes = revCmap[glyphName]
            copyMarkColor(self.characterGlyphGlyphSet.getGlyph(glyphName), glyph)
            pen = RoundingPointPen(glyph.getPointPen(), roundFunc)
            try:
                width = self.drawPointsCharacterGlyph(glyphName, location, pen)
            except InterpolationError as e:
                logger.warning(f"glyph {glyphName} can't be interpolated ({e})")
            except Exception as e:
                logger.warning(f"glyph {glyphName} caused an error: {e!r}")
                raise
            else:
                glyph.width = max(0, width)  # can't be negative
                ufo[glyphName] = glyph

    def decomposeCharacterGlyph(self, glyphName):
        glyph = self.characterGlyphGlyphSet.getGlyph(glyphName)
        newOutlines = []  # Collect first, replace later
        for varGlyph in [glyph] + glyph.variations:
            outline = MathOutline()
            self.drawPointsCharacterGlyph(glyphName, varGlyph.location, outline)
            newOutlines.append(outline)

        for varGlyph, outline in zip([glyph] + glyph.variations, newOutlines):
            varGlyph.outline = outline
            varGlyph.components = []

    def saveVarCoUFO(self, ufoPath, familyName, styleName, characterSet=None):
        """Save a UFO with Variable Components glyph.lib extensions."""
        # NOTE: this has quite a few GS-CJK assumptions that may or may
        # not be fair for RoboCJK projects in general.

        ufo = setupFont(familyName, styleName)
        ufo.lib[FILTERS_KEY] = [
            dict(namespace="rcjktools", name="AddBaseGlyphs", pre=False)
        ]
        features = self.features
        if features:
            ufo.features.text = features

        self.addGlyphsToVarCoUFO(ufo, set(self.axes.keys()), characterSet)

        doc = buildVarCoDesignSpaceDocument(ufo, ufoPath, self.axes, self.axisNames)

        ufoPath = pathlib.Path(ufoPath)
        designspacePath = ufoPath.parent / (ufoPath.stem + ".designspace")
        doc.write(designspacePath)
        ufo.save(ufoPath, overwrite=True)

    def addGlyphsToVarCoUFO(
        self, ufo, globalAxisNames, characterSet=None, glyphSet=None
    ):
        if characterSet is not None and glyphSet is not None:
            raise TypeError("can't pass both characterSet and glyphSet")
        revCmap = self.characterGlyphGlyphSet.getGlyphNamesAndUnicodes()
        characterGlyphNames = []
        for glyphName in filterGlyphNames(sorted(revCmap)):
            if glyphSet is not None:
                if glyphName not in glyphSet:
                    continue
            elif characterSet is not None:
                codePoints = set(revCmap[glyphName])
                if not codePoints & characterSet:
                    continue
            try:
                glyph = self.characterGlyphGlyphSet.getGlyph(glyphName)
            except Exception:
                logger.error(f"An error occurred while processing {glyphName}")
                raise
            try:
                glyph.instantiate({"wght": 0.5})
            except InterpolationError as e:
                logger.warning(f"glyph {glyphName} can't be interpolated ({e})")
            else:
                characterGlyphNames.append(glyphName)
                if glyph.components and not glyph.outline.isEmpty():
                    logger.warning(
                        f"decomposing {glyphName}: it has both an outline and"
                        " components"
                    )
                    self.decomposeCharacterGlyph(glyphName)

        dcNames = getComponentNames(
            self.characterGlyphGlyphSet, characterGlyphNames, self.deepComponentGlyphSet
        )
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


def buildVarCoDesignSpaceDocument(ufo, ufoPath, axes, axisNames):
    from fontTools.designspaceLib import DesignSpaceDocument

    globalAxisNames = set(axes.keys())
    globalAxes = [
        dict(
            name=axisNames[axisTag],
            tag=axisTag,
            minimum=minValue,
            default=defaultValue,
            maximum=maxValue,
        )
        for axisTag, (minValue, defaultValue, maxValue) in axes.items()
    ]

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
            if axisName not in globalAxisNames:
                localAxes.add(axisName)

        unnormalizedLocation = {}
        for axisName, axisValue in location.items():
            if axisName in globalAxisMapping:
                axisName, minimum, maximum = globalAxisMapping[axisName]
                axisValue = minimum + (maximum - minimum) * axisValue
            unnormalizedLocation[axisName] = axisValue

        doc.addSourceDescriptor(
            path=ufoPath, layerName=layerName, location=unnormalizedLocation
        )

    for axisDict in globalAxes:
        doc.addAxisDescriptor(**axisDict)

    for axisName in sorted(localAxes):
        assert axisName.startswith("V")
        assert len(axisName) == 4
        doc.addAxisDescriptor(
            name=axisName, tag=axisName, minimum=-1, default=0, maximum=1, hidden=True
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


def getComponentNames(glyphSet, glyphNames, componentGlyphSet=None):
    componentNames = set()
    for glyphName in glyphNames:
        glyph = glyphSet.getGlyph(glyphName)
        for dc in glyph.components:
            if componentGlyphSet is None or dc.name in componentGlyphSet:
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
    globalAxisNames,
):
    if renameTable is None:
        renameTable = {}
    rcjkGlyph = rcjkGlyphSet.getGlyph(srcGlyphName)
    if rcjkGlyph.components and not rcjkGlyph.outline.isEmpty():
        logger.warning(f"glyph {srcGlyphName} has both outlines and components")

    glyph = UGlyph(dstGlyphName)
    glyph.unicodes = unicodes
    glyph.width = max(0, rcjkGlyph.width)  # width can't be negative
    rcjkGlyphToVarCoGlyph(rcjkGlyph, glyph, renameTable, componentSourceGlyphSet)

    if globalAxisNames is None:
        axisNameMapping = _makeAxisNameMapping(rcjkGlyph.axes)
        axisNames = set(axisNameMapping.values())
    else:
        axisNames = globalAxisNames

    for varIndex, rcjkVarGlyph in enumerate(rcjkGlyph.variations):
        location = rcjkVarGlyph.location
        location = normalizeLocation(location, rcjkGlyph.axes)
        if globalAxisNames is None:
            location = {axisNameMapping[k]: v for k, v in location.items()}
        sparseLocation = {k: v for k, v in location.items() if v != 0}
        layerName = layerNameFromLocation(sparseLocation, axisNames)
        assert layerName, (srcGlyphName, varIndex, location, rcjkGlyph.axes)
        layer = getUFOLayer(ufo, layerName)
        varGlyph = UGlyph(dstGlyphName)
        varGlyph.width = max(0, rcjkVarGlyph.width)  # width can't be negative
        rcjkGlyphToVarCoGlyph(
            rcjkVarGlyph, varGlyph, renameTable, componentSourceGlyphSet
        )
        layer[dstGlyphName] = varGlyph

    ufo[dstGlyphName] = glyph


def _makeAxisNameMapping(axes):
    return {axisName: f"V{axisIndex:03}" for axisIndex, axisName in enumerate(axes)}


def rcjkGlyphToVarCoGlyph(rcjkGlyph, glyph, renameTable, componentSourceGlyphSet):
    copyMarkColor(rcjkGlyph, glyph)
    pen = glyph.getPointPen()
    rcjkGlyph.drawPoints(pen)
    compoVarInfo = []
    for compo in rcjkGlyph.components:
        transform = compo.transform
        x, y = transform["x"], transform["y"]
        pen.addComponent(renameTable.get(compo.name, compo.name), (1, 0, 0, 1, x, y))
        # the transformation center goes into varco data
        varCoTransform = dict(
            # TODO: We could skip values that are default (0, or 1 for scale values)
            rotation=transform["rotation"],
            scalex=transform["scalex"],
            scaley=transform["scaley"],
            tcenterx=transform["tcenterx"],
            tcentery=transform["tcentery"],
        )
        if compo.name not in componentSourceGlyphSet:
            coord = {}
        else:
            baseGlyph = componentSourceGlyphSet.getGlyph(compo.name)
            axisNameMapping = _makeAxisNameMapping(baseGlyph.axes)
            coord = normalizeLocation(compo.coord, baseGlyph.axes)
            coord = {
                axisNameMapping[k]: v for k, v in coord.items() if k in axisNameMapping
            }
        info = dict(coord=coord, transform=varCoTransform)
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
            logger.warning(
                f"glyph name {glyphName} is not ASCII, and can not be exported"
            )
        else:
            okGlyphNames.append(glyphName)
    return okGlyphNames


def getUFOLayer(ufo, layerName):
    if layerName not in ufo.layers:
        layer = ufo.newLayer(layerName)
    else:
        layer = ufo.layers[layerName]
    return layer


def layerNameFromLocation(location, axisNames):
    nameParts = []
    for axisName, axisValue in sorted(location.items()):
        assert axisName in axisNames, (axisName, axisNames)
        if isinstance(axisValue, float) and axisValue.is_integer():
            axisValue = int(axisValue)
        nameParts.append(f"{axisName}={axisValue}")
    return "+".join(nameParts)


def parseLayerName(layerName):
    location = {}
    for part in layerName.split("+"):
        axisName, axisValue = part.split("=")
        axisValue = float(axisValue)
        location[axisName] = axisValue
    return location


def copyMarkColor(fromGlyph, toGlyph):
    colorString = fromGlyph.lib.get("public.markColor")
    if colorString:
        toGlyph.lib["public.markColor"] = colorString


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
                glyphName, unicodes = extractGlyphNameAndUnicodes(data, path.name)
                glyphNames[glyphName] = unicodes
            self._revCmap = glyphNames
        return self._revCmap

    def __contains__(self, glyphName):
        if self._revCmap is not None and glyphName in self._revCmap:
            return True
        if glyphName in self._glyphs:
            return True
        fileName = userNameToFileName(glyphName, suffix=".glif")
        glyphPath = self._path / fileName
        return glyphPath.exists()

    def getGlyph(self, glyphName):
        glyph = self._glyphs.get(glyphName)
        if glyph is None:
            glyph = self.getGlyphNoCache(glyphName)
            self._glyphs[glyphName] = glyph
        return glyph

    def getGlyphNoCache(self, glyphName):
        glyph = self.getGlyphRaw(glyphName)
        glyph._postParse(self)
        return glyph

    def getGlyphRaw(self, glyphName):
        fileName = userNameToFileName(glyphName, suffix=".glif")
        glifPath = self._path / fileName
        if not glifPath.exists():
            raise GlyphNotFoundError(f"{glyphName}")
        return RCJKGlyph.loadFromGLIF(glifPath)

    def getLayerNames(self):
        if not self._path.is_dir():
            return []
        return sorted(p.name for p in self._path.iterdir() if p.is_dir())

    def getLayer(self, layerName):
        layer = self._layers.get(layerName)
        if layer is None:
            layer = GlyphSet(self._path / layerName)
            self._layers[layerName] = layer
        return layer


_glyphNamePat = re.compile(rb'<glyph\s+name\s*=\s*"([^"]+)"')
_unicodePat = re.compile(rb'<unicode\s+hex\s*=\s*"([^"]+)"')


def extractGlyphNameAndUnicodes(data, fileName=None):
    m = _glyphNamePat.search(data)
    if m is None:
        raise ValueError(f"invalid .glif file, glyph name not found ({fileName})")
    glyphName = m.group(1).decode("utf-8")
    if fileName is not None:
        refFileName = userNameToFileName(glyphName, suffix=".glif")
        if refFileName != fileName:
            logger.warning(
                "actual file name does not match predicted file name: "
                f"{refFileName} {fileName} {glyphName}"
            )
    unicodes = [int(u, 16) for u in _unicodePat.findall(data)]
    return glyphName, unicodes


class RCJKGlyph(Glyph):
    def _postParse(self, glyphSet):
        """This gets called soon after parsing the .glif file. Any layer glyphs
        and variation info is unpacked here, and put into a subglyph, as part
        of the self.variations list.
        """
        self.outline, classicComponents = self.outline.splitComponents()
        for baseGlyphName, affineTransform in classicComponents:
            xx, xy, yx, yy, dx, dy = affineTransform
            rotation, scalex, scaley, skewx, skewy = decomposeTwoByTwo((xx, xy, yx, yy))
            assert abs(skewx) < 0.00001, f"x skew is not supported ({self.name})"
            assert abs(skewy) < 0.00001, f"y skew is not supported ({self.name})"
            transform = MathDict(
                x=dx,
                y=dy,
                scalex=scalex,
                scaley=scaley,
                rotation=math.degrees(rotation),
                tcenterx=0,
                tcentery=0,
            )
            self.components.append(Component(baseGlyphName, MathDict(), transform))
        dcNames = []
        for dc in self.lib.get("robocjk.deepComponents", []):
            dcNames.append(dc["name"])
            self.components.append(_unpackDeepComponent(dc))

        axes = {}
        for axisDict in self.lib.get("robocjk.axes", []):
            minValue = axisDict["minValue"]
            maxValue = axisDict["maxValue"]
            defaultValue = axisDict.get("defaultValue", minValue)
            minValue, maxValue = sorted([minValue, maxValue])
            axes[axisDict["name"]] = minValue, defaultValue, maxValue
        self.axes = axes

        self.status = self.lib.get("robocjk.status", 0)

        variationGlyphs = self.lib.get("robocjk.variationGlyphs")
        if variationGlyphs is None:
            return

        self.glyphNotInLayer = []

        for varDict in variationGlyphs:
            if not varDict.get("on", True):
                # This source is "off", and should not be used.
                # They are a bit like background layers.
                continue
            layerName = varDict.get("layerName")
            if (not self.outline.isEmpty() or classicComponents) and layerName:
                layer = glyphSet.getLayer(layerName)
                if self.name in layer:
                    varGlyph = layer.getGlyphNoCache(self.name)
                else:
                    # Layer glyph does not exist, make one up by copying
                    # self.width and self.outline
                    self.glyphNotInLayer.append(layerName)
                    logger.warning(f"glyph {self.name} not found in layer {layerName}")
                    varGlyph = self.__class__()
                    varGlyph.width = self.width
                    varGlyph.outline = self.outline
            else:
                varGlyph = self.__class__()
                varGlyph.width = self.width
            if "width" in varDict:
                varGlyph.width = varDict["width"]

            varGlyph.status = varDict.get("status", 0)

            varGlyph.location = varDict["location"]
            if _isLocationOutOfBounds(varGlyph.location, self.axes):
                raise LocationOutOfBoundsError(
                    f"location out of bounds for {self.name}; "
                    f"location: {_formatDict(varGlyph.location)} "
                    f"axes: {_formatDict(self.axes)}"
                )

            deepComponents = varDict.get("deepComponents", [])
            if len(dcNames) != len(deepComponents):
                raise ComponentMismatchError(
                    "different number of components in variations: "
                    f"{len(dcNames)} vs {len(deepComponents)}"
                )
            for dc, dcName in zip(deepComponents, dcNames):
                varGlyph.components.append(_unpackDeepComponent(dc, dcName))
            assert len(varGlyph.components) == len(self.components), (
                self.name,
                [c.name for c in varGlyph.components],
                [c.name for c in self.components],
            )

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


def _isLocationOutOfBounds(location, axes):
    for axisName, axisValue in location.items():
        minValue, defaultValue, maxValue = axes.get(axisName, (0, 0, 1))
        if not (minValue <= axisValue <= maxValue):
            return True
    return False


def _formatDict(d):
    kvPairs = (f"{k}={v}" for k, v in d.items())
    return f"dict({', '.join(kvPairs)})"


def checkComponentScaleVariation(glyph):
    componentIndices = range(len(glyph.components))
    for varGlyph in [glyph] + glyph.variations:
        x = {varGlyph.components[i].transform["scalex"] for i in componentIndices}
        y = {varGlyph.components[i].transform["scaley"] for i in componentIndices}
        if len(x) != 1 or len(y) != 1:
            return True
    return False


def rcjk2ufo():
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument(
        "-f", "--familyname", help="The family name for the output .ufo"
    )
    parser.add_argument("-s", "--stylename", help="The style name for the output .ufo")
    parser.add_argument(
        "--location",
        metavar="AXIS=LOC",
        nargs="*",
        default=[],
        help=(
            "List of space separated locations. A location consist in "
            "the name of a variation axis, followed by '=' and a number. E.g.: "
            " wght=700 wdth=80. If no location is given, a VarCo UFO will be "
            "written, as well as a .designspace file."
        ),
    )
    parser.add_argument(
        "--characters",
        type=argparse.FileType("r", encoding="utf-8"),
        help=(
            "A path to a UTF-8 encoded text file containing characters to include "
            "in the exported UFO. When omitted, all characters will be exported."
        ),
    )
    parser.add_argument("rcjk", help="The .rcjk project folder")
    parser.add_argument("ufo", help="The output .ufo")

    args = parser.parse_args()

    location = {}
    for arg in args.location:
        try:
            tag, val = arg.split("=")
            assert len(tag) <= 4
            location[tag.ljust(4)] = float(val)
        except (ValueError, AssertionError):
            parser.error("invalid location argument format: %r" % arg)

    if args.characters:
        characterSet = set(ord(c) for c in args.characters.read())
    else:
        characterSet = None

    project = RoboCJKProject(args.rcjk)
    ufoPath = pathlib.Path(args.ufo)
    if "-" in ufoPath.stem:
        familyNameDefault, styleNameDefault = ufoPath.stem.split("-", 1)
    else:
        familyNameDefault = ufoPath.stem
        styleNameDefault = "Regular" if location else "VarCo"
    familyName = args.familyname if args.familyname else familyNameDefault
    styleName = args.stylename if args.stylename else styleNameDefault
    if location:
        axes = {}
        location = normalizeLocation(location, project.axes)
        print("normalized location:", location)
        project.saveFlattenedUFO(
            args.ufo, location, familyName, styleName, characterSet=characterSet
        )
    else:
        project.saveVarCoUFO(args.ufo, familyName, styleName, characterSet=characterSet)


if __name__ == "__main__":
    rcjk2ufo()

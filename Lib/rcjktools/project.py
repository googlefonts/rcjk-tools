import logging
import math
import re
from typing import NamedTuple
import operator
import pathlib

from fontTools.misc.transform import Transform
from fontTools.pens.recordingPen import RecordingPointPen
from fontTools.pens.pointPen import SegmentToPointPen, PointToSegmentPen
from fontTools.ufoLib.glifLib import readGlyphFromString
from fontTools.ufoLib.filenames import userNameToFileName


class RoboCJKProject:

    def __init__(self, path):
        self._path = pathlib.Path(path)
        self.characterGlyphGlyphSet = GlyphSet(self._path / "characterGlyph")
        self.deepComponentGlyphSet = GlyphSet(self._path / "deepComponent")
        self.atomicElementGlyphSet = GlyphSet(self._path / "atomicElement")

    def getGlyphNamesAndUnicodes(self):
        return self.characterGlyphGlyphSet.getGlyphNamesAndUnicodes()

    def drawCharacterGlyph(self, glyphName, location):
        glyph = self.characterGlyphGlyphSet.getGlyph(glyphName)
        components, axes = _interpolateComponents(glyph, location, "robocjk.fontVariationGlyphs")
        deepItems = []
        for component in components:
            deepItem = self.drawDeepComponent(component.name, component.coord, makeTransform(**component.transform))
            deepItems.append((component.name, deepItem))
        if glyph.outline.isEmpty():
            outline = None
        else:
            outline = _interpolateOutline(glyph, axes, location, self.characterGlyphGlyphSet)
        return outline, deepItems

    def drawDeepComponent(self, glyphName, location, transform):
        glyph = self.deepComponentGlyphSet.getGlyph(glyphName)
        components, axes = _interpolateComponents(glyph, location, "robocjk.glyphVariationGlyphs")
        atomicOutlines = []
        for component in components:
            t = transform.transform(makeTransform(**component.transform))
            atomicOutline = self.drawAtomicElement(component.name, component.coord, t)
            atomicOutlines.append((component.name, atomicOutline))
        return atomicOutlines

    def drawAtomicElement(self, glyphName, location, transform):
        glyph = self.atomicElementGlyphSet.getGlyph(glyphName)
        axes = [(axisName, variations["layerName"], variations["minValue"], variations["maxValue"])
                for axisName, variations in glyph.lib["robocjk.glyphVariationGlyphs"].items()]
        outline = _interpolateOutline(glyph, axes, location, self.atomicElementGlyphSet)
        return outline.transform(transform)


_glyphNamePat = re.compile(rb'<glyph\s+name\s*=\s*"([^"]+)"')
_unicodePat = re.compile(rb'<unicode\s+hex\s*=\s*"([^"]+)"')


class GlyphSet:

    def __init__(self, path):
        self._path = path
        self._glyphs = {}
        self._layers = {}

    def getGlyphNamesAndUnicodes(self):
        glyphNames = {}
        for path in self._path.glob("*.glif"):
            with open(path, "rb") as f:
                data = f.read(1024)  # assuming all unicodes are in the first 1024 bytes of the file
            m = _glyphNamePat.search(data)
            if m is None:
                raise ValueError(f"invalid .glif file, glyph name not found ({path})")
            glyphName = m.group(1).decode("utf-8")
            refFileName = userNameToFileName(glyphName, suffix=".glif")
            if refFileName != path.name:
                logging.warning(f"actual file name does not match predicted file name: {refFileName} {path.name} {glyphName}")
            unicodes = [int(u, 16) for u in _unicodePat.findall(data)]
            glyphNames[glyphName] = unicodes
        return glyphNames

    def getGlyph(self, glyphName):
        glyph = self._glyphs.get(glyphName)
        if glyph is None:
            fileName = userNameToFileName(glyphName, suffix=".glif")
            glyph = parseGlyph(self._path / fileName)
            glyph._postParse(self)
            self._glyphs[glyphName] = glyph
        return glyph

    def getLayer(self, layerName):
        layer = self._layers.get(layerName)
        if layer is None:
            layer = GlyphSet(self._path / layerName)
            self._layers[layerName] = layer
        return layer


class Glyph:

    def __init__(self):
        self.outline = MathOutline()
        self.components = []
        self.variations = []
        self.location = {}  # neutral

    def _postParse(self, glyphSet):
        for dc in self.lib.get("robocjk.deepComponents", []):
            self.components.append(_unpackDeepComponent(dc))

        for varKey in ("robocjk.fontVariationGlyphs", "robocjk.glyphVariationGlyphs"):
            if varKey in self.lib:
                break
        else:
            return

        for axisName, varDict in self.lib[varKey].items():
            layerName = varDict["layerName"]
            # minValue = varDict["minValue"]
            # maxValue = varDict["maxValue"]
            # location = {axisName: 1.0}  # XXX later: maxValue
            if not self.outline.isEmpty():
                varGlyph = glyphSet.getLayer(layerName).getGlyph(self.name)
            else:
                varGlyph = Glyph()

            varGlyph.location = {axisName: 1.0}

            for dc in varDict["content"]["deepComponents"]:
                varGlyph.components.append(_unpackDeepComponent(dc))
            assert len(varGlyph.components) == len(self.components)

            self.variations.append(varGlyph)

    def getPointPen(self):
        return self.outline

    def getPen(self):
        return SegmentToPointPen(self.outline)

    def drawPoints(self, pen):
        self.outline.drawPoints(pen)

    def draw(self, pen):
        self.outline.draw(pen)


class Component(NamedTuple):

    name: str
    coord: dict
    transform: dict

    def __add__(self, other):
        return Component(self.name, self.coord + other.coord, self.transform + other.transform)

    def __sub__(self, other):
        return Component(self.name, self.coord - other.coord, self.transform - other.transform)

    def __mul__(self, scalar):
        return Component(self.name, self.coord * scalar, self.transform * scalar)

    def __rmul__(self, scalar):
        return self.__mul__(scalar)


class _MathMixin:

    def __add__(self, other):
        return self._doBinaryOperator(other, operator.add)

    def __sub__(self, other):
        return self._doBinaryOperator(other, operator.sub)

    def __mul__(self, scalar):
        return self._doUnaryOperator(scalar, operator.mul)

    def __rmul__(self, scalar):
        return self._doUnaryOperator(scalar, operator.mul)


class MathDict(dict, _MathMixin):

    def _doUnaryOperator(self, scalar, op):
        result = MathDict()
        for k, v in self.items():
            if isinstance(v, (int, float)):
                result[k] = op(v, scalar)
            else:
                result[k] = v
        return result

    def _doBinaryOperator(self, other, op):
        # any missing keys will be taken from the other dict
        self_other = dict(other)
        self_other.update(self)
        other_self = dict(self)
        other_self.update(other)
        result = MathDict()
        for k, v1 in self_other.items():
            v2 = other_self[k]
            if isinstance(v1, (int, float)):
                result[k] = op(v1, v2)
            else:
                assert v1 == v2, "incompatible dicts"
                result[k] = v1
        return result


class MathOutline(RecordingPointPen, _MathMixin):

    def isEmpty(self):
        return not self.value

    def drawPoints(self, pen):
        self.replay(pen)

    def draw(self, pen):
        self.drawPoints(PointToSegmentPen(pen))

    def transform(self, t):
        if not hasattr(t, "transformPoint"):
            t = Transform(*t)
        return self.applyUnaryFunc(t.transformPoint)

    def applyUnaryFunc(self, func):
        result = MathOutline()
        for m, args, kwargs in self.value:
            if m == "addPoint":
                pt, seg, smooth, name = args
                result.addPoint(func(pt), seg, smooth, name, **kwargs)
            elif m == "beginPath":
                result.beginPath()
            elif m == "endPath":
                result.endPath()
            else:
                assert False, f"unsupported method: {m}"
        return result

    def _doUnaryOperator(self, scalar, op):
        def func(pt):
            x, y = pt
            return op(x, scalar), op(y, scalar)
        return self.applyUnaryFunc(func)

    def _doBinaryOperator(self, other, op):
        result = MathOutline()
        assert len(self.value) == len(other.value), "incompatible outline"
        for (m1, args1, kwargs1), (m2, args2, kwargs2) in zip(self.value, other.value):
            assert m1 == m2, "incompatible outline"
            if m1 == "addPoint":
                (x1, y1), seg1, smooth1, name1 = args1
                (x2, y2), seg2, smooth2, name2 = args2
                assert seg1 == seg2, "incompatible outline"
                pt = op(x1, x2), op(y1, y2)
                result.addPoint(pt, seg1, smooth1, name1, **kwargs1)
            elif m1 == "beginPath":
                result.beginPath()
            elif m1 == "endPath":
                result.endPath()
            else:
                assert False, f"unsupported method: {m1}"
        return result


def parseGlyph(p):
    with open(p) as f:
        data = f.read()
    g = Glyph()
    readGlyphFromString(data, g, g.getPointPen())
    return g


def makeTransform(x, y, rotation, scalex, scaley, rcenterx, rcentery):
    rotation = math.radians(rotation)
    rcenterx *= scalex
    rcentery *= scaley
    t = Transform()
    t = t.translate(x + rcenterx, y + rcentery)
    t = t.rotate(rotation)
    t = t.translate(-rcenterx, -rcentery)
    t = t.scale(scalex, scaley)
    return t


def _interpolateComponents(glyph, location, varKey):
    # neutral
    neutralComponents = []
    for dc in glyph.lib["robocjk.deepComponents"]:
        neutralComponents.append(_unpackDeepComponent(dc))

    # variations
    components = list(neutralComponents)
    axes = []
    for axisName, variations in glyph.lib[varKey].items():
        axes.append((axisName, variations["layerName"], variations["minValue"], variations["maxValue"]))
        scalar = location.get(axisName, 0)
        if not scalar:
            continue
        varComponents = []
        for dc in variations["content"]["deepComponents"]:
            varComponents.append(_unpackDeepComponent(dc))
        assert len(varComponents) == len(components)
        for i in range(len(components)):
            deltaComponent = varComponents[i] - neutralComponents[i]
            components[i] += scalar * deltaComponent
    return components, axes


def _unpackDeepComponent(dc):
    name = dc.get("name")
    coord = dc["coord"]
    transform = {k: v for k, v in dc.items() if k not in {"coord", "name"}}
    return Component(name, MathDict(coord), MathDict(transform))


def _interpolateOutline(glyph, axes, location, glyphSet):
    glyphName = glyph.name
    outline = neutralOutline = glyph.outline
    for axisName, layerName, minValue, maxValue in axes:
        scalar = location.get(axisName, 0)
        if not scalar:
            continue
        layer = glyphSet.getLayer(layerName)
        layerGlyph = layer.getGlyph(glyphName)
        deltaOutline = layerGlyph.outline - neutralOutline
        outline += scalar * deltaOutline
    return outline


if __name__ == "__main__":
    # DrawBot test snippet
    from drawBot import BezierPath, translate, scale, fill, stroke, drawPath

    def drawOutline(outline):
        bez = BezierPath()
        outline.drawPoints(bez)
        drawPath(bez)

    project = RoboCJKProject("/Users/just/code/git/BlackFoundry/gs-cjk-rcjk/Hanzi.rcjk/")

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
        outline, deepItems = project.drawCharacterGlyph(glyphName, {"wght": f})
        if outline is not None:
            drawOutline(outline)
        for dcName, atomicOutlines in deepItems:
            for atomicName, atomicOutline in atomicOutlines:
                drawOutline(atomicOutline)

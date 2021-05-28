from fontTools.pens.transformPen import TransformPen
from fontTools.ttLib import TTFont
from fontTools.ttLib.tables._g_l_y_f import GlyphCoordinates
from fontTools.varLib.iup import iup_delta
from fontTools.varLib.models import normalizeLocation, supportScalar
from fontTools.varLib.varStore import VarStoreInstancer
import uharfbuzz as hb
from rcjktools.table_VarC import COORD_PRECISIONBITS, VARIDX_KEY, intToDegrees
from rcjktools.utils import makeTransformVarCo


class TTVarCFont:
    def __init__(self, path, ttFont=None, hbFont=None):
        if ttFont is not None:
            assert hbFont is not None
            assert path is None
            self.ttFont = ttFont
        else:
            assert hbFont is None
            self.ttFont = TTFont(path, lazy=True)
        self.axes = {
            axis.axisTag: (axis.minValue, axis.defaultValue, axis.maxValue)
            for axis in self.ttFont["fvar"].axes
        }
        if hbFont is not None:
            self.hbFont = hbFont
        else:
            with open(path, "rb") as f:
                face = hb.Face(f.read())
            self.hbFont = hb.Font(face)

    def keys(self):
        return self.ttFont.getGlyphNames()

    def __contains__(self, glyphName):
        return glyphName in self.ttFont.getReverseGlyphMap()

    def drawGlyph(self, pen, glyphName, location):
        normLocation = normalizeLocation(location, self.axes)
        fvarTable = self.ttFont["fvar"]
        glyfTable = self.ttFont["glyf"]
        varcTable = self.ttFont.get("VarC")
        if varcTable is not None:
            glyphData = varcTable.GlyphData
        else:
            glyphData = {}

        g = glyfTable[glyphName]
        varComponents = glyphData.get(glyphName)
        if g.isComposite():
            componentOffsets = instantiateComponentOffsets(
                self.ttFont, glyphName, normLocation
            )
            if varComponents is not None:
                assert len(g.components) == len(varComponents)
                varcInstancer = VarStoreInstancer(
                    varcTable.VarStore, fvarTable.axes, normLocation
                )
                for (x, y), gc, vc in zip(
                    componentOffsets, g.components, varComponents
                ):
                    componentLocation = unpackComponentLocation(vc.coord, varcInstancer)
                    transform = unpackComponentTransform(
                        vc.transform, varcInstancer, vc.numIntBitsForScale
                    )
                    tPen = TransformPen(pen, _makeTransform(x, y, transform))
                    self.drawGlyph(tPen, gc.glyphName, componentLocation)
            else:
                for (x, y), gc in zip(componentOffsets, g.components):
                    tPen = TransformPen(pen, (1, 0, 0, 1, x, y))
                    self.drawGlyph(tPen, gc.glyphName, {})
        else:
            glyphID = self.ttFont.getGlyphID(glyphName)
            self.hbFont.set_variations(location)
            self.hbFont.draw_glyph_with_pen(glyphID, pen)


def instantiateComponentOffsets(ttFont, glyphName, location):
    glyfTable = ttFont["glyf"]
    gvarTable = ttFont["gvar"]
    assert glyfTable[glyphName].isComposite()
    variations = gvarTable.variations[glyphName]
    coordinates, _ = glyfTable.getCoordinatesAndControls(glyphName, ttFont)
    origCoords, endPts = None, None
    for var in variations:
        scalar = supportScalar(location, var.axes)
        if not scalar:
            continue
        delta = var.coordinates
        if None in delta:
            if origCoords is None:
                origCoords, g = glyfTable.getCoordinatesAndControls(glyphName, ttFont)
                endPts = g.endPts
            delta = iup_delta(delta, origCoords, endPts)
        coordinates += GlyphCoordinates(delta) * scalar
    assert len(coordinates) == len(glyfTable[glyphName].components) + 4
    return coordinates[:-4]


def unpackComponentLocation(coordDict, varcInstancer):
    componentLocation = {}
    for axis, valueDict in coordDict.items():
        value = valueDict["value"]
        if VARIDX_KEY in valueDict:
            delta = varcInstancer[valueDict[VARIDX_KEY]]
            value += delta / (1 << COORD_PRECISIONBITS)
        componentLocation[axis] = value
    return componentLocation


def unpackComponentTransform(transformDict, varcInstancer, numIntBitsForScale):
    transform = {}
    for name, valueDict in transformDict.items():
        value = valueDict["value"]
        if VARIDX_KEY in valueDict:
            delta = varcInstancer[valueDict[VARIDX_KEY]]
            if name in {"ScaleX", "ScaleY"}:
                delta = delta / (1 << (16 - numIntBitsForScale))
            elif name in {"Rotation", "SkewX", "SkewY"}:
                delta = intToDegrees(delta)
            value += delta
        transform[name] = value
    return transform


def _makeTransform(x, y, transform):
    return makeTransformVarCo(
        x=x,
        y=y,
        rotation=transform.get("Rotation", 0),
        scalex=transform.get("ScaleX", 1.0),
        scaley=transform.get("ScaleY", 1.0),
        skewx=transform.get("SkewX", 0),
        skewy=transform.get("SkewY", 0),
        tcenterx=transform.get("TCenterX", 0),
        tcentery=transform.get("TCenterY", 0),
    )

import math
from fontTools.misc.transform import Transform
from fontTools.pens.filterPen import FilterPointPen
from fontTools.varLib.models import VariationModel
from ufoLib2 import Font as UFont
from .objects import Component, Glyph, MathDict, MathOutline, TransformMathDict
from .utils import decomposeTwoByTwo, makeTransformVarCo


class VarCoGlyph(Glyph):

    @classmethod
    def loadFromUFont(cls, ufont, glyphName):
        uglyph = ufont[glyphName]
        self = cls.loadFromGlyphObject(uglyph)
        self._postParse(ufont)
        return self

    def _postParse(self, ufont):
        # Filter out and collect component info from the outline
        outline = MathOutline()
        cc = ComponentCollector(outline)
        self.outline.drawPoints(cc)
        self.outline = outline

        # Build Component objects
        vcComponentData = self.lib.get("varco.components", [])
        assert len(cc.components) == len(vcComponentData)
        assert len(self.components) == 0
        for (baseGlyph, affine), vcCompo in zip(cc.components, vcComponentData):
            transformExtra = vcCompo["transform"]
            tcenterx, tcentery = transformExtra["tcenterx"], transformExtra["tcentery"]
            x, y = affine[4:]
            rotation, scalex, scaley, skewx, skewy = decomposeTwoByTwo(affine[:4])
            transform = TransformMathDict(
                x=x,
                y=y,
                rotation=math.degrees(rotation),
                scalex=scalex,
                scaley=scaley,
                skewx=math.degrees(skewx),
                skewy=math.degrees(skewy),
                tcenterx=tcenterx,
                tcentery=tcentery,
            )
            self.components.append(Component(baseGlyph, MathDict(vcCompo["coord"]), transform))

        # Unpack axes
        self.axes = {a["name"]: (a["minValue"], a["maxValue"]) for a in self.lib.get("varco.axes", [])}
        assert len(self.variations) == 0
        for varDict in self.lib.get("varco.variations", []):
            layerName = varDict["layerName"]
            location = varDict["location"]
            for axisName in location.keys():
                assert axisName in self.axes, axisName
            varGlyph = self.__class__.loadFromGlyphObject(ufont.layers[layerName][self.name])
            varGlyph._postParse(ufont)
            varGlyph.location = location
            self.variations.append(varGlyph)
        if self.variations:
            locations = [{}] + [variation.location for variation in self.variations]
            self.model = VariationModel(locations)


class VarCoFont:

    def __init__(self, ufoPath):
        self.ufont = UFont(ufoPath)
        self.varcoGlyphs = {}

    def drawPointsGlyph(self, pen, glyphName, location, transform=None):
        varGlyph = self[glyphName]
        instanceGlyph = varGlyph.instantiate(location)
        outline = instanceGlyph.outline
        if transform is not None:
            outline = outline.transform(transform)
        outline.drawPoints(pen)
        for component in instanceGlyph.components:
            t = makeTransformVarCo(**component.transform)
            if transform is not None:
                t = transform.transform(t)
            self.drawPointsGlyph(pen, component.name, component.coord, t)

    def keys(self):
        return self.ufont.keys()

    def __contains__(self, glyphName):
        return glyphName in self.ufont

    def __len__(self):
        return len(self.ufont)

    def __iter__(self):
        return iter(self.ufont.keys())

    def __getitem__(self, glyphName):
        varcoGlyph = self.varcoGlyphs.get(glyphName)
        if varcoGlyph is None:
            varcoGlyph = VarCoGlyph.loadFromUFont(self.ufont, glyphName)
            self.varcoGlyphs[glyphName] = varcoGlyph
        return varcoGlyph

    def get(self, glyphName, default=None):
        try:
            glyph = self[glyphName]
        except KeyError:
            glyph = default
        return glyph


class ComponentCollector(FilterPointPen):

    """This pen passes all outline data on to the outPen, and
    stores component data in a list.
    """

    def __init__(self, outPen):
        super().__init__(outPen)
        self.components = []

    def addComponent(self, glyphName, transformation, **kwargs):
        self.components.append((glyphName, transformation))


if __name__ == "__main__":
    import sys
    ufoPath = sys.argv[1]
    vcFont = VarCoFont(ufoPath)
    g = vcFont["DC_5927_03"]
    print(g.components)
    print(g.axes)
    x = g + 0.5 * (g.variations[0] - g)
    print(g.components[-1].transform)
    print(x.components[-1].transform)
    print(g.variations[0].components[-1].transform)
    print(list(vcFont.keys())[:100])
    print("AE_PieZhe" in vcFont)
    # for x in vcFont:
    #     print(x)

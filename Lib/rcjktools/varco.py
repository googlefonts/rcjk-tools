from fontTools.pens.filterPen import FilterPointPen
from fontTools.varLib.models import VariationModel
from ufoLib2 import Font as UFont
from .objects import Component, Glyph, MathDict, MathOutline
from .utils import decomposeTwoByTwo


class VarCoGlyph(Glyph):

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
            tcenterx, tcentery = vcCompo["transform"]["tcenterx"], vcCompo["transform"]["tcentery"]
            x, y = affine[4:]
            rotation, scalex, scaley, skewx, skewy = decomposeTwoByTwo(affine[:4])
            transform = MathDict(
                x=x,
                y=y,
                rotation=rotation,
                scalex=scalex,
                scaley=scaley,
                skewx=skewx,
                skewy=skewy,
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

    def getGlyph(self, glyphName):
        varcoGlyph = self.varcoGlyphs.get(glyphName)
        if varcoGlyph is None:
            uglyph = self.ufont[glyphName]
            varcoGlyph = VarCoGlyph.loadFromGlyphObject(uglyph)
            varcoGlyph._postParse(self.ufont)
            self.varcoGlyphs[glyphName] = varcoGlyph
        return varcoGlyph


class ComponentCollector(FilterPointPen):

    """This pen passes all outline data on to the outPen, and
    stores component data in a list.
    """

    def __init__(self, outPen):
        self.components = []
        super().__init__(outPen)

    def addComponent(self, glyphName, transformation, **kwargs):
        self.components.append((glyphName, transformation))


if __name__ == "__main__":
    import sys
    ufoPath = sys.argv[1]
    vcFont = VarCoFont(ufoPath)
    g = vcFont.getGlyph("DC_5927_03")
    print(g.components)
    print(g.axes)
    x = g + 0.5 * (g.variations[0] - g)
    print(g.components[-1].transform)
    print(x.components[-1].transform)
    print(g.variations[0].components[-1].transform)

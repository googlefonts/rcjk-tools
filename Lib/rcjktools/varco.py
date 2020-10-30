from fontTools.pens.filterPen import FilterPointPen
from fontTools.pens.pointPen import PointToSegmentPen
from fontTools.varLib.models import VariationModel, allEqual
from ufoLib2 import Font as UFont
from .objects import Component, Glyph, MathDict, MathOutline
from .utils import makeTransformVarCo


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
            assert affine[:4] == (1, 0, 0, 1)
            x, y = affine[4:]
            transformDict = vcCompo["transform"]
            transform = MathDict(
                x=affine[4],
                y=affine[5],
                rotation=transformDict.get("rotation", 0),
                scalex=transformDict.get("scalex", 1),
                scaley=transformDict.get("scaley", 1),
                skewx=transformDict.get("skewx", 0),
                skewy=transformDict.get("skewy", 0),
                tcenterx=transformDict.get("tcenterx", 0),
                tcentery=transformDict.get("tcentery", 0),
            )
            self.components.append(Component(baseGlyph, MathDict(vcCompo["coord"]), transform))

        # Unpack axis names
        self.axisNames = {axisName: axisIndex for axisIndex, axisName in enumerate(self.lib.get("varco.axisnames", []))}

        assert len(self.variations) == 0
        for varDict in self.lib.get("varco.variations", []):
            layerName = varDict["layerName"]
            location = varDict["location"]
            for axisName, axisValue in location.items():
                assert 0 <= axisValue <= 1, (axisName, axisValue)
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

    def drawGlyph(self, pen, glyphName, location):
        self.drawPointsGlyph(PointToSegmentPen(pen), glyphName, location)

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

    def extractVarCoData(self, globalAxisNames):
        allLocations = set()
        vcData = {}
        for glyphName in sorted(self.keys()):
            glyph = self[glyphName]
            masters = [glyph] + glyph.variations
            componentAxisMappings = [
                {axisName: f"V{axisIndex:03}" for axisName, axisIndex in self[c.name].axisNames.items()}
                for c in glyph.components
            ]
            localAxisMapping = {
                axisName: axisName if axisName in globalAxisNames else f"V{axisIndex:03}"
                for axisName, axisIndex in glyph.axisNames.items()
            }
            locations = [
                {localAxisMapping[k]: v for k, v in m.location.items()}
                for m in masters
            ]
            allLocations.update(tuplifyLocation(loc) for loc in locations)
            components = []
            for i in range(len(glyph.components)):
                assert allEqual([m.components[i].name for m in masters])
                compMap = componentAxisMappings[i]
                coords = [
                    {
                        compMap[k]: m.components[i].coord.get(k, 0)
                        for k in compMap
                    }
                    for m in masters
                ]
                transforms = [
                    # Filter out x and y, as they'll be in glyf and gvar
                    {
                        _transformFieldMapping[k]: v
                        for k, v in m.components[i].transform.items()
                        if k not in {"x", "y"}
                    }
                    for m in masters
                ]
                components.append(list(zip(coords, transforms)))
            if components:
                vcData[glyphName] = components, locations
        allLocations = [dict(items) for items in sorted(allLocations)]
        return vcData, allLocations


_transformFieldMapping = {
    "rotation": "Rotation",
    "scalex": "ScaleX",
    "scaley": "ScaleY",
    "skewx": "SkewX",
    "skewy": "SkewY",
    "tcenterx": "TCenterX",
    "tcentery": "TCenterY",
}


def tuplifyLocation(loc):
    return tuple(sorted(loc.items()))


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

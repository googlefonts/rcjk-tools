from fontTools.designspaceLib import DesignSpaceDocument
from fontTools.pens.pointPen import PointToSegmentPen
from fontTools.varLib.models import VariationModel, allEqual, normalizeLocation
from ufoLib2 import Font as UFont
from .objects import Component, Glyph, MathDict
from .utils import makeTransformVarCo, tuplifyLocation


class VarCoGlyph(Glyph):
    @classmethod
    def loadFromUFOs(cls, ufos, locations, glyphName, axes):
        uglyph = ufos[0][glyphName]
        self = cls.loadFromGlyphObject(uglyph)
        self.axes = axes
        self._postParse(ufos, locations)
        return self

    def _postParse(self, ufos, locations):
        # Filter out and collect component info from the outline
        self.outline, components = self.outline.splitComponents()

        # Build Component objects
        vcComponentData = self.lib.get("varco.components", [])
        if vcComponentData:
            assert len(components) == len(vcComponentData), (
                self.name,
                len(components),
                len(vcComponentData),
                components,
            )
        else:
            vcComponentData = [None] * len(components)
        assert len(self.components) == 0
        for (baseGlyph, affine), vcCompo in zip(components, vcComponentData):
            if vcCompo is None:
                xx, xy, yx, yy, dx, dy = affine
                assert xy == 0, "rotation and skew are not implemented"
                assert yx == 0, "rotation and skew are not implemented"
                coord = {}
                transform = MathDict(
                    x=dx,
                    y=dy,
                    rotation=0,
                    scalex=xx,
                    scaley=yy,
                    skewx=0,
                    skewy=0,
                    tcenterx=0,
                    tcentery=0,
                )
            else:
                assert affine[:4] == (1, 0, 0, 1)
                x, y = affine[4:]
                coord = vcCompo["coord"]
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
            self.components.append(Component(baseGlyph, MathDict(coord), transform))

        assert len(self.variations) == 0
        if ufos:
            assert len(ufos) == len(locations)
            for ufo, location in zip(ufos[1:], locations[1:]):
                if self.name not in ufo:
                    continue
                for axisName, axisValue in location.items():
                    assert -1 <= axisValue <= 1, (axisName, axisValue)
                varGlyph = self.__class__.loadFromGlyphObject(ufo[self.name])
                varGlyph._postParse([], [])
                varGlyph.location = location
                self.variations.append(varGlyph)
            if self.variations:
                locations = [{}] + [variation.location for variation in self.variations]
                self.model = VariationModel(locations)


class VarCoFont:
    def __init__(self, designSpacePath):
        doc = DesignSpaceDocument.fromfile(designSpacePath)
        self.axes, self.ufos, self.locations = unpackDesignSpace(doc)
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
        return self.ufos[0].keys()

    def __contains__(self, glyphName):
        return glyphName in self.ufos[0]

    def __len__(self):
        return len(self.ufos[0])

    def __iter__(self):
        return iter(self.ufos[0].keys())

    def __getitem__(self, glyphName):
        varcoGlyph = self.varcoGlyphs.get(glyphName)
        if varcoGlyph is None:
            varcoGlyph = VarCoGlyph.loadFromUFOs(
                self.ufos, self.locations, glyphName, self.axes
            )
            self.varcoGlyphs[glyphName] = varcoGlyph
        return varcoGlyph

    def get(self, glyphName, default=None):
        try:
            glyph = self[glyphName]
        except KeyError:
            glyph = default
        return glyph

    def extractVarCoData(self, globalAxisNames, neutralOnly=False):
        allLocations = set()
        vcData = {}
        neutralGlyphNames = []
        for glyphName in sorted(self.keys()):
            glyph = self[glyphName]
            axisTags = {axisTag for v in glyph.variations for axisTag in v.location}
            if neutralOnly and not axisTags - globalAxisNames:
                masters = [glyph]
                neutralGlyphNames.append(glyphName)
            else:
                masters = [glyph] + glyph.variations

            if not glyph.outline.isEmpty() and glyph.components:
                assert not any(
                    c.coord for c in glyph.components
                ), "can't mix outlines and variable components"
                # ensure only the offset may vary across masters
                for attr in [
                    "rotation",
                    "scalex",
                    "scaley",
                    "skewx",
                    "skewy",
                    "tcenterx",
                    "tcentery",
                ]:
                    values = {c.transform[attr] for m in masters for c in m.components}
                    assert len(values) == 1, f"classic component varies {attr}"
                # This glyph mixes outlines and classic components, it will be
                # flattened upon TTF compilation, so should not be part of the VarC table
                continue

            locations = [m.location for m in masters]
            allLocations.update(tuplifyLocation(loc) for loc in locations)
            components = []
            for i in range(len(glyph.components)):
                assert allEqual([m.components[i].name for m in masters])
                coords = [m.components[i].coord for m in masters]
                fillMissingFromNeutral(coords)
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
        return vcData, allLocations, neutralGlyphNames


def fillMissingFromNeutral(coords):
    # This ensures that all variation coord dicts contain all the
    # keys from the neutral coord dict. If missing, the value from
    # the neutral coord is used. This is crucial for the variation
    # building mechanism.
    firstCoord = coords[0]
    for coord in coords[1:]:
        for k, v in firstCoord.items():
            coord.setdefault(k, v)


def unpackDesignSpace(doc):
    axisTagMapping = {axis.name: axis.tag for axis in doc.axes}
    axes = {axis.tag: (axis.minimum, axis.default, axis.maximum) for axis in doc.axes}

    # We want the default source to be the first in the list; the rest of
    # the order is not important
    sources = sorted(doc.sources, key=lambda src: src != doc.default)

    ufos = []
    locations = []

    _loaded = {}

    for src in sources:
        loc = src.location
        loc = {
            axisTagMapping[axisName]: axisValue for axisName, axisValue in loc.items()
        }
        loc = normalizeLocation(loc, axes)
        loc = {
            axisName: axisValue for axisName, axisValue in loc.items() if axisValue != 0
        }
        locations.append(loc)
        ufo = _loaded.get(src.path)
        if ufo is None:
            ufo = UFont(src.path)
            _loaded[src.path] = ufo
        if src.layerName is None:
            ufo.layers.defaultLayer
        else:
            ufo = ufo.layers[src.layerName]
        ufos.append(ufo)

    userAxes = {
        axis.tag: (axis.minimum, axis.default, axis.maximum)
        for axis in doc.axes
        if not axis.hidden
    }
    return userAxes, ufos, locations


_transformFieldMapping = {
    "rotation": "Rotation",
    "scalex": "ScaleX",
    "scaley": "ScaleY",
    "skewx": "SkewX",
    "skewy": "SkewY",
    "tcenterx": "TCenterX",
    "tcentery": "TCenterY",
}


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

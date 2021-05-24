from fontTools.colorLib.builder import buildCOLR
from fontTools.misc.fixedTools import floatToFixed
from fontTools.ttLib import TTFont
from fontTools.ttLib.tables import otTables as ot
from fontTools.varLib.models import VariationModel, allEqual
from fontTools.varLib.varStore import OnlineVarStoreBuilder
from rcjktools.varco import VarCoFont, fillMissingFromNeutral


def prepareVariableComponentData(vcFont, axisTags, globalAxisNames):
    storeBuilder = OnlineVarStoreBuilder(axisTags)

    vcData = {}
    for glyphName in sorted(vcFont.keys()):
        glyph = vcFont[glyphName]
        masters = [glyph] + glyph.variations

        if not glyph.outline.isEmpty() and glyph.components:
            # This glyph mixes outlines and classic components, it will have been
            # flattened upon TTF compilation
            continue

        locations = [m.location for m in masters]
        storeBuilder.setModel(VariationModel(locations))
        components = []
        for i in range(len(glyph.components)):
            assert allEqual([m.components[i].name for m in masters])
            baseName = masters[0].components[i].name

            coords = [m.components[i].coord for m in masters]
            fillMissingFromNeutral(coords)
            for c in coords[1:]:
                # TODO: if this happens, perhaps remove all keys from variations
                # that do not occur in the neutral
                assert c.keys() == coords[0].keys()

            transforms = [m.components[i].transform for m in masters]
            for t in transforms[1:]:
                assert t.keys() == transforms[0].keys()
            coordMasterValues = {
                k: [coord[k] for coord in coords] for k in coords[0].keys()
            }
            transformMasterValues = {
                k: [transform[k] for transform in transforms]
                for k in transforms[0].keys()
            }

            coord = compileMasterValuesDict(storeBuilder, coordMasterValues, 14)  # 2.14
            transform = compileMasterValuesDict(
                storeBuilder, transformMasterValues, 16
            )  # 16.16

            components.append((baseName, coord, transform))
        if components:
            vcData[glyphName] = components

    varStore = storeBuilder.finish()
    mapping = varStore.optimize()
    assert 0xFFFFFFFF not in mapping
    mapping[0xFFFFFFFF] = 0xFFFFFFFF
    for glyphName, components in vcData.items():
        for baseName, coord, transform in components:
            remapValuesDict(mapping, coord)
            remapValuesDict(mapping, transform)

    return vcData, varStore


def remapValuesDict(mapping, valuesDict):
    for k, (v, varIdx) in valuesDict.items():
        valuesDict[k] = (v, mapping[varIdx])


def compileMasterValuesDict(storeBuilder, masterValuesDict, precisionBits):
    coord = {}
    for k, masterValues in masterValuesDict.items():
        if allEqual(masterValues):
            varIdx = 0xFFFFFFFF
        else:
            _, varIdx = storeBuilder.storeMasters(
                [floatToFixed(v, precisionBits) for v in masterValues]
            )
        coord[k] = (masterValues[0], varIdx)
    return coord


def buildCOLRGlyphs(vcData, axisTagToIndex):
    colrGlyphs = {}
    colrGlyphNames = set(vcData)
    for glyphName, components in vcData.items():
        colrGlyphs[glyphName] = buildCOLRGlyph(
            glyphName, components, colrGlyphNames, axisTagToIndex
        )
    return colrGlyphs


def buildCOLRGlyph(glyphName, components, colrGlyphNames, axisTagToIndex):
    assert len(components) > 0
    layers = []

    for baseName, coord, transform in components:
        isColrGlyph = baseName in colrGlyphNames

        # We're building the color glyph from leaf to root

        # Final leaf paint
        if isColrGlyph:
            paint = dict(Format=ot.PaintFormat.PaintColrGlyph, Glyph=baseName)
        else:
            paint = dict(
                Format=ot.PaintFormat.PaintSolid,
                Color=dict(PaletteIndex=0xFFFF, Alpha=1.0),
            )
            paint = dict(Format=ot.PaintFormat.PaintGlyph, Glyph=baseName, Paint=paint)

        haveTranslate, haveTranslateVar = _haveTransformItem(
            transform, [("x", 0), ("y", 0)]
        )
        haveRotate, haveRotateVar = _haveTransformItem(transform, [("rotation", 0)])
        haveScale, haveScaleVar = _haveTransformItem(
            transform, [("scalex", 1), ("scaley", 1)]
        )
        haveTCenter, haveTCenterVar = _haveTransformItem(
            transform, [("tcenterx", 0), ("tcentery", 0)]
        )

        if haveScale:
            fmt = (
                ot.PaintFormat.PaintVarScale
                if haveScaleVar or haveTCenterVar
                else ot.PaintFormat.PaintScale
            )
            paint = dict(
                Format=fmt,
                Paint=paint,
                xScale=_unvarValue(transform["scalex"]),
                yScale=_unvarValue(transform["scaley"]),
                centerX=_unvarValue(transform["tcenterx"]),
                centerY=_unvarValue(transform["tcentery"]),
            )

        if haveRotate:
            fmt = (
                ot.PaintFormat.PaintVarRotate
                if haveRotateVar
                else ot.PaintFormat.PaintRotate
            )
            paint = dict(
                Format=fmt,
                Paint=paint,
                angle=_unvarValue(transform["rotation"]),
                centerX=_unvarValue(transform["tcenterx"]),
                centerY=_unvarValue(transform["tcentery"]),
            )

        if haveTranslate:
            fmt = (
                ot.PaintFormat.PaintVarTranslate
                if haveTranslateVar
                else ot.PaintFormat.PaintTranslate
            )
            paint = dict(
                Format=fmt,
                Paint=paint,
                dx=_unvarValue(transform["x"]),
                dy=_unvarValue(transform["y"]),
            )

        if coord:
            haveVariations = any(v[1] != 0xFFFFFFFF for v in coord.values())
            fmt = (
                ot.PaintFormat.PaintVarLocation
                if haveVariations
                else ot.PaintFormat.PaintLocation
            )
            paint = dict(
                Format=fmt,
                Paint=paint,
                Coordinate=[
                    dict(
                        AxisIndex=axisTagToIndex[tag],
                        AxisValue=value if value[1] != 0xFFFFFFFF else value[0],
                    )
                    for tag, value in coord.items()
                ],
            )

        layers.append(paint)

    if len(layers) == 1:
        glyphPaint = layers[0]
    else:
        glyphPaint = dict(Format=ot.PaintFormat.PaintColrLayers, Layers=layers)

    return glyphPaint


def _unvarValue(value):
    if isinstance(value, tuple) and value[1] == 0xFFFFFFFF:
        value = value[0]
    return value


def _haveTransformItem(transform, attrDefaultValues):
    haveItem = any(
        transform[n][0] != v or transform[n][1] != 0xFFFFFFFF
        for n, v in attrDefaultValues
    )
    haveVariations = any(transform[n][1] != 0xFFFFFFFF for n, v in attrDefaultValues)
    return haveItem, haveVariations


def buildCOLRv1(designspacePath, ttfPath, outTTFPath, saveWoff2):
    import pathlib

    ttfPath = pathlib.Path(ttfPath)
    if outTTFPath is None:
        outTTFPath = ttfPath.parent / (ttfPath.stem + "-colrv1" + ttfPath.suffix)
    else:
        outTTFPath = pathlib.Path(outTTFPath)
    ttf = TTFont(ttfPath)

    axisTags = [axis.axisTag for axis in ttf["fvar"].axes]
    axisTagToIndex = {tag: index for index, tag in enumerate(axisTags)}
    globalAxisNames = {axisTag for axisTag in axisTags if axisTag[0] != "V"}
    vcFont = VarCoFont(designspacePath)
    vcData, varStore = prepareVariableComponentData(vcFont, axisTags, globalAxisNames)
    colrGlyphs = buildCOLRGlyphs(vcData, axisTagToIndex)

    ttf["COLR"] = buildCOLR(colrGlyphs, varStore=varStore)

    # TODO: fix glyf+gvar to contain bounding boxes for color glyphs

    ttf.save(outTTFPath)

    ttf = TTFont(outTTFPath, lazy=True)  # Load from scratch

    if saveWoff2:
        outWoff2Path = outTTFPath.parent / (outTTFPath.stem + ".woff2")
        ttf.flavor = "woff2"
        ttf.save(outWoff2Path)


def main():
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("designspace", help="The VarCo .designspace source")
    parser.add_argument("ttf", help="The input Variable Font")
    parser.add_argument("--output", help="The output Variable Font")
    parser.add_argument("--no-woff2", action="store_true")
    args = parser.parse_args()
    buildCOLRv1(args.designspace, args.ttf, args.output, not args.no_woff2)


if __name__ == "__main__":
    main()

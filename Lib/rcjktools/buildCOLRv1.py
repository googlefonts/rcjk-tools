from fontTools.colorLib.builder import buildCOLR
from fontTools.misc.arrayTools import intRect
from fontTools.misc.fixedTools import floatToFixed
from fontTools.ttLib import TTFont
from fontTools.ttLib.tables import otTables as ot
from fontTools.varLib.models import VariationModel, allEqual
from fontTools.varLib.varStore import OnlineVarStoreBuilder
from rcjktools.varco import VarCoFont, fillMissingFromNeutral


def prepareVariableComponentData(vcFont, axisTags, globalAxisNames, neutralOnly=False):
    storeBuilder = OnlineVarStoreBuilder(axisTags)

    vcData = {}
    for glyphName in sorted(vcFont.keys()):
        glyph = vcFont[glyphName]
        axisTags = {axisTag for v in glyph.variations for axisTag in v.location}
        if neutralOnly and not axisTags - globalAxisNames:
            masters = [glyph]
        else:
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

            coords = [dict(m.components[i].coord) for m in masters]
            sanitizeCoords(coords, vcFont[baseName])
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
            remapValuesDict(coord, mapping)
            remapValuesDict(transform, mapping)

    return vcData, varStore


def sanitizeCoords(coords, baseGlyph):
    # - Ensure that all axes used in the neutral are also used in all variations;
    #   take value from neutral if missing. (fillMissingFromNeutral)
    # - Ensure that all axis tags that are defined for the base glyph are set
    # - Ensure that *only* axis tags that are defined for the base glyph are set
    fillMissingFromNeutral(coords)
    baseAxisTags = {
        axisTag
        for baseVar in [baseGlyph] + baseGlyph.variations
        for axisTag in baseVar.location
    }
    for c in coords:
        coordAxisTags = set(c)
        for axisTag in baseAxisTags - coordAxisTags:
            c[axisTag] = 0.0
        for nonExistentTag in coordAxisTags - baseAxisTags:
            del c[nonExistentTag]


def remapValuesDict(valuesDict, mapping):
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
    return {
        glyphName: buildCOLRGlyph(glyphName, components, vcData, axisTagToIndex)
        for glyphName, components in vcData.items()
    }


def buildCOLRGlyph(glyphName, components, vcData, axisTagToIndex):
    assert len(components) > 0
    layers = []

    for baseName, coord, transform in components:
        # We're building the color glyph from leaf to root

        # Final leaf paint
        if baseName in vcData:
            paint = dict(Format=ot.PaintFormat.PaintColrGlyph, Glyph=baseName)
        else:
            paint = dict(
                Format=ot.PaintFormat.PaintSolid,
                Color=dict(PaletteIndex=0xFFFF, Alpha=1.0),
            )
            paint = dict(Format=ot.PaintFormat.PaintGlyph, Glyph=baseName, Paint=paint)

        if coord:
            haveVariations = any(v[1] != 0xFFFFFFFF for v in coord.values())
            fmt = (
                ot.PaintFormat.PaintVarLocation
                if haveVariations
                else ot.PaintFormat.PaintLocation
            )
            coordinateArray = [
                dict(
                    AxisIndex=axisTagToIndex[tag],
                    AxisValue=value if value[1] != 0xFFFFFFFF else value[0],
                )
                for tag, value in coord.items()
            ]
            paint = dict(
                Format=fmt,
                Paint=paint,
            )
            if True:
                # Offset to coord list
                paint["Location"] = dict(
                    Coordinate=coordinateArray,
                )
            else:
                # Inline to coord list
                paint["Coordinate"] = coordinateArray

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
                if haveRotateVar or haveTCenterVar
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


def buildCOLRv1(designspacePath, ttfPath, outTTFPath, saveWoff2, neutralOnly=False):
    import pathlib

    ttfPath = pathlib.Path(ttfPath)
    if outTTFPath is None:
        outTTFPath = ttfPath.parent / (ttfPath.stem + "-colrv1" + ttfPath.suffix)
    else:
        outTTFPath = pathlib.Path(outTTFPath)
    ttf = TTFont(ttfPath)

    axisTags = [axis.axisTag for axis in ttf["fvar"].axes]
    axisTagToIndex = {tag: index for index, tag in enumerate(axisTags)}
    globalAxisNames = {
        axis.axisTag for axis in ttf["fvar"].axes if not axis.flags & 0x0001
    }
    assert globalAxisNames == {axisTag for axisTag in axisTags if axisTag[0] != "V"}

    vcFont = VarCoFont(designspacePath)

    # Update the glyf table to contain bounding boxes for color glyphs
    estimateCOLRv1BoundingBoxes(vcFont, ttf, neutralOnly)

    vcData, varStore = prepareVariableComponentData(
        vcFont, axisTags, globalAxisNames, neutralOnly
    )
    colrGlyphs = buildCOLRGlyphs(vcData, axisTagToIndex)
    ttf["COLR"] = buildCOLR(colrGlyphs, varStore=varStore)

    ttf.save(outTTFPath)

    ttf = TTFont(outTTFPath, lazy=True)  # Load from scratch

    if saveWoff2:
        outWoff2Path = outTTFPath.parent / (outTTFPath.stem + ".woff2")
        ttf.flavor = "woff2"
        ttf.save(outWoff2Path)


def estimateCOLRv1BoundingBoxes(vcFont, ttFont, neutralOnly):
    from fontTools.pens.ttGlyphPen import TTGlyphPointPen
    from fontTools.pens.boundsPen import ControlBoundsPen

    locations = [{}]
    if not neutralOnly:
        for axis in ttFont["fvar"].axes:
            if axis.flags & 0x0001:
                # hidden axis
                continue

            values = {0}
            if axis.minValue < axis.defaultValue:
                values.add(-1)
            if axis.defaultValue < axis.maxValue:
                values.add(1)
            locations = [
                dictUpdate(loc, axis.axisTag, v)
                for loc in locations
                for v in sorted(values)
            ]
    glyfTable = ttFont["glyf"]
    gvarTable = ttFont["gvar"]
    hmtxTable = ttFont["hmtx"]
    # TODO: fix tsb if we have "vmtx"
    for glyphName in sorted(vcFont.keys()):
        glyph = vcFont[glyphName]
        if not glyph.components or not glyph.outline.isEmpty():
            continue

        # calculate the bounding box that would fit on all locations
        bpen = ControlBoundsPen(None)
        for loc in locations:
            vcFont.drawGlyph(bpen, glyphName, loc)
        gvarTable.variations.pop(glyphName, None)
        pen = TTGlyphPointPen(None)
        if bpen.bounds is not None:
            bounds = intRect(bpen.bounds)
            for pt in [bounds[:2], bounds[2:]]:
                pen.beginPath()
                pen.addPoint(pt, segmentType="line")
                pen.endPath()
        glyfTable[glyphName] = pen.glyph()
        adv, lsb = hmtxTable.metrics[glyphName]
        hmtxTable.metrics[glyphName] = adv, bounds[0]


def dictUpdate(d1, axisTag, axisValue):
    d = dict(d1)
    d[axisTag] = axisValue
    return d


def main():
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("designspace", help="The VarCo .designspace source")
    parser.add_argument("ttf", help="The input Variable Font")
    parser.add_argument("--output", help="The output Variable Font")
    parser.add_argument("--no-woff2", action="store_true")
    parser.add_argument(
        "--neutral-only",
        action="store_true",
        help="hack: build a pseudo static COLRv1 table, that won't respond to the "
        "non-hidden axes",
    )
    args = parser.parse_args()
    buildCOLRv1(
        args.designspace, args.ttf, args.output, not args.no_woff2, args.neutral_only
    )


if __name__ == "__main__":
    main()

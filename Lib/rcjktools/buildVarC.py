from fontTools.misc.fixedTools import floatToFixed
from fontTools.ttLib import TTFont, newTable, registerCustomTableClass
from fontTools.varLib.models import VariationModel, allEqual
from fontTools.varLib.varStore import OnlineVarStoreBuilder
from rcjktools.varco import VarCoFont
from rcjktools.table_VarC import (
    fixed2dot14, getToFixedConverterForNumIntBitsForScale, transformToIntConverters,
    transformDefaults, VARIDX_KEY, ComponentRecord, CoordinateRecord, TransformRecord)


def precompileAllComponents(vcData, allLocations, axisTags):
    precompiled = {}
    masterModel = VariationModel(allLocations, axisTags)
    storeBuilder = OnlineVarStoreBuilder(axisTags)
    for gn in vcData.keys():
        components, locations = vcData[gn]
        items = [None] * len(allLocations)
        for loc in locations:
            index = allLocations.index(loc)
            items[index] = True  # anything not None
        subModel, subItems = masterModel.getSubModel(items)
        storeBuilder.setModel(subModel)
        precompiledGlyph = precompileVarComponents(gn, components, storeBuilder, axisTags)
        if precompiledGlyph is not None:
            # glyph components do not contain data that has to go to the 'VarC' table
            precompiled[gn] = precompiledGlyph
    return precompiled, storeBuilder.finish()


def precompileVarComponents(glyphName, components, storeBuilder, axisTags):
    precompiled = []
    haveVarCData = False
    for component in components:
        coordKeys = sorted({k for coord, transform in component for k in coord})
        coordDefaults = {k: 0 for k in coordKeys}
        coordConverters = {k: fixed2dot14 for k in coordKeys}
        dicts = [coord for coord, transform in component]
        coordDict = compileDicts(dicts, coordDefaults, coordConverters, storeBuilder, allowIndividualVarIdx=True)

        dicts = [transform for coord, transform in component]
        transformToIntConvertersLocal = dict(transformToIntConverters)
        numIntBitsForScale = calcNumIntBitsForScale(dicts)
        scaleConvert = getToFixedConverterForNumIntBitsForScale(numIntBitsForScale)
        transformToIntConvertersLocal["ScaleX"] = scaleConvert
        transformToIntConvertersLocal["ScaleY"] = scaleConvert
        transformDict = compileDicts(dicts, transformDefaults, transformToIntConvertersLocal, storeBuilder)
        if coordDict or transformDict:
            haveVarCData = True
        precompiled.append(
            ComponentRecord(
                CoordinateRecord(coordDict),
                TransformRecord(transformDict),
                numIntBitsForScale,
            ),
        )

    if haveVarCData:
        return precompiled
    else:
        return None


def compileDicts(dicts, dictDefaults, dictConverters, storeBuilder, allowIndividualVarIdx=False):
    resultDict = {}
    convertedMasterValues = {}
    hasVariations = False  # True if any key has variations
    for k, default in dictDefaults.items():
        masterValues = [d.get(k, default) for d in dicts]
        if not allEqual(masterValues):
            hasVariations = True
        elif masterValues[0] == default:
            # No variations, value is default, skip altogether
            continue
        resultDict[k] = dict(value=masterValues[0])
        convertedMasterValues[k] = [dictConverters[k](value) for value in masterValues]

    if hasVariations:
        for k, masterValues in convertedMasterValues.items():
            if allowIndividualVarIdx and allEqual(masterValues):  # TODO: Avoid second allEqual() call?
                continue
            base, varIdx = storeBuilder.storeMasters(masterValues)
            assert base == masterValues[0], (k, base, masterValues)
            resultDict[k][VARIDX_KEY] = varIdx
    return resultDict


def calcNumIntBitsForScale(dicts):
    minScale, maxScale = _calcMinMaxScale(dicts)
    numIntBits = _calcNumIntBits(minScale, maxScale)
    return numIntBits


def _calcNumIntBits(minValue, maxValue, maxIntBits=7):
    # TODO: there must be a better way, but at least this is correct
    assert minValue <= maxValue
    for i in range(maxIntBits + 1):
        precisionBits = 16 - i
        minIntVal = floatToFixed(minValue, precisionBits)
        maxIntVal = floatToFixed(maxValue, precisionBits)
        if -32768 <= minIntVal and maxIntVal <= 32767:
            return i
    raise ValueError("value does not fit in maxBits")


def _calcMinMaxScale(transformDicts):
    minScale = 0
    maxScale = 0
    for d in transformDicts:
        minScale = min(minScale, d.get("ScaleX", 0))
        minScale = min(minScale, d.get("ScaleY", 0))
        maxScale = max(maxScale, d.get("ScaleX", 0))
        maxScale = max(maxScale, d.get("ScaleY", 0))
    return minScale, maxScale


def remapVarIdxs(precompiled, mapping):
    for glyphName, components in precompiled.items():
        for component in components:
            for v in component.coord.values():
                if VARIDX_KEY in v:
                    v[VARIDX_KEY] = mapping[v[VARIDX_KEY]]


def buildVarCTable(ttf, vcData, allLocations):
    axisTags = [axis.axisTag for axis in ttf["fvar"].axes]
    varc_table = ttf["VarC"] = newTable("VarC")
    varc_table.Version = 0x00010000
    precompiled, store = precompileAllComponents(vcData, allLocations, axisTags)
    mapping = store.optimize()
    remapVarIdxs(precompiled, mapping)
    varc_table.GlyphData = precompiled
    varc_table.VarStore = store


if __name__ == "__main__":
    import pathlib
    import sys

    registerCustomTableClass("VarC", "rcjktools.table_VarC", "table_VarC")

    ufoPath, ttfPath = sys.argv[1:]
    ttfPath = pathlib.Path(ttfPath)

    ttf = TTFont(ttfPath, lazy=True)

    axisTags = [axis.axisTag for axis in ttf["fvar"].axes]
    globalAxisNames = {axisTag for axisTag in axisTags if axisTag[0] != "V"}
    vcFont = VarCoFont(ufoPath)
    vcData, allLocations = vcFont.extractVarCoData(globalAxisNames)

    buildVarCTable(ttf, vcData, allLocations)

    outTTXPath = ttfPath.parent / (ttfPath.stem + "-varc.ttx")
    refTTXPath = ttfPath.parent / (ttfPath.stem + "-varc-ref.ttx")
    outTTFPath = ttfPath.parent / (ttfPath.stem + "-varc.ttf")
    outWoff2Path = ttfPath.parent / (ttfPath.stem + "-varc.woff2")
    ttf.saveXML(outTTXPath, tables=["VarC"])
    ttf.save(outTTFPath)

    ttf = TTFont(outTTFPath)
    ttf.flavor = "woff2"
    ttf.save(outWoff2Path)
    # varcTable = ttf["VarC"]
    # print(varcTable.GlyphData)
    # ttf.saveXML(refTTXPath, tables=["VarC"])

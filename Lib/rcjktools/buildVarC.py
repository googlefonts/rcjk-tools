from collections import defaultdict
import functools
import itertools
import struct
from typing import NamedTuple
from fontTools.misc.fixedTools import floatToFixed, otRound
from fontTools.ttLib import TTFont
from fontTools.varLib.models import VariationModel, allEqual
from fontTools.varLib.varStore import OnlineVarStoreBuilder
from rcjktools.varco import VarCoFont


VARIDX_KEY = "varIdx"


def degreesToInt(value):
    # Fit the range -360..360 into -32768..32768
    # If angle is outside the range, force it into the range
    if value >= 360:
        # print("warning, angle out of range:", value)
        value %= 360
    elif value <= -360:
        # print("warning, angle out of range:", value)
        value %= -360
    return otRound(0x8000 * value / 360)


fixed2dot14 = functools.partial(floatToFixed, precisionBits=14)
fixed5dot11 = functools.partial(floatToFixed, precisionBits=11)

NUM_INT_BITS_FOR_SCALE_MASK = 0x07
AXIS_INDICES_ARE_WORDS = (1 << 3)
HAS_TRANSFORM_VARIATIONS = (1 << 4)
_FIRST_TRANSFORM_FIELD_BIT = 5

transformFieldNames = ["rotation", "scalex", "scaley", "skewx", "skewy", "tcenterx", "tcentery"]
transformFieldFlags = {
    fieldName: (1 << bitNum)
    for bitNum, fieldName in enumerate(transformFieldNames, _FIRST_TRANSFORM_FIELD_BIT)
}

transformDefaults = {
    # "x": 0,  # handled by gvar
    # "y": 0,  # handled by gvar
    "rotation": 0,
    "scalex": 1,
    "scaley": 1,
    "skewx": 0,
    "skewy": 0,
    "tcenterx": 0,
    "tcentery": 0,
}

transformConverters = {
    # "x": int,  # handled by gvar
    # "y": int,  # handled by gvar
    "rotation": degreesToInt,
    "scalex": None,  # Filled in locally
    "scaley": None,  # Filled in locally
    "skewx": degreesToInt,
    "skewy": degreesToInt,
    "tcenterx": int,
    "tcentery": int,
}


def splitVarIdx(value):
    # outer, inner
    return value >> 16, value & 0xFFFF


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


class PrecompiledComponents(NamedTuple):
    coord: dict
    transform: dict
    numIntBitsForScale: int


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
        transformConvertersLocal = dict(transformConverters)
        numIntBitsForScale, scaleConvert = _calcNumIntBitsForScale(dicts)
        transformConvertersLocal["scalex"] = scaleConvert
        transformConvertersLocal["scaley"] = scaleConvert
        transformDict = compileDicts(dicts, transformDefaults, transformConvertersLocal, storeBuilder)
        if coordDict or transformDict:
            haveVarCData = True
        precompiled.append(PrecompiledComponents(coordDict, transformDict, numIntBitsForScale))

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


def compileComponents(glyphName, precompiledComponents, axisTags, axisTagToIndex):
    data = []
    for component in precompiledComponents:
        flags = component.numIntBitsForScale
        assert flags == flags & NUM_INT_BITS_FOR_SCALE_MASK

        numAxes = len(component.coord)
        coordFlags, coordData, coordVarIdxs = _compileCoords(component.coord, axisTags, axisTagToIndex)
        flags |= coordFlags

        transformFlags, transformData, transformVarIdxs = _compileTransform(component.transform, component.numIntBitsForScale)
        varIdxs = coordVarIdxs + transformVarIdxs
        varIdxFormat, varIdxData = _packVarIdxs(varIdxs)

        if flags & AXIS_INDICES_ARE_WORDS:
            headerFormat = ">HBH"
        else:
            headerFormat = ">HBB"
        componentData = struct.pack(headerFormat, flags, varIdxFormat, numAxes) + coordData + transformData + varIdxData
        data.append(componentData)

    return data


def packArrayUInt8(idxs):
    return packArray("H", idxs)


def packArrayUInt16(idxs):
    return packArray("H", idxs)


def packArrayUInt24(idxs):
    return b"".join(struct.pack(">L", idx)[1:] for idx in idxs)


def packArrayUInt32(idxs):
    return packArray("L", idxs)


def packArray(fmt, values):
    return struct.pack(">" + fmt * len(values), *values)


def _packVarIdxs(varIdxs):
    # Mostly taken from fontTools.ttLib.tables.otTable.VarIdxMap.preWrite()
    ored = 0
    for idx in varIdxs:
        ored |= idx

    inner = ored & 0xFFFF
    innerBits = 0
    while inner:
        innerBits += 1
        inner >>= 1
    innerBits = max(innerBits, 1)
    assert innerBits <= 16

    ored = (ored >> (16-innerBits)) | (ored & ((1 << innerBits)-1))
    if ored <= 0x000000FF:
        entrySize = 1
        packArray = packArrayUInt8
    elif ored <= 0x0000FFFF:
        entrySize = 2
        packArray = packArrayUInt16
    elif ored <= 0x00FFFFFF:
        entrySize = 3
        packArray = packArrayUInt24
    else:
        entrySize = 4
        packArray = packArrayUInt32

    entryFormat = ((entrySize - 1) << 4) | (innerBits - 1)
    outerShift = 16 - innerBits
    varIdxData = packArray([(idx >> outerShift) | (idx & 0xFFFF) for idx in varIdxs])
    return entryFormat, varIdxData


def _compileCoords(coordDict, axisTags, axisTagToIndex):
    coordFlags = 0
    axisIndices = sorted(axisTagToIndex[k] for k in coordDict)
    numAxes = len(axisIndices)
    if numAxes and max(axisIndices) > 127:
        axisIndexFormat = ">" + "H" * numAxes
        coordFlags |= AXIS_INDICES_ARE_WORDS
        maxAxisIndex = 0x7FFF
        hasVarIdxFlag = 0x8000
    else:
        axisIndexFormat = ">" + "B" * numAxes
        maxAxisIndex = 0x7F
        hasVarIdxFlag = 0x80

    coordValues = []
    coordVarIdxs = []
    for i, axisIndex in enumerate(axisIndices):
        assert axisIndex <= maxAxisIndex
        axisName = axisTags[axisIndex]
        valueDict = coordDict[axisName]
        coordValues.append(fixed2dot14(valueDict["value"]))
        if VARIDX_KEY in valueDict:
            coordVarIdxs.append(valueDict[VARIDX_KEY])
            axisIndices[i] |= hasVarIdxFlag

    axisIndicesData = struct.pack(axisIndexFormat, *axisIndices)
    axisValuesData = struct.pack(">" + "h" * numAxes, *coordValues)
    return coordFlags, axisIndicesData + axisValuesData, coordVarIdxs


def _compileTransform(transformDict, numIntBitsForScale):
    transformFlags = 0
    hasTransformVariations = transformDict and VARIDX_KEY in next(iter(transformDict.values()))
    if hasTransformVariations:
        transformFlags |= HAS_TRANSFORM_VARIATIONS

    scaleConverter = _getConverterForNumIntBitsForScale(numIntBitsForScale)

    transformValues = []
    transformVarIdxs = []
    for fieldName in transformFieldNames:
        valueDict = transformDict.get(fieldName)
        if valueDict is None:
            continue
        convert = transformConverters[fieldName]
        if convert is None:
            assert fieldName in {"scalex", "scaley"}
            convert = scaleConverter
        transformFlags |= transformFieldFlags[fieldName]
        transformValues.append(convert(valueDict["value"]))
        if hasTransformVariations:
            transformVarIdxs.append(valueDict[VARIDX_KEY])

    transformData = struct.pack(">" + "h" * len(transformValues), *transformValues)
    return transformFlags, transformData, transformVarIdxs


def _calcNumIntBitsForScale(dicts):
    minScale, maxScale = _calcMinMaxScale(dicts)
    numIntBits = _calcNumIntBits(minScale, maxScale)
    scaleConvert = _getConverterForNumIntBitsForScale(numIntBits)
    return numIntBits, scaleConvert


def _getConverterForNumIntBitsForScale(numIntBits):
    return functools.partial(floatToFixed, precisionBits=16-numIntBits)


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
        minScale = min(minScale, d.get("scalex", 0))
        minScale = min(minScale, d.get("scaley", 0))
        maxScale = max(maxScale, d.get("scalex", 0))
        maxScale = max(maxScale, d.get("scaley", 0))
    return minScale, maxScale


def remapVarIdxs(precompiled, mapping):
    for glyphName, components in precompiled.items():
        for component in components:
            for v in component.coord.values():
                if VARIDX_KEY in v:
                    v[VARIDX_KEY] = mapping[v[VARIDX_KEY]]


def optimizeSharedComponentData(allComponentData):
    sharedComponentDataCounter = defaultdict(int)
    for glyphData in allComponentData.values():
        for compData in glyphData:
            sharedComponentDataCounter[compData] += 1

    sharedComponentData = [
        compData
        for compData, count in sharedComponentDataCounter.items()
        if count > 1
    ]
    sharedComponentDataIndices = {compData: index for index, compData in enumerate(sharedComponentData)}

    for glyphName, glyphData in allComponentData.items():
        newGlyphData = []
        for compData in glyphData:
            sharedIndex = sharedComponentDataIndices.get(compData)
            if sharedIndex is not None:
                if sharedIndex <= 0x3FFF:
                    compData = struct.pack(">H", sharedIndex | 0x8000)
                else:
                    assert sharedIndex <= 0x3FFFFFFF, "index overflow"
                    compData = struct.pack(">L", sharedIndex | 0xC0000000)
            newGlyphData.append(compData)
        allComponentData[glyphName] = newGlyphData

    return sharedComponentData


def compileOffsets(offsets):
    numOffsets = len(offsets)
    assert numOffsets <= 0x3FFFFFFF
    maxOffset = max(offsets)
    if maxOffset <= 0xFF:
        entrySize = 1
        packArray = packArrayUInt8
    elif maxOffset <= 0xFFFF:
        entrySize = 2
        packArray = packArrayUInt16
    elif maxOffset <= 0xFFFFFF:
        entrySize = 3
        packArray = packArrayUInt24
    else:
        entrySize = 4
        packArray = packArrayUInt32
    headerData = struct.pack(">L", ((entrySize - 1) << 30) + numOffsets)
    return headerData + packArray(offsets)


def buildVarCTable(ttf, vcData, allLocations, axisTags):
    precompiled, store = precompileAllComponents(vcData, allLocations, axisTags)
    mapping = store.optimize()
    remapVarIdxs(precompiled, mapping)

    axisTagToIndex = {tag: i for i, tag in enumerate(axisTags)}

    allComponentData = {}
    for gn, components in precompiled.items():
        allComponentData[gn] = compileComponents(gn, components, axisTags, axisTagToIndex)

    beforeCount = sum(len(d) for g in allComponentData.values() for d in g)

    sharedComponentData = optimizeSharedComponentData(allComponentData)
    sharedComponentOffsets = list(itertools.accumulate(len(data) for data in sharedComponentData))
    sharedComponentOffsetsData = compileOffsets(sharedComponentOffsets)

    glyphData = {
        glyphName: b"".join(componentData)
        for glyphName, componentData in allComponentData.items()
    }
    glyphData = [glyphData.get(glyphName, b"") for glyphName in ttf.getGlyphOrder()]
    trailingEmptyCount = 0
    for data in reversed(glyphData):
        if data:
            break
        trailingEmptyCount += 1
    if trailingEmptyCount:
        glyphData = glyphData[:-trailingEmptyCount]
    glyphOffsets = list(itertools.accumulate(len(data) for data in glyphData))
    glyphOffsetsData = compileOffsets(glyphOffsets)


    # VarC table overview:
    # Version
    # SharedComponentDataOffsets
    # GlyphDataOffsets
    # VarStoreOffset
    # SharedComponentData
    # GlyphData
    # VarStoreData

    varcOTData = [

        ('VarC', [
            ('Version', 'Version', None, None, 'Version of the VarC table-initially 0x00010000'),
            ('LOffset', 'SharedComponents', None, None, '...'),
            ('LOffset', 'GlyphData', None, None, '...'),
            ('LOffset', 'VarStore', None, None, 'Offset to variation store (may be NULL)'),
        ]),

        # ('SharedComponents', [
        #     ('uint32', 'SharedComponentsCount', None, None, '...'),
        #     ('LOffset', 'SharedComponents', 'SharedComponentsCount', None, '...'),
        # ]),

        # ('GlyphData', [
        #     ('Index', 'GlyphDataIndex', None, None, '...'),
        #     ('xxx', 'xxx', None, None, '...'),
        # ]),

    ]

    print("index data size:", len(sharedComponentOffsetsData))

    afterCount = sum(len(d) for g in allComponentData.values() for d in g)
    sharedCount = sum(len(d) for d in sharedComponentData)
    print("before:", beforeCount)
    print("after:", afterCount)
    print("shared:", sharedCount)
    print("after + shared:", afterCount + sharedCount)
    print("saved:", beforeCount - (afterCount + sharedCount))
    print("percentage saved:", round(100 * (1 - (afterCount + sharedCount) / beforeCount), 1))
    print("number of shared compo blocks:", len(sharedComponentData))


if __name__ == "__main__":
    import sys

    ufoPath, ttfPath = sys.argv[1:]

    ttf = TTFont(ttfPath, lazy=True)
    axisTags = [axis.axisTag for axis in ttf["fvar"].axes]

    globalAxisNames = {axisTag for axisTag in axisTags if axisTag[0] != "V"}
    vcFont = VarCoFont(ufoPath)
    vcData, allLocations = vcFont.extractVarCoData(globalAxisNames)

    buildVarCTable(ttf, vcData, allLocations, axisTags)

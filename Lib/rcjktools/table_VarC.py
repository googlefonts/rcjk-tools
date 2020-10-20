from collections import defaultdict
import functools
import itertools
import struct
from fontTools.misc.fixedTools import floatToFixed, otRound
from fontTools.ttLib.tables.DefaultTable import DefaultTable


VARIDX_KEY = "varIdx"

NUM_INT_BITS_FOR_SCALE_MASK = 0x07
AXIS_INDICES_ARE_WORDS = (1 << 3)
HAS_TRANSFORM_VARIATIONS = (1 << 4)
_FIRST_TRANSFORM_FIELD_BIT = 5


fixed2dot14 = functools.partial(floatToFixed, precisionBits=14)


transformFieldNames = ["Rotation", "ScaleX", "ScaleY", "SkewX", "SkewY", "TCenterX", "TCenterY"]
transformFieldFlags = {
    fieldName: (1 << bitNum)
    for bitNum, fieldName in enumerate(transformFieldNames, _FIRST_TRANSFORM_FIELD_BIT)
}

transformDefaults = {
    # "x": 0,  # handled by gvar
    # "y": 0,  # handled by gvar
    "Rotation": 0,
    "ScaleX": 1,
    "ScaleY": 1,
    "SkewX": 0,
    "SkewY": 0,
    "TCenterX": 0,
    "TCenterY": 0,
}


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


transformConverters = {
    # "x": int,  # handled by gvar
    # "y": int,  # handled by gvar
    "Rotation": degreesToInt,
    "ScaleX": None,  # Filled in locally
    "ScaleY": None,  # Filled in locally
    "SkewX": degreesToInt,
    "SkewY": degreesToInt,
    "TCenterX": int,
    "TCenterY": int,
}


class table_VarC(DefaultTable):

    def decompile(self, data, ttFont):
        ...

    def compile(self, ttFont):
        axisTags = [axis.axisTag for axis in ttFont["fvar"].axes]
        axisTagToIndex = {tag: i for i, tag in enumerate(axisTags)}

        allComponentData = {}
        for gn, components in self.GlyphData.items():
            allComponentData[gn] = compileComponents(gn, components, axisTags, axisTagToIndex)

        sharedComponentData = optimizeSharedComponentData(allComponentData)
        sharedComponentOffsets = list(itertools.accumulate(len(data) for data in sharedComponentData))
        sharedComponentOffsetsData = compileOffsets(sharedComponentOffsets)

        glyphData = {
            glyphName: b"".join(componentData)
            for glyphName, componentData in allComponentData.items()
        }
        glyphData = [glyphData.get(glyphName, b"") for glyphName in ttFont.getGlyphOrder()]
        trailingEmptyCount = 0
        for data in reversed(glyphData):
            if data:
                break
            trailingEmptyCount += 1
        if trailingEmptyCount:
            glyphData = glyphData[:-trailingEmptyCount]
        glyphOffsets = list(itertools.accumulate(len(data) for data in glyphData))
        glyphOffsetsData = compileOffsets(glyphOffsets)

        # writer = OTTableWriter()
        # writer.writeULong(0x00010000)

        # sharedComponentsWriter = writer.getSubWriter()
        # sharedComponentsWriter.longOffset = True
        # writer.writeSubTable(sharedComponentsWriter)

        # glyphDataWriter = writer.getSubWriter()
        # glyphDataWriter.longOffset = True
        # writer.writeSubTable(glyphDataWriter)

        # varStoreWriter = writer.getSubWriter()
        # varStoreWriter.longOffset = True
        # writer.writeSubTable(varStoreWriter)
        # store.compile(varStoreWriter, ttf)

        # VarC table overview:
        # Version
        # SharedComponentDataOffsets
        # GlyphDataOffsets
        # VarStoreOffset
        # SharedComponentData
        # GlyphData
        # VarStoreData

        # varcOTData = [

        #     ('VarC', [
        #         ('Version', 'Version', None, None, 'Version of the VarC table-initially 0x00010000'),
        #         ('LOffset', 'SharedComponents', None, None, ''),
        #         ('LOffset', 'GlyphData', None, None, ''),
        #         ('LOffset', 'VarStore', None, None, 'Offset to variation store (may be NULL)'),
        #     ]),

        #     ('SharedComponents', [
        #         ('LOffset', 'SharedComponentsIndex', None, None, ''),
        #         ('LOffset', 'SharedComponentsX', None, None, ''),
        #     ]),

        #     ('GlyphData', [
        #         ('LOffset', 'GlyphDataIndex', None, None, ''),
        #         ('LOffset', 'GlyphDataX', None, None, ''),
        #     ]),

        # ]

        # print("index data size:", len(sharedComponentOffsetsData))

        # afterCount = sum(len(d) for g in allComponentData.values() for d in g)
        # sharedCount = sum(len(d) for d in sharedComponentData)
        # print("before:", beforeCount)
        # print("after:", afterCount)
        # print("shared:", sharedCount)
        # print("after + shared:", afterCount + sharedCount)
        # print("saved:", beforeCount - (afterCount + sharedCount))
        # print("percentage saved:", round(100 * (1 - (afterCount + sharedCount) / beforeCount), 1))
        # print("number of shared compo blocks:", len(sharedComponentData))

    def toXML(self, writer, ttFont, **kwargs):
        glyphTable = ttFont["glyf"]
        writer.simpletag("Version", [("value", f"0x{self.Version:08X}")])
        writer.newline()

        writer.begintag("GlyphData")
        writer.newline()
        for glyphName, glyphData in sorted(self.GlyphData.items()):
            try:
                glyfGlyph = glyphTable[glyphName]
            except KeyError:
                print(f"WARNING: glyph {glyphName} does not exist in the VF, skipping")
                continue
            if not glyfGlyph.isComposite():
                print(f"WARNING: glyph {glyphName} is not a composite in the VF, skipping")
                continue
            assert len(glyfGlyph.components) == len(glyphData)  # TODO: Proper error
            writer.begintag("Glyph", [("name", glyphName)])
            writer.newline()
            for index, (varcComponent, glyfComponent) in enumerate(zip(glyphData, glyfGlyph.components)):
                writer.begintag("Component", [("numIntBitsForScale", varcComponent.numIntBitsForScale)])
                writer.newline()
                writer.comment(f"component index: {index}; "
                               f"base glyph: {glyfComponent.glyphName}; "
                               f"offset: ({glyfComponent.x},{glyfComponent.y})")
                writer.newline()

                for axisName, valueDict in sorted(varcComponent.coord.items()):
                    attrs = [("axis", axisName), ("value", valueDict["value"])]
                    if "varIdx" in valueDict:
                        outer, inner = splitVarIdx(valueDict["varIdx"])
                        attrs.extend([("outer", outer), ("inner", inner)])
                    writer.simpletag("Coord", attrs)
                    writer.newline()

                for transformFieldName, valueDict in sorted(varcComponent.transform.items()):
                    attrs = [("value", valueDict["value"])]
                    if "varIdx" in valueDict:
                        outer, inner = splitVarIdx(valueDict["varIdx"])
                        attrs.extend([("outer", outer), ("inner", inner)])
                    writer.simpletag(transformFieldName, attrs)
                    writer.newline()

                writer.endtag("Component")
                writer.newline()
            writer.endtag("Glyph")
            writer.newline()
        writer.endtag("GlyphData")
        writer.newline()

        if hasattr(self, "VarStore"):
            self.VarStore.toXML(writer, ttFont)

    def fromXML(self, name, attrs, content, ttFont):
        ...


def splitVarIdx(value):
    # outer, inner
    return value >> 16, value & 0xFFFF


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

    scaleConverter = getConverterForNumIntBitsForScale(numIntBitsForScale)

    transformValues = []
    transformVarIdxs = []
    for fieldName in transformFieldNames:
        valueDict = transformDict.get(fieldName)
        if valueDict is None:
            continue
        convert = transformConverters[fieldName]
        if convert is None:
            assert fieldName in {"ScaleX", "ScaleY"}
            convert = scaleConverter
        transformFlags |= transformFieldFlags[fieldName]
        transformValues.append(convert(valueDict["value"]))
        if hasTransformVariations:
            transformVarIdxs.append(valueDict[VARIDX_KEY])

    transformData = struct.pack(">" + "h" * len(transformValues), *transformValues)
    return transformFlags, transformData, transformVarIdxs


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


def getConverterForNumIntBitsForScale(numIntBits):
    return functools.partial(floatToFixed, precisionBits=16-numIntBits)

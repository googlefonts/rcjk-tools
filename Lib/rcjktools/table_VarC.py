from collections import defaultdict
import functools
import itertools
import struct
from typing import NamedTuple
from fontTools.misc.fixedTools import fixedToFloat, floatToFixed, otRound
from fontTools.ttLib.tables.DefaultTable import DefaultTable
from fontTools.ttLib.tables.otConverters import OTTableReader, OTTableWriter
from fontTools.ttLib.tables.otTables import VarStore


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


def intToDegrees(value):
    return value * 360 / 0x8000


transformToIntConverters = {
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


transformFromIntConverters = {
    # "x": int,  # handled by gvar
    # "y": int,  # handled by gvar
    "Rotation": intToDegrees,
    "ScaleX": None,  # Filled in locally
    "ScaleY": None,  # Filled in locally
    "SkewX": intToDegrees,
    "SkewY": intToDegrees,
    "TCenterX": int,
    "TCenterY": int,
}


class CoordinateRecord(dict):
    pass


class TransformRecord(dict):
    pass


class ComponentRecord(NamedTuple):
    coord: CoordinateRecord
    transform: TransformRecord
    numIntBitsForScale: int


def _getSubWriter(writer):
    subWriter = writer.getSubWriter()
    subWriter.longOffset = True
    writer.writeSubTable(subWriter)
    return subWriter


class table_VarC(DefaultTable):

    def decompile(self, data, ttFont):
        axisTags = [axis.axisTag for axis in ttFont["fvar"].axes]

        reader = OTTableReader(data)
        self.Version = reader.readULong()
        if self.Version != 0x00010000:
            raise ValueError(f"unknown VarC.Version: {self.Version:08X}")

        sharedComponentOffsetsOffset = reader.readULong()
        if sharedComponentOffsetsOffset:
            sub = reader.getSubReader(sharedComponentOffsetsOffset)
            sharedComponentOffsets = decompileOffsets(sub)

            sub = reader.getSubReader(reader.readULong())
            sharedComponents = decompileSharedComponents(sub, sharedComponentOffsets, axisTags)
        else:
            nullOffset = reader.readULong()
            assert nullOffset == 0x00000000
            sharedComponents = []

        sub = reader.getSubReader(reader.readULong())
        glyphOffsets = decompileOffsets(sub)

        sub = reader.getSubReader(reader.readULong())
        self.GlyphData = decompileGlyphData(ttFont, sub, glyphOffsets, sharedComponents, axisTags)

        varStoreOffset = reader.readULong()
        if varStoreOffset:
            self.VarStore = VarStore()
            self.VarStore.decompile(reader.getSubReader(varStoreOffset), ttFont)
        else:
            self.VarStore = None


    def compile(self, ttFont):
        axisTags = [axis.axisTag for axis in ttFont["fvar"].axes]
        axisTagToIndex = {tag: i for i, tag in enumerate(axisTags)}

        allComponentData = {}
        for gn, components in self.GlyphData.items():
            allComponentData[gn] = compileComponents(gn, components, axisTags, axisTagToIndex)

        sharedComponentData = optimizeSharedComponentData(allComponentData)
        sharedComponentOffsets = list(itertools.accumulate(len(data) for data in sharedComponentData))

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

        writer = OTTableWriter()
        assert self.Version == 0x00010000
        writer.writeULong(self.Version)

        if sharedComponentOffsets:
            sub = _getSubWriter(writer)
            sub.writeData(compileOffsets(sharedComponentOffsets))

            sub = _getSubWriter(writer)
            sub.writeData(b"".join(sharedComponentData))
        else:
            writer.writeULong(0x00000000)
            writer.writeULong(0x00000000)

        sub = _getSubWriter(writer)
        sub.writeData(compileOffsets(glyphOffsets))

        sub = _getSubWriter(writer)
        sub.writeData(b"".join(glyphData))

        sub = _getSubWriter(writer)
        self.VarStore.compile(sub, ttFont)

        return writer.getAllData()

    def toXML(self, writer, ttFont, **kwargs):
        glyfTable = ttFont["glyf"]
        writer.simpletag("Version", [("value", f"0x{self.Version:08X}")])
        writer.newline()

        writer.begintag("GlyphData")
        writer.newline()
        for glyphName, glyphData in sorted(self.GlyphData.items()):
            try:
                glyfGlyph = glyfTable[glyphName]
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
        flags |= transformFlags
        varIdxs = coordVarIdxs + transformVarIdxs
        varIdxFormat, varIdxData = compileVarIdxs(varIdxs)

        # refVarIdxs = decompileVarIdxs(OTTableReader(varIdxData), varIdxFormat, len(varIdxs))
        # assert varIdxs == refVarIdxs, (varIdxs, refVarIdxs)

        if flags & AXIS_INDICES_ARE_WORDS:
            headerFormat = ">HBH"
        else:
            headerFormat = ">HBB"
        componentData = struct.pack(headerFormat, flags, varIdxFormat, numAxes) + coordData + transformData + varIdxData

        # testCompo = decompileComponent(OTTableReader(componentData), None, axisTags)
        # if component != testCompo:
        #     print("??? 1", component)
        #     print("??? 2", testCompo)

        data.append(componentData)

    return data


def packArrayUInt8(idxs):
    return packArray("B", idxs)


def packArrayUInt16(idxs):
    return packArray("H", idxs)


def packArrayUInt24(idxs):
    return b"".join(struct.pack(">L", idx)[1:] for idx in idxs)


def packArrayUInt32(idxs):
    return packArray("L", idxs)


def packArray(fmt, values):
    return struct.pack(">" + fmt * len(values), *values)


def compileVarIdxs(varIdxs):
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
    innerMask = (1 << innerBits) - 1
    outerMask = 0xFFFFFFFF - innerMask

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
    varIdxData = packArray([((idx & outerMask) >> outerShift) | (idx & innerMask) for idx in varIdxs])
    assert len(varIdxData) == entrySize * len(varIdxs)
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

    scaleConverter = getToFixedConverterForNumIntBitsForScale(numIntBitsForScale)

    transformValues = []
    transformVarIdxs = []
    for fieldName in transformFieldNames:
        valueDict = transformDict.get(fieldName)
        if valueDict is None:
            continue
        convert = transformToIntConverters[fieldName]
        if convert is None:
            assert fieldName in {"ScaleX", "ScaleY"}
            convert = scaleConverter
        transformFlags |= transformFieldFlags[fieldName]
        transformValues.append(convert(valueDict["value"]))
        if hasTransformVariations:
            transformVarIdxs.append(valueDict[VARIDX_KEY])

    transformData = struct.pack(">" + "h" * len(transformValues), *transformValues)
    return transformFlags, transformData, transformVarIdxs


def decompileComponent(reader, sharedComponents, axisTags):
    flags = reader.readUShort()
    if flags & 0x8000:
        # component is shared
        if flags & 0x4000:
            index = ((flags & 0x3FFF) << 16) + reader.readUShort()
        else:
            index = flags & 0x3FFF
        return sharedComponents[index]

    numIntBitsForScale = flags & NUM_INT_BITS_FOR_SCALE_MASK
    scaleConverter = getToFloatConverterForNumIntBitsForScale(numIntBitsForScale)
    varIdxFormat = reader.readUInt8()

    if flags & AXIS_INDICES_ARE_WORDS:
        numAxes = reader.readUShort()
        axisIndices = reader.readArray("H", 2, numAxes)
        hasVarIdxFlag = 0x8000
        axisIndexMask = 0xFFFF - hasVarIdxFlag
    else:
        numAxes = reader.readUInt8()
        axisIndices = reader.readArray("B", 1, numAxes)
        hasVarIdxFlag = 0x80
        axisIndexMask = 0xFF - hasVarIdxFlag

    axisHasVarIdx = [bool(axisIndex & hasVarIdxFlag) for axisIndex in axisIndices]
    axisIndices = [axisIndex & axisIndexMask for axisIndex in axisIndices]

    coord = [
        (axisTags[i], dict(value=fixedToFloat(reader.readShort(), 14)))
        for i in axisIndices
    ]
    numVarIdxs = sum(axisHasVarIdx)

    transform = []
    for fieldName, mask in transformFieldFlags.items():
        if not (flags & mask):
            continue
        value = reader.readShort()

        convert = transformFromIntConverters[fieldName]
        if convert is None:
            assert fieldName in {"ScaleX", "ScaleY"}
            convert = scaleConverter
        transform.append((fieldName, dict(value=convert(value))))

    if flags & HAS_TRANSFORM_VARIATIONS:
        numVarIdxs += len(transform)

    varIdxs = decompileVarIdxs(reader, varIdxFormat, numVarIdxs)
    assert len(axisHasVarIdx) == len(coord)
    for hasVarIdx, (axisTag, valueDict) in zip(axisHasVarIdx, coord):
        if hasVarIdx:
            valueDict[VARIDX_KEY] = varIdxs.pop(0)

    if flags & HAS_TRANSFORM_VARIATIONS:
        for fieldName, valueDict in transform:
            valueDict[VARIDX_KEY] = varIdxs.pop(0)

    assert not varIdxs

    return ComponentRecord(dict(coord), dict(transform), numIntBitsForScale)


def decompileVarIdxs(reader, entryFormat, count):
    innerBits = (entryFormat & 0x0F) + 1
    entrySize = (entryFormat >> 4) + 1
    innerMask = (1 << innerBits) - 1
    outerMask = 0xFFFFFFFF - innerMask
    outerShift = 16 - innerBits
    if entrySize == 1:
        varIdxs = reader.readArray("B", 1, count)
    elif entrySize == 2:
        varIdxs = reader.readArray("H", 2, count)
    elif entrySize == 3:
        varIdxs = [reader.readUInt24() for i in range(count)]
    elif entrySize == 4:
        varIdxs = reader.readArray("L", 3, count)
    else:
        assert False, "oops"
    varIdxs = [(varIdx & innerMask) + ((varIdx & outerMask) << outerShift) for varIdx in varIdxs]
    return varIdxs


def decompileSharedComponents(reader, sharedComponentOffsets, axisTags):
    absPos = reader.pos
    components = []
    prevOffset = 0
    for nextOffset in sharedComponentOffsets:
        components.append(decompileComponent(reader, None, axisTags))
        assert (nextOffset + absPos) == reader.pos, (nextOffset + absPos, reader.pos, nextOffset - prevOffset)
        prevOffset = nextOffset
    return components


def decompileGlyphData(ttFont, reader, glyphOffsets, sharedComponents, axisTags):
    glyfTable = ttFont["glyf"]
    glyphOrder = ttFont.getGlyphOrder()
    glyphData = {}
    prevOffset = 0
    for glyphID, nextOffset in enumerate(glyphOffsets):
        if nextOffset - prevOffset:
            glyphName = glyphOrder[glyphID]
            glyfGlyph = glyfTable[glyphName]
            assert glyfGlyph.isComposite()
            numComponents = len(glyfGlyph.components)
            components = []
            for i in range(numComponents):
                components.append(decompileComponent(reader, sharedComponents, axisTags))
            glyphData[glyphName] = components
        prevOffset = nextOffset
    return glyphData


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
    x = packArray(offsets)
    return headerData + x


def decompileOffsets(reader):
    headerData = reader.readULong()
    numOffsets = headerData & 0x3FFFFFFF
    entrySize = (headerData >> 30) + 1

    if entrySize == 1:
        offsets = reader.readArray("B", 1, numOffsets)
    elif entrySize == 2:
        offsets = reader.readArray("H", 2, numOffsets)
    elif entrySize == 3:
        offsets = [reader.readUInt24() for i in range(numOffsets)]
    elif entrySize == 4:
        offsets = reader.readArray("L", 3, numOffsets)
    else:
        assert False, "oops"
    return offsets


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


def getToFixedConverterForNumIntBitsForScale(numIntBits):
    return functools.partial(floatToFixed, precisionBits=16-numIntBits)


def getToFloatConverterForNumIntBitsForScale(numIntBits):
    return functools.partial(fixedToFloat, precisionBits=16-numIntBits)
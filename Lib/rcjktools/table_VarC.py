import functools
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
    "ScaleY": 1,  # TODO: figure out how to make ScaleY default to ScaleX
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
        glyfTable = ttFont["glyf"]

        reader = OTTableReader(data)
        self.Version = reader.readULong()
        if self.Version != 0x00010000:
            raise ValueError(f"unknown VarC.Version: {self.Version:08X}")

        self.GlyphData = {}
        glyphOrder = ttFont.getGlyphOrder()
        numGlyphs = reader.readUShort()
        for glyphID in range(numGlyphs):
            glyphOffset = reader.readULong()
            if glyphOffset:
                sub = reader.getSubReader(glyphOffset)
                glyphName = glyphOrder[glyphID]
                glyfGlyph = glyfTable[glyphName]
                self.GlyphData[glyphName] = decompileGlyph(sub, glyfGlyph, axisTags)

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

        writer = OTTableWriter()
        assert self.Version == 0x00010000
        writer.writeULong(self.Version)
        numGlyphs = len(glyphData)
        writer.writeUShort(numGlyphs)  # numGlyphs <= maxp.numGlyphs
        for glyph in glyphData:
            if glyph:
                sub = _getSubWriter(writer)
                sub.writeData(glyph)
            else:
                writer.writeULong(0x00000000)

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


def compileComponents(glyphName, components, axisTags, axisTagToIndex):
    data = []
    for component in components:
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


def decompileComponent(reader, axisTags):
    flags = reader.readUShort()

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
        varIdxs = reader.readArray("I", 4, count)
    else:
        assert False, "oops"
    varIdxs = [(varIdx & innerMask) + ((varIdx & outerMask) << outerShift) for varIdx in varIdxs]
    return varIdxs


def decompileGlyph(reader, glyfGlyph, axisTags):
    assert glyfGlyph.isComposite()
    numComponents = len(glyfGlyph.components)
    components = []
    for i in range(numComponents):
        components.append(decompileComponent(reader, axisTags))
    return components


def compileOffsets(offsets):
    headerData = struct.pack(">L", len(offsets))
    return headerData + packArrayUInt32(offsets)


def decompileOffsets(reader):
    numOffsets = reader.readULong()
    return reader.readArray("I", 4, numOffsets)


def getToFixedConverterForNumIntBitsForScale(numIntBits):
    return functools.partial(floatToFixed, precisionBits=16-numIntBits)


def getToFloatConverterForNumIntBitsForScale(numIntBits):
    return functools.partial(fixedToFloat, precisionBits=16-numIntBits)

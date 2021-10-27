from ast import literal_eval
from collections import UserDict
import functools
import struct
from typing import NamedTuple
from fontTools.misc.fixedTools import (
    fixedToFloat,
    floatToFixed,
    floatToFixedToStr,
    otRound,
    strToFixedToFloat,
)
from fontTools.ttLib.tables.DefaultTable import DefaultTable
from fontTools.ttLib.tables.otTables import VarStore


VARIDX_KEY = "varIdx"

NUM_INT_BITS_FOR_SCALE_MASK = 0x07
AXIS_INDICES_ARE_WORDS = 1 << 3
HAS_TRANSFORM_VARIATIONS = 1 << 4
_FIRST_TRANSFORM_FIELD_BIT = 5

COORD_PRECISIONBITS = 12
fixedCoord = functools.partial(floatToFixed, precisionBits=COORD_PRECISIONBITS)
strToFixedCoordToFloat = functools.partial(
    strToFixedToFloat, precisionBits=COORD_PRECISIONBITS
)


transformFieldNames = [
    "Rotation",
    "ScaleX",
    "ScaleY",
    "SkewX",
    "SkewY",
    "TCenterX",
    "TCenterY",
]
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


DEGREES_SCALE = 0x8000 / (4 * 360)


def degreesToInt(value):
    # Fit the range -360..360 into -32768..32768
    # If angle is outside the range, force it into the range
    if value >= 360:
        # print("warning, angle out of range:", value)
        value %= 360
    elif value <= -360:
        # print("warning, angle out of range:", value)
        value %= -360
    return otRound(value * DEGREES_SCALE)


def degreestToIntToStr(value):
    # Mostly taken from fixedToStr()
    if not value:
        return "0.0"
    value = degreesToInt(value) / DEGREES_SCALE
    eps = 0.5 / DEGREES_SCALE
    lo = value - eps
    hi = value + eps
    # If the range of valid choices spans an integer, return the integer.
    if int(lo) != int(hi):
        return str(float(round(value)))
    fmt = "%.8f"
    lo = fmt % lo
    hi = fmt % hi
    assert len(lo) == len(hi) and lo != hi
    for i in range(len(lo)):
        if lo[i] != hi[i]:
            break
    period = lo.find(".")
    assert period < i
    fmt = "%%.%df" % (i - period)
    return fmt % value


def intToDegrees(value):
    return value / DEGREES_SCALE


def strToIntToDegrees(value):
    return intToDegrees(degreesToInt(float(value)))


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


class _LazyGlyphData(UserDict):
    def __init__(self, reader, glyfTable, axisTags):
        super().__init__()
        self.reader = reader
        self.glyfTable = glyfTable
        self.axisTags = axisTags

    def __getitem__(self, glyphName):
        item = super().__getitem__(glyphName)
        if isinstance(item, int):
            glyphOffset = item
            glyfGlyph = self.glyfTable[glyphName]
            sub = self.reader.getSubReader(glyphOffset)
            item = self[glyphName] = decompileGlyph(sub, glyfGlyph, self.axisTags)
        return item


class table_VarC(DefaultTable):
    def decompile(self, data, ttFont):
        from fontTools.ttLib.tables.otConverters import OTTableReader

        axisTags = [axis.axisTag for axis in ttFont["fvar"].axes]
        glyfTable = ttFont["glyf"]

        reader = OTTableReader(data)
        self.Version = reader.readULong()
        if self.Version != 0x00010000:
            raise ValueError(f"unknown VarC.Version: {self.Version:08X}")

        self.GlyphData = _LazyGlyphData(reader, glyfTable, axisTags)
        glyphOrder = ttFont.getGlyphOrder()

        numGlyphs = reader.readUShort()
        for glyphID in range(numGlyphs):
            glyphOffset = reader.readULong()
            if glyphOffset:
                glyphName = glyphOrder[glyphID]
                self.GlyphData[glyphName] = glyphOffset

        varStoreOffset = reader.readULong()
        if varStoreOffset:
            self.VarStore = VarStore()
            self.VarStore.decompile(reader.getSubReader(varStoreOffset), ttFont)
        else:
            self.VarStore = None

    def compile(self, ttFont):
        from fontTools.ttLib.tables.otConverters import OTTableWriter

        axisTags = [axis.axisTag for axis in ttFont["fvar"].axes]
        axisTagToIndex = {tag: i for i, tag in enumerate(axisTags)}
        glyfTable = ttFont["glyf"]

        writer = OTTableWriter()
        assert self.Version == 0x00010000
        writer.writeULong(self.Version)

        glyphData = self.GlyphData
        glyphOrder = ttFont.getGlyphOrder()

        numGlyphs = 0
        for glyphID, glyphName in enumerate(glyphOrder):
            if glyphName in glyphData:
                numGlyphs = max(numGlyphs, glyphID + 1)

        writer.writeUShort(numGlyphs)  # numGlyphs <= maxp.numGlyphs
        for glyphID in range(numGlyphs):
            glyphName = glyphOrder[glyphID]
            components = glyphData.get(glyphName)
            if components:
                glyfGlyph = glyfTable[glyphName]
                assert glyfGlyph.isComposite()
                assert len(components) == len(glyfGlyph.components), (
                    glyphName,
                    len(components),
                    len(glyfGlyph.components),
                )
                sub = _getSubWriter(writer)
                compileGlyph(sub, components, axisTags, axisTagToIndex)
            else:
                writer.writeULong(0x00000000)

        if self.VarStore is not None:
            sub = _getSubWriter(writer)
            self.VarStore.compile(sub, ttFont)
        else:
            writer.writeULong(0x00000000)

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
                print(
                    f"WARNING: glyph {glyphName} is not a composite in the VF, skipping"
                )
                continue
            assert len(glyfGlyph.components) == len(glyphData)  # TODO: Proper error
            writer.begintag("Glyph", [("name", glyphName)])
            writer.newline()
            for index, (varcComponent, glyfComponent) in enumerate(
                zip(glyphData, glyfGlyph.components)
            ):
                writer.begintag(
                    "Component",
                    [("numIntBitsForScale", varcComponent.numIntBitsForScale)],
                )
                writer.newline()
                writer.comment(
                    f"component index: {index}; "
                    f"base glyph: {glyfComponent.glyphName}; "
                    f"offset: ({glyfComponent.x},{glyfComponent.y})"
                )
                writer.newline()

                for axisName, valueDict in sorted(varcComponent.coord.items()):
                    attrs = [
                        ("axis", axisName),
                        (
                            "value",
                            floatToFixedToStr(valueDict["value"], COORD_PRECISIONBITS),
                        ),
                    ]
                    if "varIdx" in valueDict:
                        outer, inner = splitVarIdx(valueDict["varIdx"])
                        attrs.extend([("outer", outer), ("inner", inner)])
                    writer.simpletag("Coord", attrs)
                    writer.newline()

                scalePrecisionBits = 16 - varcComponent.numIntBitsForScale

                for transformFieldName, valueDict in sorted(
                    varcComponent.transform.items()
                ):
                    value = valueDict["value"]
                    if transformFieldName in {"ScaleX", "ScaleY"}:
                        value = floatToFixedToStr(value, scalePrecisionBits)
                    elif transformFieldName in {"Rotation", "SkewX", "SkewY"}:
                        value = degreestToIntToStr(value)
                    attrs = [("value", value)]
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

        if hasattr(self, "VarStore") and self.VarStore is not None:
            self.VarStore.toXML(writer, ttFont)

    def fromXML(self, name, attrs, content, ttFont):
        if name == "Version":
            self.Version = literal_eval(attrs["value"])
        elif name == "GlyphData":
            self.GlyphData = {}
            for name, attrs, content in _filterContent(content):
                glyphName = attrs["name"]
                self.GlyphData[glyphName] = _glyph_fromXML(name, attrs, content, ttFont)
        elif name == "VarStore":
            self.VarStore = VarStore()
            for name, attrs, content in _filterContent(content):
                self.VarStore.fromXML(name, attrs, content, ttFont)
        else:
            assert False, f"Unknown VarC sub-element {name}"


def _glyph_fromXML(name, attrs, content, ttFont):
    assert name == "Glyph"
    components = []
    for name, attrs, content in _filterContent(content):
        components.append(_component_fromXML(name, attrs, content, ttFont))
    return components


def _component_fromXML(name, attrs, content, ttFont):
    assert name == "Component"
    numIntBitsForScale = literal_eval(attrs["numIntBitsForScale"])
    scaleConverter = functools.partial(
        strToFixedToFloat, precisionBits=16 - numIntBitsForScale
    )
    coord = dict()
    transform = dict()
    for name, attrs, content in _filterContent(content):
        if name == "Coord":
            coord[attrs["axis"]] = _makeValueDict(attrs, strToFixedCoordToFloat)
        else:
            if name in {"ScaleX", "ScaleY"}:
                converter = scaleConverter
            elif name in {"Rotation", "SkewX", "SkewY"}:
                converter = strToIntToDegrees
            else:
                converter = None
            transform[name] = _makeValueDict(attrs, converter)
    return ComponentRecord(coord, transform, numIntBitsForScale)


def _makeValueDict(attrs, converter=None):
    value = attrs["value"]
    if converter is not None:
        value = converter(value)
    else:
        value = literal_eval(value)
    valueDict = dict(value=value)
    if "outer" in attrs:
        outer = literal_eval(attrs["outer"])
        inner = literal_eval(attrs["inner"])
        varIdx = (outer << 16) | inner
        valueDict[VARIDX_KEY] = varIdx
    return valueDict


def _filterContent(content):
    return [item for item in content if isinstance(item, tuple)]


def splitVarIdx(value):
    # outer, inner
    return value >> 16, value & 0xFFFF


# Compile


def compileGlyph(writer, components, axisTags, axisTagToIndex):
    for component in components:
        compileComponent(writer, component, axisTags, axisTagToIndex)


def compileComponent(writer, component, axisTags, axisTagToIndex):
    flags = component.numIntBitsForScale
    assert flags == flags & NUM_INT_BITS_FOR_SCALE_MASK

    numAxes = len(component.coord)
    coordFlags, coordData, coordVarIdxs = _compileCoords(
        component.coord, axisTags, axisTagToIndex
    )
    flags |= coordFlags

    transformFlags, transformData, transformVarIdxs = _compileTransform(
        component.transform, component.numIntBitsForScale
    )
    flags |= transformFlags
    varIdxs = coordVarIdxs + transformVarIdxs

    writer.writeUShort(flags)
    if flags & AXIS_INDICES_ARE_WORDS:
        writer.writeUShort(numAxes)
    else:
        writer.writeUInt8(numAxes)

    writer.writeData(coordData)
    writer.writeData(transformData)
    compileVarIdxs(writer, varIdxs)


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
        coordValues.append(fixedCoord(valueDict["value"]))
        if VARIDX_KEY in valueDict:
            coordVarIdxs.append(valueDict[VARIDX_KEY])
            axisIndices[i] |= hasVarIdxFlag

    axisIndicesData = struct.pack(axisIndexFormat, *axisIndices)
    axisValuesData = struct.pack(">" + "h" * numAxes, *coordValues)
    return coordFlags, axisIndicesData + axisValuesData, coordVarIdxs


def _compileTransform(transformDict, numIntBitsForScale):
    transformFlags = 0
    hasTransformVariations = transformDict and VARIDX_KEY in next(
        iter(transformDict.values())
    )
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


def compileVarIdxs(writer, varIdxs):
    # Mostly taken from fontTools.ttLib.tables.otTables.VarIdxMap.preWrite()
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

    ored = (ored >> (16 - innerBits)) | (ored & ((1 << innerBits) - 1))
    if ored <= 0x000000FF:
        entrySize = 1
        write = writer.writeUInt8
    elif ored <= 0x0000FFFF:
        entrySize = 2
        write = writer.writeUShort
    elif ored <= 0x00FFFFFF:
        entrySize = 3
        write = writer.writeUInt24
    else:
        entrySize = 4
        write = writer.writeULong

    entryFormat = ((entrySize - 1) << 4) | (innerBits - 1)
    writer.writeUInt8(entryFormat)
    outerShift = 16 - innerBits
    varIdxInts = [
        ((idx & outerMask) >> outerShift) | (idx & innerMask) for idx in varIdxs
    ]
    for value in varIdxInts:
        write(value)


# Decompile


def decompileGlyph(reader, glyfGlyph, axisTags):
    assert glyfGlyph.isComposite()
    numComponents = len(glyfGlyph.components)
    components = []
    for i in range(numComponents):
        components.append(decompileComponent(reader, axisTags))
    return components


def decompileComponent(reader, axisTags):
    flags = reader.readUShort()

    numIntBitsForScale = flags & NUM_INT_BITS_FOR_SCALE_MASK
    scaleConverter = getToFloatConverterForNumIntBitsForScale(numIntBitsForScale)

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
        (axisTags[i], dict(value=fixedToFloat(reader.readShort(), COORD_PRECISIONBITS)))
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

    varIdxs = decompileVarIdxs(reader, numVarIdxs)
    assert len(axisHasVarIdx) == len(coord)
    for hasVarIdx, (axisTag, valueDict) in zip(axisHasVarIdx, coord):
        if hasVarIdx:
            valueDict[VARIDX_KEY] = varIdxs.pop(0)

    if flags & HAS_TRANSFORM_VARIATIONS:
        for fieldName, valueDict in transform:
            valueDict[VARIDX_KEY] = varIdxs.pop(0)

    assert not varIdxs

    return ComponentRecord(dict(coord), dict(transform), numIntBitsForScale)


def decompileVarIdxs(reader, count):
    entryFormat = reader.readUInt8()
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
    varIdxs = [
        (varIdx & innerMask) + ((varIdx & outerMask) << outerShift)
        for varIdx in varIdxs
    ]
    return varIdxs


# Helpers


def getToFixedConverterForNumIntBitsForScale(numIntBits):
    return functools.partial(floatToFixed, precisionBits=16 - numIntBits)


def getToFloatConverterForNumIntBitsForScale(numIntBits):
    return functools.partial(fixedToFloat, precisionBits=16 - numIntBits)

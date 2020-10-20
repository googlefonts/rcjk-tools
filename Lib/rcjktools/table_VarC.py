from fontTools.ttLib.tables.DefaultTable import DefaultTable


class table_VarC(DefaultTable):

    def decompile(self, data, ttFont):
        ...

    def compile(self, ttFont):
        ...
        raise NotImplementedError()

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

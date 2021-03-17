from ufo2ft.filters import BaseFilter


class AddBaseGlyphsFilter(BaseFilter):
    def filter(self, glyph):
        glyphSet = self.context.glyphSet
        for component in glyph.components:
            if (
                component.baseGlyph not in glyphSet
                and component.baseGlyph in self.context.font
            ):
                emptyGlyph = type(glyph)(component.baseGlyph)
                emptyGlyph.width = self.context.font[component.baseGlyph].width
                glyphSet[component.baseGlyph] = emptyGlyph

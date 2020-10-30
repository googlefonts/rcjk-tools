import importlib
import sys
import os
import objc
from AppKit import NSFormatter
from vanilla import *

from fontTools.ttLib import registerCustomTableClass
import drawBot as db
from drawBot.ui.drawView import DrawView
from drawBot.drawBotDrawingTools import _drawBotDrawingTool
from drawBot.context.drawBotContext import DrawBotContext

try:
    # import rcjktools.utils
    # importlib.reload(rcjktools.utils)
    # import rcjktools.varco
    # importlib.reload(rcjktools.varco)
    import rcjktools
except ImportError:
    libPath = os.path.join(os.path.dirname(os.path.abspath(__file__)), "Lib")
    assert libPath not in sys.path
    sys.path.append(libPath)
    from rcjktools.varco import VarCoFont
    from rcjktools.ttVarCFont import TTVarCFont
else:
    from rcjktools.varco import VarCoFont
    from rcjktools.ttVarCFont import TTVarCFont


def ClassNameIncrementer(clsName, bases, dct):
    orgName = clsName
    counter = 0
    while True:
        try:
            objc.lookUpClass(clsName)
        except objc.nosuchclass_error:
            break
        counter += 1
        clsName = orgName + str(counter)
    return type(clsName, bases, dct)


class VarCoPreviewer:

    def __init__(self, fontPath):
        base, ext = os.path.splitext(fontPath)
        if ext.lower() == ".ufo":
            self.varcoFont = VarCoFont(fontPath)
        elif ext.lower() == ".ttf":
            self.varcoFont = TTVarCFont(fontPath)
        else:
            assert 0, "unsupported file type"

        self.glyphList = sorted(self.varcoFont.keys())

        self.w = Window((1000, 400), f"VarCo Previewer — {fontPath}",
            minSize=(600, 400), autosaveName="VarCoPreviewer")
        self.w.findGlyphField = EditText((10, 10, 180, 20), callback=self.findGlyphFieldCallback)
        self.w.axisSlider = Slider((210, 8, 180, 20), value=0, minValue=0, maxValue=1,
            callback=self.axisSliderCallback)

        top = 40
        self.w.characterGlyphList = List((0, top, 200, 0), self.glyphList,
            allowsMultipleSelection=False,
            allowsSorting=False,
            showColumnTitles=False,
            drawFocusRing=False,
            selectionCallback=self.characterGlyphListSelectionChangedCallback)

        self.w.dbView = DrawView((200, top, 0, 0))  # The DrawBot PDF view
        self.w.characterGlyphList.setSelection([])
        self.w.open()

    def findGlyphFieldCallback(self, sender):
        pat = sender.get().lower()
        if not pat:
            self.w.characterGlyphList.set(self.glyphList)
        else:
            items = [item for item in self.glyphList if pat in item.lower()]
            self.w.characterGlyphList.set(items)
        self.w.characterGlyphList.setSelection([])

    def characterGlyphListSelectionChangedCallback(self, sender):
        self.updateCurrentGlyph()
        self.drawCurrentGlyph()
        self.displayDrawing()

    def updateCurrentGlyph(self):
        sel = self.w.characterGlyphList.getSelection()
        if sel:
            location = dict(wght=self.w.axisSlider.get())
            glyphName = self.w.characterGlyphList[sel[0]]
            self._currentGlyphPath = BezierPath()
            self.varcoFont.drawGlyph(
                self._currentGlyphPath,
                glyphName,
                location,
            )
        else:
            self._currentGlyphPath = None

    def axisSliderCallback(self, sender):
        self.updateCurrentGlyph()
        self.drawCurrentGlyph()
        self.displayDrawing()

    def drawCurrentGlyph(self):
        db.newDrawing()
        db.translate(100, 100)
        db.scale(0.8)
        db.fill(None)
        db.stroke(0.2, 0.3, 1)
        db.rect(0, 0, 1000, 1000)
        db.stroke(None)
        db.translate(0, 120)  # Baseline at 120 from the bottom of the Ideographic Em Square
        db.fill(0, 0.3)
        db.stroke(0)
        if self._currentGlyphPath is not None:
            db.drawPath(self._currentGlyphPath)
        db.endDrawing()

    def displayDrawing(self):
        context = DrawBotContext()
        _drawBotDrawingTool._drawInContext(context)
        self.w.dbView.setPDFDocument(context.getNSPDFDocument())


if __name__ == "__main__":
    from vanilla.dialogs import getFile

    registerCustomTableClass("VarC", "rcjktools.table_VarC", "table_VarC")

    result = getFile("Please select a VarCo .ufo", fileTypes=["ufo", "ttf"])
    if result:
        VarCoPreviewer(result[0])
